import os
import aiosqlite
import logging
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import UserNotParticipant

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHANNEL_ID = int(os.environ.get("CHANNEL_ID", 0))
PUBLIC_CHANNEL = os.environ.get("PUBLIC_CHANNEL", "").replace("@", "")

app = Client("BottemoBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

class VideoBot:
    def __init__(self, client):
        self.client = client

    async def init_db(self):
        async with aiosqlite.connect("bot_data.db") as db:
            await db.execute('''
                CREATE TABLE IF NOT EXISTS videos (
                    v_id TEXT PRIMARY KEY,
                    duration INTEGER,
                    poster_id TEXT,
                    status TEXT,
                    ep_num INTEGER,
                    user_id INTEGER
                )
            ''')
            await db.commit()

    async def db_execute(self, query, params=(), fetch=True):
        async with aiosqlite.connect("bot_data.db") as db:
            cursor = await db.execute(query, params)
            if fetch:
                rows = await cursor.fetchall()
            else:
                rows = None
            await db.commit()
            return rows

    def format_duration(self, seconds):
        if not seconds:
            return "غير محدد"
        mins, secs = divmod(seconds, 60)
        return f"{mins}:{secs:02d} دقيقة"

    # -------------------- استقبال الفيديو --------------------
    async def receive_video(self, client, message):
        v_id = str(message.id)
        duration_sec = message.video.duration if message.video else getattr(message.document, "duration", 0)

        # جلب آخر poster_id
        last_poster = await self.db_execute(
            "SELECT poster_id FROM videos WHERE status='posted' ORDER BY rowid DESC LIMIT 1"
        )
        poster_id = last_poster[0][0] if last_poster else None

        await self.db_execute(
            "INSERT INTO videos (v_id, duration, poster_id, status, user_id) VALUES (?, ?, ?, ?, ?)",
            (v_id, duration_sec, poster_id, "waiting", message.from_user.id),
            fetch=False
        )
        await message.reply_text("✅ تم استلام الفيديو.\n🖼 الآن أرسل البوستر أو تجاهل إذا المسلسل موجود مسبقاً.")

    # -------------------- استقبال البوستر --------------------
    async def receive_poster(self, client, message):
        res = await self.db_execute(
            "SELECT v_id FROM videos WHERE status='waiting' AND user_id=? ORDER BY rowid DESC LIMIT 1",
            (message.from_user.id,)
        )
        if not res:
            return
        v_id = res[0][0]

        current_poster = await self.db_execute("SELECT poster_id FROM videos WHERE v_id=?", (v_id,))
        if not current_poster[0][0]:
            await self.db_execute(
                "UPDATE videos SET poster_id=?, status='awaiting_ep' WHERE v_id=?",
                (message.photo.file_id, v_id),
                fetch=False
            )
        else:
            await self.db_execute(
                "UPDATE videos SET status='awaiting_ep' WHERE v_id=?",
                (v_id,),
                fetch=False
            )
        await message.reply_text("🖼 تم حفظ البوستر أو استخدام البوستر السابق.\n🔢 أرسل الآن رقم الحلقة:")

    # -------------------- استلام رقم الحلقة --------------------
    async def receive_ep_number(self, client, message):
        if not message.text.isdigit():
            return
        res = await self.db_execute(
            "SELECT v_id FROM videos WHERE status='awaiting_ep' AND user_id=? ORDER BY rowid DESC LIMIT 1",
            (message.from_user.id,)
        )
        if not res:
            return
        v_id = res[0][0]
        ep_num = int(message.text)

        await self.db_execute(
            "UPDATE videos SET ep_num=?, status='ready' WHERE v_id=?",
            (ep_num, v_id),
            fetch=False
        )

        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("SD", callback_data=f"p_SD_{v_id}"),
             InlineKeyboardButton("HD", callback_data=f"p_HD_{v_id}"),
             InlineKeyboardButton("4K", callback_data=f"p_4K_{v_id}")]
        ])
        await message.reply_text(f"✅ الحلقة {ep_num} جاهزة. اختر الجودة للنشر:", reply_markup=markup)

    # -------------------- النشر --------------------
    async def publish_now(self, client, query):
        _, quality, v_id = query.data.split("_")
        res = await self.db_execute("SELECT ep_num, poster_id, duration FROM videos WHERE v_id=?", (v_id,))
        if not res:
            await query.message.edit_text("❌ خطأ: لم يتم العثور على البيانات.")
            return
        ep_num, poster_id, duration = res[0]
        duration_str = self.format_duration(duration)

        watch_link = f"https://t.me/{(await self.client.get_me()).username}?start={v_id}"
        caption = (f"🎬 الحلقة {ep_num}\n"
                   f"⏱ المدة: {duration_str}\n"
                   f"✨ الجودة: {quality}\n\n"
                   f"📥 مشاهدة الآن")
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("▶️ مشاهدة الآن", url=watch_link)]])
        try:
            await self.client.send_video(chat_id=f"@{PUBLIC_CHANNEL}", video=int(v_id), thumb=poster_id, caption=caption, reply_markup=markup)
            await self.db_execute("UPDATE videos SET status='posted' WHERE v_id=?", (v_id,), fetch=False)
            await query.message.edit_text("🚀 تم النشر بنجاح.")
        except Exception as e:
            await query.message.edit_text(f"❌ خطأ في النشر: {e}")

    # -------------------- إرسال الحلقة مع قائمة الحلقات نفسها --------------------
    async def send_video_with_list(self, client, chat_id, v_id):
        # جلب poster_id للحلقات
        res = await self.db_execute("SELECT poster_id FROM videos WHERE v_id=?", (v_id,))
        if not res or not res[0][0]:
            return
        poster_id = res[0][0]

        # جلب جميع الحلقات المرتبطة بالبوستر
        all_eps = await self.db_execute(
            "SELECT v_id, ep_num FROM videos WHERE poster_id=? AND status='posted' ORDER BY ep_num ASC",
            (poster_id,)
        )
        if not all_eps:
            return

        # بناء أزرار الحلقات
        buttons = []
        row = []
        bot_username = (await client.get_me()).username
        for vid, num in all_eps:
            text = f"▶️ {num}" if vid == v_id else f"{num}"
            row.append(InlineKeyboardButton(text, callback_data=f"watch_{vid}"))
            if len(row) == 4:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

        # إرسال الفيديو مع لوحة الحلقات أسفل نفس الرسالة
        res_video = await self.db_execute("SELECT duration FROM videos WHERE v_id=?", (v_id,))
        duration = res_video[0][0] if res_video else 0
        caption = f"🎬 الحلقة {v_id}\n⏱ المدة: {self.format_duration(duration)}"
        try:
            await client.send_video(chat_id, video=int(v_id), thumb=poster_id, caption=caption, reply_markup=InlineKeyboardMarkup(buttons))
        except Exception as e:
            logging.error(f"Error sending video with list: {e}")
            await client.send_message(chat_id, "❌ الحلقة غير متوفرة حالياً.")

    # -------------------- مشاهدة حلقة من قائمة الحلقات --------------------
    async def watch_episode(self, client, query):
        v_id = query.data.split("_")[1]
        try:
            await query.message.delete()
        except:
            pass
        await self.send_video_with_list(client, query.from_user.id, v_id)

    # -------------------- التحقق من الاشتراك --------------------
    async def check_sub(self, client, query):
        v_id = query.data.split("_")[1]
        try:
            await client.get_chat_member(f"@{PUBLIC_CHANNEL}", query.from_user.id)
            await query.message.delete()
            await self.send_video_with_list(client, query.from_user.id, v_id)
        except UserNotParticipant:
            await query.answer("⚠️ لم تشترك بعد!", show_alert=True)

# =================== Instance ===================
bot = VideoBot(app)

# =================== Handlers ===================
@app.on_message(filters.chat(CHANNEL_ID) & (filters.video | filters.document))
async def handle_video(client, message):
    await bot.receive_video(client, message)

@app.on_message(filters.chat(CHANNEL_ID) & filters.photo)
async def handle_poster(client, message):
    await bot.receive_poster(client, message)

@app.on_message(filters.chat(CHANNEL_ID) & filters.text & ~filters.command(["start"]))
async def handle_ep_number(client, message):
    await bot.receive_ep_number(client, message)

@app.on_callback_query(filters.regex(r"^p_"))
async def handle_publish(client, query):
    await bot.publish_now(client, query)

@app.on_message(filters.command("start") & filters.private)
async def handle_start(client, message):
    if len(message.command) <= 1:
        await message.reply_text("أهلاً بك!")
        return
    v_id = message.command[1]

    try:
        await client.get_chat_member(f"@{PUBLIC_CHANNEL}", message.from_user.id)
    except UserNotParticipant:
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 اشترك هنا", url=f"https://t.me/{PUBLIC_CHANNEL}")],
            [InlineKeyboardButton("✅ تم الاشتراك", callback_data=f"chk_{v_id}")]
        ])
        await message.reply_text("⚠️ اشترك أولاً لمشاهدة الحلقة.", reply_markup=markup)
        return
    await bot.send_video_with_list(client, message.chat.id, v_id)

@app.on_callback_query(filters.regex(r"^chk_"))
async def handle_check_sub(client, query):
    await bot.check_sub(client, query)

@app.on_callback_query(filters.regex(r"^watch_"))
async def handle_watch_episode(client, query):
    await bot.watch_episode(client, query)

# =================== Run Bot ===================
app.run()
