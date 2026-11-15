/**
 * Cloudflare Worker: кнопки ручной сверки в Telegram.
 *
 * Telegram присылает сюда нажатия (webhook) → Worker запускает GitHub Actions
 * «Сверка кассы» с нужными параметрами → отчёт приходит в Telegram через ~1 минуту.
 *
 * Переменные (Settings → Variables and Secrets), все как Secret:
 *   TELEGRAM_BOT_TOKEN  — токен бота
 *   ALLOWED_CHAT_IDS    — ID админов через запятую
 *   GITHUB_TOKEN        — fine-grained токен GitHub с правом Actions: Read and write на этот репозиторий
 *   GITHUB_REPO         — владелец/репозиторий, например matrix12345695-afk/Sverka-Tatyana-Kassa
 *   WEBHOOK_SECRET      — любая длинная случайная строка (защита от чужих запросов)
 * Необязательно:
 *   WORKFLOW_FILE       — по умолчанию sverka.yml
 *   TIMEZONE_OFFSET_HOURS — по умолчанию 5 (Ташкент)
 */

const BTN = {
  TODAY: "📊 Сегодня",
  YESTERDAY: "📅 Вчера",
  MONTH: "🗓 Этот месяц",
  PREV_MONTH: "🗓 Прошлый месяц",
  SPOT: "🎲 Выборочная проверка",
  HELP: "❓ Помощь",
};

const KEYBOARD = {
  keyboard: [
    [{ text: BTN.TODAY }, { text: BTN.YESTERDAY }],
    [{ text: BTN.MONTH }, { text: BTN.PREV_MONTH }],
    [{ text: BTN.SPOT }, { text: BTN.HELP }],
  ],
  resize_keyboard: true,
  is_persistent: true,
};

const HELP_TEXT =
  "Кнопки внизу запускают сверку Billz ↔ таблица. Отчёт приходит примерно через минуту.\n\n" +
  "Можно написать дату или период:\n" +
  "• 23.10.2026 — сверка за день\n" +
  "• 01.10.2026 31.10.2026 — сверка за период\n" +
  "• 10.2026 — сверка за месяц\n\n" +
  "Каждый день в 21:00 отчёт приходит сам.";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // Разовая настройка: https://<worker>/setup?key=<WEBHOOK_SECRET>
    if (request.method === "GET" && url.pathname === "/setup") {
      if (!env.WEBHOOK_SECRET || url.searchParams.get("key") !== env.WEBHOOK_SECRET) {
        return new Response("forbidden", { status: 403 });
      }
      const hook = `${url.origin}/telegram`;
      const r = await tg(env, "setWebhook", {
        url: hook,
        secret_token: env.WEBHOOK_SECRET,
        allowed_updates: ["message"],
        drop_pending_updates: true,
      });
      await tg(env, "setMyCommands", {
        commands: [
          { command: "start", description: "Показать кнопки" },
          { command: "help", description: "Помощь" },
          { command: "id", description: "Мой ID чата" },
        ],
      });
      return new Response(`Webhook: ${hook}\nTelegram: ${JSON.stringify(r)}`, {
        headers: { "content-type": "text/plain; charset=utf-8" },
      });
    }

    if (request.method === "POST" && url.pathname === "/telegram") {
      if (request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.WEBHOOK_SECRET) {
        return new Response("forbidden", { status: 403 });
      }
      const update = await request.json().catch(() => null);
      try {
        await handleUpdate(update, env);
      } catch (e) {
        console.log("handle error", e && e.stack);
      }
      return new Response("ok"); // Telegram всегда получает 200, иначе будет слать повторно
    }

    return new Response("Kassa bot worker", { status: 200 });
  },
};

async function handleUpdate(update, env) {
  const msg = update && update.message;
  if (!msg || !msg.chat || typeof msg.text !== "string") return;
  const chatId = msg.chat.id;
  const text = msg.text.trim();

  if (text === "/id" || text.startsWith("/id@")) {
    return send(env, chatId, `ID этого чата: ${chatId}`);
  }

  const allowed = String(env.ALLOWED_CHAT_IDS || "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  if (!allowed.includes(String(chatId))) {
    return send(env, chatId, "Нет доступа. Попросите администратора добавить ваш ID (команда /id).");
  }

  if (text.startsWith("/start") || text === BTN.HELP || text.startsWith("/help")) {
    return send(env, chatId, HELP_TEXT, KEYBOARD);
  }

  const job = parseRequest(text, nowLocal(env));
  if (!job) {
    return send(env, chatId, "Не понял. Нажмите кнопку внизу или напишите дату, например 23.10.2026.", KEYBOARD);
  }

  const res = await dispatch(env, job.inputs);
  if (res.ok) {
    return send(env, chatId, `⏳ Запустил: ${job.title}. Отчёт придёт примерно через минуту.`, KEYBOARD);
  }
  return send(env, chatId, `⚠️ Не удалось запустить сверку (${res.status}): ${res.text.slice(0, 300)}`, KEYBOARD);
}

/** Текст сообщения → параметры запуска. now — «местная» дата (Ташкент). */
export function parseRequest(text, now) {
  const fmt = (d) => `${pad(d.getUTCDate())}.${pad(d.getUTCMonth() + 1)}.${d.getUTCFullYear()}`;
  const fmtMonth = (d) => `${pad(d.getUTCMonth() + 1)}.${d.getUTCFullYear()}`;

  if (text === BTN.TODAY || text === "/today") {
    return { title: `сверка за ${fmt(now)}`, inputs: { kind: "daily", day: fmt(now) } };
  }
  if (text === BTN.YESTERDAY) {
    const y = new Date(now.getTime() - 86400000);
    return { title: `сверка за ${fmt(y)}`, inputs: { kind: "daily", day: fmt(y) } };
  }
  if (text === BTN.MONTH) {
    return { title: `сверка за ${fmtMonth(now)} (с начала месяца)`, inputs: { kind: "month", month: fmtMonth(now) } };
  }
  if (text === BTN.PREV_MONTH) {
    const p = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - 1, 1));
    return { title: `сверка за ${fmtMonth(p)}`, inputs: { kind: "month", month: fmtMonth(p) } };
  }
  if (text === BTN.SPOT) {
    return { title: "выборочная проверка (5 случайных чеков месяца)", inputs: { kind: "spot" } };
  }

  const dates = [...text.matchAll(/(\d{1,2})\.(\d{1,2})\.(\d{4})/g)].map((m) => valid(+m[3], +m[2], +m[1]));
  if (dates.length === 2 && dates[0] && dates[1]) {
    let [a, b] = dates;
    if (b < a) [a, b] = [b, a];
    return { title: `сверка за ${fmt(a)}–${fmt(b)}`, inputs: { kind: "period", start: fmt(a), end: fmt(b) } };
  }
  if (dates.length === 1 && dates[0]) {
    return { title: `сверка за ${fmt(dates[0])}`, inputs: { kind: "daily", day: fmt(dates[0]) } };
  }
  const m = text.match(/^(\d{1,2})\.(\d{4})$/);
  if (m && +m[1] >= 1 && +m[1] <= 12) {
    return { title: `сверка за ${pad(+m[1])}.${m[2]}`, inputs: { kind: "month", month: `${pad(+m[1])}.${m[2]}` } };
  }
  return null;
}

function valid(y, mo, d) {
  const dt = new Date(Date.UTC(y, mo - 1, d));
  return dt.getUTCFullYear() === y && dt.getUTCMonth() === mo - 1 && dt.getUTCDate() === d ? dt : null;
}

function pad(n) {
  return String(n).padStart(2, "0");
}

function nowLocal(env) {
  const off = Number(env.TIMEZONE_OFFSET_HOURS || 5);
  const t = new Date(Date.now() + off * 3600000);
  return new Date(Date.UTC(t.getUTCFullYear(), t.getUTCMonth(), t.getUTCDate()));
}

async function dispatch(env, inputs) {
  const wf = env.WORKFLOW_FILE || "sverka.yml";
  const r = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/${wf}/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "kassa-bot-worker",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ ref: "main", inputs }),
  });
  return { ok: r.status === 204, status: r.status, text: r.ok ? "" : await r.text() };
}

async function tg(env, method, body) {
  const r = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return r.json().catch(() => ({ ok: false }));
}

function send(env, chatId, text, replyMarkup) {
  const body = { chat_id: chatId, text };
  if (replyMarkup) body.reply_markup = replyMarkup;
  return tg(env, "sendMessage", body);
}
