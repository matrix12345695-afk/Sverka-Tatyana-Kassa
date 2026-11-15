"""Разовый запуск сверки и отправка отчёта в Telegram (для GitHub Actions / планировщика).

Примеры:
  python run_once.py                      # «авто»: дневной отчёт; 1-го — ещё и месяц; в понедельник — выборка
  python run_once.py --kind daily --day 23.10.2026
  python run_once.py --kind month --month 10.2026
  python run_once.py --kind period --start 01.10.2026 --end 31.10.2026
  python run_once.py --kind spot --n 10
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from billz_client import token_expiry
from config import get_settings
from reconcile import slice_result
from report import Reporter, split_message
from service import Service, month_bounds

settings = get_settings()
TZ = ZoneInfo(settings.timezone)


def parse_date(s: str) -> date:
    return datetime.strptime(s, "%d.%m.%Y").date()


def send(parts: list[str]) -> None:
    if not settings.telegram_token or not settings.allowed_chat_ids:
        print("\n\n".join(parts))
        raise SystemExit("Не заданы TELEGRAM_BOT_TOKEN / ALLOWED_CHAT_IDS — отчёт выведен в консоль.")
    url = f"https://api.telegram.org/bot{settings.telegram_token}/sendMessage"
    failed = 0
    for cid in settings.allowed_chat_ids:
        for part in parts:
            for chunk in split_message(part):
                r = requests.post(url, json={"chat_id": cid, "text": chunk, "parse_mode": "HTML",
                                             "disable_web_page_preview": True}, timeout=30)
                if not r.ok:
                    failed += 1
                    print(f"Telegram {cid}: {r.status_code} {r.text[:200]}", file=sys.stderr)
    if failed:
        raise SystemExit(f"Не доставлено сообщений: {failed}")


def token_warning() -> str:
    # при ключе API или автовходе по паролю напоминать про токен не нужно
    if settings.billz_secret_token or (settings.billz_phone and settings.billz_password):
        return ""
    token = settings.billz_access_token
    if not token:
        return "🔑 Нет токена Billz: добавьте секрет BILLZ_ACCESS_TOKEN в GitHub (Settings → Secrets → Actions)."
    exp = token_expiry(token)
    if not exp:
        return ""
    days = (exp.astimezone(TZ).date() - datetime.now(TZ).date()).days
    if days <= 3:
        return (f"🔑 Токен Billz действует до {exp.astimezone(TZ):%d.%m.%Y} (осталось {max(days, 0)} дн.). "
                "Обновите секрет BILLZ_ACCESS_TOKEN в GitHub: Settings → Secrets and variables → Actions.")
    return ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", default="auto", choices=["auto", "daily", "month", "period", "spot"])
    ap.add_argument("--day", help="ДД.ММ.ГГГГ для daily (по умолчанию сегодня)")
    ap.add_argument("--month", help="ММ.ГГГГ для month (по умолчанию прошлый месяц)")
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--n", type=int, default=5)
    a = ap.parse_args()

    today = datetime.now(TZ).date()
    service = Service(settings)
    rep = Reporter(settings.spreadsheet_id, settings.billz_subdomain or "app")
    jobs: list[tuple[str, date, date]] = []

    if a.kind == "auto":
        jobs.append(("daily", today, today))
        if today.day == 1:
            prev = today - timedelta(days=1)
            jobs.append(("month", prev.replace(day=1), prev))
        if today.weekday() == 0:
            jobs.append(("spot", today - timedelta(days=7), today - timedelta(days=1)))
    elif a.kind == "daily":
        d = parse_date(a.day) if a.day else today
        jobs.append(("daily", d, d))
    elif a.kind == "month":
        if a.month:
            mm, yy = a.month.split(".")
            s, e = month_bounds(int(yy), int(mm))
        else:
            prev = today.replace(day=1) - timedelta(days=1)
            s, e = month_bounds(prev.year, prev.month)
        jobs.append(("month", s, min(e, today)))
    elif a.kind == "period":
        jobs.append(("period", parse_date(a.start), parse_date(a.end)))
    else:
        jobs.append(("spot", today.replace(day=1), today))

    parts: list[str] = []
    warn = token_warning()
    if warn:
        parts.append(warn)
    errors = 0
    for kind, s, e in jobs:
        try:
            if kind == "daily":
                mtd = service.run(s.replace(day=1), s)
                parts += rep.daily(slice_result(mtd, s, s), mtd)
            else:
                res = service.run(s, e)
                parts += {"month": rep.monthly, "period": rep.period}.get(kind, lambda r: rep.spot(r, a.n))(res)
        except Exception as ex:  # молчание = «всё хорошо», поэтому об ошибке сообщаем
            errors += 1
            parts.append(f"⚠️ Не удалось сделать сверку ({s:%d.%m}–{e:%d.%m}): {type(ex).__name__}: {ex}")
    send(parts)
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
