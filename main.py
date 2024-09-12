import telebot
from telebot import types
import sqlite3
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from pytz import timezone
import os
import logging
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

load_dotenv()

# Get the token from environment variable
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')

if not BOT_TOKEN:
    logger.error("No TELEGRAM_BOT_TOKEN found in environment variables")
    raise ValueError("No TELEGRAM_BOT_TOKEN found in environment variables")

logger.info("Loaded bot token")

bot = telebot.TeleBot(BOT_TOKEN)

def init_db():
    conn = sqlite3.connect('products.db')
    cursor = conn.cursor()
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        photo TEXT,
        expiration_date TEXT,
        returnable BOOLEAN
    )
    ''')
    conn.commit()
    conn.close()


# Welcome message and prompts
welcome_text = '''Приветствую👋. Этот бот будет напоминать об окончании срока годности товаров.
Чтобы добавить товар, ножмите на кнопку ниже'''
return_item = "Введите дату окончания срока годности в формате \"дд.мм.гггг\"."


# Command handler for /start
@bot.message_handler(commands=['start'])
def start(message):
    bot.send_message(message.chat.id, welcome_text)
    show_main_menu(message)


@bot.message_handler(func=lambda message: True, content_types=['text'])
def handle_text(message):
    if message.text == 'Добавить новый товар':
        bot.send_message(message.chat.id, 'Отправь фото товара🫡')
        bot.register_next_step_handler(message, handle_photo)
    elif message.text == 'Помощь':
        help_text = """
Команды бота:
/start - Начать работу с ботом
Добавить новый товар - Добавить новый товар с фото и датой
Список товаров - Показать все ваши товары
        """
        bot.send_message(message.chat.id, help_text)
        show_main_menu(message)
    elif message.text == 'Список товаров':
        list_products(message)
    else:
        try:
            # Assume that user input is an expiration date
            date_text = message.text.strip().replace('-', '.').replace('/', '.').replace(' ', '.').replace(',', '.')
            exp_date = datetime.strptime(date_text, "%d.%m.%Y").date()

            if exp_date < datetime.today().date():
                bot.send_message(message.chat.id, "Дата окончания срока годности не может быть в прошлом. Введите корректную дату.")
                bot.register_next_step_handler(message, handle_text)
                return
            
            # If the user sends a date without selecting returnable, register it as non-returnable
            if message.chat.id in user_photos:
                photo = user_photos.pop(message.chat.id)

                # Save the non-returnable item
                conn = sqlite3.connect('products.db')
                cursor = conn.cursor()
                cursor.execute("INSERT INTO products (user_id, photo, expiration_date, returnable) VALUES (?, ?, ?, ?)",
                               (message.from_user.id, photo, exp_date.strftime("%Y-%m-%d"), False))
                conn.commit()
                conn.close()

                bot.send_message(message.chat.id, f"Товар добавлен!🤗")

                # After saving, return to the main menu (no more next step handlers)
                show_main_menu(message)
        except ValueError:
            bot.send_message(message.chat.id, "Некорректная дата. Введите дату в формате \"дд.мм.гггг\".")
            bot.register_next_step_handler(message, handle_text)

def show_main_menu(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    btn_add_item = types.KeyboardButton('Добавить новый товар')
    btn_list_items = types.KeyboardButton('Список товаров')
    btn_help = types.KeyboardButton('Помощь')
    markup.row(btn_add_item, btn_list_items)
    markup.row(btn_help)
    bot.send_message(message.chat.id, 'Выберите действие:', reply_markup=markup)


# Temporary storage for user photo data (user_id -> file_id)
user_photos = {}


def handle_photo(message):
    if message.photo:
        # Get the highest resolution photo
        photo = message.photo[-1].file_id

        # Store the photo file_id in the dictionary with user_id as key
        user_photos[message.chat.id] = photo

        # Add inline button for 'Возвратная категория' with simple callback data
        markup = types.InlineKeyboardMarkup()
        btn_returnable = types.InlineKeyboardButton('Возвратная категория', callback_data='return_item')
        markup.add(btn_returnable)

        # Message prompting either a date or the returnable selection
        bot.send_message(message.chat.id,
                         "Если товар возвратный, нажмите на кнопку ниже, иначе введите дату окончания срока годности в"
                         "формате \"дд.мм.гггг\".",
                         reply_markup=markup)
        # Don't register a next step handler here!


@bot.callback_query_handler(func=lambda call: call.data == 'return_item')
def handle_returnable_category(call):
    # Retrieve the photo file_id from the user_photos dictionary using chat_id
    photo_file_id = user_photos.get(call.message.chat.id)

    if not photo_file_id:
        bot.send_message(call.message.chat.id, "Произошла ошибка. Пожалуйста, отправьте фото товара заново.")
        bot.register_next_step_handler(call.message, handle_photo)
        return

    # Inform the user that they should send the expiration date
    bot.answer_callback_query(call.id, "Вы выбрали возвратную категорию")
    bot.send_message(call.message.chat.id, "Введите дату окончания срока годности в формате \"дд.мм.гггг\".")

    # Register the next step handler for the expiration date input (for returnable items)
    bot.register_next_step_handler(call.message, handle_date, photo_file_id, returnable=True)


# Handle receiving date
def handle_date(message, photo, returnable):
    try:
        # Normalize the date format by replacing common separators with dots
        date_text = message.text.strip().replace('-', '.').replace('/', '.').replace(' ', '.').replace(',', '.')

        # Validate and parse the date entered by the user
        exp_date = datetime.strptime(date_text, "%d.%m.%Y").date()

        # Adjust the expiration date if the item is returnable (4 days before actual expiry)
        adjusted_exp_date = exp_date - timedelta(days=4) if returnable else exp_date

        # Check if the adjusted expiration date is in the future
        if adjusted_exp_date < datetime.today().date():
            bot.send_message(message.chat.id, "Дата окончания срока годности или возврата не может быть в прошлом."
                                              " Пожалуйста, введите корректную дату.")
            bot.register_next_step_handler(message, handle_date, photo, returnable)
            return

        # Insert into SQLite
        conn = sqlite3.connect('products.db')
        cursor = conn.cursor()
        cursor.execute("INSERT INTO products (user_id, photo, expiration_date, returnable) VALUES (?, ?, ?, ?)",
                       (message.from_user.id, photo, exp_date.strftime("%Y-%m-%d"), returnable))
        conn.commit()
        conn.close()

        # Send confirmation message
        bot.send_message(message.chat.id, "Товар добавлен!🤗")
        show_main_menu(message)

    except ValueError:
        bot.send_message(message.chat.id, "Некорректная дата. Введите дату в формате \"дд.мм.гггг\".")
        bot.register_next_step_handler(message, handle_date, photo, returnable)
    except Exception as e:
        bot.send_message(message.chat.id, f"Произошла ошибка: {e}")


# Reminder function
def check_expiry():
    conn = sqlite3.connect('products.db')
    cursor = conn.cursor()

    today = datetime.today().date()
    yesterday = today - timedelta(days=1)

    # Check for items expiring today
    cursor.execute("SELECT * FROM products WHERE expiration_date = ?", (today.strftime("%Y-%m-%d"),))
    expiring_today = cursor.fetchall()
    expiring_returnable_item = today + timedelta(days=4)
    cursor.execute("SELECT * FROM products WHERE expiration_date = ? AND returnable = ?",
                   (expiring_returnable_item.strftime("%Y-%m-%d"), True))
    expiring_returnable_item = cursor.fetchall()
    expiring_items = expiring_today + expiring_returnable_item

    for row in expiring_items:
        product_id, user_id, photo, expiration_date, returnable = row
        try:
            bot.send_photo(user_id, photo, caption="Напоминаем! Срок годности этого товара истек сегодня!")
            if returnable:
                bot.send_message(user_id,
                                 "Этот товар относится к возвратной категории. Не забудьте вернуть его!")

        except Exception as e:
            print(f"Error sending notification: {e}")

    # Delete items that expired yesterday
    cursor.execute("DELETE FROM products WHERE expiration_date = ?", (yesterday.strftime("%Y-%m-%d"),))
    deleted_count = cursor.rowcount
    print(f"Deleted {deleted_count} expired items.")
    conn.commit()
    conn.close()


def list_products(message):
    conn = sqlite3.connect('products.db')
    cursor = conn.cursor()
    cursor.execute("SELECT id, expiration_date, returnable FROM products WHERE user_id = ?", (message.from_user.id,))
    products = cursor.fetchall()
    conn.close()

    if products:
        response = "Ваши товары:\n"
        for product in products:
            product_id, exp_date, returnable = product
            response += f"ID: {product_id}, Срок годности: {exp_date}, {'Возвратный' if returnable else 'Невозвратный'}\n"
        bot.send_message(message.chat.id, response)
    else:
        bot.send_message(message.chat.id, "У вас пока нет добавленных товаров.")


# checks expiry every 24 hours
def init_scheduler():
    local_scheduler = BackgroundScheduler()
    local_scheduler.add_job(check_expiry, 'cron', hour=5, minute=0, timezone=timezone('Europe/Moscow'))
    local_scheduler.start()
    return local_scheduler


# main execution block
if __name__ == '__main__':
    init_db()
    scheduler = init_scheduler()
    try:
        bot.polling(none_stop=True)
    except KeyboardInterrupt:
        scheduler.shutdown()
        logger.info("Bot stopped.")
