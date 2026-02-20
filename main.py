import os
import sqlite3
import logging
import asyncio
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import UserNotParticipant, FloodWait

# ===== إعدادات التسجيل =====
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ===== المتغيرات الأساسية =====
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHANNEL_ID = int(os.environ.get("CHANNEL_ID", 0)) 
PUBLIC_CHANNEL = os.environ.get("PUBLIC_CHANNEL", "").replace("@", "") 
DB_PATH = os.environ.get("DB_PATH", "bot_data.db") 

app = Client("MohammedSmartBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# تتبع الخطوات لكل مسؤول
user_steps = {} 

# ===== إدارة قاعدة البيانات =====
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS videos 
                      (v_id TEXT PRIMARY KEY, duration TEXT, title TEXT, 
                       poster_id TEXT, status TEXT, ep_num INTEGER)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS subscriptions 
                      (user_id INTEGER, poster_id TEXT, UNIQUE(user_id, poster_id))''')
    conn.commit()
    conn.close()

init_db()

def db_execute(query, params=(), fetch=True):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(query, params)
    conn.commit()
    res = cursor.fetchall() if fetch else None
    conn.close()
    return res

def format_duration(seconds):
    if not seconds: return "00:00"
    mins, secs = divmod(seconds, 60)
    return f"{mins}:{secs:02d} دقيقة"

# ===== الخطوة 1: استلام الفيديو =====

@app.on_message(filters.chat(CHANNEL_ID) & (filters.video | filters.document))
async def receive_video(client, message):
    v_id = str(message.id)
    duration = 0
    if message.video:
        duration = message.video.duration
    elif message.document and hasattr(message.document, "duration"):
        duration = message.document.duration
    
    db_execute("INSERT OR REPLACE INTO videos (v_id, duration, status) VALUES (?, ?, ?)", 
               (v_id, format_duration(duration), "waiting"), fetch=False)
    
    # تحديد الخطوة التالية لهذا المستخدم
    user_steps[message.from_user.id] = {"v_id": v_id, "step": "awaiting_poster"}
    
    await message.reply_text(f"✅ تم استلام الفيديو (ID: {v_id})\n🖼 **الآن أرسل صورة البوستر لهذا الفيديو:**")

# ===== الخطوة 2: استلام البوستر (يجب أن يكون رداً على طلب البوت) =====

@app.on_message(filters.chat(CHANNEL_ID) & filters.photo)
async def receive_poster(client, message):
    data = user_steps.get(message.from_user.id)
    
    if not data or data.get("step") != "awaiting_poster":
        return # يتجاهل الصور إذا لم يسبقها فيديو

    v_id = data.get("v_id")
    db_execute("UPDATE videos SET title = ?, poster_id = ?, status = 'awaiting_ep' WHERE v_id = ?",
               ("حلقة جديدة", message.photo.file_id, v_id), fetch=False)
    
    # الانتقال للخطوة التالية
    user_steps[message.from_user.id] = {"v_id": v_id, "step": "awaiting_ep_num"}
    
    await message.reply_text(f"🖼 تم ربط البوستر بنجاح.\n🔢 **أرسل الآن رقم الحلقة فقط:**")

# ===== الخطوة 3: استلام رقم الحلقة =====

@app.on_message(filters.chat(CHANNEL_ID) & filters.text & ~filters.command(["start"]))
async def receive_ep_number(client, message):
    data = user_steps.get(message.from_user.id)
    
    if not data or data.get("step") != "awaiting_ep_num" or not message.text.isdigit():
        return

    v_id = data.get("v_id")
    ep_num = int(message.text)
    db_execute("UPDATE videos SET ep_num = ?, status = 'ready_quality' WHERE v_id = ?", (ep_num, v_id), fetch=False)
    
    # تنظيف الخطوات
    user_steps.pop(message.from_user.id, None)

    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("SD", callback_data=f"q_SD_{v_id}"),
         InlineKeyboardButton("HD", callback_data=f"q_HD_{v_id}"),
         InlineKeyboardButton("4K", callback_data=f"q_4K_{v_id}")]
    ])
    await message.reply_text(f"✅ تم تحديد رقم الحلقة: {ep_num}\n🚀 **اختر الجودة للنشر في القناة:**", reply_markup=markup)

# ===== الخطوة 4: النشر (Callback Query) =====

@app.on_callback_query(filters.regex(r"^q_"))
async def quality_callback(client, query):
    _, quality, v_id = query.data.split("_")
    res = db_execute("SELECT duration, title, poster_id, ep_num FROM videos WHERE v_id = ?", (v_id,))
    if not res: return
    
    duration, title, p_id, ep_num = res[0]
    bot_info = await client.get_me()
    watch_link = f"https://t.me/{bot_info.username}?start={v_id}"
    
    if PUBLIC_CHANNEL:
        try:
            caption = (f"🎬 **حلقة جديدة جاهزة**\n"
                       f"🔹 **الحلقة رقم:** {ep_num}\n"
                       f"⏱ **المدة:** {duration}\n"
                       f"✨ **الجودة:** {quality}\n\n"
                       f"📥 **لمشاهدة الحلقة اضغط على الزر أدناه:**")
            
            await client.send_photo(chat_id=f"@{PUBLIC_CHANNEL}", photo=p_id, caption=caption, 
                                   reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("▶️ فتح الحلقة الآن", url=watch_link)]]))
        except Exception as e:
            logging.error(f"Error publishing: {e}")

    # إرسال إشعارات للمشتركين
    subscribers = db_execute("SELECT user_id FROM subscriptions WHERE poster_id = ?", (p_id,))
    for sub in subscribers:
        try:
            await client.send_message(sub[0], f"🔔 **تحديث جديد!**\nتمت إضافة الحلقة {ep_num} جودة {quality}.\n\n📥 [اضغط هنا للمشاهدة]({watch_link})", disable_web_page_preview=True)
            await asyncio.sleep(0.1)
        except: continue

    db_execute("UPDATE videos SET status = 'posted' WHERE v_id = ?", (v_id,), fetch=False)
    await query.message.edit_text(f"🚀 تم النشر بنجاح بجودة {quality}!")

# ===== نظام Start للمستخدمين =====

@app.on_message(filters.command("start") & filters.private)
async def start_handler(client, message):
    if len(message.command) <= 1:
        await message.reply_text(f"أهلاً بك يا محمد! البوت يعمل وجاهز.")
        return
    v_id = message.command[1]
    
    # التحقق من الاشتراك
    try:
        await client.get_chat_member(f"@{PUBLIC_CHANNEL}", message.from_user.id)
    except UserNotParticipant:
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("📢 اشترك هنا", url=f"https://t.me/{PUBLIC_CHANNEL}")],
                                       [InlineKeyboardButton("✅ تم الاشتراك", callback_data=f"chk_{v_id}")]])
        await message.reply_text("⚠️ اشترك أولاً لمتابعة المشاهدة.", reply_markup=markup)
        return
        
    await send_video_to_user(client, message.chat.id, v_id)

async def send_video_to_user(client, chat_id, v_id):
    try:
        await client.copy_message(chat_id, CHANNEL_ID, int(v_id), protect_content=True)
        video_info = db_execute("SELECT poster_id FROM videos WHERE v_id = ?", (v_id,))
        if video_info and video_info[0][0]:
            p_id = video_info[0][0]
            db_execute("INSERT OR IGNORE INTO subscriptions (user_id, poster_id) VALUES (?, ?)", (chat_id, p_id), fetch=False)
            all_ep = db_execute("SELECT v_id, ep_num FROM videos WHERE poster_id = ? AND status = 'posted' ORDER BY ep_num ASC", (p_id,))
            if len(all_ep) > 1:
                btns = []; row = []
                bot_user = (await client.get_me()).username
                for vid, num in all_ep:
                    label = f"▶️ {num}" if vid == v_id else f"{num}"
                    row.append(InlineKeyboardButton(label, url=f"https://t.me/{bot_user}?start={vid}"))
                    if len(row) == 4: btns.append(row); row = []
                if row: btns.append(row)
                await client.send_message(chat_id, "📺 حلقات أخرى:", reply_markup=InlineKeyboardMarkup(btns))
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

app.run()
