"""Сверка оплат Billz с таблицей по каждому чеку."""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from models import BillzData, Payment, SheetData


@dataclass
class Mismatch:
    kind: str                 # "specialist" | "method" | "specialist+method"
    sheet: Payment
    billz: Payment


@dataclass
class Result:
    start: date
    end: date
    billz: BillzData
    sheet: SheetData
    matched: list[tuple[Payment, Payment]] = field(default_factory=list)
    mismatches: list[Mismatch] = field(default_factory=list)
    only_billz: list[Payment] = field(default_factory=list)
    only_sheet: list[Payment] = field(default_factory=list)
    cancelled_returns: list[tuple[Payment, Payment]] = field(default_factory=list)

    @property
    def name_diffs(self) -> list[tuple[Payment, Payment]]:
        """Пары, совпавшие по деньгам, но с непохожим именем клиента (возможна подмена)."""
        return [(s, b) for s, b in self.matched
                if s.client and b.client and not (_name_words(s.client) & _name_words(b.client))]

    # --- итоги -----------------------------------------------------------
    @staticmethod
    def _sum(payments, key):
        out: dict[str, int] = defaultdict(int)
        for p in payments:
            out[key(p)] += p.amount
        return dict(out)

    def billz_payments(self):
        cancelled = {id(x) for pair in self.cancelled_returns for x in pair}
        return [p for p in self.billz.payments if id(p) not in cancelled]

    def by_method(self):
        return self._sum(self.billz_payments(), lambda p: p.method), self._sum(self.sheet.payments, lambda p: p.method)

    def by_specialist(self):
        return (self._sum(self.billz_payments(), lambda p: p.specialist),
                self._sum(self.sheet.payments, lambda p: p.specialist))

    def by_day(self):
        return self._sum(self.billz_payments(), lambda p: p.day), self._sum(self.sheet.payments, lambda p: p.day)

    def totals(self):
        return sum(p.amount for p in self.billz_payments()), sum(p.amount for p in self.sheet.payments)

    @property
    def ok(self) -> bool:
        b, s = self.totals()
        return b == s and not self.mismatches and not self.only_billz and not self.only_sheet


def _name_words(s: str) -> set[str]:
    return {w for w in re.findall(r"[а-яёa-z]+", s.lower()) if len(w) > 3}


def _score(s: Payment, b: Payment) -> int:
    sc = 0
    if s.specialist == b.specialist:
        sc += 2
    if s.method == b.method:
        sc += 1
    if _name_words(s.client) & _name_words(b.client):
        sc += 4
    return sc


def slice_result(res: Result, start: date, end: date) -> Result:
    """Сверка за под-период из уже загруженных данных (без повторной загрузки)."""
    inside = lambda dt: dt is not None and start <= (dt.date() if hasattr(dt, "date") else dt) <= end  # noqa: E731
    billz = BillzData(
        payments=[p for p in res.billz.payments if start <= p.day <= end],
        deleted=[x for x in res.billz.deleted if inside(x.created) or inside(x.deleted)],
        partials=[x for x in res.billz.partials if start <= x.day <= end],
    )
    sheet = SheetData(payments=list(res.sheet.payments), issues=list(res.sheet.issues), gid=res.sheet.gid)
    return reconcile(billz, sheet, start, end)


def reconcile(billz: BillzData, sheet: SheetData, start: date, end: date) -> Result:
    res = Result(start=start, end=end, billz=billz, sheet=sheet)
    sheet.payments = [p for p in sheet.payments if start <= p.day <= end]

    # 1) возвраты гасят продажу того же клиента на ту же сумму
    bz = list(billz.payments)
    used_ret: set[int] = set()
    #    (берём ближайшую по дате продажу не позже возврата)
    for r in [p for p in bz if p.amount < 0]:
        cands = [p for p in bz if id(p) not in used_ret and p.amount == -r.amount
                 and p.client == r.client and p.day <= r.day]
        if not cands:
            continue
        p = max(cands, key=lambda p: (p.day, p.method == r.method))
        res.cancelled_returns.append((p, r))
        used_ret.update({id(p), id(r)})
    pool = [p for p in bz if id(p) not in used_ret]

    used: set[int] = set()

    # 2) точное совпадение: день, сумма, специалист, способ (лучше — ещё и клиент)
    left_sheet = []
    for s in sheet.payments:
        cands = [b for b in pool if id(b) not in used and b.day == s.day and b.amount == s.amount
                 and b.specialist == s.specialist and b.method == s.method]
        if cands:
            b = max(cands, key=lambda b: _score(s, b))
            used.add(id(b))
            res.matched.append((s, b))
        else:
            left_sheet.append(s)

    # 3) тот же день и сумма, но другой специалист/способ
    left2 = []
    for s in left_sheet:
        cands = [b for b in pool if id(b) not in used and b.day == s.day and b.amount == s.amount]
        if cands:
            b = max(cands, key=lambda b: _score(s, b))
            used.add(id(b))
            kind = "+".join(k for k, bad in (("specialist", b.specialist != s.specialist),
                                              ("method", b.method != s.method)) if bad)
            res.mismatches.append(Mismatch(kind=kind or "other", sheet=s, billz=b))
        else:
            left2.append(s)

    res.only_sheet = left2
    res.only_billz = [b for b in pool if id(b) not in used]
    return res
