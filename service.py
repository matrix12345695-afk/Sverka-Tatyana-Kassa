"""Сборка сверки за период: Billz + нужные листы таблицы."""
from __future__ import annotations

from datetime import date, timedelta

from billz_client import BillzClient
from config import Settings, load_config, save_config
from models import SheetData
from names import SpecialistNormalizer
from reconcile import Result, reconcile
from sheets_client import SheetsClient, guess_tab, parse_sheet_csv


def months_between(start: date, end: date) -> list[tuple[int, int]]:
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def month_bounds(y: int, m: int) -> tuple[date, date]:
    first = date(y, m, 1)
    nxt = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
    return first, nxt - timedelta(days=1)


class Service:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.billz = BillzClient(settings.billz_api_url, settings.billz_secret_token, settings.billz_company_id,
                                 access_token=settings.billz_access_token)
        self.sheets = SheetsClient(settings.spreadsheet_id, settings.google_service_account_file)

    def resolve_tab(self, y: int, m: int) -> tuple[str, str]:
        cfg = load_config()
        key = f"{y}-{m:02d}"
        tab = cfg.get("sheet_tabs", {}).get(key)
        if tab:
            return tab["gid"], tab.get("title", key)
        found = guess_tab(self.sheets.list_tabs(), y, m)
        if not found:
            raise LookupError(
                f"Не найден лист кассы за {m:02d}.{y}. Добавьте его командой "
                f"/sheet {m:02d}.{y} <gid> (gid — число после #gid= в адресе листа).")
        title, gid = found
        cfg.setdefault("sheet_tabs", {})[key] = {"gid": gid, "title": title}
        save_config(cfg)
        return gid, title

    def run(self, start: date, end: date) -> Result:
        cfg = load_config()
        norm = SpecialistNormalizer(cfg["specialists"])
        sheet = SheetData()
        gids = []
        for y, m in months_between(start, end):
            gid, _title = self.resolve_tab(y, m)
            part = parse_sheet_csv(self.sheets.fetch_csv(gid), cfg, norm, gid)
            sheet.payments += part.payments
            sheet.issues += part.issues
            gids.append(gid)
        sheet.gid = gids[0] if gids else ""
        billz = self.billz.load(start, end, cfg, norm)
        return reconcile(billz, sheet, start, end)
