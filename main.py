import os
import sqlite3
import logging
import asyncio
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import UserNotParticipant, FloodWait

# ===== إعدادات التسجيل =====
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ===== المتغيرات الأساسية (تُسحب من Railway) =====
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHANNEL_ID = int(os.environ.get("CHANNEL_ID", 0)) # قناة التخزين
PUBLIC_CHANNEL = os.environ.get("PUBLIC_CHANNEL", "").replace("@", "") # يوزر القناة بدون @
DB_PATH = os.environ.get("DB_PATH", "bot_data.db") # مسار قاعدة البيانات (يفضل ربطه بـ Volume)

app = Client("MohammedSmartBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ===== إدارة قاعدة البيانات =====
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # جدول الفيديوهات
    cursor.execute('''CREATE TABLE IF NOT EXISTS videos 
                      (v_id TEXT PRIMARY KEY, duration TEXT, title TEXT, 
                       poster_id TEXT, status TEXT, ep_num INTEGER)''')
    # جدول الاشتراكات للإشعارات
    cursor.execute('''CREATE TABLE IF NOT EXISTS subscriptions 
                      (user_id INTEGER, poster_id TEXT, UNIQUE(user_id, poster_id))''')
    
    # تحديث الجدول إذا كان قديماً (إضافة عمود ep_num)
    cursor.execute("PRAGMA table_info(videos)")
    columns = [col[1] for col in cursor.fetchall()]
    if "ep_num" not in columns:
        cursor.execute("ALTER TABLE videos ADD COLUMN ep_num INTEGER")
        logging.info("✅ تم إضافة عمود ep_num لقاعدة البيانات.")
        
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

# ===== استقبال المحتوى من قناة التخزين =====

@app.on_message(filters.chat(CHANNEL_ID) & (filters.video | filters.document))
async def receive_video(client, message):
    v_id = str(message.id)
    duration = message.video.duration if message.video else getattr(message.document, "duration", 0)
    
    db_execute("INSERT OR REPLACE INTO videos (v_id, duration, status) VALUES (?, ?, ?)", 
               (v_id, format_duration(duration), "waiting"), fetch=False)
    
    await message.reply_text(f"✅ تم استلام الفيديو (ID: {v_id})\nالآن أرسل البوستر (الصورة) مع كتابة اسم المسلسل في الوصف (Caption).")

@app.on_message(filters.chat(CHANNEL_ID) & filters.photo)
async def receive_poster(client, message):
    # جلب آخر فيديو ينتظر البوستر
    res = db_execute("SELECT v_id FROM videos WHERE status = 'waiting' ORDER BY rowid DESC LIMIT 1")
    if not res: return
    
    v_id = res[0][0]
    title = message.caption or "مسلسل جديد"
    db_execute("UPDATE videos SET title = ?, poster_id = ?, status = 'awaiting_ep' WHERE v_id = ?",
               (title, message.photo.file_id, v_id), fetch=False)
    
    await message.reply_text(f"📌 تم حفظ البوستر لـ **{title}**\n🔢 أرسل الآن رقم الحلقة فقط:")

@app.on_message(filters.chat(CHANNEL_ID) & filters.text & ~filters.command(["start", "edit"]))
async def receive_ep_number(client, message):
    if not message.text.isdigit(): return
    
    res = db_execute("SELECT v_id, title FROM videos WHERE status = 'awaiting_ep' ORDER BY rowid DESC LIMIT 1")
    if not res: return
    
    v_id = res[0][0]
    ep_num = int(message.text)
    db_execute("UPDATE videos SET ep_num = ?, status = 'ready_quality' WHERE v_id = ?", (ep_num, v_id), fetch=False)
    
    # خيارات الجودة للنشر
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("SD", callback_data=f"q_SD_{v_id}"),
         InlineKeyboardButton("HD", callback_data=f"q_HD_{v_id}"),
         InlineKeyboardButton("4K", callback_data=f"q_4K_{v_id}")]
    ])
    await message.reply_text(f"✅ تم تحديد الحلقة {ep_num}.\nاختر الجودة المطلوبة للنشر في القناة العامة:", reply_markup=markup)

# ===== معالجة اختيار الجودة والنشر التلقائي =====

@app.on_callback_query(filters.regex(r"^q_"))
async def quality_callback(client, query):
    _, quality, v_id = query.data.split("_")
    res = db_execute("SELECT duration, title, poster_id, ep_num FROM videos WHERE v_id = ?", (v_id,))
    if not res: return
    
    duration, title, p_id, ep_num = res[0]
    bot_info = await client.get_me()
    watch_link = f"https://t.me/{bot_info.username}?start={v_id}"
    
    # 1. النشر في القناة العامة
    if PUBLIC_CHANNEL:
        try:
            caption = (f"🎬 **{title}**\n"
                       f"🔹 **الحلقة رقم:** {ep_num}\n"
                       f"⏱ **المدة:** {duration}\n"
                       f"✨ **الجودة:** {quality}\n\n"
                       f"📥 **لمشاهدة الحلقة اضغط على الزر أدناه:**")
            
            reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("▶️ فتح الحلقة الآن", url=watch_link)]])
            await client.send_photo(chat_id=f"@{PUBLIC_CHANNEL}", photo=p_id, caption=caption, reply_markup=reply_markup)
        except Exception as e:
            logging.error(f"Error publishing: {e}")

    # 2. إرسال إشعارات للمشتركين في هذا المسلسل
    subscribers = db_execute("SELECT user_id FROM subscriptions WHERE poster_id = ?", (p_id,))
    for sub in subscribers:
        try:
            await client.send_message(
                sub[0], 
                f"🔔 **تحديث جديد لـ {title}!**\nتمت إضافة الحلقة {ep_num} جودة {quality}.\n\n📥 [اضغط هنا للمشاهدة]({watch_link})",
                disable_web_page_preview=True
            )
            await asyncio.sleep(0.1) # حماية من الـ Flood
        except FloodWait as e:
            await asyncio.sleep(e.value)
        except:
            continue

    db_execute("UPDATE videos SET status = 'posted' WHERE v_id = ?", (v_id,), fetch=False)
    await query.message.edit_text(f"🚀 تم النشر بنجاح بجودة {quality}!")

# ===== نظام تشغيل البوت للمستخدمين (Start) =====

@app.on_message(filters.command("start") & filters.private)
async def start_handler(client, message):
    if len(message.command) <= 1:
        await message.reply_text(f"أهلاً بك يا محمد! أرسل رابط الحلقة للمشاهدة.")
        return

    v_id = message.command[1]
    
    # التحقق من الاشتراك الإجباري
    try:
        await client.get_chat_member(f"@{PUBLIC_CHANNEL}", message.from_user.id)
    except UserNotParticipant:
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 اشترك في القناة", url=f"https://t.me/{PUBLIC_CHANNEL}")],
            [InlineKeyboardButton("✅ تم الاشتراك", callback_data=f"chk_{v_id}")]
        ])
        await message.reply_text("⚠️ عذراً، يجب عليك الاشتراك في القناة أولاً لمشاهدة الحلقة.", reply_markup=markup)
        return

    # إرسال الفيديو للمشتركين
    await send_video_to_user(client, message.chat.id, v_id)

async def send_video_to_user(client, chat_id, v_id):
    try:
        # إرسال الفيديو من قناة التخزين
        await client.copy_message(chat_id, CHANNEL_ID, int(v_id), protect_content=True)
        
        # جلب بيانات المسلسل لعرض قائمة الحلقات
        video_info = db_execute("SELECT poster_id, title FROM videos WHERE v_id = ?", (v_id,))
        if video_info and video_info[0][0]:
            p_id = video_info[0][0]
            # تسجيل المستخدم في قائمة الإشعارات لهذا المسلسل تلقائياً
            db_execute("INSERT OR IGNORE INTO subscriptions (user_id, poster_id) VALUES (?, ?)", (chat_id, p_id), fetch=False)
            
            # جلب كل الحلقات المنشورة لهذا المسلسل
            all_ep = db_execute("SELECT v_id, ep_num FROM videos WHERE poster_id = ? AND status = 'posted' ORDER BY ep_num ASC", (p_id,))
            if len(all_ep) > 1:
                btns = []; row = []
                bot_user = (await client.get_me()).username
                for vid, num in all_ep:
                    label = f"▶️ {num}" if vid == v_id else f"{num}"
                    row.append(InlineKeyboardButton(label, url=f"https://t.me/{bot_user}?start={vid}"))
                    if len(row) == 4: btns.append(row); row = []
                if row: btns.append(row)
                await client.send_message(chat_id, "📺 باقي حلقات هذا المسلسل:", reply_markup=InlineKeyboardMarkup(btns))
    except Exception as e:
        logging.error(f"Start Error: {e}")
        await client.send_message(chat_id, "❌ عذراً، الحلقة غير متوفرة حالياً.")

# ===== التحقق من الاشتراك عبر الزر =====
@app.on_callback_query(filters.regex(r"^chk_"))
async def check_sub_callback(client, query):
    v_id = query.data.split("_")[1]
    try:
        await client.get_chat_member(f"@{PUBLIC_CHANNEL}", query.from_user.id)
        await query.message.delete()
        await send_video_to_user(client, query.from_user.id, v_id)
    except:
        await query.answer("⚠️ لم تشترك بعد في القناة!", show_alert=True)

# ===== تعديل العناوين (اختياري) =====
@app.on_message(filters.command("edit") & filters.private)
async def edit_title(client, message):
    if len(message.command) < 3: return
    # تعديل عنوان حلقة محددة: /edit [v_id] [العنوان الجديد]
    v_id = message.command[1]
    new_title = " ".join(message.command[2:])
    db_execute("UPDATE videos SET title = ? WHERE v_id = ?", (new_title, v_id), fetch=False)
    await message.reply_text("✅ تم تحديث العنوان.")

print("🚀 البوت الذكي يعمل الآن بنجاح...")
app.run()
