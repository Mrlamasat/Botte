import os
import sqlite3
import logging
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# إعدادات التسجيل
logging.basicConfig(level=logging.INFO)

API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHANNEL_ID = int(os.environ.get("CHANNEL_ID", 0))
PUBLIC_CHANNEL = os.environ.get("PUBLIC_CHANNEL", "").replace("@", "")

app = Client("MohammedSmartBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# دالة قاعدة البيانات
def db_execute(query, params=(), fetch=True):
    conn = sqlite3.connect("bot_data.db", timeout=10)
    cursor = conn.cursor()
    cursor.execute(query, params)
    conn.commit()
    res = cursor.fetchall() if fetch else None
    conn.close()
    return res

# تهيئة الجدول
db_execute("CREATE TABLE IF NOT EXISTS videos (v_id TEXT PRIMARY KEY, poster_id TEXT, ep_num INTEGER, status TEXT)", fetch=False)

# فحص الاشتراك
async def is_subscribed(client, user_id):
    try:
        chat = f"@{PUBLIC_CHANNEL}" if not str(PUBLIC_CHANNEL).startswith("-100") else int(PUBLIC_CHANNEL)
        member = await client.get_chat_member(chat, user_id)
        return member.status in ["member", "administrator", "creator"]
    except: return True

# --- نظام النشر للمشرف ---
@app.on_message(filters.chat(CHANNEL_ID) & (filters.video | filters.document))
async def receive_video(client, message):
    db_execute("INSERT OR REPLACE INTO videos (v_id, status) VALUES (?, ?)", (str(message.id), "waiting"), fetch=False)
    await message.reply_text("✅ استلمت الفيديو. أرسل البوستر.")

@app.on_message(filters.chat(CHANNEL_ID) & filters.photo)
async def receive_poster(client, message):
    res = db_execute("SELECT v_id FROM videos WHERE status = 'waiting' ORDER BY rowid DESC LIMIT 1")
    if res:
        db_execute("UPDATE videos SET poster_id = ?, status = 'awaiting_ep' WHERE v_id = ?", (message.photo.file_id, res[0][0]), fetch=False)
        await message.reply_text("🔢 أرسل رقم الحلقة:")

@app.on_message(filters.chat(CHANNEL_ID) & filters.text & ~filters.command("start"))
async def receive_ep(client, message):
    if not message.text.isdigit(): return
    res = db_execute("SELECT v_id, poster_id FROM videos WHERE status = 'awaiting_ep' ORDER BY rowid DESC LIMIT 1")
    if res:
        v_id, p_id = res[0]
        db_execute("UPDATE videos SET ep_num = ?, status = 'posted' WHERE v_id = ?", (int(message.text), v_id), fetch=False)
        
        # الرابط الرسمي (لا بديل عنه)
        link = f"https://t.me/{(await client.get_me()).username}?start={v_id}"
        caption = f"🎬 حلقة رقم {message.text}\n\nأرسل الرقم {message.text} للبوت للمشاهدة مباشرة!"
        
        await client.send_photo(f"@{PUBLIC_CHANNEL}", photo=p_id, caption=caption, 
                               reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("▶️ فتح في البوت", url=link)]]))
        await message.reply_text("🚀 تم النشر!")

# --- نظام الاستجابة الذكي (بدون تعليق) ---

@app.on_message(filters.private)
async def handle_all_messages(client, message):
    text = message.text
    v_id = None

    # 1. إذا كان رابط /start
    if text and text.startswith("/start") and len(text.split()) > 1:
        v_id = text.split()[1]
    
    # 2. إذا أرسل المستخدم رقم الحلقة مباشرة (الحل البديل)
    elif text and text.isdigit():
        res = db_execute("SELECT v_id FROM videos WHERE ep_num = ? AND status = 'posted' LIMIT 1", (int(text),))
        if res: v_id = res[0][0]
        else:
            await message.reply_text("❌ الحلقة غير موجودة.")
            return

    # إرسال الفيديو
    if v_id:
        if await is_subscribed(client, message.from_user.id):
            try:
                await client.copy_message(message.chat.id, CHANNEL_ID, int(v_id), protect_content=True)
            except:
                await message.reply_text("❌ الفيديو غير متاح حالياً.")
        else:
            await message.reply_text(f"📢 اشترك أولاً في @{PUBLIC_CHANNEL}")
    else:
        await message.reply_text("مرحباً بك! أرسل رقم الحلقة التي تريد مشاهدتها.")

app.run()
