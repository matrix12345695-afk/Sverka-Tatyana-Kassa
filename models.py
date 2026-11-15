"""Общие структуры данных для сверки."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

# Канонические способы оплаты
CASH, TRANSFER, CARD, QR, PAYME = "cash", "transfer", "card", "qr", "payme"
METHOD_LABELS = {
    CASH: "Наличные",
    TRANSFER: "Перевод",
    CARD: "Карта",
    QR: "QR code",
    PAYME: "Payme",
}
METHODS = [CASH, TRANSFER, CARD, QR, PAYME]


@dataclass
class Payment:
    """Одна оплата (атом сверки): день, специалист, способ, сумма."""
    day: date
    specialist: str
    method: str
    amount: int
    client: str
    source: str              # "billz" | "sheet"
    ref: str = ""            # номер чека Billz или "строка N" таблицы
    order_id: str = ""
    product: str = ""
    sheet_row: int = 0
    gid: str = ""            # лист таблицы


@dataclass
class DeletedOrder:
    number: str
    created: datetime
    deleted: datetime | None
    client: str
    amount: int
    specialist: str
    methods: str
    deleted_by: str


@dataclass
class PartialPayment:
    """Продажа пакета со скидкой >= порога — возможная рассрочка/долг."""
    day: date
    number: str
    client: str
    product: str
    specialist: str
    list_price: int
    paid: int


@dataclass
class SheetIssue:
    day: date | None
    row: int
    text: str
    gid: str = ""


@dataclass
class BillzData:
    payments: list[Payment] = field(default_factory=list)
    deleted: list[DeletedOrder] = field(default_factory=list)
    partials: list[PartialPayment] = field(default_factory=list)


@dataclass
class SheetData:
    payments: list[Payment] = field(default_factory=list)
    issues: list[SheetIssue] = field(default_factory=list)
    gid: str = ""
