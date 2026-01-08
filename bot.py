import os
import time
import asyncio
import subprocess
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from db import conn

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

TOOLS = {
    "boltnew": "boltnew-verify-tool",
    "k12": "k12-verify-tool",
    "one": "one-verify-tool",
    "perplexity": "perplexity-verify-tool",
    "spotify": "spotify-verify-tool",
    "veterans": "veterans-verify-tool",
    "youtube": "youtube-verify-tool",
}

user_tool = {}
user_cooldown = {}
job_queue = asyncio.Queue()
COOLDOWN_SECONDS = 60

def ensure_user(uid, username):
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (id, username) VALUES (%s,%s) ON CONFLICT DO NOTHING",
        (uid, username)
    )

def get_credits(uid):
    cur = conn.cursor()
    cur.execute("SELECT credits FROM users WHERE id=%s", (uid,))
    row = cur.fetchone()
    return row[0] if row else 0

def deduct_credit(uid):
    cur = conn.cursor()
    cur.execute("UPDATE users SET credits = credits - 1 WHERE id=%s", (uid,))

def tools_keyboard():
    kb = InlineKeyboardMarkup(row_width=2)
    for k in TOOLS:
        kb.insert(InlineKeyboardButton(k.upper(), callback_data=f"tool:{k}"))
    return kb

@dp.message_handler(commands=["start"])
async def start(message: types.Message):
    ensure_user(message.from_user.id, message.from_user.username)
    await message.reply(
        "🤖 SheerID Multi-Tool Bot\n\nSelect a verification tool:",
        reply_markup=tools_keyboard()
    )

@dp.message_handler(commands=["buy"])
async def buy(message: types.Message):
    await message.reply(
        "💳 Binance Pay\n\nSend payment to YOUR_BINANCE_PAY_ID\n"
        "1 USDT = 1 Credit\n\nAfter payment send:\n/paid TX_ID CREDITS"
    )

@dp.message_handler(commands=["paid"])
async def paid(message: types.Message):
    parts = message.text.split()
    if len(parts) != 3:
        return await message.reply("Usage: /paid TX_ID CREDITS")
    tx, credits = parts[1], int(parts[2])
    uid = message.from_user.id
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO payments (user_id, credits, tx_id) VALUES (%s,%s,%s)",
        (uid, credits, tx)
    )
    await message.reply("Payment submitted. Await admin approval.")

@dp.message_handler(commands=["approve"])
async def approve(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    _, tx = message.text.split()
    cur = conn.cursor()
    cur.execute(
        "SELECT user_id, credits FROM payments WHERE tx_id=%s AND status='pending'",
        (tx,)
    )
    row = cur.fetchone()
    if not row:
        return await message.reply("Invalid TX")
    uid, credits = row
    cur.execute("UPDATE users SET credits = credits + %s WHERE id=%s", (credits, uid))
    cur.execute("UPDATE payments SET status='approved' WHERE tx_id=%s", (tx,))
    await message.reply("Credits added")

@dp.callback_query_handler(lambda c: c.data.startswith("tool:"))
async def tool_select(cb: types.CallbackQuery):
    tool = cb.data.split(":")[1]
    user_tool[cb.from_user.id] = tool
    await cb.message.edit_text(f"{tool.upper()} selected. Send verification URL")
    await cb.answer()

@dp.message_handler(lambda m: m.text.startswith("http"))
async def handle_url(message: types.Message):
    uid = message.from_user.id
    now = time.time()
    if uid not in user_tool:
        return await message.reply("Select a tool first")
    if uid in user_cooldown and now - user_cooldown[uid] < COOLDOWN_SECONDS:
        return await message.reply("Cooldown active")
    if get_credits(uid) <= 0:
        return await message.reply("No credits. Use /buy")
    user_cooldown[uid] = now
    tool = user_tool[uid]
    folder = TOOLS[tool]
    await message.reply("Added to queue")
    await job_queue.put((message, uid, tool, folder, message.text))

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
            await message.reply(f"{tool.upper()} RESULT:\n{output[:3900]}")
        except Exception as e:
            await message.reply(f"Error: {e}")
        job_queue.task_done()

async def on_startup(dp):
    asyncio.create_task(worker())

if __name__ == "__main__":
    executor.start_polling(dp, on_startup=on_startup, skip_updates=True)
