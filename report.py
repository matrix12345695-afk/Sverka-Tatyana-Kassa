"""Тексты отчётов для Telegram (HTML)."""
from __future__ import annotations

import random
from datetime import date
from html import escape

from models import METHOD_LABELS, METHODS, Payment
from reconcile import Result

MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль",
          "август", "сентябрь", "октябрь", "ноябрь", "декабрь"]


def money(v: int) -> str:
    return f"{v:,}".replace(",", " ")


def signed(v: int) -> str:
    return ("+" if v > 0 else "−" if v < 0 else "") + money(abs(v))


def d(day: date) -> str:
    return day.strftime("%d.%m")


def billz_day_link(day: date, sub: str) -> str:
    return f"https://{sub}.billz.ai/order/all?page=1&start_date={day:%Y-%m-%d}&end_date={day:%Y-%m-%d}"


def sheet_link(spreadsheet_id: str, gid: str, row: int) -> str:
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit#gid={gid}&range=A{row}"


def _table(rows: list[tuple[str, int, int]], name_w: int = 0) -> str:
    """Узкая раскладка под телефон: имя строкой, ниже Billz · Таблица."""
    lines = []
    for name, b, s in rows:
        if b == s:
            lines.append(f"✅ {escape(name)}: <b>{money(b)}</b>")
        else:
            lines.append(f"❗ {escape(name)}: Billz <b>{money(b)}</b> · Табл. <b>{money(s)}</b> · "
                         f"<b>{signed(b - s)}</b>")
    return "\n".join(lines)


class Reporter:
    def __init__(self, spreadsheet_id: str, billz_sub: str = "app"):
        self.sid = spreadsheet_id
        self.sub = billz_sub

    # --- блоки ------------------------------------------------------------
    def _p_billz(self, p: Payment) -> str:
        return (f'чек <a href="{billz_day_link(p.day, self.sub)}">№{escape(p.ref)}</a>, '
                f"{escape(p.specialist)}, {METHOD_LABELS.get(p.method, p.method)} {money(p.amount)}")

    def _p_sheet(self, p: Payment, gid: str) -> str:
        return (f'<a href="{sheet_link(self.sid, p.gid or gid, p.sheet_row)}">{escape(p.ref)}</a>, '
                f"{escape(p.specialist)}, {METHOD_LABELS.get(p.method, p.method)} {money(p.amount)}")

    def methods_block(self, res: Result) -> str:
        b, s = res.by_method()
        rows = [(METHOD_LABELS[m], b.get(m, 0), s.get(m, 0)) for m in METHODS if b.get(m) or s.get(m)]
        for m in sorted(set(b) | set(s)):
            if m not in METHODS:
                rows.append((m, b.get(m, 0), s.get(m, 0)))
        tb, ts = res.totals()
        rows.append(("ВСЕГО", tb, ts))
        return "<b>Суммы по способам оплаты</b>\n" + _table(rows, 12)

    def specialists_block(self, res: Result, only_diff: bool = False) -> str:
        b, s = res.by_specialist()
        names = sorted(set(b) | set(s), key=lambda n: -max(b.get(n, 0), s.get(n, 0)))
        rows = [(n, b.get(n, 0), s.get(n, 0)) for n in names if not only_diff or b.get(n, 0) != s.get(n, 0)]
        if not rows:
            return "<b>Специалисты</b>: все совпадают ✅"
        tb, ts = res.totals()
        if not only_diff:
            rows.append(("ИТОГО", tb, ts))
        title = "Специалисты с расхождением" if only_diff else "Итоги по специалистам"
        return f"<b>{title}</b>\n" + _table(rows, 17)

    def problems_block(self, res: Result) -> str:
        gid = res.sheet.gid
        out = []
        for p in sorted(res.only_billz, key=lambda x: (x.day, x.ref)):
            out.append(f"• {d(p.day)} <b>{escape(p.client or 'без клиента')}</b> — есть в Billz, НЕТ в таблице: "
                       f"{self._p_billz(p)}")
        for p in sorted(res.only_sheet, key=lambda x: (x.day, x.sheet_row)):
            out.append(f"• {d(p.day)} <b>{escape(p.client)}</b> — есть в таблице, НЕТ в Billz: {self._p_sheet(p, gid)}")
        for m in sorted(res.mismatches, key=lambda x: x.sheet.day):
            what = {"specialist": "другой специалист", "method": "другой способ оплаты",
                    "specialist+method": "другой специалист и способ оплаты"}.get(m.kind, "расхождение")
            out.append(f"• {d(m.sheet.day)} <b>{escape(m.billz.client or m.sheet.client)}</b> — {what}:\n"
                       f"   Billz: {self._p_billz(m.billz)}\n   Таблица: {self._p_sheet(m.sheet, gid)}")
        if not out:
            return "<b>Чеки</b>: все чеки совпали с таблицей ✅"
        return f"<b>Расхождения по чекам ({len(out)})</b>\n" + "\n".join(out)

    def names_block(self, res: Result) -> str:
        pairs = res.name_diffs
        if not pairs:
            return ""
        out = [f"• {d(b.day)} {escape(b.specialist)}, {money(b.amount)}: в таблице «{escape(s.client)}», "
               f"в Billz «{escape(b.client)}» (чек №{escape(b.ref)})" for s, b in sorted(pairs, key=lambda x: x[1].day)]
        return (f"<b>ℹ️ Сумма сходится, но имя клиента другое ({len(out)})</b>\n"
                "<i>Часто это разное написание имени, но так же выглядит и перестановка сумм между клиентами.</i>\n"
                + "\n".join(out))

    def deleted_block(self, res: Result) -> str:
        if not res.billz.deleted:
            return ""
        out = []
        for x in sorted(res.billz.deleted, key=lambda x: x.deleted or x.created):
            line = (f"• №{escape(x.number)} {escape(x.client)}, {money(x.amount)} ({escape(x.methods)}), "
                    f"{escape(x.specialist)}")
            if x.created:
                line += f"; создан {x.created:%d.%m %H:%M}"
            if x.deleted:
                line += f", удалён {x.deleted:%d.%m %H:%M}"
                if x.created:
                    hours = (x.deleted - x.created).total_seconds() / 3600
                    delay = f"через {hours:.0f} ч" if hours < 48 else f"через {hours / 24:.0f} дн."
                    line += f" ({'⚠️ ' if hours >= 24 else ''}{delay})"
            line += f", кто удалил: {escape(x.deleted_by)}"
            out.append(line)
        return f"<b>🗑 Удалённые чеки ({len(out)})</b>\n" + "\n".join(out)

    def partials_block(self, res: Result) -> str:
        if not res.billz.partials:
            return ""
        out = [f"• {d(p.day)} {escape(p.client)} — {escape(p.product[:40])} ({escape(p.specialist)}): "
               f"оплачено {money(p.paid)} из {money(p.list_price)}, чек №{escape(p.number)}"
               for p in sorted(res.billz.partials, key=lambda x: x.day)]
        return (f"<b>💳 Частичные оплаты / крупные скидки ({len(out)})</b>\n"
                "<i>Проверьте: это скидка или клиент ещё должен?</i>\n" + "\n".join(out))

    def sheet_issues_block(self, res: Result) -> str:
        issues = [i for i in res.sheet.issues if i.day and res.start <= i.day <= res.end]
        if not issues:
            return ""
        out = [f'• {d(i.day)} <a href="{sheet_link(self.sid, i.gid or res.sheet.gid, i.row)}">строка {i.row}</a>: {escape(i.text)}'
               for i in issues]
        return f"<b>📝 Ошибки в таблице ({len(out)})</b>\n" + "\n".join(out)

    def days_block(self, res: Result) -> str:
        b, s = res.by_day()
        rows = [(d(day), b.get(day, 0), s.get(day, 0)) for day in sorted(set(b) | set(s)) if b.get(day, 0) != s.get(day, 0)]
        if not rows:
            return "<b>Дни</b>: все дни совпадают ✅"
        return "<b>Дни с расхождением</b>\n" + _table(rows, 8)

    def header(self, res: Result, title: str) -> str:
        tb, ts = res.totals()
        status = "✅ ВСЁ СХОДИТСЯ" if res.ok else "❌ ЕСТЬ РАСХОЖДЕНИЯ"
        diff = "" if tb == ts else f"\nРазница: <b>{signed(tb - ts)}</b>"
        return (f"<b>{title}</b>\n{status}\n"
                f"Billz: <b>{money(tb)}</b> · Таблица: <b>{money(ts)}</b>{diff}")

    # --- отчёты -----------------------------------------------------------
    def daily(self, res: Result, mtd: Result | None = None) -> list[str]:
        parts = [
            self.header(res, f"📊 Сверка кассы за {res.start:%d.%m.%Y}"),
            self.methods_block(res),
            self.specialists_block(res, only_diff=True),
            self.problems_block(res),
            self.deleted_block(res),
            self.names_block(res),
            self.partials_block(res),
            self.sheet_issues_block(res),
        ]
        if not res.billz.payments and not res.sheet.payments:
            parts.insert(1, "Продаж за день нет ни в Billz, ни в таблице.")
        if mtd is not None:
            tb, ts = mtd.totals()
            open_items = len(mtd.only_billz) + len(mtd.only_sheet) + len(mtd.mismatches)
            parts.append(f"<b>С начала месяца</b> ({mtd.start:%d.%m}–{mtd.end:%d.%m}): Billz {money(tb)} · "
                         f"Таблица {money(ts)} · {'✅' if mtd.ok else f'❌ разница {signed(tb - ts)}, открытых расхождений: {open_items}'}")
        return [p for p in parts if p]

    def period(self, res: Result, title: str | None = None) -> list[str]:
        title = title or f"📅 Сверка кассы {res.start:%d.%m.%Y} – {res.end:%d.%m.%Y}"
        parts = [
            self.header(res, title),
            self.methods_block(res),
            self.specialists_block(res),
            self.days_block(res),
            self.problems_block(res),
            self.deleted_block(res),
            self.names_block(res),
            self.partials_block(res),
            self.sheet_issues_block(res),
        ]
        return [p for p in parts if p]

    def monthly(self, res: Result) -> list[str]:
        return self.period(res, f"🗓 Итоги месяца: {MONTHS[res.start.month - 1]} {res.start.year}")

    def spot(self, res: Result, n: int = 5, seed: int | None = None) -> list[str]:
        """Выборочная проверка: случайные чеки для ручной проверки (касса, терминал, банк)."""
        rnd = random.Random(seed)
        pool = res.matched + [(m.sheet, m.billz) for m in res.mismatches]
        if not pool:
            return ["Нет чеков для выборочной проверки за этот период."]
        pick = rnd.sample(pool, min(n, len(pool)))
        lines = []
        for s, b in sorted(pick, key=lambda x: x[1].day):
            lines.append(f"• {d(b.day)} <b>{escape(b.client)}</b> — {escape(b.product[:45])}\n"
                         f"   {self._p_billz(b)}\n   таблица: {self._p_sheet(s, res.sheet.gid)}")
        return [f"<b>🎲 Выборочная проверка: {len(pick)} чек(ов) за {res.start:%d.%m}–{res.end:%d.%m}</b>\n"
                "<i>Сверьте эти чеки с деньгами: наличные — с кассой, карта/QR — с терминалом, перевод — с банком.</i>\n"
                + "\n".join(lines)]


def split_message(text: str, limit: int = 3900) -> list[str]:
    """Telegram ограничивает сообщение 4096 символами — режем по строкам, не ломая <pre>."""
    if len(text) <= limit:
        return [text]
    chunks, cur = [], ""
    in_pre = False
    for line in text.split("\n"):
        add = line + "\n"
        if len(cur) + len(add) > limit and cur:
            if in_pre:
                cur += "</pre>"
            chunks.append(cur.rstrip())
            cur = "<pre>" if in_pre else ""
        cur += add
        in_pre = (in_pre or "<pre>" in line) and "</pre>" not in line
    if cur.strip():
        chunks.append(cur.rstrip())
    return chunks
