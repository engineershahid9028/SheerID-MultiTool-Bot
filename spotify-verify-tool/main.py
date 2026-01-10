import os
import time
import asyncio
import random
import requests
import psycopg2
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

# ================= DATABASE =================

conn = psycopg2.connect(DATABASE_URL)
conn.autocommit = True

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

# ================= PROXY MANAGER =================

PROXIES = []

def load_proxies():
    global PROXIES
    if os.path.exists("proxies.txt"):
        with open("proxies.txt") as f:
            PROXIES = [x.strip() for x in f if x.strip()]
    print(f"Loaded {len(PROXIES)} proxies")

def check_proxy(proxy):
    try:
        r = requests.get(
            "https://ipv4.webshare.io/",
            proxies={"http": proxy, "https": proxy},
            timeout=8
        )
        return r.status_code == 200
    except:
        return False

def filter_proxies():
    global PROXIES
    good = []
    for p in PROXIES:
        if check_proxy(p):
            good.append(p)
    PROXIES = good
    print(f"Working proxies: {len(PROXIES)}")

def get_random_proxy():
    if not PROXIES:
        return None
    return random.choice(PROXIES)

# ================= QUEUE =================

job_queue = asyncio.Queue()

# ================= PROGRESS BAR =================

def progress_bar(percent):
    filled = int(percent / 10)
    return "█" * filled + "░" * (10 - filled)

async def update_progress(msg, percent):
    bar = progress_bar(percent)
    await msg.edit_text(f"⚙ Processing...\n\n[{bar}] {percent}%")

# ================= BOT COMMANDS =================

@dp.message_handler(commands=["start"])
async def start(message: types.Message):
    ensure_user(message.from_user.id, message.from_user.username)
    await message.reply("🤖 Worker Bot Ready\n\nSend a URL to start a job.")

@dp.message_handler(commands=["balance"])
async def balance(message: types.Message):
    credits = get_credits(message.from_user.id)
    await message.reply(f"💳 Credits: {credits}")

@dp.message_handler(commands=["addcredits"])
async def addcredits(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    _, uid, amount = message.text.split()
    cur = conn.cursor()
    cur.execute("UPDATE users SET credits = credits + %s WHERE id=%s", (amount, uid))
    await message.reply("Credits added")

# ================= JOB HANDLER =================

@dp.message_handler(lambda m: m.text.startswith("http"))
async def handle_job(message: types.Message):
    uid = message.from_user.id

    if get_credits(uid) <= 0:
        return await message.reply("❌ No credits")

    msg = await message.reply("⏳ Added to queue...")
    await job_queue.put((uid, message.text, msg))

# ================= WORKER =================

async def worker():
    while True:
        uid, url, msg = await job_queue.get()

        try:
            proxy = get_random_proxy()

            await update_progress(msg, 10)
            await asyncio.sleep(1)

            proxies = None
            if proxy:
                proxies = {"http": proxy, "https": proxy}

            await update_progress(msg, 30)

            r = requests.get(url, proxies=proxies, timeout=20)

            await update_progress(msg, 70)

            result = r.text[:3000]

            await update_progress(msg, 100)

            deduct_credit(uid)

            cur = conn.cursor()
            cur.execute(
                "INSERT INTO jobs (user_id, url, status, result) VALUES (%s,%s,%s,%s)",
                (uid, url, "done", result)
            )

            await msg.edit_text(f"✅ Job Done\n\n{result}")

        except Exception as e:
            await msg.edit_text(f"❌ Error: {e}")

        job_queue.task_done()

# ================= STARTUP =================

async def on_startup(dp):
    load_proxies()
    filter_proxies()
    asyncio.create_task(worker())
    print("Bot started")

if __name__ == "__main__":
    executor.start_polling(dp, on_startup=on_startup)
