"""Kampanya kategorilendirme.

İki katman:
  1) KURAL TABANLI  : Başlık + URL içinde anahtar kelime araması. Ücretsiz, anında,
                      deterministik. Kampanyaların ~%90'ını yakalar.
  2) LLM YEDEĞİ     : Hiçbir kurala uymayanlar Gemini'ye sorulur. Böylece "Diğer"
                      kutusu şişmez, LLM kotası da yanmaz (günde ~5-10 çağrı).

Bir kampanya birden çok etikete sahip olabilir (örn. "Migros'ta 500 TL chip-para"
hem MARKET hem PUAN). Excel'de:
    Kategori  = ana kategori (öncelik sırasına göre ilk eşleşen)
    Etiketler = tüm eşleşmeler, virgülle
"""
from __future__ import annotations

import logging
import re
import unicodedata

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Kategori tanımları
# --------------------------------------------------------------------------- #
# ÖNEMLİ: Sıra = öncelik. Bir kampanya birden çok kategoriye uyarsa, listede
# YUKARIDA olan "ana kategori" seçilir. Yeni kategori eklemek için buraya bir
# satır eklemen yeterli; kodun geri kalanına dokunmana gerek yok.
# Anahtar kelimeler Türkçe karakterden bağımsız eşleşir (ı/i, ş/s, ğ/g ...).

CATEGORIES: list[tuple[str, list[str]]] = [
    ("Eğitim & Kırtasiye", [
        "egitim", "okul", "ogrenci", "kirtasiye", "universite", "kampus",
        "kitap", "idefix", "kitapyurdu", "ders", "kurs", "isic", "genc akbankli",
        "harclik", "okul odemesi", "ofix", "d&r", "back to school", "burs",
    ]),
    ("Market & Gıda", [
        "market", "migros", "a101", "sok market", "carrefour", "bim", "gida",
        "getiryemek", "yemeksepeti", "yemek", "manav", "sok marketlerde",
        "tarim kredi", "hizli market",
    ]),
    ("Restoran & Kahve", [
        "restoran", "kafe", "cafe", "kahve", "starbucks", "tchibo", "coffee",
        "kahhve", "bar ", "brunch",
    ]),
    ("Akaryakıt & Ulaşım", [
        "akaryakit", "benzin", "motorin", "shell", "opet", "petrol ofisi",
        "totalenergies", "yakitmatik", "otopark", "ispark", "hgs", "ogs",
        "uber", "tiktak", "arac kiralama", "enterprise", "europcar", "sarj",
        "elektrikli arac", "deniz dolmus", "toplu tasima",
    ]),
    ("Seyahat & Turizm", [
        "seyahat", "tatil", "otel", "ucak bileti", "ucus", "bilet.com",
        "biletcom", "ets", "etstur", "jolly", "duty free", "yurt disi cikis",
        "feribot", "turizm", "ucuzabilet", "mil puan", "havalimani", "lounge",
        "vize", "muzekart",
    ]),
    ("Giyim & Moda", [
        "giyim", "moda", "lc waikiki", "zara", "bershka", "stradivarius",
        "massimo dutti", "pull&bear", "pullandbear", "oysho", "lefties",
        "koton", "mavi", "flo", "superstep", "puma", "converse", "lacoste",
        "gant", "derimod", "nine west", "boyner", "civil", "nautica",
        "lee ve wrangler", "fashfed", "ayakkabi", "aksesuar",
    ]),
    ("Elektronik & Teknoloji", [
        "elektronik", "teknoloji", "iphone", "samsung", "tablet", "telefon",
        "bilgisayar", "laptop", "apple", "microsoft", "esim", "beyaz esya",
        "hepsiburada", "amazon", "trendyol", "pazarama", "n11", "e-ticaret",
        "eticaret", "internet alisveris",
    ]),
    ("Dijital Abonelik & Eğlence", [
        "netflix", "spotify", "youtube", "disney", "blutv", "exxen", "gain",
        "mubi", "tod ", "digiturk", "dijital platform", "oyun", "game",
        "chatgpt", "gemini", "claude", "canva", "meditopia", "abonelik",
        "tiyatro", "sinema", "etkinlik", "mobilet", "zorlu psm", "konser",
        "kultur sanat",
    ]),
    ("Sağlık & Kişisel Bakım", [
        "saglik", "hastane", "acibadem", "dunyagoz", "eczane", "eczaci",
        "watsons", "kozmetik", "guzellik", "spor salonu", "yoga", "fit",
        "mamografi", "beije", "ped", "ino beauty", "diyet", "beslenme",
        "sigorta poli", "tamamlayici saglik",
    ]),
    ("Ev & Yaşam", [
        "mobilya", "dekorasyon", "yapi market", "english home", "madame coco",
        "miniso", "beyaz esya", "cicek", "ciceksepeti", "petlebi", "evcil",
        "sosyopix", "zuppin", "ecrou", "heartbeat", "koltuk", "tefal",
    ]),
    ("Kredi & Faiz", [
        "kredi", "faiz", "nakit avans", "taksitli avans", "arti para",
        "ek hesap", "konut kredisi", "tasit kredisi", "ihtiyac kredisi",
        "borc transferi", "kmh", "kredili mevduat", "limit",
    ]),
    ("Mevduat & Yatırım", [
        "mevduat", "vadeli hesap", "birikim", "yatirim", "fon", "hisse",
        "altin", "doviz", "bes", "bireysel emeklilik", "borsa", "portfoy",
        "kumbara", "faiz orani", "stablex", "kripto",
    ]),
    ("Fatura & Ödeme", [
        "fatura", "otomatik odeme", "talimat", "vergi", "mtv", "aidat",
        "kira odeme", "eft", "havale", "fast", "para transferi", "qr odeme",
        "karekod", "temassiz", "dbs", "pos", "yazarkasa", "tahsilat",
    ]),
    ("Yeni Müşteri & Davet", [
        "musteri ol", "musterimiz olun", "davet et", "arkadasini", "tavsiye",
        "hos geldin", "yeni musteri", "uzaktan musteri", "mobilden akbankli",
        "getirfinansli ol", "ilk kez", "kodu ile", "koduyla", "davet kodu",
    ]),
    ("Çekiliş & Hediye", [
        "cekilis", "hediye ceki", "surpriz", "sansli", "odul kazan",
        "hediye kutu", "kazanan", "sansini dene",
    ]),
    ("KOBİ & Ticari", [
        "kobi", "ticari", "esnaf", "isletme", "firma", "sirket", "girisimci",
        "ciftci", "tarimsal", "yem alim", "uye is yeri", "uye isyeri",
        "business", "cek plus", "avukatlara", "pazarci",
    ]),
    ("Emekli & Maaş", [
        "emekli", "maas", "promosyon", "emeklilere", "maasini",
    ]),
]

# Kazanım tipi etiketleri — kategoriden BAĞIMSIZ, her zaman ayrıca aranır.
# Kullanıcının "nakit iade" vs "chip-para/worldpuan gibi nakit olmayan" ayrımı burada.
REWARD_TAGS: list[tuple[str, list[str]]] = [
    ("Nakit İade", [
        "nakit iade", "cashback", "para iade", "geri odeme", "iade edilir",
        "ekstre indirimi", "nakit hediye", "tl hediye",
    ]),
    ("Puan (Nakit Değil)", [
        "chip-para", "chip para", "chippara", "worldpuan", "world puan",
        "maxipuan", "parapuan", "bonus", "getirpara", "bankkart lira",
        "bankomat para", "mil puan", "milpuan", "puan kazan", "tl puan",
    ]),
    ("İndirim", [
        "indirim", "%", "yuzde", "ucretsiz", "bedava", "1 alana 1",
    ]),
    ("Taksit", [
        "taksit", "vade farksiz", "pesin fiyatina", "odemesiz donem",
        "vade avantaji", "aya varan vade",
    ]),
    ("Faizsiz / %0", [
        "faizsiz", "%0 faiz", "0 faiz", "sifir faiz", "masrafsiz",
    ]),
]

DEFAULT_CATEGORY = "Diğer"

# Bunlar kampanya değil, liste/kategori sayfası — Excel'i kirletmesin.
JUNK_TITLE_PATTERNS = [
    r"^detayl[ıi] bilgi$",
    r"^biten kampanyalar$",
    r"^di[ğg]er kampanyalar$",
    r"^kart kampanyalar[ıi]$",
    r"^kampanyalar$",
    r"^ba[ğg]lant[ıi]$",
    r"^maximum$",
    r"kampanyalar[ıi]?\s*\(\d+\)$",   # "Giyim-Aksesuar Kampanyaları (48)"
    r"^[a-zçğıöşü\s]+ kampanyalar[ıi]$",
    r"^[a-zçğıöşü\s]+ f[ıi]rsatlar[ıi]$",
    r"^[a-zçğıöşü\s\-]*kampanyalar[ıi]?$",
]

ENDED_PATTERNS = [
    "bu kampanya sona ermistir", "sona ermistir", "kampanya sonlanmistir",
    "biten kampanya",
]


# --------------------------------------------------------------------------- #
def normalize(text: str) -> str:
    """Türkçe karakterleri sadeleştirip küçük harfe çevirir (ı→i, ş→s ...)."""
    if not text:
        return ""
    text = text.replace("İ", "i").replace("I", "i").replace("ı", "i")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.lower().strip()


def is_junk(title: str) -> bool:
    """Kampanya değil, liste sayfası mı?"""
    t = (title or "").strip().lower()
    if len(t) < 3:
        return True
    return any(re.search(p, t) for p in JUNK_TITLE_PATTERNS)


def clean_title(title: str, url: str) -> str:
    """Başlık işe yaramazsa ('Detaylı Bilgi', 'Bağlantı') URL slug'ından üretir.

    Halkbank gibi bazı siteler kartın üzerine tek tip buton metni koyuyor;
    o zaman gerçek kampanya adı yalnızca linkin içinde kalıyor.
    Slug da anlamsızsa boş string döner -> satır Excel'e alınmaz.
    """
    title = (title or "").strip()
    # DenizBank gibi siteler başlığın sonuna iç ID yapıştırıyor: "... 141100"
    title = re.sub(r"\s+\d{5,}$", "", title).strip()
    if not is_junk(title):
        return title

    slug = url.rstrip("/").split("/")[-1]
    slug = re.sub(r"\.(aspx|html?|php)$", "", slug, flags=re.I)
    slug = re.sub(r"[-_]+\d{4,}$", "", slug)          # sondaki id: ...-141100
    derived = re.sub(r"[-_]+", " ", slug).strip().title()

    if not derived or is_junk(derived):
        return ""
    return derived


def is_ended(title: str) -> bool:
    n = normalize(title)
    return any(p in n for p in ENDED_PATTERNS)


def _match_keywords(haystack: str, keywords: list[str]) -> bool:
    return any(kw in haystack for kw in keywords)


def rule_categorize(title: str, url: str = "", extra: str = "") -> tuple[str, list[str]]:
    """(ana_kategori, etiketler) döndürür. Eşleşme yoksa ("", []) döner."""
    hay = normalize(f"{title} {url} {extra}")
    # Kazanım etiketleri SADECE başlıkta aranır. URL'de aranırsa
    # "bonusdenizbank.com" gibi alan adları her kampanyaya yanlışlıkla
    # "Puan" etiketi yapıştırır.
    title_hay = normalize(f"{title} {extra}")

    matched: list[str] = []
    for name, keywords in CATEGORIES:
        if _match_keywords(hay, keywords):
            matched.append(name)

    rewards: list[str] = []
    for name, keywords in REWARD_TAGS:
        if _match_keywords(title_hay, keywords):
            rewards.append(name)

    primary = matched[0] if matched else ""
    tags = matched + rewards
    return primary, tags


# --------------------------------------------------------------------------- #
# LLM yedeği — yalnızca kurallar boş dönerse çağrılır
# --------------------------------------------------------------------------- #
_LLM_CACHE: dict[str, str] = {}

CATEGORY_NAMES = [name for name, _ in CATEGORIES] + [DEFAULT_CATEGORY]


def llm_categorize(title: str, url: str = "") -> str:
    """Kurallar yakalayamazsa Gemini'ye sorar. Hata olursa 'Diğer' döner."""
    key = normalize(title)[:120]
    if key in _LLM_CACHE:
        return _LLM_CACHE[key]

    try:
        from .config import settings
        if not (settings.gemini_api_key or settings.groq_api_key):
            return DEFAULT_CATEGORY

        from .llm import _call_gemini, _call_groq, _strip_fences
        import json

        options = "\n".join(f"- {c}" for c in CATEGORY_NAMES)
        prompt = (
            "Aşağıdaki banka kampanyasını SADECE şu listeden bir kategoriye ata:\n"
            f"{options}\n\n"
            f"Kampanya başlığı: {title}\nLink: {url}\n\n"
            'Yanıtı sadece şu JSON formatında ver: {"kategori": "..."}'
        )
        raw = _call_gemini(prompt) if settings.gemini_api_key else _call_groq(prompt)
        parsed = json.loads(_strip_fences(raw))
        result = str(parsed.get("kategori", "")).strip()
        if result not in CATEGORY_NAMES:
            result = DEFAULT_CATEGORY
    except Exception as exc:  # noqa: BLE001
        log.debug("LLM kategori başarısız (%s): %s", title[:40], exc)
        result = DEFAULT_CATEGORY

    _LLM_CACHE[key] = result
    return result


def categorize(title: str, url: str = "", extra: str = "",
               use_llm: bool = True) -> tuple[str, list[str]]:
    """Ana giriş noktası. (kategori, etiketler)"""
    primary, tags = rule_categorize(title, url, extra)
    if primary:
        return primary, tags
    if use_llm:
        primary = llm_categorize(title, url)
        if primary and primary != DEFAULT_CATEGORY:
            tags = [primary] + tags
            return primary, tags
    return DEFAULT_CATEGORY, tags or [DEFAULT_CATEGORY]
