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
WEBHOOK_HOST = os.getenv("WEBHOOK_HOST")  # e.g. https://your-app.up.railway.app
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"
PORT = int(os.getenv("PORT", 8000))
COOLDOWN_SECONDS = 60

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

# ================= STATE =================
user_selected_tool = {}
user_cooldown = {}
job_queue = asyncio.Queue()

# ================= DB HELPERS =================
def ensure_user(uid, username):
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO users (id, username)
        VALUES (%s, %s)
        ON CONFLICT (id) DO NOTHING
        """,
        (uid, username)
    )

def get_credits(uid):
    cur = conn.cursor()
    cur.execute("SELECT credits FROM users WHERE id=%s", (uid,))
    row = cur.fetchone()
    return row[0] if row else 0

def deduct_credit(uid):
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET credits = credits - 1 WHERE id=%s",
        (uid,)
    )

# ================= UI =================
def tools_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    for t in TOOLS:
        kb.insert(
            InlineKeyboardButton(
                text=t.upper(),
                callback_data=f"tool:{t}"
            )
        )
    return kb

# ================= COMMANDS =================
@dp.message_handler(commands=["start"])
async def start_cmd(message: types.Message):
    ensure_user(message.from_user.id, message.from_user.username)
    await message.reply(
        "🤖 *SheerID Multi-Tool Bot*\n\nSelect a verification tool:",
        reply_markup=tools_keyboard(),
        parse_mode="Markdown"
    )

@dp.message_handler(commands=["buy"])
async def buy_cmd(message: types.Message):
    await message.reply(
        "💳 *Binance Pay*\n\n"
        "1 USDT = 1 Credit\n\n"
        "After payment send:\n"
        "`/paid TX_ID CREDITS`",
        parse_mode="Markdown"
    )

@dp.message_handler(commands=["paid"])
async def paid_cmd(message: types.Message):
    parts = message.text.split()
    if len(parts) != 3:
        return await message.reply("Usage: /paid TX_ID CREDITS")

    tx_id, credits = parts[1], int(parts[2])
    uid = message.from_user.id

    cur = conn.cursor()
    cur.execute(
        "INSERT INTO payments (user_id, credits, tx_id) VALUES (%s,%s,%s)",
        (uid, credits, tx_id)
    )

    await message.reply("✅ Payment submitted. Await admin approval.")

@dp.message_handler(commands=["approve"])
async def approve_cmd(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    parts = message.text.split()
    if len(parts) != 2:
        return await message.reply("Usage: /approve TX_ID")

    tx_id = parts[1]
    cur = conn.cursor()
    cur.execute(
        "SELECT user_id, credits FROM payments WHERE tx_id=%s AND status='pending'",
        (tx_id,)
    )
    row = cur.fetchone()

    if not row:
        return await message.reply("❌ Invalid TX")

    uid, credits = row
    cur.execute("UPDATE users SET credits = credits + %s WHERE id=%s", (credits, uid))
    cur.execute("UPDATE payments SET status='approved' WHERE tx_id=%s", (tx_id,))
    await message.reply("✅ Credits added")

# ================= TOOL SELECTION =================
@dp.callback_query_handler(lambda c: c.data.startswith("tool:"))
async def tool_select(cb: types.CallbackQuery):
    tool = cb.data.split(":")[1]
    user_selected_tool[cb.from_user.id] = tool
    await cb.message.edit_text(
        f"✅ *{tool.upper()}* selected\n\nSend the verification URL",
        parse_mode="Markdown"
    )
    await cb.answer()

# ================= URL HANDLER =================
@dp.message_handler(lambda m: m.text.startswith("http"))
async def handle_url(message: types.Message):
    uid = message.from_user.id
    now = time.time()

    if uid not in user_selected_tool:
        return await message.reply("❗ Use /start and select a tool first")

    if uid in user_cooldown and now - user_cooldown[uid] < COOLDOWN_SECONDS:
        return await message.reply("⏳ Cooldown active")

    if get_credits(uid) <= 0:
        return await message.reply("💳 No credits left. Use /buy")

    user_cooldown[uid] = now
    tool = user_selected_tool[uid]
    folder = TOOLS[tool]

    await message.reply("🕒 Added to queue...")
    await job_queue.put((message, uid, tool, folder, message.text))

# ================= QUEUE WORKER =================
async def worker():
    while True:
        message, uid, tool, folder, url = await job_queue.get()
        try:
            if tool == "veterans":
                cmd = ["python", f"{folder}/main.py"]
            else:
                cmd = ["python", f"{folder}/main.py", url]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300
            )

            output = result.stdout or result.stderr or "No output"
            deduct_credit(uid)

            if len(output) > 4000:
                output = output[:3900] + "\n⚠️ Output truncated"

            await message.reply(
                f"📄 *{tool.upper()} RESULT:*\n\n{output}",
                parse_mode="Markdown"
            )

        except Exception as e:
            await message.reply(f"❌ Error: {e}")

        job_queue.task_done()

# ================= WEBHOOK STARTUP =================
async def on_startup(dp):
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.set_webhook(WEBHOOK_URL)
    asyncio.create_task(worker())
    print("Webhook set:", WEBHOOK_URL)

async def on_shutdown(dp):
    await bot.delete_webhook()

# ================= RUN =================
if __name__ == "__main__":
    start_webhook(
        dispatcher=dp,
        webhook_path=WEBHOOK_PATH,
        on_startup=on_startup,
        on_shutdown=on_shutdown,
        skip_updates=True,
        host="0.0.0.0",
        port=PORT,
    )
