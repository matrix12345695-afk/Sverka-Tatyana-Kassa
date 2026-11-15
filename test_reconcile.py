"""Тест на реальных октябрьских расхождениях (данные — фрагмент листа и чеков Billz)."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from billz_client import BillzClient  # noqa: E402
from config import load_config  # noqa: E402
from names import SpecialistNormalizer  # noqa: E402
from reconcile import reconcile, slice_result  # noqa: E402
from report import Reporter, split_message  # noqa: E402
from sheets_client import guess_tab, parse_sheet_csv  # noqa: E402

SHEET_CSV = '''ю,Ф.И.О,Услуга ,Имя,Наличные ,Перевод,Карта,Qr code,Соль/Памперс/прочее,,,
03.10.2026,Татьяна,,,,,,,,,,
,Иванова Анна ,бассейн абонимент ,Т2,,"2,100,000",,,,,,
,Петрова Вера ,гидромассаж разовое ,М3,,,,,,,,
,Олег Ким ,бассейн абонимент 8,Т1,,,"  2,100,000 ",,,,,
,Итог :,"4,200,000",,  - ,"  2,100,000 ","  2,100,000 ",,,,,
09.10.2026,Татьяна,,,,,,,,,,
,Тимур Алиев ,лечебный + парафин ,М5,"200,000",,,,,,,
,Итог :,"200,000",,"200,000",,,,,,,
23.10.2026,Татьяна,,,,,,,,,,
,Сидоров Марк ,,,,,,,,,,
,Ольга Смирнова ,общеукрепляющий массаж раз,М5,"800,000",,,,,,,
,Итог :,"800,000",,"800,000",,,,,,,
26.10.2026,Татьяна,,,,,,,,,,
,Зарина Каримова ,общеукрепляющий массаж,М4,,,"575,000",,,,,
,Лев Пак,Бассейн пробное ,Т6,"  500,000 ",,,,,,,
,Мила/Лиза Орловы,сплит,Т6,"  300,000 ",,,,,,,
,Итог :,"800,000",,"800,000",  - ,"575,000",,,,,
31.10.2026,Татьяна,,,,,,,,,,
,Нина Белова ,бассейн разовое,Т2,"  350,000 ",,,,,,,
,Итог :,"350,000",,"350,000",,,,,,,
'''


def order(num, created, client, seller, product, price, paid, pays, sold=None, deleted=None):
    return {
        "id": num, "order_number": num, "display_sold_at": sold or created,
        "display_deleted_at": deleted, "deleted_by_user_name": "Kinder Spa",
        "order_detail": {
            "created_at": created, "customer": {"name": client}, "total_price": paid,
            "order_items": [{"product": {"name": product}, "price": price, "measurement_value": 1,
                             "total_price": paid, "sellers": [{"seller": {"name": seller}}]}],
            "order_payments": [{"company_payment_type": {"name": n}, "paid_amount": a} for n, a in pays],
        },
    }


ORDERS = [
    order("010000030236", "2026-10-03 10:00:00", "Анна Иванова", "Тренер Два", "Бассейн (8 посещений)", 2100000, 2100000, [("Пер", 2100000)]),
    order("010000030286", "2026-10-03 11:00:00", "Петрова Вера", "Массажист Три", "Гидромассаж (разовое)", 160000, 160000, [("Карта", 160000)]),
    order("010000031246", "2026-10-03 12:00:00", "Олег Ким", "Тренер Один", "Бассейн (8 посещений)", 2100000, 2100000, [("QR code единный", 2100000)]),
    order("010000090236", "2026-10-09 10:00:00", "Тимур Алиев", "Массажист Четыре", "Парафин + Лечебный массаж", 200000, 200000, [("Наличные", 200000)]),
    order("010002030216", "2026-10-23 10:00:00", "Сидоров Марк", "Массажист Четыре", "Общеукрепляющий курс 10 дней", 1450000, 650000, [("Карта", 650000)]),
    order("010002032206", "2026-10-23 11:00:00", "Ольга Смирнова", "Массажист Пять", "Общеукрепляющий разовый", 800000, 800000, [("Наличные", 800000)]),
    order("010002060266", "2026-10-26 10:00:00", "Зарина Каримова", "Массажист Четыре", "Общеукрепляющий", 575000, 575000, [("Карта", 575000)]),
    order("010002060296", "2026-10-26 11:00:00", "Лев Пак", "Тренер Шесть", "Бассейн пробное", 300000, 300000, [("Наличные", 300000)]),
    order("010002061206", "2026-10-26 12:00:00", "Мила/Лиза Орловы", "Тренер Шесть", "Сплит", 500000, 500000, [("Наличные", 500000)]),
    order("010003001246", "2026-10-30 10:00:00", "Олег Ким", "Тренер Один", "Бассейн (8 посещений)", 2100000, 2100000, [("QR code единный", 2100000)]),
    order("010003001247", "2026-10-30 10:30:00", "Олег Ким", "Тренер Один", "Бассейн (8 посещений)", 2100000, -2100000, [("QR code единный", -2100000)]),
    order("010003011236", "2026-10-31 10:00:00", "Нина Белова", "Тренер Два", "Бассейн разовое", 350000, 350000, [("Наличные", 350000)]),
    order("010003011246", "2026-10-31 11:00:00", "Ева Козлова", "Тренер Один", "Бассейн (4 посещения)", 1200000, 1200000, [("Наличные", 1200000)]),
]
DELETED = [
    order("010001090226", "2026-10-19 12:25:37", "Ольга Смирнова", "Массажист Пять", "Общеукрепляющий курс 10 дней",
          1450000, 1300000, [("Наличные", 1300000)], deleted="2026-10-23 15:11:33"),
]


TEST_SPECIALISTS = {
    "Тренер Один": ["Т1"], "Тренер Два": ["Т2"], "Массажист Три": ["М3"],
    "Массажист Четыре": ["М4"], "Массажист Пять": ["М5"], "Тренер Шесть": ["Т6"],
}


class FakeBillz(BillzClient):
    def __init__(self):
        pass

    def iter_orders(self, start, end, deleted=False):
        src = DELETED if deleted else ORDERS
        return [o for o in src if start <= date.fromisoformat(o["order_detail"]["created_at"][:10]) <= end]


def run(start=date(2026, 10, 1), end=date(2026, 10, 31)):
    cfg = load_config()
    cfg["specialists"] = TEST_SPECIALISTS
    norm = SpecialistNormalizer(cfg["specialists"])
    sheet = parse_sheet_csv(SHEET_CSV, cfg, norm, gid="4540295")
    billz = FakeBillz().load(start, end, cfg, norm)
    return reconcile(billz, sheet, start, end)


def test_findings_match_manual_reconciliation():
    res = run()
    only_billz = sorted((p.ref, p.amount) for p in res.only_billz)
    assert only_billz == [("010000030286", 160000), ("010002030216", 650000), ("010003011246", 1200000)]
    assert res.only_sheet == []
    kinds = sorted((m.billz.ref, m.kind) for m in res.mismatches)
    assert kinds == [("010000031246", "method"), ("010000090236", "specialist")]
    assert len(res.cancelled_returns) == 1
    # 26.10: суммы двух клиентов одного тренера переставлены — деньги сходятся, но имена разные
    swapped = sorted(b.client for _, b in res.name_diffs)
    assert swapped == ["Лев Пак", "Мила/Лиза Орловы"]
    assert not res.ok


def test_sheet_issues_found():
    res = run()
    texts = " | ".join(i.text for i in res.sheet.issues)
    assert "Петрова Вера" in texts and "без суммы" in texts
    assert "Сидоров Марк" in texts
    assert "800 000" in texts  # 26.10 «Итог» не совпадает со строками (575 000 + 500 000)


def test_deleted_and_partials():
    res = run()
    assert [x.number for x in res.billz.deleted] == ["010001090226"]
    assert any(p.number == "010002030216" and p.paid == 650000 for p in res.billz.partials)
    # дневной срез 23.10 тоже видит удаление (чек создан 19.10)
    day = slice_result(res, date(2026, 10, 23), date(2026, 10, 23))
    assert [x.number for x in day.billz.deleted] == ["010001090226"]


def test_reports_render():
    res = run()
    rep = Reporter("SHEET")
    for parts in (rep.period(res), rep.monthly(res), rep.daily(slice_result(res, date(2026, 10, 23), date(2026, 10, 23)), res),
                  rep.spot(res, 3, seed=1)):
        for p in parts:
            for chunk in split_message(p):
                assert len(chunk) <= 4096
    text = "\n".join(rep.period(res))
    assert "ЕСТЬ РАСХОЖДЕНИЯ" in text and "Ева Козлова" in text and "⚠️" in text


def test_token_expiry():
    import base64
    import json as _json

    from billz_client import token_expiry

    payload = base64.urlsafe_b64encode(_json.dumps({"exp": 1795999582}).encode()).decode().rstrip("=")
    exp = token_expiry(f"aaa.{payload}.sig")
    assert exp is not None and exp.year == 2026
    assert token_expiry("not-a-token") is None


def test_guess_tab():
    tabs = [("Касса Октябрь", "1"), ("Касса Октябрь 2026", "2"), ("Касса ноябрь", "3"), ("Аванс Март", "4")]
    assert guess_tab(tabs, 2026, 10) == ("Касса Октябрь 2026", "2")
    assert guess_tab(tabs, 2026, 11) == ("Касса ноябрь", "3")
    assert guess_tab(tabs, 2026, 12) is None
