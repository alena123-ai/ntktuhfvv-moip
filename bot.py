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
from gigachat import GigaChat
from gigachat.models import Chat, Messages, MessagesRole

# ---------- Переменные окружения ----------
TELEGRAM_TOKEN    = "8996291992:AAHNa_fAbtYzH9DUTfctv59lb5P8dG5z4i8"
GIGACHAT_AUTH_KEY = "MDFhMGMyZWMtMGFhMy03YTUxLThiNzYtNWQ0NDIwNGYzMjNjOmFhOTQxMzg3LTBmZjUtNDk3Yi1hMDYxLWRlNjYyNjI2OWRmMA=="   # Base64 из Sber Studio
GIGACHAT_SCOPE    = "GIGACHAT_API_PERS"
PORT = int(os.getenv("PORT", 10000))

# ---------- Логирование ----------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------- Health-сервер (для Render) ----------
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Bot is alive")

    def log_message(self, format, *args):
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
        "Я ИИ-бот на базе GigaChat. Задай мне любой вопрос.\n\n"
        "Команды:\n"
        "/start — это сообщение\n"
        "/help — помощь\n"
        "/reset — сбросить контекст диалога"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Просто напиши мне свой вопрос текстом, и я отвечу.\n"
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

    await update.message.chat.send_action(action="typing")

    # История диалога в памяти
    history = context.chat_data.get("history", [])
    history.append({"role": "user", "content": user_message})
    history = history[-10:]  # ограничиваем историю

    try:
        # Инициализируем клиент GigaChat для каждого запроса (или можно один раз глобально)
        client = GigaChat(
            credentials=GIGACHAT_AUTH_KEY,
            scope=GIGACHAT_SCOPE,
            model="GigaChat",
            verify_ssl_certs=False,
        )

        # Формируем сообщения для GigaChat
        messages = [
            Messages(role=MessagesRole.SYSTEM, content="Ты полезный ассистент. Отвечай на русском языке, кратко и по делу.")
        ]
        for msg in history:
            if msg["role"] == "user":
                messages.append(Messages(role=MessagesRole.USER, content=msg["content"]))
            elif msg["role"] == "assistant":
                messages.append(Messages(role=MessagesRole.ASSISTANT, content=msg["content"]))

        # Отправляем запрос
        response = client.chat(Chat(messages=messages))
        ai_reply = response.choices[0].message.content.strip()

        # Сохраняем ответ в историю
        history.append({"role": "assistant", "content": ai_reply})
        context.chat_data["history"] = history

        # Отправляем ответ (разбиваем длинные сообщения)
        if len(ai_reply) > 4000:
            for i in range(0, len(ai_reply), 4000):
                await update.message.reply_text(ai_reply[i : i + 4000])
        else:
            await update.message.reply_text(ai_reply)

    except Exception as e:
        logger.error(f"Ошибка GigaChat: {e}")
        await update.message.reply_text(
            "⚠️ Ошибка при обработке запроса. Попробуйте позже."
        )

# ---------- Запуск ----------
def main() -> None:
    if not TELEGRAM_TOKEN:
        raise RuntimeError("Не задан TELEGRAM_TOKEN!")
    if not GIGACHAT_AUTH_KEY:
        raise RuntimeError("Не задан GIGACHAT_AUTH_KEY!")

    Thread(target=run_health_server, daemon=True).start()

    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("reset", reset_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Бот запущен.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
