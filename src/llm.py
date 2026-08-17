"""Ücretsiz LLM ile yapılandırılmış kampanya özetleme.

Zincir:  Gemini (free tier)  ->  Groq (free tier)  ->  heuristik fallback
Herhangi bir sağlayıcı çökerse sistem DURMAZ; e-posta yine gider,
sadece özet daha ham olur. (Production-ready olmanın şartı: graceful degradation.)
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass

import requests

from .config import settings
from .scraper import ScrapedItem

log = logging.getLogger(__name__)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

SYSTEM_PROMPT = """Sen bir finansal içerik analistisin. Sana bir banka kampanya
sayfasının metni verilecek. Bu metinden kampanya bilgilerini çıkar.

KURALLAR:
- Yalnızca metinde AÇIKÇA yazan bilgiyi kullan. Bilgi yoksa "Belirtilmemiş" yaz.
- Asla tahmin veya uydurma yapma (tutar, tarih, oran uydurmak kesinlikle yasak).
- Türkçe yaz, kısa ve net ol.
- Çıktı SADECE geçerli JSON olsun; markdown, ``` veya açıklama ekleme.

JSON şeması:
{
  "kampanya_adi": "string",
  "ozet": "1-2 cümlelik sade özet",
  "kazanim": "Müşterinin ne kazandığı (tutar/oran/puan)",
  "sartlar": ["koşul 1", "koşul 2"],
  "bitis_tarihi": "GG.AA.YYYY veya Belirtilmemiş",
  "hedef_kitle": "Kimler yararlanabilir",
  "onem_puani": 1-5 arası tam sayı (5 = çok cazip/genel geçer)
}"""


@dataclass
class CampaignSummary:
    kampanya_adi: str = ""
    ozet: str = ""
    kazanim: str = "Belirtilmemiş"
    sartlar: list[str] | None = None
    bitis_tarihi: str = "Belirtilmemiş"
    hedef_kitle: str = "Belirtilmemiş"
    onem_puani: int = 3
    source: str = ""
    url: str = ""
    engine: str = "fallback"

    def dict(self) -> dict:
        d = asdict(self)
        d["sartlar"] = self.sartlar or []
        return d


def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    match = re.search(r"\{.*\}", text, re.S)
    return match.group(0) if match else text


def _build_user_prompt(item: ScrapedItem) -> str:
    body = item.detail_text or "(Detay metni alınamadı)"
    return (
        f"Banka/Kaynak: {item.source}\n"
        f"Sayfa Başlığı: {item.title}\n"
        f"Link: {item.url}\n\n"
        f"--- SAYFA METNİ ---\n{body[:6000]}"
    )


# --------------------------------------------------------------------------- #
# Sağlayıcılar
# --------------------------------------------------------------------------- #
def _call_gemini(prompt: str) -> str:
    url = GEMINI_URL.format(model=settings.gemini_model)
    payload = {
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 900,
            "responseMimeType": "application/json",
        },
    }
    resp = requests.post(
        url,
        params={"key": settings.gemini_api_key},
        json=payload,
        timeout=60,
        headers={"Content-Type": "application/json"},
    )
    if resp.status_code == 429:
        raise RuntimeError("Gemini rate limit (429)")
    resp.raise_for_status()
    data = resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]


def _call_groq(prompt: str) -> str:
    payload = {
        "model": settings.groq_model,
        "temperature": 0.2,
        "max_tokens": 900,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    }
    resp = requests.post(
        GROQ_URL,
        json=payload,
        timeout=60,
        headers={"Authorization": f"Bearer {settings.groq_api_key}"},
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def _heuristic(item: ScrapedItem) -> CampaignSummary:
    text = item.detail_text or ""
    date_match = re.search(r"(\d{1,2}[./]\d{1,2}[./]\d{2,4})", text)
    return CampaignSummary(
        kampanya_adi=item.title,
        ozet=(text[:220] + "…") if text else "Otomatik özet üretilemedi, linke bakın.",
        bitis_tarihi=date_match.group(1) if date_match else "Belirtilmemiş",
        sartlar=[],
        source=item.source,
        url=item.url,
        engine="fallback",
    )


# --------------------------------------------------------------------------- #
def summarize(item: ScrapedItem) -> CampaignSummary:
    prompt = _build_user_prompt(item)
    providers: list[tuple[str, callable]] = []
    if settings.gemini_api_key:
        providers.append(("gemini", _call_gemini))
    if settings.groq_api_key:
        providers.append(("groq", _call_groq))

    for name, fn in providers:
        for attempt in range(1, 4):
            try:
                raw = fn(prompt)
                parsed = json.loads(_strip_fences(raw))
                summary = CampaignSummary(
                    kampanya_adi=str(parsed.get("kampanya_adi") or item.title)[:180],
                    ozet=str(parsed.get("ozet") or "")[:600],
                    kazanim=str(parsed.get("kazanim") or "Belirtilmemiş")[:300],
                    sartlar=[str(s)[:220] for s in (parsed.get("sartlar") or [])][:6],
                    bitis_tarihi=str(parsed.get("bitis_tarihi") or "Belirtilmemiş")[:40],
                    hedef_kitle=str(parsed.get("hedef_kitle") or "Belirtilmemiş")[:160],
                    onem_puani=int(parsed.get("onem_puani") or 3),
                    source=item.source,
                    url=item.url,
                    engine=name,
                )
                log.info("Özetlendi (%s): %s", name, summary.kampanya_adi[:60])
                return summary
            except Exception as exc:  # noqa: BLE001
                wait = 4 * attempt
                log.warning("%s denemesi %d başarısız (%s). %ss bekleniyor.",
                            name, attempt, exc, wait)
                time.sleep(wait)
    log.error("Tüm LLM sağlayıcıları başarısız -> heuristik özet kullanılıyor.")
    return _heuristic(item)


def summarize_all(items: list[ScrapedItem], pause: float = 4.0) -> list[CampaignSummary]:
    """Free tier RPM limitlerine takılmamak için istekler arasında bekler."""
    out: list[CampaignSummary] = []
    for idx, item in enumerate(items):
        out.append(summarize(item))
        if idx < len(items) - 1:
            time.sleep(pause)
    out.sort(key=lambda s: (-s.onem_puani, s.source))
    return out
