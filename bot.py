import os
import time
import asyncio
import subprocess
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.executor import start_webhook
from db import conn

# ================= CONFIG =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
PORT = int(os.getenv("PORT", 8000))
COOLDOWN_SECONDS = 60

# Railway auto domain fallback
PUBLIC_HOST = (
    os.getenv("WEBHOOK_HOST")
    or os.getenv("RAILWAY_PUBLIC_DOMAIN")
    or os.getenv("RAILWAY_URL")
)

if not PUBLIC_HOST:
    raise RuntimeError("No public host found. Set WEBHOOK_HOST.")

if not PUBLIC_HOST.startswith("http"):
    PUBLIC_HOST = "https://" + PUBLIC_HOST

WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
WEBHOOK_URL = f"{PUBLIC_HOST}{WEBHOOK_PATH}"

TOOLS = {
    "boltnew": "boltnew-verify-tool",
    "k12": "k12-verify-tool",
    "one": "one-verify-tool",
    "perplexity": "perplexity-verify-tool",
    "spotify": "spotify-verify-tool",
    "veterans": "veterans-verify-tool",
    "youtube": "youtube-verify-tool",
}

# ================= BOT INIT =================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

user_selected_tool = {}
user_cooldown = {}
job_queue = asyncio.Queue()

# ================= DB =================
def ensure_user(uid, username):
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (id, username) VALUES (%s,%s) ON CONFLICT DO NOTHING",
        (uid, username)
    )

def get_credits(uid):
    cur = conn.cursor()
    cur.execute("SELECT credits FROM users WHERE id=%s", (uid,))
    r = cur.fetchone()
    return r[0] if r else 0

def deduct_credit(uid):
    cur = conn.cursor()
    cur.execute("UPDATE users SET credits = credits - 1 WHERE id=%s", (uid,))

# ================= UI =================
def tools_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    for t in TOOLS:
        kb.insert(InlineKeyboardButton(t.upper(), callback_data=f"tool:{t}"))
    return kb

# ================= COMMANDS =================
@dp.message_handler(commands=["start"])
async def start_cmd(message: types.Message):
    ensure_user(message.from_user.id, message.from_user.username)
    await message.reply(
        "🤖 *SheerID Multi-Tool Bot*\n\nSelect a tool:",
        reply_markup=tools_keyboard(),
        parse_mode="Markdown"
    )

@dp.callback_query_handler(lambda c: c.data.startswith("tool:"))
async def tool_select(cb: types.CallbackQuery):
    tool = cb.data.split(":")[1]
    user_selected_tool[cb.from_user.id] = tool
    await cb.message.edit_text(
        f"✅ *{tool.upper()} selected*\nSend verification URL",
        parse_mode="Markdown"
    )
    await cb.answer()

@dp.message_handler(lambda m: m.text.startswith("http"))
async def handle_url(message: types.Message):
    uid = message.from_user.id
    now = time.time()

    if uid not in user_selected_tool:
        return await message.reply("❗ Select a tool first")

    if uid in user_cooldown and now - user_cooldown[uid] < COOLDOWN_SECONDS:
        return await message.reply("⏳ Cooldown active")

    if get_credits(uid) <= 0:
        return await message.reply("💳 No credits")

    user_cooldown[uid] = now
    tool = user_selected_tool[uid]
    folder = TOOLS[tool]

    await message.reply("🕒 Processing...")
    await job_queue.put((message, uid, tool, folder, message.text))

# ================= WORKER =================
async def worker():
    while True:
        message, uid, tool, folder, url = await job_queue.get()
        try:
            cmd = ["python", f"{folder}/main.py"] if tool == "veterans" else ["python", f"{folder}/main.py", url]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            output = result.stdout or result.stderr or "No output"
            deduct_credit(uid)
            await message.reply(output[:3900])
        except Exception as e:
            await message.reply(f"❌ Error: {e}")
        job_queue.task_done()

# ================= WEBHOOK =================
async def on_startup(dp):
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.set_webhook(WEBHOOK_URL)
    asyncio.create_task(worker())
    print("Webhook set:", WEBHOOK_URL)

async def on_shutdown(dp):
    await bot.delete_webhook()

if __name__ == "__main__":
    start_webhook(
        dispatcher=dp,
        webhook_path=WEBHOOK_PATH,
        on_startup=on_startup,
        on_shutdown=on_shutdown,
        host="0.0.0.0",
        port=PORT,
        skip_updates=True,
    )
