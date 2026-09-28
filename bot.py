import os
import logging
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from openai import OpenAI

# ---------- Переменные окружения ----------
TELEGRAM_TOKEN    = "8996291992:AAHNa_fAbtYzH9DUTfctv59lb5P8dG5z4i8"
GIGACHAT_AUTH_KEY = "MDFhMGMyZWMtMGFhMy03YTUxLThiNzYtNWQ0NDIwNGYzMjNjOmFhOTQxMzg3LTBmZjUtNDk3Yi1hMDYxLWRlNjYyNjI2OWRmMA=="   # Base64 из Sber Studio
GIGACHAT_SCOPE    = "GIGACHAT_API_PERS"

# ---------- Логирование ----------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------- Клиент OpenRouter ----------
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)

# Бесплатная модель OpenRouter. Можно заменить на любую с openrouter.ai/models
MODEL_NAME = "mistralai/mistral-7b-instruct:free"


# ---------- Health-сервер (нужен для Render Web Service) ----------
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Bot is alive")

    def log_message(self, format, *args):
        # Отключаем стандартный лог http-сервера, чтобы не засорять вывод
        return


def run_health_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    logger.info(f"Health-сервер запущен на порту {PORT}")
    server.serve_forever()


# ---------- Команды бота ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await update.message.reply_text(
        f"Привет, {user.first_name}! 👋\n"
        "Я ИИ-бот. Задай мне любой вопрос — я постараюсь ответить.\n\n"
        "Команды:\n"
        "/start — это сообщение\n"
        "/help — помощь\n"
        "/reset — сбросить контекст диалога"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Просто напиши мне свой вопрос текстом, и я отвечу.\n"
        "Я не генерирую картинки — только текстовые ответы.\n"
        "/reset — очистить историю диалога."
    )


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.chat_data.clear()
    context.user_data.clear()
    await update.message.reply_text("🧹 История диалога очищена.")


# ---------- Обработка сообщений ----------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_message = update.message.text
    if not user_message:
        return

    # Показываем "печатает..."
    await update.message.chat.send_action(action="typing")

    # История диалога в памяти (по чату)
    history = context.chat_data.get("history", [])
    history.append({"role": "user", "content": user_message})
    # Ограничиваем историю, чтобы не превысить лимит токенов
    history = history[-10:]

    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты полезный ассистент. Отвечай на русском языке, "
                        "кратко и по делу. Не генерируешь изображения."
                    ),
                },
                *history,
            ],
            temperature=0.7,
            max_tokens=1000,
        )

        ai_reply = response.choices[0].message.content.strip()
        history.append({"role": "assistant", "content": ai_reply})
        context.chat_data["history"] = history

        # Telegram ограничивает длину сообщения 4096 символами
        if len(ai_reply) > 4000:
            for i in range(0, len(ai_reply), 4000):
                await update.message.reply_text(ai_reply[i : i + 4000])
        else:
            await update.message.reply_text(ai_reply)

    except Exception as e:
        logger.error(f"Ошибка при обращении к OpenRouter: {e}")
        await update.message.reply_text(
            "⚠️ Извините, произошла ошибка при обработке запроса. "
            "Попробуйте ещё раз через минуту."
        )


# ---------- Запуск ----------
def main() -> None:
    if not TELEGRAM_TOKEN:
        raise RuntimeError("Не задан TELEGRAM_TOKEN в переменных окружения!")
    if not OPENROUTER_API_KEY:
        raise RuntimeError("Не задан OPENROUTER_API_KEY в переменных окружения!")

    # Health-сервер в фоне (для Render)
    Thread(target=run_health_server, daemon=True).start()

    # Создаём и настраиваем бота
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("reset", reset_command))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    logger.info("Бот запущен. Нажмите Ctrl+C для остановки.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
