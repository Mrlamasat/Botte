import os
import asyncio
import logging
import asyncpg
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import UserNotParticipant, FloodWait

# ===== إعدادات التسجيل =====
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ===== متغيرات البيئة =====
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHANNEL_ID = int(os.environ.get("CHANNEL_ID", 0))
PUBLIC_CHANNEL = os.environ.get("PUBLIC_CHANNEL", "").replace("@", "")
DB_URL = os.environ.get("DATABASE_URL")  # PostgreSQL URL

app = Client("SmartBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ===== إدارة قاعدة البيانات PostgreSQL =====
async def init_db():
    conn = await asyncpg.connect(DB_URL)
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS videos (
            v_id TEXT PRIMARY KEY,
            duration TEXT,
            title TEXT,
            poster_id TEXT,
            status TEXT,
            ep_num INTEGER
        )
    ''')
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS subscriptions (
            user_id BIGINT,
            poster_id TEXT,
            UNIQUE(user_id, poster_id)
        )
    ''')
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS user_steps (
            user_id BIGINT PRIMARY KEY,
            v_id TEXT,
            step TEXT
        )
    ''')
    await conn.close()

asyncio.get_event_loop().run_until_complete(init_db())

async def db_execute(query, *args, fetch=False, fetchval=False):
    conn = await asyncpg.connect(DB_URL)
    if fetch:
        res = await conn.fetch(query, *args)
    elif fetchval:
        res = await conn.fetchval(query, *args)
    else:
        res = await conn.execute(query, *args)
    await conn.close()
    return res

def format_duration(seconds):
    if not seconds:
        return "00:00"
    mins, secs = divmod(seconds, 60)
    return f"{mins}:{secs:02d} دقيقة"

# ===== استلام الفيديو =====
@app.on_message(filters.chat(CHANNEL_ID) & (filters.video | filters.document))
async def receive_video(client, message):
    v_id = str(message.id)
    duration = getattr(message.video or message.document, "duration", 0)
    await db_execute(
        "INSERT INTO videos (v_id, duration, status) VALUES ($1, $2, $3) ON CONFLICT (v_id) DO UPDATE SET duration=$2, status=$3",
        v_id, format_duration(duration), "waiting"
    )
    await db_execute(
        "INSERT INTO user_steps (user_id, v_id, step) VALUES ($1, $2, $3) ON CONFLICT (user_id) DO UPDATE SET v_id=$2, step=$3",
        message.from_user.id, v_id, "awaiting_poster"
    )
    await message.reply_text(f"✅ تم استلام الفيديو (ID: {v_id})\n🖼 أرسل الآن صورة البوستر.")

# ===== استلام البوستر =====
@app.on_message(filters.chat(CHANNEL_ID) & filters.photo)
async def receive_poster(client, message):
    step = await db_execute("SELECT v_id, step FROM user_steps WHERE user_id=$1", message.from_user.id, fetch=True)
    if not step or step[0]['step'] != "awaiting_poster":
        return
    v_id = step[0]['v_id']
    await db_execute(
        "UPDATE videos SET poster_id=$1, title=$2, status=$3 WHERE v_id=$4",
        message.photo.file_id, "حلقة جديدة", "awaiting_ep", v_id
    )
    await db_execute(
        "UPDATE user_steps SET step=$1 WHERE user_id=$2",
        "awaiting_ep_num", message.from_user.id
    )
    await message.reply_text("🖼 تم ربط البوستر.\n🔢 أرسل الآن رقم الحلقة.")

# ===== استلام رقم الحلقة =====
@app.on_message(filters.chat(CHANNEL_ID) & filters.text & ~filters.command(["start"]))
async def receive_ep_number(client, message):
    if not message.text.isdigit():
        return
    step = await db_execute("SELECT v_id, step FROM user_steps WHERE user_id=$1", message.from_user.id, fetch=True)
    if not step or step[0]['step'] != "awaiting_ep_num":
        return
    v_id = step[0]['v_id']
    ep_num = int(message.text)
    await db_execute(
        "UPDATE videos SET ep_num=$1, status=$2 WHERE v_id=$3",
        ep_num, "ready_quality", v_id
    )
    await db_execute("DELETE FROM user_steps WHERE user_id=$1", message.from_user.id)
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("SD", callback_data=f"q_SD_{v_id}"),
         InlineKeyboardButton("HD", callback_data=f"q_HD_{v_id}"),
         InlineKeyboardButton("4K", callback_data=f"q_4K_{v_id}")]
    ])
    await message.reply_text(f"✅ رقم الحلقة: {ep_num}\n🚀 اختر الجودة:", reply_markup=markup)

# ===== النشر =====
@app.on_callback_query(filters.regex(r"^q_"))
async def quality_callback(client, query):
    _, quality, v_id = query.data.split("_")
    video = await db_execute("SELECT duration, title, poster_id, ep_num FROM videos WHERE v_id=$1", v_id, fetch=True)
    if not video: return
    duration, title, p_id, ep_num = video[0]['duration'], video[0]['title'], video[0]['poster_id'], video[0]['ep_num']
    bot_user = (await client.get_me()).username
    watch_link = f"https://t.me/{bot_user}?start={v_id}"

    if PUBLIC_CHANNEL:
        caption = f"🎬 حلقة جديدة\n🔹 الحلقة رقم: {ep_num}\n⏱ المدة: {duration}\n✨ الجودة: {quality}\n\n📥 اضغط على الزر للمشاهدة:"
        try:
            await client.send_photo(
                chat_id=f"@{PUBLIC_CHANNEL}", photo=p_id, caption=caption,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("▶️ فتح الحلقة الآن", url=watch_link)]])
            )
        except Exception as e:
            logging.error(f"خطأ أثناء النشر: {e}")

    subscribers = await db_execute("SELECT user_id FROM subscriptions WHERE poster_id=$1", p_id, fetch=True)
    for sub in subscribers:
        try:
            await client.send_message(sub['user_id'], f"🔔 حلقة جديدة {ep_num} جودة {quality}\n📥 [مشاهدة الحلقة]({watch_link})", disable_web_page_preview=True)
            await asyncio.sleep(0.1)
        except:
            continue

    await db_execute("UPDATE videos SET status='posted' WHERE v_id=$1", v_id)
    await query.message.edit_text(f"🚀 تم النشر بجودة {quality}!")

# ===== نظام Start للمستخدم =====
@app.on_message(filters.command("start") & filters.private)
async def start_handler(client, message):
    if len(message.command) <= 1:
        await message.reply_text("أهلاً! البوت جاهز.")
        return
    v_id = message.command[1]
    try:
        await client.get_chat_member(f"@{PUBLIC_CHANNEL}", message.from_user.id)
    except UserNotParticipant:
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 اشترك", url=f"https://t.me/{PUBLIC_CHANNEL}")],
            [InlineKeyboardButton("✅ تم الاشتراك", callback_data=f"chk_{v_id}")]
        ])
        await message.reply_text("⚠️ اشترك أولاً.", reply_markup=markup)
        return
    await send_video_to_user(client, message.chat.id, v_id)

async def send_video_to_user(client, chat_id, v_id):
    try:
        await client.copy_message(chat_id, CHANNEL_ID, int(v_id), protect_content=True)
        video_info = await db_execute("SELECT poster_id FROM videos WHERE v_id=$1", v_id, fetch=True)
        if video_info:
            p_id = video_info[0]['poster_id']
            await db_execute("INSERT INTO subscriptions (user_id, poster_id) VALUES ($1, $2) ON CONFLICT DO NOTHING", chat_id, p_id)
    except:
        await client.send_message(chat_id, "❌ الحلقة غير متوفرة.")

@app.on_callback_query(filters.regex(r"^chk_"))
async def check_sub_callback(client, query):
    v_id = query.data.split("_")[1]
    try:
        await client.get_chat_member(f"@{PUBLIC_CHANNEL}", query.from_user.id)
        await query.message.delete()
        await send_video_to_user(client, query.from_user.id, v_id)
    except:
        await query.answer("⚠️ اشترك أولاً!", show_alert=True)

print("🚀 البوت يعمل الآن على Railway!")
app.run()
