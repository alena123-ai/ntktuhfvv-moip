import os
import logging
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler

import pymysql
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
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GIGACHAT_AUTH_KEY = os.getenv("GIGACHAT_AUTH_KEY")
GIGACHAT_SCOPE = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
PORT = int(os.getenv("PORT", 10000))

# ---------- Параметры БД ----------
DB_HOST = os.getenv("DB_HOST")
DB_PORT = int(os.getenv("DB_PORT", 3306))
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_NAME = os.getenv("DB_NAME")

# ---------- Логирование ----------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ---------- Функции для работы с БД ----------
def get_db_connection():
    """Создаёт подключение к MySQL."""
    return pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


def save_message(chat_id: int, user_id: int, username: str, role: str, content: str):
    """Сохраняет одно сообщение (вопрос или ответ) в БД."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO chat_history (chat_id, user_id, username, role, content)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (chat_id, user_id, username, role, content),
            )
        conn.close()
        logger.info(f"Сообщение сохранено: chat_id={chat_id}, role={role}")
    except Exception as e:
        logger.error(f"Ошибка сохранения в БД: {e}", exc_info=True)


def get_history(chat_id: int, limit: int = 10):
    """Возвращает последние N сообщений из БД для контекста."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT role, content FROM chat_history
                WHERE chat_id = %s
                ORDER BY id DESC
                LIMIT %s
                """,
                (chat_id, limit),
            )
            rows = cursor.fetchall()
        conn.close()
        # Возвращаем в хронологическом порядке
        return list(reversed(rows))
    except Exception as e:
        logger.error(f"Ошибка чтения истории из БД: {e}")
        return []


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


# ---------- Команды ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await update.message.reply_text(
        f"Привет, {user.first_name}! 👋\n"
        "Я ИИ-бот на GigaChat. Задай любой вопрос.\n\n"
        "/help — помощь\n"
        "/reset — сбросить историю"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Просто напиши мне вопрос текстом, и я отвечу.\n"
        "/reset — очистить историю диалога."
    )


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Удаляет историю диалога пользователя из БД."""
    chat_id = update.effective_chat.id
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM chat_history WHERE chat_id = %s", (chat_id,))
        conn.close()
        await update.message.reply_text("🧹 История диалога очищена.")
    except Exception as e:
        logger.error(f"Ошибка очистки БД: {e}")
        await update.message.reply_text("⚠️ Не удалось очистить историю.")


# ---------- Обработка сообщений ----------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_message = update.message.text
    if not user_message:
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    username = update.effective_user.username or update.effective_user.first_name

    await update.message.chat.send_action(action="typing")

    # 1. Сохраняем вопрос пользователя в БД
    save_message(chat_id, user_id, username, "user", user_message)

    # 2. Получаем историю из БД для контекста
    history = get_history(chat_id, limit=10)

    try:
        with GigaChat(
            credentials=GIGACHAT_AUTH_KEY,
            scope=GIGACHAT_SCOPE,
            model="GigaChat",
            verify_ssl_certs=False,
        ) as client:
            messages = [
                Messages(
                    role=MessagesRole.SYSTEM,
                    content="Ты полезный ассистент. Отвечай на русском языке, кратко и по делу.",
                )
            ]
            for msg in history:
                if msg["role"] == "user":
                    messages.append(Messages(role=MessagesRole.USER, content=msg["content"]))
                else:
                    messages.append(Messages(role=MessagesRole.ASSISTANT, content=msg["content"]))

            response = client.chat(Chat(messages=messages))
            ai_reply = response.choices[0].message.content.strip()

        # 3. Сохраняем ответ бота в БД
        save_message(chat_id, user_id, username, "assistant", ai_reply)

        # 4. Отправляем ответ (разбиваем длинные сообщения)
        if len(ai_reply) > 4000:
            for i in range(0, len(ai_reply), 4000):
                await update.message.reply_text(ai_reply[i : i + 4000])
        else:
            await update.message.reply_text(ai_reply)

    except Exception as e:
        logger.error(f"Ошибка GigaChat: {e}", exc_info=True)
        await update.message.reply_text(
            "⚠️ Ошибка при обработке запроса. Попробуйте позже."
        )


# ---------- Запуск ----------
def main() -> None:
    if not TELEGRAM_TOKEN:
        raise RuntimeError("Не задан TELEGRAM_TOKEN!")
    if not GIGACHAT_AUTH_KEY:
        raise RuntimeError("Не задан GIGACHAT_AUTH_KEY!")
    if not DB_HOST or not DB_USER or not DB_PASSWORD or not DB_NAME:
        raise RuntimeError("Не заданы переменные для БД (DB_HOST, DB_USER, DB_PASSWORD, DB_NAME)!")

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
