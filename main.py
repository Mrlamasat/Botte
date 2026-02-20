import os
import sqlite3
import logging
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# ===== إعدادات التسجيل =====
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ===== المتغيرات الأساسية (تُسحب من Railway) =====
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHANNEL_ID = int(os.environ.get("CHANNEL_ID", 0)) # قناة التخزين
PUBLIC_CHANNEL = os.environ.get("PUBLIC_CHANNEL", "") # قناة النشر العامة

app = Client("BottemoBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ===== قاعدة البيانات =====
def init_db():
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS videos 
                      (v_id TEXT PRIMARY KEY, duration TEXT, title TEXT, 
                       poster_id TEXT, status TEXT, ep_num INTEGER)''')
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

# ===== استقبال المحتوى من قناة التخزين =====

@app.on_message(filters.chat(CHANNEL_ID) & (filters.video | filters.document))
async def receive_video(client, message):
    v_id = str(message.id)
    db_execute("INSERT OR REPLACE INTO videos (v_id, status) VALUES (?, ?)", (v_id, "waiting"), fetch=False)
    await message.reply_text(f"✅ تم استلام الفيديو (ID: {v_id})\nالآن أرسل البوستر (الصورة) مع كتابة اسم المسلسل في الوصف (Caption).")

@app.on_message(filters.chat(CHANNEL_ID) & filters.photo)
async def receive_poster(client, message):
    res = db_execute("SELECT v_id FROM videos WHERE status = 'waiting' ORDER BY rowid DESC LIMIT 1")
    if not res: return
    v_id = res[0][0]
    title = message.caption or "مسلسل جديد"
    db_execute("UPDATE videos SET title = ?, poster_id = ?, status = 'awaiting_ep' WHERE v_id = ?",
               (title, message.photo.file_id, v_id), fetch=False)
    await message.reply_text(f"📌 تم حفظ البوستر لـ **{title}**\n🔢 أرسل الآن رقم الحلقة فقط:")

@app.on_message(filters.chat(CHANNEL_ID) & filters.text & ~filters.command(["start"]))
async def receive_ep_number(client, message):
    if not message.text.isdigit(): return
    res = db_execute("SELECT v_id, title, poster_id FROM videos WHERE status = 'awaiting_ep' ORDER BY rowid DESC LIMIT 1")
    if not res: return
    
    v_id, title, poster_id = res[0]
    ep_num = int(message.text)
    db_execute("UPDATE videos SET ep_num = ?, status = 'posted' WHERE v_id = ?", (ep_num, v_id), fetch=False)
    
    bot_info = await client.get_me()
    watch_link = f"https://t.me/{bot_info.username}?start={v_id}"
    
    # --- النشر التلقائي في القناة العامة ---
    if PUBLIC_CHANNEL:
        try:
            caption = f"🎬 **{title}**\n🔹 **الحلقة رقم:** {ep_num}\n\n📥 **لمشاهدة الحلقة اضغط على الزر أدناه:**"
            reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("▶️ فتح الحلقة الآن", url=watch_link)]])
            await client.send_photo(chat_id=PUBLIC_CHANNEL, photo=poster_id, caption=caption, reply_markup=reply_markup)
            await message.reply_text(f"🚀 تم النشر بنجاح في @{PUBLIC_CHANNEL}")
        except Exception as e:
            await message.reply_text(f"⚠️ تم الحفظ ولكن فشل النشر: {e}")
    else:
        await message.reply_text(f"✅ تم الحفظ. الرابط المباشر:\n{watch_link}")

# ===== نظام التشغيل (Start) =====

@app.on_message(filters.command("start") & filters.private)
async def start_handler(client, message):
    if len(message.command) <= 1:
        await message.reply_text(f"أهلاً بك يا محمد! أرسل رابط الحلقة للمشاهدة.")
        return

    v_id = message.command[1]
    try:
        # إرسال الفيديو فوراً
        await client.copy_message(message.chat.id, CHANNEL_ID, int(v_id), protect_content=True)
        
        # عرض حلقات المسلسل (اختياري)
        video_info = db_execute("SELECT poster_id FROM videos WHERE v_id = ?", (v_id,))
        if video_info and video_info[0][0]:
            p_id = video_info[0][0]
            all_ep = db_execute("SELECT v_id, ep_num FROM videos WHERE poster_id = ? AND status = 'posted' ORDER BY ep_num ASC", (p_id,))
            if len(all_ep) > 1:
                btns = []; row = []
                bot_user = (await client.get_me()).username
                for vid, num in all_ep:
                    label = f"▶️ {num}" if vid == v_id else f"{num}"
                    row.append(InlineKeyboardButton(label, url=f"https://t.me/{bot_user}?start={vid}"))
                    if len(row) == 4: btns.append(row); row = []
                if row: btns.append(row)
                await message.reply_text("📺 باقي حلقات المسلسل:", reply_markup=InlineKeyboardMarkup(btns))
    except:
        await message.reply_text("❌ عذراً، الحلقة غير متوفرة حالياً.")

app.run()
