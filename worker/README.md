# Кнопки ручной сверки в Telegram (Cloudflare Worker)

Worker — бесплатный «приёмник» в облаке Cloudflare, работает круглосуточно (компьютер не нужен).
Нажали кнопку в Telegram → Worker запускает GitHub Actions «Сверка кассы» → через ~1 минуту приходит отчёт.

Кнопки: **📊 Сегодня · 📅 Вчера · 🗓 Этот месяц · 🗓 Прошлый месяц · 🎲 Выборочная проверка**.
Можно написать и текстом: `23.10.2026` (день), `01.10.2026 31.10.2026` (период), `10.2026` (месяц).

## Настройка (один раз, ~15 минут)

### Шаг 1. Ключ GitHub (чтобы Worker мог запускать сверку)
1. Откройте https://github.com/settings/personal-access-tokens/new
2. **Token name:** `kassa-bot-worker`. **Expiration:** 1 год (или «No expiration»).
3. **Repository access:** *Only select repositories* → выберите `Sverka-Tatyana-Kassa`.
4. **Permissions → Repository permissions → Actions:** *Read and write*. Больше ничего не включайте.
5. **Generate token** → скопируйте ключ `github_pat_...` (его покажут один раз).

### Шаг 2. Worker в Cloudflare
1. Зарегистрируйтесь на https://dash.cloudflare.com/sign-up (бесплатный план).
2. Слева **Workers & Pages** → **Create** → **Start with Hello World** → имя `kassa-bot` → **Deploy**.
3. **Edit code** → удалите весь код → вставьте содержимое файла `worker/worker.js` → **Deploy**.
4. Запомните адрес Worker'а, вида `https://kassa-bot.<ваше-имя>.workers.dev`.

### Шаг 3. Секреты Worker'а
В Worker: **Settings → Variables and Secrets → Add** → тип **Secret** для каждого:

| Имя | Значение |
|---|---|
| `TELEGRAM_BOT_TOKEN` | токен бота (тот же, что в GitHub Secrets) |
| `ALLOWED_CHAT_IDS` | ID админов через запятую |
| `GITHUB_TOKEN` | ключ `github_pat_...` из шага 1 |
| `GITHUB_REPO` | `matrix12345695-afk/Sverka-Tatyana-Kassa` |
| `WEBHOOK_SECRET` | придумайте длинную строку из латинских букв и цифр (30+ символов) |

Нажмите **Deploy**, чтобы секреты применились.

### Шаг 4. Подключить Worker к боту
Откройте в браузере (подставьте свой адрес и свой `WEBHOOK_SECRET`):
```
https://kassa-bot.<ваше-имя>.workers.dev/setup?key=<WEBHOOK_SECRET>
```
Должно появиться `"ok":true`.

### Шаг 5. Проверка
Напишите боту `/start` — внизу появятся кнопки. Нажмите **📊 Сегодня** → бот ответит «⏳ Запустил…», через минуту придёт отчёт.

## Если что-то не так
- **«Нет доступа»** — ID чата не в `ALLOWED_CHAT_IDS`. Узнать ID: команда `/id`.
- **«Не удалось запустить сверку (401/403)»** — ключ GitHub неверный, истёк или без права *Actions: Read and write*.
- **«Не удалось запустить сверку (404)»** — неверный `GITHUB_REPO`.
- **Кнопки не отвечают** — заново откройте ссылку из шага 4.
- **«⏳ Запустил», но отчёта нет** — смотрите вкладку **Actions** в GitHub: там видна ошибка запуска.

Безопасность: Worker принимает запросы только от Telegram (проверка `WEBHOOK_SECRET`) и выполняет команды только от ID из `ALLOWED_CHAT_IDS`. Ключ GitHub может только запускать сверку в этом репозитории.
