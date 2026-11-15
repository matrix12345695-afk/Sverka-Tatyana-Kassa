"""Настройки: секреты из .env, справочники из config.json."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"


def _load_env(path: Path) -> None:
    """Простой загрузчик .env (без внешних зависимостей)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env(BASE_DIR / ".env")


@dataclass
class Settings:
    telegram_token: str
    allowed_chat_ids: set[int]
    billz_secret_token: str
    billz_access_token: str
    billz_api_url: str
    billz_company_id: str
    billz_subdomain: str
    spreadsheet_id: str
    google_service_account_file: str
    timezone: str
    daily_report_time: str  # "21:00"


def get_settings() -> Settings:
    ids = {int(x) for x in os.environ.get("ALLOWED_CHAT_IDS", "").replace(" ", "").split(",") if x}
    return Settings(
        telegram_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        allowed_chat_ids=ids,
        billz_secret_token=os.environ.get("BILLZ_SECRET_TOKEN", ""),
        billz_access_token=os.environ.get("BILLZ_ACCESS_TOKEN", ""),
        billz_api_url=os.environ.get("BILLZ_API_URL", "https://api-admin.billz.ai").rstrip("/"),
        billz_company_id=os.environ.get("BILLZ_COMPANY_ID", ""),
        billz_subdomain=os.environ.get("BILLZ_SUBDOMAIN", ""),
        spreadsheet_id=os.environ.get("SPREADSHEET_ID", ""),
        google_service_account_file=os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json"),
        timezone=os.environ.get("TIMEZONE", "Asia/Tashkent"),
        daily_report_time=os.environ.get("DAILY_REPORT_TIME", "21:00"),
    )


def load_config() -> dict:
    if not CONFIG_PATH.exists():  # первый запуск: берём шаблон
        CONFIG_PATH.write_text((BASE_DIR / "config.example.json").read_text(encoding="utf-8"), encoding="utf-8")
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
