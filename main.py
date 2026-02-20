import os
import sqlite3
import logging
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# ===== إعدادات التسجيل =====
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ===== المتغيرات الأساسية =====
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHANNEL_ID = int(os.environ.get("CHANNEL_ID", 0))  # قناة التخزين
PUBLIC_CHANNEL = os.environ.get("PUBLIC_CHANNEL", "")  # قناة النشر العامة

app = Client("BottemoBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ===== قاعدة البيانات =====
def init_db():
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS videos 
                      (v_id TEXT PRIMARY KEY, duration TEXT, poster_id TEXT, status TEXT, ep_num INTEGER, quality TEXT)''')
    conn.commit()
    conn.close()

init_db()

def db_execute(query, params=(), fetch=True):
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute(query, params)
    conn.commit()
    res = cursor.fetchall() if fetch else None
    conn.close()
    return res

# ===== استقبال الفيديوهات =====
@app.on_message(filters.chat(CHANNEL_ID) & (filters.video | filters.document))
async def receive_video(client, message):
    v_id = str(message.id)
    duration_sec = message.video.duration if message.video else getattr(message.document, "duration", 0)
    mins, secs = divmod(duration_sec, 60)
    duration = f"{mins}:{secs:02d} دقيقة" if duration_sec else "غير محدد"
    db_execute("INSERT OR REPLACE INTO videos (v_id, duration, status) VALUES (?, ?, ?)", (v_id, duration, "waiting"), fetch=False)
    await message.reply_text(f"✅ تم استلام الفيديو (ID: {v_id})\nالآن أرسل البوستر (الصورة)")

# ===== استقبال البوستر =====
@app.on_message(filters.chat(CHANNEL_ID) & filters.photo)
async def receive_poster(client, message):
    res = db_execute("SELECT v_id FROM videos WHERE status='waiting' ORDER BY rowid DESC LIMIT 1")
    if not res: return
    v_id = res[0][0]
    db_execute("UPDATE videos SET poster_id=?, status='awaiting_ep' WHERE v_id=?", (message.photo.file_id, v_id), fetch=False)
    await message.reply_text(f"🖼 تم حفظ البوستر.\n🔢 أرسل الآن رقم الحلقة:")

# ===== استقبال رقم الحلقة =====
@app.on_message(filters.chat(CHANNEL_ID) & filters.text & ~filters.command(["start"]))
async def receive_ep_number(client, message):
    if not message.text.isdigit(): return
    res = db_execute("SELECT v_id, poster_id, duration FROM videos WHERE status='awaiting_ep' ORDER BY rowid DESC LIMIT 1")
    if not res: return
    v_id, poster_id, duration = res[0]
    ep_num = int(message.text)
    db_execute("UPDATE videos SET ep_num=?, status='posted' WHERE v_id=?", (ep_num, v_id), fetch=False)

    bot_info = await client.get_me()
    watch_link = f"https://t.me/{bot_info.username}?start={v_id}"

    # نشر تلقائي في القناة العامة
    if PUBLIC_CHANNEL:
        try:
            caption = f"🎬 الحلقة {ep_num}\n⏱ المدة: {duration}\n✨ الجودة: غير محددة بعد\n\n📥 اضغط الزر لمشاهدة الحلقة"
            await client.send_photo(chat_id=PUBLIC_CHANNEL, photo=poster_id, caption=caption,
                                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("▶️ فتح الحلقة الآن", url=watch_link)]]))
            await message.reply_text(f"🚀 تم النشر بنجاح في @{PUBLIC_CHANNEL}")
        except Exception as e:
            await message.reply_text(f"⚠️ تم الحفظ ولكن فشل النشر: {e}")
    else:
        await message.reply_text(f"✅ تم الحفظ. الرابط المباشر:\n{watch_link}")

# ===== تشغيل الحلقة وعرض قائمة الحلقات =====
@app.on_message(filters.command("start") & filters.private)
async def start_handler(client, message):
    if len(message.command) <= 1:
        await message.reply_text("أهلاً بك! أرسل رابط الحلقة للمشاهدة.")
        return

    v_id = message.command[1]
    await send_video_with_list(client, message.chat.id, v_id)

async def send_video_with_list(client, chat_id, v_id):
    try:
        # إرسال الفيديو الحالي
        await client.copy_message(chat_id, CHANNEL_ID, int(v_id), protect_content=True)

        # جلب poster_id للحلقة
        video_info = db_execute("SELECT poster_id, duration, quality, ep_num FROM videos WHERE v_id=?", (v_id,))
        if not video_info: return
        poster_id, duration, quality, ep_num = video_info[0]

        # جلب كل الحلقات لنفس البوستر
        all_ep = db_execute("SELECT v_id, ep_num FROM videos WHERE poster_id=? AND status='posted' ORDER BY ep_num ASC", (poster_id,))
        if all_ep and len(all_ep) > 1:
            btns = []
            row = []
            bot_user = (await client.get_me()).username
            for vid, num in all_ep:
                label = f"▶️ {num}" if vid == v_id else f"{num}"
                row.append(InlineKeyboardButton(label, callback_data=f"watch_{vid}"))
                if len(row) == 4:
                    btns.append(row)
                    row = []
            if row: btns.append(row)
            caption = f"🎬 الحلقة {ep_num}\n⏱ المدة: {duration}\n✨ الجودة: {quality}\n\n📥 شاهد باقي الحلقات أسفل الفيديو"
            await client.send_message(chat_id, caption, reply_markup=InlineKeyboardMarkup(btns))
    except:
        await client.send_message(chat_id, "❌ عذراً، الحلقة غير متوفرة حالياً.")

# ===== التعامل مع الضغط على أي حلقة =====
@app.on_callback_query(filters.regex(r"^watch_"))
async def watch_episode(client, query):
    v_id = query.data.split("_")[1]
    try:
        await query.message.delete()
    except: pass
    await send_video_with_list(client, query.from_user.id, v_id)

app.run()
