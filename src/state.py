"""Durum yönetimi (hafıza) + diff mantığı.

Depolama: repo içindeki data/state.json — GitHub Actions her koşudan sonra bu
dosyayı commit'ler. Böylece ücretsiz, kalıcı ve versiyonlanabilir bir "veritabanı"
elde edilir (SQLite alternatifi için dosyanın sonundaki nota bak).

Kayıt anahtarı = sha256(normalize edilmiş URL). Başlık değişse bile aynı kampanya
tekrar bildirilmez; yalnızca gerçekten yeni URL'ler "yeni" sayılır.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from .config import STATE_PATH, settings
from .scraper import ScrapedItem

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def item_key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]


class StateStore:
    def __init__(self, path: Path = STATE_PATH) -> None:
        self.path = Path(path)
        self.data: dict = self._load()

    # ------------------------------------------------------------------ #
    def _load(self) -> dict:
        if not self.path.exists():
            log.info("State dosyası yok, sıfırdan oluşturulacak: %s", self.path)
            return {"version": SCHEMA_VERSION, "seen": {}, "runs": []}
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            data.setdefault("seen", {})
            data.setdefault("runs", [])
            data.setdefault("version", SCHEMA_VERSION)
            return data
        except (json.JSONDecodeError, OSError) as exc:
            # Bozuk state -> yedekle, sıfırdan başla ama HİÇBİR ŞEYİ silme
            log.error("State okunamadı (%s). Yedekleniyor.", exc)
            backup = self.path.with_suffix(".corrupt.json")
            try:
                self.path.replace(backup)
            except OSError:
                pass
            return {"version": SCHEMA_VERSION, "seen": {}, "runs": []}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(self.data, fh, ensure_ascii=False, indent=2, sort_keys=True)
        tmp.replace(self.path)  # atomik yazma
        log.info("State kaydedildi: %d kayıt", len(self.data["seen"]))

    # ------------------------------------------------------------------ #
    def is_known(self, url: str) -> bool:
        return item_key(url) in self.data["seen"]

    def source_count(self, source: str) -> int:
        return sum(1 for v in self.data["seen"].values() if v.get("source") == source)

    def is_first_run_for(self, source: str) -> bool:
        """Bu kaynak daha önce hiç kaydedilmediyse True -> bootstrap modu."""
        return self.source_count(source) == 0

    def diff(self, items: Iterable[ScrapedItem]) -> list[ScrapedItem]:
        """State'te olmayan item'ları döndürür (asıl diff mantığı)."""
        new_items: list[ScrapedItem] = []
        for it in items:
            if not self.is_known(it.url):
                new_items.append(it)
        return new_items

    def mark_seen(self, items: Iterable[ScrapedItem], notified: bool) -> None:
        ts = now_iso()
        for it in items:
            key = item_key(it.url)
            if key in self.data["seen"]:
                self.data["seen"][key]["last_seen"] = ts
                continue
            self.data["seen"][key] = {
                "url": it.url,
                "title": it.title,
                "source": it.source,
                "first_seen": ts,
                "last_seen": ts,
                "notified": notified,
            }

    def record_run(self, summary: dict) -> None:
        summary["ts"] = now_iso()
        self.data["runs"].append(summary)
        self.data["runs"] = self.data["runs"][-60:]  # son 60 koşu

    def prune(self, days: int | None = None) -> int:
        """Çok eski kayıtları temizler; dosya sonsuza kadar şişmesin."""
        days = days or settings.retention_days
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        removed = 0
        for key in list(self.data["seen"].keys()):
            raw = self.data["seen"][key].get("last_seen") or ""
            try:
                seen_at = datetime.fromisoformat(raw)
            except ValueError:
                continue
            if seen_at.tzinfo is None:
                seen_at = seen_at.replace(tzinfo=timezone.utc)
            if seen_at < cutoff:
                del self.data["seen"][key]
                removed += 1
        if removed:
            log.info("%d eski kayıt temizlendi (>%d gün)", removed, days)
        return removed


# --------------------------------------------------------------------------- #
# SQLite istersen: aynı arayüzü sqlite3 ile uygulayıp StateStore yerine koyabilirsin.
# JSON tercih edilme sebebi: GitHub Actions'ta commit-diff'i insan tarafından
# okunabilir olması ve merge çakışmalarının kolay çözülmesi.
# --------------------------------------------------------------------------- #
