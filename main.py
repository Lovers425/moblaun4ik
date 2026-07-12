import os
import logging
import time
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
CHANNEL_ID = os.environ.get("CHANNEL_ID")  # Проверка подписки
ADMIN_IDS = [int(x.strip()) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()]
ATERNOS_USER = os.environ.get("ATERNOS_USER")
ATERNOS_PASS = os.environ.get("ATERNOS_PASS")
MEGA_EMAIL = os.environ.get("MEGA_EMAIL")
MEGA_PASS = os.environ.get("MEGA_PASS")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")

# ================= БАЗА ДАННЫХ (PostgreSQL) =================
DATABASE_URL = os.environ.get("DATABASE_URL")

def init_db():
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
        logging.error(f"Ошибка записи в БД: {e}")

# ================= ИНИЦИАЛИЗАЦИЯ =================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

init_db()

aternos_user = ATERNOS_USER
aternos_pass = ATERNOS_PASS

# ================= ПРОВЕРКА ПОДПИСКИ НА КАНАЛ =================
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
            backup_path = os.path.join("/tmp", backup_name)
            
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

def manage_backups():
    """Оставляет только 5 последних бекапов в /tmp"""
    try:
        files = [f for f in os.listdir("/tmp") if f.endswith('.zip')]
        files.sort(key=lambda x: os.path.getctime(os.path.join("/tmp", x)))
        
        while len(files) > 5:
            oldest = files.pop(0)
            os.remove(os.path.join("/tmp", oldest))
            logger.info(f"Удалён старый бекап: {oldest}")
    except Exception as e:
        logger.error(f"Ошибка управления бекапами: {e}")

# ================= ОБРАБОТЧИКИ КОМАНД =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    
    if not await is_subscribed(user_id, context):
        await update.message.reply_text(
            "❌ Доступ запрещён!\n\n"
            "Для использования бота необходимо подписаться на канал:\n"
            "[ССЫЛКА НА КАНАЛ]\n\n"
            "После подписки нажми /start снова."
        )
        log_action(user, "/start", "Доступ запрещён (не подписан)")
        return
    
    keyboard = [
        [InlineKeyboardButton("🚀 Запустить", callback_data="start_server")],
        [InlineKeyboardButton("📊 Статус", callback_data="status_server")],
        [InlineKeyboardButton("ℹ️ Инфо", callback_data="info_server")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🎮 Управление сервером\n\n"
        "Выбери действие:",
        reply_markup=reply_markup
    )
    log_action(user, "/start", "Меню показано")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    user_id = user.id
    
    if not await is_subscribed(user_id, context):
        await query.edit_message_text(
            "❌ Доступ запрещён!\n\n"
            "Ты не подписан на канал. Подпишись и попробуй снова."
        )
        log_action(user, query.data, "Доступ запрещён (не подписан)")
        return
    
    if query.data == "start_server":
        await handle_start_server(query, user, context)
    elif query.data == "status_server":
        await handle_status_server(query, user, context)
    elif query.data == "info_server":
        await handle_info_server(query, user, context)

async def handle_start_server(query, user, context):
    await query.edit_message_text("🔄 Подключаюсь к Aternos...")
    
    try:
        client = get_aternos_client()
        servers = client.list_servers()
        
        if not servers:
            await query.edit_message_text("❌ Сервера не найдены!")
            log_action(user, "Запуск сервера", "Ошибка: Сервера не найдены")
            return
        
        server = servers[0]
        status = server.status
        
        if status == "online":
            await query.edit_message_text(
                f"🟢 Сервер уже запущен!\n"
                f"📍 {server.host}:{server.port}"
            )
            log_action(user, "Запуск сервера", "Уже запущен")
            return
        
        await query.edit_message_text("💾 Скачиваю мир для бекапа...")
        backup_path = download_world()
        
        if backup_path:
            await query.edit_message_text("☁️ Загружаю бекап в Mega.nz...")
            upload_to_mega(backup_path, os.path.basename(backup_path))
            manage_backups()
            await query.edit_message_text("✅ Бекап сохранён в Mega.nz!")
            log_action(user, "Запуск сервера", "Бекап создан и загружен в Mega")
        
        await query.edit_message_text("⏳ Запускаю сервер... (3-5 минут)")
        server.start()
        
        wait_counter = 0
        while wait_counter < 24:
            time.sleep(5)
            wait_counter += 1
            updated_server = client._get_server(server.id)
            if updated_server.status == "online":
                success_msg = (
                    f"✅ Сервер запущен!\n"
                    f"📍 {updated_server.host}:{updated_server.port}\n"
                    f"🎮 Версия: {updated_server.version if hasattr(updated_server, 'version') else '1.21.4'}"
                )
                await query.edit_message_text(success_msg)
                log_action(user, "Запуск сервера", "Успешно")
                return
        
        await query.edit_message_text("⚠️ Сервер запускается дольше обычного. Проверь статус через минуту.")
        log_action(user, "Запуск сервера", "Долгий запуск")
        
    except Exception as e:
        error_text = str(e).lower()
        if "banned" in error_text or "login" in error_text:
            await query.edit_message_text(
                "🚫 Аккаунт Aternos забанен!\n\n"
                "Создай новый аккаунт и обнови данные:\n"
                "/update_credentials ЛОГИН ПАРОЛЬ"
            )
            log_action(user, "Запуск сервера", "ОШИБКА: Аккаунт забанен")
        else:
            await query.edit_message_text(f"❌ Ошибка: {str(e)[:200]}")
            log_action(user, "Запуск сервера", f"Ошибка: {str(e)[:100]}")

async def handle_status_server(query, user, context):
    await query.edit_message_text("🔄 Проверяю статус...")
    
    try:
        client = get_aternos_client()
        servers = client.list_servers()
        
        if not servers:
            await query.edit_message_text("❌ Сервера не найдены!")
            log_action(user, "Статус сервера", "Ошибка: Сервера не найдены")
            return
        
        server = servers[0]
        status = server.status
        
        status_emoji = {
            "online": "🟢",
            "offline": "🔴",
            "starting": "🟡",
            "stopping": "🟡"
        }.get(status, "⚪")
        
        if status == "online":
            players = getattr(server, 'players_online', '?')
            max_players = getattr(server, 'players_max', '?')
            response = f"{status_emoji} Сервер: **{status.upper()}**\n👥 Онлайн: {players}/{max_players}"
        else:
            response = f"{status_emoji} Сервер: **{status.upper()}**"
        
        await query.edit_message_text(response, parse_mode='Markdown')
        log_action(user, "Статус сервера", f"Статус: {status}")
        
    except Exception as e:
        error_text = str(e).lower()
        if "banned" in error_text or "login" in error_text:
            await query.edit_message_text("🚫 Аккаунт Aternos забанен! Обнови данные: /update_credentials")
            log_action(user, "Статус сервера", "ОШИБКА: Аккаунт забанен")
        else:
            await query.edit_message_text(f"❌ Ошибка: {str(e)[:200]}")
            log_action(user, "Статус сервера", f"Ошибка: {str(e)[:100]}")

async def handle_info_server(query, user, context):
    await query.edit_message_text("🔄 Загружаю информацию...")
    
    try:
        client = get_aternos_client()
        servers = client.list_servers()
        
        if not servers:
            await query.edit_message_text("❌ Сервера не найдены!")
            log_action(user, "Инфо сервера", "Ошибка: Сервера не найдены")
            return
        
        server = servers[0]
        
        response = (
            f"ℹ️ **Информация о сервере**\n\n"
            f"📍 IP: `{server.host}`\n"
            f"🔌 Порт: `{server.port}`\n"
            f"📦 Версия: {server.version if hasattr(server, 'version') else '1.21.4'}\n"
            f"🎮 Режим: {server.mode if hasattr(server, 'mode') else 'Выживание'}\n"
            f"👥 Онлайн: {getattr(server, 'players_online', 0)}/{getattr(server, 'players_max', 20)}"
        )
        
        await query.edit_message_text(response, parse_mode='Markdown')
        log_action(user, "Инфо сервера", "Показана информация")
        
    except Exception as e:
        error_text = str(e).lower()
        if "banned" in error_text or "login" in error_text:
            await query.edit_message_text("🚫 Аккаунт Aternos забанен! Обнови данные: /update_credentials")
            log_action(user, "Инфо сервера", "ОШИБКА: Аккаунт забанен")
        else:
            await query.edit_message_text(f"❌ Ошибка: {str(e)[:200]}")
            log_action(user, "Инфо сервера", f"Ошибка: {str(e)[:100]}")

# ================= КОМАНДЫ АДМИНА =================

async def update_credentials(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    if user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ Нет прав для этой команды!")
        log_action(user, "/update_credentials", "Доступ запрещён (не админ)")
        return
    
    args = context.args
    if len(args) >= 2:
        new_login = args[0]
        new_password = " ".join(args[1:])
        
        global aternos_user, aternos_pass
        aternos_user = new_login
        aternos_pass = new_password
        
        with open("credentials.txt", "w", encoding="utf-8") as f:
            f.write(f"{new_login}\n{new_password}")
        
        await update.message.reply_text(f"✅ Данные Aternos обновлены!\nЛогин: {new_login}")
        log_action(user, "/update_credentials", f"Данные обновлены для {new_login}")
    else:
        await update.message.reply_text(
            "❌ Неверный формат!\n"
            "Используй: `/update_credentials ЛОГИН ПАРОЛЬ`",
            parse_mode='Markdown'
        )

async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    if user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ Нет прав для этой команды!")
        log_action(user, "/backup", "Доступ запрещён (не админ)")
        return
    
    await update.message.reply_text("💾 Создаю бекап мира...")
    
    try:
        backup_path = download_world()
        
        if backup_path:
            upload_to_mega(backup_path, os.path.basename(backup_path))
            manage_backups()
            
            await update.message.reply_text(
                f"✅ Бекап создан и загружен в Mega.nz!\n"
                f"📁 Файл: {os.path.basename(backup_path)}"
            )
            log_action(user, "/backup", "Бекап создан и загружен в Mega")
        else:
            await update.message.reply_text("❌ Ошибка создания бекапа!")
            log_action(user, "/backup", "Ошибка создания бекапа")
            
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {str(e)[:200]}")
        log_action(user, "/backup", f"Ошибка: {str(e)[:100]}")

async def logs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    if user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ Нет прав для этой команды!")
        log_action(user, "/logs", "Доступ запрещён (не админ)")
        return
    
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor(cursor_factory=DictCursor)
        cur.execute("""
            SELECT timestamp, username, first_name, command, result
            FROM logs
            ORDER BY id DESC
            LIMIT 10
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        
        if not rows:
            await update.message.reply_text("📝 Логов пока нет.")
            return
        
        response = "📝 **Последние 10 действий:**\n\n"
        for row in rows:
            response += f"[{row['timestamp']}] @{row['username']} | {row['command']} → {row['result']}\n"
        
        if len(response) > 4096:
            response = response[:4000] + "\n... (обрезано)"
        
        await update.message.reply_text(response, parse_mode='Markdown')
        log_action(user, "/logs", "Логи показаны")
        
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {str(e)[:200]}")

# ================= FLASK + WEBHOOK =================

flask_app = Flask(__name__)
bot_app = None

@flask_app.route('/')
def index():
    return "Bot is running!", 200

@flask_app.route('/webhook', methods=['POST'])
def webhook():
    global bot_app
    if bot_app is None:
        return "Bot not initialized", 500
    
    update = Update.de_json(request.get_json(force=True), bot_app.bot)
    bot_app.process_update(update)
    return "OK", 200

def run_flask():
    flask_app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

async def setup_webhook():
    global bot_app
    await bot_app.bot.set_webhook(url=f"{WEBHOOK_URL}/webhook")
    logger.info(f"Webhook установлен: {WEBHOOK_URL}/webhook")

def main():
    global bot_app
    
    bot_app = Application.builder().token(BOT_TOKEN).build()
    
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CommandHandler("update_credentials", update_credentials))
    bot_app.add_handler(CommandHandler("backup", backup_command))
    bot_app.add_handler(CommandHandler("logs", logs_command))
    bot_app.add_handler(CallbackQueryHandler(button_handler))
    
    import threading
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    import asyncio
    asyncio.run(setup_webhook())
    
    logger.info("Бот запущен на Railway!")
    
    while True:
        time.sleep(60)

if __name__ == "__main__":
    main()
