import os
import logging
import time
import json
import shutil
import requests
from datetime import datetime
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from python_aternos import Client
from mega import Mega
import psycopg2
from psycopg2.extras import DictCursor

# ================= НАСТРОЙКИ =================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_ID = os.environ.get("CHANNEL_ID")
ADMIN_IDS = [int(x.strip()) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()]
GROUP_ID = os.environ.get("GROUP_ID")
ATERNOS_USER = os.environ.get("ATERNOS_USER")
ATERNOS_PASS = os.environ.get("ATERNOS_PASS")
MEGA_EMAIL = os.environ.get("MEGA_EMAIL")
MEGA_PASS = os.environ.get("MEGA_PASS")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")  # URL твоего Railway приложения

# ================= БАЗА ДАННЫХ (PostgreSQL) =================
DATABASE_URL = os.environ.get("DATABASE_URL")

def init_db():
    """Создаёт таблицу для логов в PostgreSQL"""
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id SERIAL PRIMARY KEY,
            timestamp TEXT,
            username TEXT,
            first_name TEXT,
            user_id TEXT,
            command TEXT,
            result TEXT
        )
    """)
    conn.commit()
    cur.close()
    conn.close()

def log_action(user, command, result):
    """Запись действия в PostgreSQL"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    username = user.username if user.username else "None"
    first_name = user.first_name if user.first_name else "None"
    user_id = str(user.id)
    
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO logs (timestamp, username, first_name, user_id, command, result)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (timestamp, username, first_name, user_id, command, result))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f"Ошибка записи в БД: {e}")

# ================= ИНИЦИАЛИЗАЦИЯ =================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Инициализируем базу данных
init_db()

# Текущие данные Aternos
aternos_user = ATERNOS_USER
aternos_pass = ATERNOS_PASS

# ================= ПРОВЕРКА ПОДПИСКИ =================
async def is_subscribed(user_id, context):
    try:
        member = await context.bot.get_chat_member(CHANNEL_ID, user_id)
        return member.status in ['member', 'administrator', 'creator']
    except Exception as e:
        logger.error(f"Ошибка проверки подписки: {e}")
        return False

# ================= РАБОТА С ATERNOS =================
def get_aternos_client():
    client = Client()
    client.login(aternos_user, aternos_pass)
    return client

# ================= РАБОТА С MEGA =================
def upload_to_mega(file_path, file_name):
    try:
        mega = Mega()
        m = mega.login(MEGA_EMAIL, MEGA_PASS)
        m.upload(file_path)
        logger.info(f"Файл загружен в Mega: {file_name}")
        return True
    except Exception as e:
        logger.error(f"Ошибка загрузки в Mega: {e}")
        return False

# ================= СКАЧИВАНИЕ МИРА =================
def download_world():
    try:
        client = get_aternos_client()
        servers = client.list_servers()
        if not servers:
            return None
        
        server = servers[0]
        session = client._session
        download_url = f"https://aternos.org/worlds/download"
        response = session.get(download_url, stream=True)
        
        if response.status_code == 200:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"world_backup_{timestamp}.zip"
            backup_path = os.path.join("/tmp", backup_name)  # Временная папка
            
            with open(backup_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            
            logger.info(f"Мир скачан: {backup_path}")
            return backup_path
        
        return None
    except Exception as e:
        logger.error(f"Ошибка скачивания мира: {e}")
        return None

# ================= ОБРАБОТЧИКИ КОМАНД =================
# (Все обработчики остаются ТАКИМИ ЖЕ, как в предыдущей версии)
# Я их не буду дублировать, чтобы не захламлять ответ,
# но в финальном коде они будут полными.

# ================= ЗАПУСК С ВЕБХУКОМ =================

# Создаём Flask приложение
flask_app = Flask(__name__)

# Глобальная переменная для бота
bot_app = None

@flask_app.route('/')
def index():
    return "Bot is running!", 200

@flask_app.route('/webhook', methods=['POST'])
def webhook():
    """Принимает обновления от Telegram"""
    global bot_app
    if bot_app is None:
        return "Bot not initialized", 500
    
    update = Update.de_json(request.get_json(force=True), bot_app.bot)
    bot_app.process_update(update)
    return "OK", 200

def run_flask():
    """Запускает Flask-сервер"""
    flask_app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

async def setup_webhook():
    """Устанавливает вебхук для бота"""
    global bot_app
    await bot_app.bot.set_webhook(url=f"{WEBHOOK_URL}/webhook")
    logger.info(f"Webhook установлен: {WEBHOOK_URL}/webhook")

def main():
    global bot_app
    
    # Создаём приложение Telegram
    bot_app = Application.builder().token(BOT_TOKEN).build()
    
    # Регистрируем команды
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CommandHandler("update_credentials", update_credentials))
    bot_app.add_handler(CommandHandler("backup", backup_command))
    bot_app.add_handler(CommandHandler("logs", logs_command))
    bot_app.add_handler(CallbackQueryHandler(button_handler))
    
    # Запускаем Flask в отдельном потоке
    import threading
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    # Устанавливаем вебхук
    import asyncio
    asyncio.run(setup_webhook())
    
    logger.info("Бот запущен на Railway!")
    
    # Держим поток живым
    while True:
        time.sleep(60)

if __name__ == "__main__":
    main()