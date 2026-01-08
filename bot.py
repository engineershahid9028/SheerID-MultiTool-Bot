import os
import time
import asyncio
import subprocess
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from db import conn

# ================== CONFIG ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
COOLDOWN_SECONDS = 60

# Tool → folder mapping
TOOLS = {
    "boltnew": "boltnew-verify-tool",
    "k12": "k12-verify-tool",
    "one": "one-verify-tool",
    "perplexity": "perplexity-verify-tool",
    "spotify": "spotify-verify-tool",
    "veterans": "veterans-verify-tool",
    "youtube": "youtube-verify-tool",
}

# ================== BOT INIT ==================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

# ================== STATE ==================
user_selected_tool = {}   # user_id → tool
user_cooldown = {}        # user_id → last_time
job_queue = asyncio.Queue()

# ================== DB HELPERS ==================
def ensure_user(user_id, username):
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO users (id, username)
        VALUES (%s, %s)
        ON CONFLICT (id) DO NOTHING
        """,
        (user_id, username)
    )

def get_credits(user_id):
    cur = conn.cursor()
    cur.execute("SELECT credits FROM users WHERE id = %s", (user_id,))
    row = cur.fetchone()
    return row[0] if row else 0

def deduct_credit(user_id):
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET credits = credits - 1 WHERE id = %s",
        (user_id,)
    )

# ================== UI ==================
def tools_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    for tool in TOOLS:
        kb.insert(
            InlineKeyboardButton(
                text=tool.upper(),
                callback_data=f"tool:{tool}"
            )
        )
    return kb

# ================== COMMANDS ==================
@dp.message_handler(commands=["start"])
async def start(message: types.Message):
    ensure_user(message.from_user.id, message.from_user.username)
    await message.reply(
        "🤖 *SheerID Multi-Tool Bot*\n\n"
        "Select a verification tool:",
        reply_markup=tools_keyboard(),
        parse_mode="Markdown"
    )

@dp.message_handler(commands=["buy"])
async def buy(message: types.Message):
    await message.reply(
        "💳 *Binance Pay*\n\n"
        "Send payment to your Binance Pay ID\n"
        "💰 1 USDT = 1 credit\n\n"
        "After payment send:\n"
        "`/paid TX_ID CREDITS`",
        parse_mode="Markdown"
    )

@dp.message_handler(commands=["paid"])
async def paid(message: types.Message):
    parts = message.text.split()
    if len(parts) != 3:
        return await message.reply("Usage: /paid TX_ID CREDITS")

    tx_id = parts[1]
    credits = int(parts[2])
    user_id = message.from_user.id

    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO payments (user_id, credits, tx_id)
        VALUES (%s, %s, %s)
        """,
        (user_id, credits, tx_id)
    )

    await message.reply("✅ Payment submitted. Await admin approval.")

@dp.message_handler(commands=["approve"])
async def approve(message: types.Message):
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
        return await message.reply("❌ Invalid or already approved TX")

    user_id, credits = row

    cur.execute(
        "UPDATE users SET credits = credits + %s WHERE id = %s",
        (credits, user_id)
    )
    cur.execute(
        "UPDATE payments SET status='approved' WHERE tx_id = %s",
        (tx_id,)
    )

    await message.reply("✅ Credits added successfully")

# ================== TOOL SELECTION ==================
@dp.callback_query_handler(lambda c: c.data.startswith("tool:"))
async def select_tool(callback: types.CallbackQuery):
    tool = callback.data.split(":")[1]
    user_selected_tool[callback.from_user.id] = tool

    await callback.message.edit_text(
        f"✅ *{tool.upper()}* selected\n\n"
        "Send the SheerID verification URL",
        parse_mode="Markdown"
    )
    await callback.answer()

# ================== URL HANDLER ==================
@dp.message_handler(lambda m: m.text.startswith("http"))
async def handle_url(message: types.Message):
    user_id = message.from_user.id
    now = time.time()

    if user_id not in user_selected_tool:
        return await message.reply("❗ Select a tool first using /start")

    if user_id in user_cooldown and now - user_cooldown[user_id] < COOLDOWN_SECONDS:
        return await message.reply("⏳ Cooldown active. Please wait.")

    if get_credits(user_id) <= 0:
        return await message.reply("💳 No credits left. Use /buy")

    user_cooldown[user_id] = now
    tool = user_selected_tool[user_id]
    folder = TOOLS[tool]

    await message.reply("🕒 Added to queue. Please wait...")
    await job_queue.put((message, user_id, tool, folder, message.text))

# ================== QUEUE WORKER ==================
async def worker():
    while True:
        message, user_id, tool, folder, url = await job_queue.get()

        try:
            # 🔥 CORRECT COMMAND HANDLING
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
            deduct_credit(user_id)

            if len(output) > 4000:
                output = output[:3900] + "\n\n⚠️ Output truncated"

            await message.reply(
                f"📄 *{tool.upper()} RESULT:*\n\n{output}",
                parse_mode="Markdown"
            )

        except Exception as e:
            await message.reply(f"❌ Error: {e}")

        job_queue.task_done()

# ================== STARTUP ==================
async def on_startup(dp):
    asyncio.create_task(worker())

if __name__ == "__main__":
    executor.start_polling(dp, on_startup=on_startup, skip_updates=True)
