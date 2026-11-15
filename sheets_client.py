"""Чтение кассовых листов Google Sheets.

Листы устроены так:
  колонка A — дата дня (02.10.2026) в строке-заголовке дня;
  B — клиент, C — услуга, D — специалист;
  дальше колонки оплат (Наличные, Перевод, Карта, Qr code, Payme) — ищутся по заголовку;
  строки «Итог :» — итоги дня (сверяем с суммой строк).
Правее итогов бывают расходы и черновики — их не читаем.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime

from models import Payment, SheetData, SheetIssue
from names import SpecialistNormalizer, clean

DATE_RE = re.compile(r"^\s*(\d{2})\.(\d{2})\.(\d{4})")


def parse_amount(s: str | None) -> int:
    s = re.sub(r"[\s, ]", "", s or "")
    s = s.replace("UZS", "").replace("сум", "")
    if not s or s in {"-", "—"}:
        return 0
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return int(float(s))
    return 0


def find_payment_columns(rows: list[list[str]], header_map: dict[str, str]) -> dict[int, str]:
    """Колонки оплат по заголовку (первая строка, где они есть), только в первых 12 колонках."""
    for r in rows[:5]:
        cols: dict[int, str] = {}
        for idx, cell in enumerate(r[:12]):
            if idx < 4:
                continue
            low = clean(cell).lower()
            for key, method in header_map.items():
                if key in low and method not in cols.values():
                    cols[idx] = method
                    break
        if len(cols) >= 3:
            return cols
    # запасной вариант — стандартная раскладка
    return {4: "cash", 5: "transfer", 6: "card", 7: "qr"}


def parse_sheet_csv(text: str, cfg: dict, norm: SpecialistNormalizer, gid: str = "") -> SheetData:
    rows = list(csv.reader(io.StringIO(text)))
    cols = find_payment_columns(rows, cfg["sheet_payment_headers"])
    data = SheetData(gid=gid)
    day: date | None = None
    day_sum = 0

    for i, r in enumerate(rows, start=1):
        r = r + [""] * (12 - len(r))
        m = DATE_RE.match(r[0])
        if m:
            day = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            day_sum = 0
            continue
        if day is None:
            continue
        client = clean(r[1])
        if not client and not any(parse_amount(r[c]) for c in cols):
            continue
        if client.lower().startswith("итог"):
            stated = parse_amount(r[2])
            if stated and stated != day_sum:
                data.issues.append(SheetIssue(day, i, f"строка «Итог» = {stated:,} , а по строкам клиентов {day_sum:,}".replace(",", " ")))
            continue
        amounts = {method: parse_amount(r[c]) for c, method in cols.items()}
        total = sum(amounts.values())
        if total == 0:
            if client:
                data.issues.append(SheetIssue(day, i, f"«{client}» — строка без суммы"))
            continue
        spec_raw = clean(r[3])
        if not spec_raw:
            data.issues.append(SheetIssue(day, i, f"«{client}» — не указан специалист"))
        day_sum += total
        for method, amount in amounts.items():
            if amount:
                data.payments.append(Payment(
                    day=day, specialist=norm(spec_raw), method=method, amount=amount,
                    client=client, source="sheet", ref=f"строка {i}", product=clean(r[2]), sheet_row=i,
                    gid=gid,
                ))
    for issue in data.issues:
        issue.gid = gid
    return data


class SheetsClient:
    """Скачивает лист как CSV.

    Если есть service_account.json — через Google API (таблица может быть закрытой,
    достаточно дать доступ «читатель» почте сервисного аккаунта).
    Иначе — через публичную ссылку экспорта (таблица должна быть доступна по ссылке).
    """

    def __init__(self, spreadsheet_id: str, service_account_file: str | None):
        self.spreadsheet_id = spreadsheet_id
        self.sa_file = service_account_file
        self._session = None

    def _authed_session(self):
        if self._session is None:
            from pathlib import Path

            path = Path(self.sa_file) if self.sa_file else None
            if path and not path.is_absolute():
                path = Path(__file__).resolve().parent / path
            if path and path.exists():
                from google.auth.transport.requests import AuthorizedSession
                from google.oauth2.service_account import Credentials

                creds = Credentials.from_service_account_file(
                    str(path), scopes=["https://www.googleapis.com/auth/spreadsheets.readonly",
                                       "https://www.googleapis.com/auth/drive.readonly"])
                self._session = AuthorizedSession(creds)
            else:
                import requests

                self._session = requests.Session()
        return self._session

    def fetch_csv(self, gid: str) -> str:
        url = f"https://docs.google.com/spreadsheets/d/{self.spreadsheet_id}/export?format=csv&gid={gid}"
        r = self._authed_session().get(url, timeout=60)
        if r.status_code != 200 or "<html" in r.text[:200].lower():
            raise RuntimeError(
                f"Не удалось скачать лист gid={gid} ({r.status_code}). "
                "Проверьте доступ сервисного аккаунта к таблице.")
        return r.content.decode("utf-8")

    def list_tabs(self) -> list[tuple[str, str]]:
        """[(название, gid)] — только при наличии сервисного аккаунта."""
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{self.spreadsheet_id}?fields=sheets.properties"
        r = self._authed_session().get(url, timeout=60)
        if r.status_code != 200:
            return []
        return [(s["properties"]["title"], str(s["properties"]["sheetId"])) for s in r.json().get("sheets", [])]


MONTHS_RU = ["январ", "феврал", "март", "апрел", "ма", "июн", "июл", "август", "сентябр", "октябр", "ноябр", "декабр"]


def guess_tab(tabs: list[tuple[str, str]], year: int, month: int) -> tuple[str, str] | None:
    """Ищет лист «Касса <месяц> [год]». Лист с нужным годом в названии — приоритетнее."""
    stem = MONTHS_RU[month - 1]
    cands = []
    for title, gid in tabs:
        low = title.lower()
        if "касса" not in low:
            continue
        words = re.findall(r"[а-яё]+", low)
        if month == 5:
            ok = "мая" in words or "май" in words
        else:
            ok = any(w.startswith(stem) for w in words)
        if not ok:
            continue
        years = re.findall(r"20\d\d", low)
        if years and str(year) not in years:
            continue
        cands.append((0 if years else 1, title, gid))
    if not cands:
        return None
    cands.sort()
    return cands[0][1], cands[0][2]
