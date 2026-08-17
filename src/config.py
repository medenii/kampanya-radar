"""Ortam değişkenleri ve hedef listesi yükleyici."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "targets.yaml"
STATE_PATH = Path(os.getenv("STATE_PATH", ROOT / "data" / "state.json"))


def _env_bool(key: str, default: bool = False) -> bool:
    val = os.getenv(key)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, "").strip() or default)
    except ValueError:
        return default


@dataclass
class Target:
    name: str
    url: str
    item_selector: str = ""
    link_selector: str = "a"
    title_selector: str = ""
    include_patterns: list[str] = field(default_factory=list)
    exclude_patterns: list[str] = field(default_factory=list)
    wait_selector: str = ""
    wait_ms: int = 2000
    scroll: bool = False
    click_more: str = ""
    max_clicks: int = 5
    detail: bool = True
    enabled: bool = True

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Target":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in raw.items() if k in known and v is not None})


@dataclass
class Settings:
    # LLM
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    groq_model: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

    # SMTP
    smtp_host: str = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port: int = _env_int("SMTP_PORT", 465)
    smtp_user: str = os.getenv("SMTP_USER", "")
    smtp_pass: str = os.getenv("SMTP_PASS", "")
    mail_to: str = os.getenv("MAIL_TO", "")
    mail_from_name: str = os.getenv("MAIL_FROM_NAME", "Kampanya Radar")

    # Davranış
    max_new_per_target: int = _env_int("MAX_NEW_PER_TARGET", 15)
    max_detail_fetch: int = _env_int("MAX_DETAIL_FETCH", 25)
    nav_timeout_ms: int = _env_int("NAV_TIMEOUT_MS", 45000)
    retention_days: int = _env_int("RETENTION_DAYS", 180)
    send_error_report: bool = _env_bool("SEND_ERROR_REPORT", True)
    headless: bool = _env_bool("HEADLESS", True)

    def validate_mail(self) -> list[str]:
        missing = []
        for key in ("smtp_user", "smtp_pass", "mail_to"):
            if not getattr(self, key):
                missing.append(key.upper())
        return missing

    @property
    def recipients(self) -> list[str]:
        return [x.strip() for x in self.mail_to.split(",") if x.strip()]


def load_targets(path: Path = CONFIG_PATH) -> list[Target]:
    if not path.exists():
        raise FileNotFoundError(f"Hedef dosyası bulunamadı: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    raw_targets = data.get("targets") or []
    targets = [Target.from_dict(t) for t in raw_targets]
    return [t for t in targets if t.enabled]


settings = Settings()
