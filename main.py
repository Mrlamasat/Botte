import os
import sqlite3
import logging
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

# ===== إعدادات التسجيل =====
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ===== المتغيرات الأساسية =====
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHANNEL_ID = int(os.environ.get("CHANNEL_ID", 0))  
PUBLIC_CHANNEL = os.environ.get("PUBLIC_CHANNEL", "")  

app = Client("BottemoBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ===== قاعدة البيانات =====
def db_execute(query, params=(), fetch=True):
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute(query, params)
    conn.commit()
    res = cursor.fetchall() if fetch else None
    conn.close()
    return res

# إنشاء الجدول مع إضافة عمود الجودة ورقم الحلقة
db_execute('''CREATE TABLE IF NOT EXISTS videos 
              (v_id TEXT PRIMARY KEY, duration TEXT, poster_id TEXT, status TEXT, ep_num INTEGER, quality TEXT)''', fetch=False)

# ===== 1. استقبال الفيديو =====
@app.on_message(filters.chat(CHANNEL_ID) & (filters.video | filters.document))
async def receive_video(client, message):
    v_id = str(message.id)
    duration_sec = 0
    if message.video:
        duration_sec = message.video.duration
    elif message.document and hasattr(message.document, "duration"):
        duration_sec = message.document.duration
        
    mins, secs = divmod(duration_sec, 60)
    duration = f"{mins}:{secs:02d} دقيقة" if duration_sec else "غير محدد"
    
    db_execute("INSERT OR REPLACE INTO videos (v_id, duration, status) VALUES (?, ?, ?)", (v_id, duration, "waiting_poster"), fetch=False)
    await message.reply_text(f"✅ تم استلام الفيديو.\n🖼 الآن أرسل **البوستر** (صورة فقط بدون وصف):")

# ===== 2. استقبال البوستر =====
@app.on_message(filters.chat(CHANNEL_ID) & filters.photo)
async def receive_poster(client, message):
    res = db_execute("SELECT v_id FROM videos WHERE status='waiting_poster' ORDER BY rowid DESC LIMIT 1")
    if not res: return
    v_id = res[0][0]
    
    db_execute("UPDATE videos SET poster_id=?, status='waiting_ep' WHERE v_id=?", (message.photo.file_id, v_id), fetch=False)
    await message.reply_text(f"🖼 تم حفظ البوستر.\n🔢 أرسل الآن **رقم الحلقة**:")

# ===== 3. استقبال رقم الحلقة وعرض خيارات الجودة =====
@app.on_message(filters.chat(CHANNEL_ID) & filters.text & ~filters.command(["start"]))
async def receive_ep_number(client, message):
    if not message.text.isdigit(): return
    
    res = db_execute("SELECT v_id FROM videos WHERE status='waiting_ep' ORDER BY rowid DESC LIMIT 1")
    if not res: return
    v_id = res[0][0]
    
    db_execute("UPDATE videos SET ep_num=?, status='waiting_quality' WHERE v_id=?", (int(message.text), v_id), fetch=False)
    
    # أزرار اختيار الجودة
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎬 HD (720p/1080p)", callback_data=f"setq_{v_id}_HD"),
            InlineKeyboardButton("📺 SD (480p)", callback_data=f"setq_{v_id}_SD")
        ]
    ])
    await message.reply_text("✨ ممتاز، الآن اختر **جودة الحلقة** ليتم النشر:", reply_markup=keyboard)

# ===== 4. معالجة اختيار الجودة والنشر النهائي =====
@app.on_callback_query(filters.regex(r"^setq_"))
async def set_quality_and_post(client, query: CallbackQuery):
    _, v_id, quality = query.data.split("_")
    
    res = db_execute("SELECT ep_num, duration, poster_id FROM videos WHERE v_id=?", (v_id,))
    if not res:
        await query.answer("⚠️ خطأ: تعذر العثور على بيانات الفيديو.", show_alert=True)
        return
    
    ep_num, duration, poster_id = res[0]
    db_execute("UPDATE videos SET quality=?, status='posted' WHERE v_id=?", (quality, v_id), fetch=False)
    
    bot_info = await client.get_me()
    watch_link = f"https://t.me/{bot_info.username}?start={v_id}"
    
    caption = (f"🎬 **الحلقة {ep_num}**\n"
               f"⏱ **المدة:** {duration}\n"
               f"✨ **الجودة:** {quality}\n\n"
               f"📥 اضغط الزر لمشاهدة الحلقة")

    # النشر في القناة العامة
    if PUBLIC_CHANNEL:
        try:
            await client.send_photo(
                chat_id=PUBLIC_CHANNEL,
                photo=poster_id,
                caption=caption,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("▶️ فتح الحلقة الآن", url=watch_link)]])
            )
            await query.message.edit_text(f"🚀 تم تحديد الجودة ({quality}) والنشر بنجاح في القناة!")
        except Exception as e:
            await query.message.edit_text(f"⚠️ فشل النشر في القناة: {e}")
    else:
        await query.message.edit_text(f"✅ تم الحفظ بجودة {quality}. الرابط:\n{watch_link}")

# ===== تشغيل الحلقة (للمشاهدين) =====
@app.on_message(filters.command("start") & filters.private)
async def start_handler(client, message):
    if len(message.command) <= 1:
        await message.reply_text("أهلاً بك يا محمد! أرسل رابط الحلقة للمشاهدة.")
        return
    v_id = message.command[1]
    await send_video_with_list(client, message.chat.id, v_id)

async def send_video_with_list(client, chat_id, v_id):
    try:
        video_info = db_execute("SELECT poster_id, duration, quality, ep_num FROM videos WHERE v_id=?", (v_id,))
        if not video_info:
            await client.send_message(chat_id, "❌ عذراً، الحلقة غير متوفرة.")
            return
            
        poster_id, duration, quality, ep_num = video_info[0]
        
        # إرسال الفيديو الأصلي من قناة التخزين
        await client.copy_message(chat_id, CHANNEL_ID, int(v_id), protect_content=True)
        
        # إرسال الوصف وقائمة الحلقات
        caption = f"🎬 **الحلقة {ep_num}**\n⏱ **المدة:** {duration}\n✨ **الجودة:** {quality}"
        await client.send_message(chat_id, caption)
    except Exception as e:
        logging.error(f"Error in send_video: {e}")

app.run()
