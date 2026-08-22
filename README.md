# 🏦 Kampanya Radar — Banka Kampanya Takip Agent'ı

Banka kampanya sayfalarını her gün tarar, **sadece yeni eklenenleri** tespit eder,
ücretsiz bir LLM ile özetler ve sana HTML e-posta gönderir. Sunucu yok, maliyet yok.

| Bileşen | Çözüm | Maliyet |
|---|---|---|
| Cron / Runner | GitHub Actions (public repo: sınırsız dakika) | $0 |
| Tarayıcı | Playwright Chromium (runner içinde) | $0 |
| Hafıza / DB | `data/state.json` + otomatik commit | $0 |
| LLM | Google Gemini free tier (yedek: Groq) | $0 |
| E-posta | Gmail SMTP + App Password | $0 |

---

## 1. Sistem Mimarisi

```
                 ┌──────────────────────────────┐
   cron 06:00 UTC│  GitHub Actions Runner        │
   (09:00 TR)    │  (ubuntu-latest, ephemeral)   │
                 └───────────────┬──────────────┘
                                 ▼
   config/targets.yaml ──▶ [1] SCRAPE (Playwright/Chromium)
                                 │   JS render, scroll, "daha fazla" tıkla
                                 ▼
                          liste: (url, başlık)[]
                                 │
      data/state.json ──▶ [2] DIFF  (sha256(normalize(url)) karşılaştırması)
        (git'ten gelen              │
         önceki hafıza)             ▼
                          YALNIZCA yeni item'lar
                                 │
                          [3] DETAY ÇEK (sadece yeni olanlar → hız + token tasarrufu)
                                 ▼
                          [4] LLM ÖZET (Gemini → Groq → heuristik fallback)
                                 │      JSON şema: ad/şart/kazanım/bitiş/link
                                 ▼
                          [5] HTML E-POSTA (yeni yoksa gönderme!)
                                 ▼
                          [6] state.json güncelle → git commit → repo'ya push
                                 (bir sonraki günün hafızası)
```

### Durum yönetimi (state/diff) nasıl çalışır?

1. **Anahtar üretimi:** Her item için `sha256(normalize_url(url))[:20]`.
   `normalize_url` fragment'i (`#...`), `utm_*`/`gclid` gibi takip parametrelerini
   ve sondaki `/` işaretini siler. Böylece aynı kampanya farklı linkle gelse de
   **tekrar bildirilmez**.
2. **Diff:** Bugün taranan item'lardan `state.seen` içinde anahtarı olmayanlar = yeni.
3. **Kalıcılık:** Runner her koşuda sıfırlanır; bu yüzden `state.json` iş bitince
   repoya commit'lenir. Bir sonraki koşu `checkout` ile onu geri okur.
4. **Bootstrap koruması:** Bir kaynak ilk kez taranıyorsa (state'te o kaynaktan hiç
   kayıt yoksa) **e-posta gönderilmez**, tüm item'lar sessizce kaydedilir. İlk gün
   80 kampanyalık spam yemezsin.
5. **Anomali koruması:** Bir kaynaktan aniden `MAX_NEW_PER_TARGET` (varsayılan 15)
   üstü "yeni" gelirse, site yapısı değişmiş demektir; ilk 15'i bildirilir, kalanı
   sessizce kaydedilir.
6. **Budama:** `RETENTION_DAYS` (180) günden eski, artık sitede görünmeyen kayıtlar
   silinir → dosya sonsuza kadar şişmez.
7. **Atomik yazma:** Önce `.tmp`, sonra `replace()`. Koşu ortada kesilse bile
   state bozulmaz; bozulursa `.corrupt.json` olarak yedeklenir.

### Dayanıklılık (production-ready) detayları
- Her hedef izole: biri patlarsa diğerleri devam eder, hata raporu ayrı mail olur.
- Navigasyonda 3 deneme + artan bekleme (exponential backoff).
- LLM zinciri: Gemini → Groq → heuristik. LLM tamamen çökse bile mail yine gider.
- `networkidle` bekleme opsiyonel (bazı siteler asla idle olmaz) — takılma yok.
- Görsel/font/video istekleri engellenir → 2-3x hız.
- Free tier RPM limiti için LLM çağrıları arasında 4 sn bekleme.
- `concurrency` grubu ile eşzamanlı koşu ve state çakışması engellenir.

---

## 2. Kurulum (sıfırdan, ~15 dakika)

### Adım 1 — Repo
```bash
git clone <senin-repon> kampanya-radar && cd kampanya-radar
# veya dosyaları boş bir repoya kopyala
git add . && git commit -m "init" && git push
```
> **Public repo öner:** Actions dakikaları sınırsız. Private repo'da ayda 2000 dk
> ücretsiz var; bu iş günde ~3 dk sürer (~90 dk/ay), o da yeter.

### Adım 2 — Gemini API Key (ücretsiz)
1. https://aistudio.google.com/apikey → **Create API key**
2. Kredi kartı istemez. `gemini-2.0-flash` free tier: ~15 istek/dk, 1500 istek/gün.
   Bu senaryo günde 5-20 istek yapar → bolca yeter.

### Adım 3 — Groq API Key (opsiyonel yedek)
https://console.groq.com/keys → key oluştur. Gemini kota yerse devreye girer.

### Adım 4 — Gmail App Password
1. Google Hesabı → **Güvenlik** → 2 Adımlı Doğrulama'yı **aç** (zorunlu).
2. https://myaccount.google.com/apppasswords → "Mail" için şifre üret.
3. Çıkan 16 haneli kodu boşluksuz kopyala. (Normal Gmail şifren SMTP'de çalışmaz.)

### Adım 5 — GitHub Secrets
Repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret adı | Değer |
|---|---|
| `GEMINI_API_KEY` | AIza... |
| `GROQ_API_KEY` | gsk_... *(opsiyonel)* |
| `SMTP_USER` | gonderen@gmail.com |
| `SMTP_PASS` | 16 haneli app password |
| `MAIL_TO` | alici@ornek.com (virgülle çoklu) |

*(İstersen `Variables` sekmesine `SMTP_HOST`, `SMTP_PORT`, `GEMINI_MODEL` ekleyebilirsin;
eklemezsen varsayılanlar kullanılır.)*

### Adım 6 — Workflow izni
Repo → **Settings → Actions → General → Workflow permissions** →
**Read and write permissions** seç ve kaydet. (state.json commit'i için şart.)

### Adım 7 — Hedefleri ayarla ve test et
`config/targets.yaml` içindeki URL'leri kendi listenle değiştir, sonra:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env      # değerleri doldur
set -a; source .env; set +a

# 1) Selector doğru mu? (mail atmaz, state yazmaz)
python -m src.main --dry-run --debug --only "Garanti"

# 2) LLM'siz hızlı test
python -m src.main --dry-run --no-llm
```
**Beklenen:** her hedef için makul sayıda (5-60) item. `0 item` görüyorsan
`include_patterns` / `item_selector` ayarını düzelt.

### Adım 8 — İlk gerçek koşu
Actions sekmesi → **Daily Campaign Check** → **Run workflow**.
İlk koşu **bootstrap**'tır: mail gelmez, sadece `data/state.json` commit'lenir.
İkinci günden itibaren yalnızca yeni kampanyalar mailine düşer.

---

## 3. Saat ayarı

`daily_check.yml` içindeki cron **UTC**:

| İstenen TR saati | cron |
|---|---|
| 09:00 | `0 6 * * *` |
| 08:00 | `0 5 * * *` |
| 20:00 | `0 17 * * *` |
| Günde 2 kez (09 & 18) | `0 6,15 * * *` |

> GitHub cron'u yoğunlukta 5-30 dk gecikebilir; bu iş için sorun değil.

---

## 4. Komutlar

```bash
python -m src.main                       # normal koşu
python -m src.main --dry-run --debug     # test, hiçbir şeyi değiştirmez
python -m src.main --no-llm              # LLM'siz
python -m src.main --only "Yapı Kredi"   # tek hedef
python -m src.main --reset-source "Yapı Kredi Kampanyalar"  # o kaynağı sıfırla
```

## 5. Sorun giderme

| Belirti | Sebep / Çözüm |
|---|---|
| `0 item bulundu` | `include_patterns` eşleşmiyor. `--dry-run --debug` ile linkleri incele; pattern'i gevşet. |
| Her gün aynı kampanyalar geliyor | Site URL'e değişken parametre ekliyor. `normalize_url`'deki `TRACKING_PARAMS` regex'ine o parametreyi ekle. |
| `SMTP AuthenticationError` | App Password kullanmıyorsun ya da 2FA kapalı. |
| Mail spam'e düşüyor | Gönderen adresini kişilerine ekle / "spam değil" işaretle. |
| `429` Gemini | `GROQ_API_KEY` ekle, ya da `summarize_all(pause=8)` yap. |
| Cron çalışmayı bıraktı | Public repo'da 60 gün commit yoksa GitHub cron'u askıya alır. Bu sistem her gün state commit'lediği için normalde bu olmaz. |
| Site bot koruması (Cloudflare) | O hedefi `enabled: false` yap; alternatif olarak bankanın RSS/mobil sayfasını hedefle. |

## 6. Yasal not
Yalnızca **herkese açık** sayfaları, günde bir kez, düşük hızda tarar. Yine de
hedef sitelerin `robots.txt` ve kullanım şartlarını kontrol et. LLM özetleri hata
içerebilir — resmî koşulları bankanın sayfasından doğrula.

## Dosya yapısı
```
├── .github/workflows/daily_check.yml   # cron + runner
├── config/targets.yaml                 # izlenecek sayfalar
├── data/state.json                     # hafıza (otomatik oluşur/commit'lenir)
├── src/
│   ├── config.py    # ayarlar + hedef yükleyici
│   ├── scraper.py   # Playwright, dinamik içerik, detay çekme
│   ├── state.py     # hafıza + diff mantığı
│   ├── llm.py       # Gemini/Groq yapılandırılmış özetleme
│   ├── mailer.py    # HTML e-posta + SMTP
│   └── main.py      # orkestrasyon
├── requirements.txt
└── .env.example
```
