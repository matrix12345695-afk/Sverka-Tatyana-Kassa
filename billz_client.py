"""Загрузка чеков из Billz.

Способы входа (по приоритету):
  1) секретный ключ API (BILLZ_SECRET_TOKEN), если когда-нибудь появится;
  2) токен входа (BILLZ_ACCESS_TOKEN или файл billz_token.txt), пока он действует;
  3) телефон + пароль (BILLZ_PHONE, BILLZ_PASSWORD) — автоматический вход,
     когда токена нет или он истёк. Тогда обновлять токен вручную не нужно.
"""
from __future__ import annotations

import base64
import json
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import requests

from models import BillzData, DeletedOrder, PartialPayment, Payment
from names import SpecialistNormalizer, clean

TOKEN_FILE = Path(__file__).resolve().parent / "billz_token.txt"


class BillzError(RuntimeError):
    pass


def token_expiry(token: str) -> datetime | None:
    """Срок действия токена (поле exp внутри JWT); подпись не проверяем."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        exp = json.loads(base64.urlsafe_b64decode(payload)).get("exp")
        return datetime.fromtimestamp(exp, tz=timezone.utc) if exp else None
    except (IndexError, ValueError, TypeError):
        return None


def save_access_token(token: str) -> datetime | None:
    token = token.strip()
    if token.count(".") != 2:
        raise BillzError("Это не похоже на токен Billz (ожидается строка вида xxx.yyy.zzz)")
    exp = token_expiry(token)
    if exp and exp < datetime.now(timezone.utc):
        raise BillzError(f"Этот токен уже истёк ({exp:%d.%m.%Y})")
    TOKEN_FILE.write_text(token, encoding="utf-8")
    return exp


def load_access_token() -> str:
    return TOKEN_FILE.read_text(encoding="utf-8").strip() if TOKEN_FILE.exists() else ""


class BillzClient:
    PAGE_LIMIT = 100

    DEFAULT_PLATFORM_ID = "7d4a4c38-dd84-4902-b744-0488b80a4c01"  # «веб» в Billz

    def __init__(self, api_url: str, secret_token: str, company_id: str, timeout: int = 30,
                 access_token: str = "", phone: str = "", password: str = "",
                 login_url: str = "", platform_id: str = ""):
        self.api_url = api_url
        self.secret_token = secret_token
        self.env_access_token = access_token
        self.phone = phone
        self.password = password
        self.login_url = login_url
        self.platform_id = platform_id or self.DEFAULT_PLATFORM_ID
        self.company_id = company_id
        self.timeout = timeout
        self._token: str | None = None
        self._token_time = 0.0
        self._token_rejected = False  # Billz ответил 401 на сохранённый токен
        self.session = requests.Session()

    def current_access_token(self) -> str:
        return load_access_token() or self.env_access_token

    # --- авторизация ---------------------------------------------------
    @property
    def can_password_login(self) -> bool:
        return bool(self.phone and self.password and self.login_url)

    def _password_login(self) -> str:
        """Вход по телефону и паролю, как на сайте Billz. Возвращает свежий токен."""
        digits = "".join(ch for ch in self.phone if ch.isdigit())
        if len(digits) == 9:  # номер ввели без кода страны
            digits = "998" + digits
        variants = [("998…", digits), ("+998…", "+" + digits)]
        if digits.startswith("998") and len(digits) > 9:
            variants.append(("без 998", digits[3:]))
        # Billz определяет компанию по сайту, с которого пришёл вход (как в браузере)
        site = "/".join(self.login_url.split("/")[:3])  # https://<поддомен>.billz.ai
        headers = {"platform-id": self.platform_id, "Accept": "application/json",
                   "Origin": site, "Referer": site + "/login"}
        seen, errors = set(), []
        for label, phone in variants:  # пробуем разные форматы номера
            if phone in seen:
                continue
            seen.add(phone)
            try:
                r = self.session.post(
                    self.login_url,
                    json={"phone": phone, "password": self.password},
                    headers=headers,
                    timeout=self.timeout,
                )
            except requests.RequestException as e:
                errors.append(f"{label}: нет связи ({type(e).__name__})")
                continue
            if r.status_code == 200:
                token = _find_key(r.json(), "access_token")
                if token:
                    return token
                errors.append(f"{label}: в ответе нет токена")
                continue
            # только код и короткое техническое сообщение Billz — без номера и пароля
            try:
                reason = str((r.json() or {}).get("error") or "")[:60]
            except ValueError:
                reason = "не JSON"
            errors.append(f"{label}: {r.status_code} {reason}".strip())
        raise BillzError(f"Автовход в Billz не удался [{'; '.join(errors)}]. "
                         f"Адрес входа: {self.login_url}. Проверьте BILLZ_PHONE / BILLZ_PASSWORD.")

    def _login(self) -> str:
        if not self.secret_token:
            token = self.current_access_token()
            exp = token_expiry(token) if token else None
            # токен годится, если Billz его ещё не отклонял и он действует хотя бы час
            if token and not self._token_rejected and (
                    not exp or exp - datetime.now(timezone.utc) > timedelta(hours=1)):
                return token
            if self.can_password_login:
                return self._password_login()
            if not token:
                raise BillzError("Нет токена Billz. Добавьте секреты BILLZ_PHONE и BILLZ_PASSWORD "
                                 "(автовход) или токен BILLZ_ACCESS_TOKEN. Как — в README.")
            why = "Billz отклонил токен (был выход из аккаунта)" if self._token_rejected else \
                f"Токен Billz истёк {exp:%d.%m.%Y}" if exp else "Токен Billz недействителен"
            raise BillzError(f"{why}. Добавьте секреты BILLZ_PHONE и BILLZ_PASSWORD для автовхода "
                             "или обновите BILLZ_ACCESS_TOKEN.")
        r = self.session.post(
            f"{self.api_url}/v1/auth/login",
            json={"secret_token": self.secret_token},
            timeout=self.timeout,
        )
        if r.status_code != 200:
            raise BillzError(f"Billz: вход не удался ({r.status_code}): {r.text[:200]}")
        body = r.json()
        token = _find_key(body, "access_token")
        if not token:
            raise BillzError(f"Billz: в ответе нет access_token: {str(body)[:200]}")
        return token

    def _headers(self) -> dict[str, str]:
        # перечитываем токен каждый час (его могли обновить командой /billz_token)
        if not self._token or time.time() - self._token_time > 3600:
            self._token = self._login()
            self._token_time = time.time()
        return {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}

    def _get(self, path: str, params: dict[str, Any]) -> dict:
        for attempt in range(3):
            try:
                r = self.session.get(f"{self.api_url}{path}", params=params,
                                     headers=self._headers(), timeout=self.timeout)
            except requests.RequestException as e:
                if attempt == 2:
                    raise BillzError(f"Billz недоступен: {e}") from e
                time.sleep(2 * (attempt + 1))
                continue
            if r.status_code == 401 and attempt < 2:
                self._token = None
                self._token_rejected = True  # следующий вход — по телефону и паролю, если они заданы
                continue
            if r.status_code >= 500 and attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            if r.status_code in (401, 403):
                raise BillzError("Billz не принял вход (токен истёк, был выход из аккаунта или неверный пароль). "
                                 "Проверьте секреты BILLZ_PHONE / BILLZ_PASSWORD или обновите BILLZ_ACCESS_TOKEN.")
            if r.status_code != 200:
                raise BillzError(f"Billz {path}: {r.status_code} {r.text[:200]}")
            return r.json()
        raise BillzError(f"Billz {path}: не удалось получить данные")

    # --- чеки ----------------------------------------------------------
    def iter_orders(self, start: date, end: date, deleted: bool = False) -> Iterable[dict]:
        page = 1
        seen = 0
        while True:
            params = {
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "page": page,
                "limit": self.PAGE_LIMIT,
                "epos_flag": 0,
                "only_deleted": "true" if deleted else "false",
            }
            if self.company_id:
                params["company_id"] = self.company_id
            body = self._get("/v3/order-search", params)
            batch = [o for g in (body.get("orders_sorted_by_date_list") or []) for o in g.get("orders", [])]
            yield from batch
            seen += len(batch)
            total = body.get("count") or 0
            if not batch or seen >= total:
                break
            page += 1

    def load(self, start: date, end: date, cfg: dict, norm: SpecialistNormalizer) -> BillzData:
        """Чеки с датой продажи в [start, end].

        Billz ищет по дате создания, а отчёты Billz считают по дате проведения
        (бывает на день позже), поэтому берём запас ±1 день и фильтруем сами.
        """
        from datetime import timedelta

        pay_map = {clean(k).lower(): v for k, v in cfg["billz_payment_types"].items()}
        min_disc = cfg.get("partial_payment_min_discount_percent", 30)
        keywords = [k.lower() for k in cfg.get("partial_payment_products", [])]
        data = BillzData()

        for o in self.iter_orders(start - timedelta(days=1), end):
            d = o.get("order_detail") or {}
            sold = _parse_dt(o.get("display_sold_at")) or _parse_dt(d.get("created_at"))
            if not sold or not (start <= sold.date() <= end):
                continue
            items = d.get("order_items") or []
            sellers = [clean(s.get("seller", {}).get("name")) for i in items for s in (i.get("sellers") or [])]
            specialist = norm(sellers[0] if sellers else "")
            client = clean((d.get("customer") or {}).get("name"))
            product = " + ".join(clean((i.get("product") or {}).get("name")) for i in items)
            for p in d.get("order_payments") or []:
                mname = clean((p.get("company_payment_type") or {}).get("name"))
                method = pay_map.get(mname.lower(), mname or "?")
                amount = int(round(p.get("paid_amount") or 0)) - int(round(p.get("returned_amount") or 0))
                if amount == 0:
                    continue
                data.payments.append(Payment(
                    day=sold.date(), specialist=specialist, method=method, amount=amount,
                    client=client, source="billz", ref=o.get("order_number", ""),
                    order_id=o.get("id", ""), product=product,
                ))
            # возможные рассрочки / долги
            for i in items:
                price = int(round(i.get("price") or 0)) * max(1, int(i.get("measurement_value") or 1))
                paid = int(round(i.get("total_price") or 0))
                if price <= 0 or paid <= 0 or paid >= price:
                    continue
                pname = clean((i.get("product") or {}).get("name"))
                disc = 100 * (price - paid) / price
                if disc >= min_disc and any(k in pname.lower() for k in keywords):
                    s = [clean(x.get("seller", {}).get("name")) for x in (i.get("sellers") or [])]
                    data.partials.append(PartialPayment(
                        day=sold.date(), number=o.get("order_number", ""), client=client,
                        product=pname, specialist=norm(s[0] if s else ""), list_price=price, paid=paid,
                    ))

        # удалённые: чек мог быть создан задолго до удаления (видели удаление через 4 дня),
        # поэтому смотрим чеки, созданные за 31 день до начала периода
        for o in self.iter_orders(start - timedelta(days=31), end, deleted=True):
            d = o.get("order_detail") or {}
            created = _parse_dt(d.get("created_at"))
            removed = _parse_dt(o.get("display_deleted_at")) or _parse_dt(o.get("deleted_at"))
            in_period = [x for x in (created, removed) if x and start <= x.date() <= end]
            if not in_period:
                continue
            items = d.get("order_items") or []
            sellers = [clean(s.get("seller", {}).get("name")) for i in items for s in (i.get("sellers") or [])]
            data.deleted.append(DeletedOrder(
                number=o.get("order_number", ""), created=created, deleted=removed,
                client=clean((d.get("customer") or {}).get("name")) or "без клиента",
                amount=int(round(d.get("total_price") or 0)),
                specialist=norm(sellers[0] if sellers else ""),
                methods=", ".join(clean((p.get("company_payment_type") or {}).get("name"))
                                  for p in d.get("order_payments") or []),
                deleted_by=clean(o.get("deleted_by_user_name")) or "?",
            ))
        return data


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(s[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _find_key(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            hit = _find_key(v, key)
            if hit:
                return hit
    elif isinstance(obj, list):
        for v in obj:
            hit = _find_key(v, key)
            if hit:
                return hit
    return None
