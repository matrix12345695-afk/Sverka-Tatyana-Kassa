"""Telegram-бот сверки кассы Billz ↔ Google Sheets.

Запуск:  python bot.py
Расписание (время Ташкента, настраивается в .env):
  • каждый день в 21:00 — сверка за сегодня + итог с начала месяца;
  • 1-го числа в 21:00 — полный отчёт за прошлый месяц;
  • по понедельникам в 21:00 — выборочная проверка 5 случайных чеков за прошлую неделю.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from billz_client import BillzError, save_access_token, token_expiry
from config import get_settings, load_config, save_config
from reconcile import slice_result
from report import Reporter, split_message
from service import Service, month_bounds

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger("kassa_bot")

settings = get_settings()
TZ = ZoneInfo(settings.timezone)
service = Service(settings)
reporter = Reporter(settings.spreadsheet_id, settings.billz_subdomain or "app")

HELP = (
    "<b>Бот сверки кассы Billz ↔ таблица</b>\n\n"
    "/today — сверка за сегодня\n"
    "/day 12.11 — сверка за день\n"
    "/period 01.10 31.10 — сверка за период\n"
    "/month — текущий месяц; /month 10.2026 — нужный месяц\n"
    "/spot — 5 случайных чеков текущего месяца для ручной проверки; /spot 10 — десять\n"
    "/sheet 12.2026 123456 — указать лист таблицы (gid) для месяца\n"
    "/billz_token &lt;токен&gt; — обновить вход в Billz (раз в 14 дней, только в личке)\n"
    "/status — срок токена Billz и подключённые листы\n"
    "/id — показать ID этого чата\n\n"
    "Автоматически: каждый день в {t} — дневной отчёт; 1-го числа — итоги прошлого месяца; "
    "по понедельникам — выборочная проверка."
)


def today() -> date:
    return datetime.now(TZ).date()


def parse_day(s: str, default_year: int) -> date:
    m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?", s.strip())
    if not m:
        raise ValueError(f"Не понял дату «{s}». Пример: 12.11 или 12.11.2026")
    y = int(m.group(3)) if m.group(3) else default_year
    if y < 100:
        y += 2000
    return date(y, int(m.group(2)), int(m.group(1)))


def allowed(update: Update) -> bool:
    chat = update.effective_chat
    return bool(chat and chat.id in settings.allowed_chat_ids)


async def send(app_or_ctx, chat_id: int, parts: list[str]) -> None:
    bot = app_or_ctx.bot
    for part in parts:
        for chunk in split_message(part):
            await bot.send_message(chat_id, chunk, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


def build_report(kind: str, start: date, end: date, n: int = 5) -> list[str]:
    if kind == "daily":
        # одна загрузка с начала месяца: из неё и сам день, и итог месяца
        mtd = service.run(start.replace(day=1), start)
        return reporter.daily(slice_result(mtd, start, start), mtd)
    res = service.run(start, end)
    if kind == "month":
        return reporter.monthly(res)
    if kind == "spot":
        return reporter.spot(res, n)
    return reporter.period(res)


async def run_report(ctx, chat_ids, kind: str, start: date, end: date, n: int = 5) -> None:
    try:
        parts = await asyncio.to_thread(build_report, kind, start, end, n)
    except Exception as e:  # отчёт об ошибке тоже важен: молчание = «всё хорошо»
        log.exception("report failed")
        parts = [f"⚠️ Не удалось сделать сверку ({start:%d.%m}–{end:%d.%m}): {type(e).__name__}: {e}"]
    for cid in chat_ids:
        await send(ctx, cid, parts)


# --- команды --------------------------------------------------------------
async def cmd_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"ID этого чата: {update.effective_chat.id}\n"
                                    "Добавьте его в ALLOWED_CHAT_IDS в файле .env и перезапустите бота.")


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return await cmd_id(update, ctx)
    await update.message.reply_text(HELP.format(t=settings.daily_report_time), parse_mode=ParseMode.HTML)


async def cmd_today(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    await update.message.reply_text("⏳ Сверяю…")
    t = today()
    await run_report(ctx, [update.effective_chat.id], "daily", t, t)


async def cmd_day(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    try:
        d = parse_day(ctx.args[0], today().year) if ctx.args else today()
    except ValueError as e:
        return await update.message.reply_text(str(e))
    await update.message.reply_text("⏳ Сверяю…")
    await run_report(ctx, [update.effective_chat.id], "daily", d, d)


async def cmd_period(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    if len(ctx.args) != 2:
        return await update.message.reply_text("Пример: /period 01.10 31.10")
    try:
        a, b = parse_day(ctx.args[0], today().year), parse_day(ctx.args[1], today().year)
    except ValueError as e:
        return await update.message.reply_text(str(e))
    if b < a:
        a, b = b, a
    await update.message.reply_text("⏳ Сверяю…")
    await run_report(ctx, [update.effective_chat.id], "period", a, b)


async def cmd_month(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    t = today()
    if ctx.args:
        m = re.fullmatch(r"(\d{1,2})\.(\d{4})", ctx.args[0])
        if not m:
            return await update.message.reply_text("Пример: /month 10.2026")
        start, end = month_bounds(int(m.group(2)), int(m.group(1)))
    else:
        start, end = month_bounds(t.year, t.month)
    end = min(end, t)
    await update.message.reply_text("⏳ Сверяю месяц…")
    await run_report(ctx, [update.effective_chat.id], "month", start, end)


async def cmd_spot(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    n = int(ctx.args[0]) if ctx.args and ctx.args[0].isdigit() else 5
    t = today()
    await run_report(ctx, [update.effective_chat.id], "spot", t.replace(day=1), t, n=min(n, 30))


async def cmd_sheet(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    if len(ctx.args) != 2 or not re.fullmatch(r"\d{1,2}\.\d{4}", ctx.args[0]) or not ctx.args[1].isdigit():
        return await update.message.reply_text("Пример: /sheet 12.2026 123456789")
    mm, yy = ctx.args[0].split(".")
    cfg = load_config()
    cfg.setdefault("sheet_tabs", {})[f"{yy}-{int(mm):02d}"] = {"gid": ctx.args[1], "title": f"{mm}.{yy}"}
    save_config(cfg)
    await update.message.reply_text(f"Готово: месяц {mm}.{yy} → лист gid={ctx.args[1]}")


def token_status() -> tuple[str, int | None]:
    """Текст о сроке токена Billz и сколько дней осталось."""
    if settings.billz_secret_token:
        return "Billz: вход по секретному ключу ✅", None
    token = service.billz.current_access_token()
    if not token:
        return "Billz: ❌ токена нет — отправьте /billz_token <токен>", 0
    exp = token_expiry(token)
    if not exp:
        return "Billz: токен есть, срок действия неизвестен", None
    days = (exp.astimezone(TZ).date() - today()).days
    mark = "✅" if days > 3 else "⚠️"
    return f"Billz: токен действует до {exp.astimezone(TZ):%d.%m.%Y %H:%M} ({days} дн.) {mark}", days


async def cmd_billz_token(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    # токен не должен висеть в переписке: удаляем сообщение сразу
    try:
        await update.message.delete()
    except Exception:  # в группе у бота может не быть прав на удаление
        pass
    chat = update.effective_chat
    if chat.type != "private":
        return await ctx.bot.send_message(chat.id, "Токен отправляйте только в личном чате с ботом. "
                                                   "Если сообщение не удалилось — удалите его вручную.")
    if not ctx.args:
        return await ctx.bot.send_message(chat.id, "Пример: /billz_token eyJhbGciOi...  (как получить — в README)")
    try:
        exp = save_access_token(ctx.args[0])
    except BillzError as e:
        return await ctx.bot.send_message(chat.id, f"❌ {e}")
    service.billz._token = None
    until = f" до {exp.astimezone(TZ):%d.%m.%Y}" if exp else ""
    await ctx.bot.send_message(chat.id, f"✅ Токен Billz сохранён{until}. Сообщение с токеном удалено. "
                                        "Проверка: /today")


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    text, _ = token_status()
    cfg = load_config()
    tabs = ", ".join(f"{k}: {v.get('title', v['gid'])}" for k, v in sorted(cfg.get("sheet_tabs", {}).items()))
    await update.message.reply_text(f"{text}\nЛисты таблицы: {tabs or 'нет'}\n"
                                    f"Ежедневный отчёт: {settings.daily_report_time} ({settings.timezone})")


# --- расписание -------------------------------------------------------------
async def job_token_check(ctx: ContextTypes.DEFAULT_TYPE):
    text, days = token_status()
    if days is not None and days <= 3:
        for cid in settings.allowed_chat_ids:
            await ctx.bot.send_message(cid, f"🔑 {text}\nОбновите токен до 21:00, иначе отчёт не придёт: "
                                            "/billz_token <новый токен> (в личном чате с ботом).")


async def job_daily(ctx: ContextTypes.DEFAULT_TYPE):
    t = today()
    await run_report(ctx, settings.allowed_chat_ids, "daily", t, t)
    if t.day == 1:  # итоги прошлого месяца
        prev_end = t - timedelta(days=1)
        await run_report(ctx, settings.allowed_chat_ids, "month", prev_end.replace(day=1), prev_end)
    if t.weekday() == 0:  # понедельник — выборочная проверка за прошлую неделю
        await run_report(ctx, settings.allowed_chat_ids, "spot", t - timedelta(days=7), t - timedelta(days=1))


def main() -> None:
    if not settings.telegram_token:
        raise SystemExit("Не задан TELEGRAM_BOT_TOKEN в .env")
    if not settings.allowed_chat_ids:
        log.warning("ALLOWED_CHAT_IDS пуст: бот ответит только на /id. Отчёты никому не уйдут.")
    app = Application.builder().token(settings.telegram_token).build()
    for name, fn in [("start", cmd_help), ("help", cmd_help), ("id", cmd_id), ("today", cmd_today),
                     ("day", cmd_day), ("period", cmd_period), ("month", cmd_month),
                     ("spot", cmd_spot), ("sheet", cmd_sheet), ("billz_token", cmd_billz_token),
                     ("status", cmd_status)]:
        app.add_handler(CommandHandler(name, fn))
    hh, mm = (int(x) for x in settings.daily_report_time.split(":"))
    app.job_queue.run_daily(job_daily, time(hh, mm, tzinfo=TZ), name="daily")
    app.job_queue.run_daily(job_token_check, time(10, 0, tzinfo=TZ), name="token_check")
    log.info("Бот запущен, ежедневный отчёт в %s (%s)", settings.daily_report_time, settings.timezone)
    app.run_polling()


if __name__ == "__main__":
    main()
