"""
SheerID Spotify Verification – ALL IN ONE FILE

Includes:
- Telegram Bot
- Async Queue
- Rate Limiting
- PostgreSQL Storage
- Admin Dashboard (FastAPI)
- Correct Verification Logic

Environment variables required:
- BOT_TOKEN
- DATABASE_URL
- ADMIN_IDS   (comma-separated Telegram IDs, e.g. "123,456")

Run:
python main.py
"""

import os
import re
import time
import json
import asyncio
import httpx
import psycopg2
from typing import Dict, Optional
from datetime import datetime

# ===================== CONFIG =====================
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x}

SHEERID_API = "https://services.sheerid.com/rest/v2"
PROGRAM_ID = "67c8c14f5f17a83b745e3f82"

RATE_LIMIT_SECONDS = 300  # 5 minutes per user

# ===================== STATES =====================
STATE_INSTANT_APPROVED = "instant_approved"
STATE_PENDING_REVIEW = "pending_review"
STATE_FAILED = "failed"

# ===================== DB =====================
conn = psycopg2.connect(DATABASE_URL)
conn.autocommit = True

def init_db():
    with conn.cursor() as cur:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS verifications (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT,
            verification_id TEXT UNIQUE,
            state TEXT,
            email TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        );
        """)

init_db()

def save_verification(tg_id, vid, state, email):
    with conn.cursor() as cur:
        cur.execute("""
        INSERT INTO verifications (telegram_id, verification_id, state, email)
        VALUES (%s,%s,%s,%s)
        ON CONFLICT (verification_id) DO NOTHING
        """, (tg_id, vid, state, email))

# ===================== QUEUE & RATE LIMIT =====================
queue = asyncio.Queue()
last_used = {}

def allowed(user_id: int) -> bool:
    now = time.time()
    if user_id in last_used and now - last_used[user_id] < RATE_LIMIT_SECONDS:
        return False
    last_used[user_id] = now
    return True

# ===================== VERIFIER =====================
def parse_vid(url: str) -> Optional[str]:
    m = re.search(r"verificationId=([a-f0-9]+)", url, re.I)
    return m.group(1) if m else None

class SpotifyVerifier:
    def __init__(self, url: str):
        self.vid = parse_vid(url)
        self.client = httpx.Client(timeout=30)

    def close(self):
        self.client.close()

    def request(self, method, endpoint, body=None):
        r = self.client.request(
            method,
            f"{SHEERID_API}{endpoint}",
            json=body,
            headers={"Content-Type": "application/json"},
        )
        return (r.json() if r.text else {}), r.status_code

    def verify(self) -> Dict:
        if not self.vid:
            return {"state": STATE_FAILED, "error": "Invalid verification URL"}

        # Step 1: check
        data, status = self.request("GET", f"/verification/{self.vid}")
        if status != 200:
            return {"state": STATE_FAILED, "error": "Verification not accessible"}

        # Step 2: submit minimal info (example data)
        payload = {
            "firstName": "John",
            "lastName": "Doe",
            "email": "john.doe@university.edu",
            "birthDate": "2002-05-12",
            "locale": "en-US",
            "metadata": {
                "verificationId": self.vid,
                "refererUrl": f"https://services.sheerid.com/verify/{PROGRAM_ID}/?verificationId={self.vid}",
            },
        }

        data, status = self.request(
            "POST",
            f"/verification/{self.vid}/step/collectStudentPersonalInfo",
            payload,
        )

        if status != 200:
            return {"state": STATE_FAILED, "error": f"Submit failed ({status})"}

        step = data.get("currentStep")
        ver_status = data.get("verificationStatus")

        if step == "success" or ver_status == "approved":
            return {
                "state": STATE_INSTANT_APPROVED,
                "email": payload["email"],
                "redirect": data.get("redirectUrl"),
            }

        if step in ["docUpload", "sso", "collectStudentPersonalInfo"]:
            return {
                "state": STATE_PENDING_REVIEW,
                "email": payload["email"],
            }

        return {"state": STATE_FAILED, "error": "Unknown state"}

# ===================== TELEGRAM BOT =====================
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Send verification link:\n/verify <sheerid_url>"
    )

async def verify_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not allowed(user_id):
        await update.message.reply_text("⏳ Please wait before retrying.")
        return

    if not context.args:
        await update.message.reply_text("❌ Usage: /verify <url>")
        return

    url = context.args[0]
    await queue.put((user_id, url))
    await update.message.reply_text("📥 Added to queue. Please wait…")

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    with conn.cursor() as cur:
        cur.execute("""
        SELECT state, COUNT(*) FROM verifications GROUP BY state
        """)
        rows = cur.fetchall()
    msg = "📊 Stats:\n" + "\n".join(f"{r[0]}: {r[1]}" for r in rows)
    await update.message.reply_text(msg)

# ===================== WORKER =====================
async def worker(app):
    while True:
        user_id, url = await queue.get()
        verifier = SpotifyVerifier(url)
        result = verifier.verify()

        save_verification(
            user_id,
            verifier.vid,
            result["state"],
            result.get("email"),
        )

        if result["state"] == STATE_INSTANT_APPROVED:
            msg = "🎉 INSTANT APPROVED"
        elif result["state"] == STATE_PENDING_REVIEW:
            msg = "⏳ Submitted for manual review (24–48h)"
        else:
            msg = f"❌ Failed: {result.get('error')}"

        await app.bot.send_message(user_id, msg)
        verifier.close()
        queue.task_done()

# ===================== ADMIN DASHBOARD =====================
from fastapi import FastAPI
import uvicorn

api = FastAPI()

@api.get("/admin/verifications")
def admin_verifications():
    with conn.cursor() as cur:
        cur.execute("""
        SELECT telegram_id, verification_id, state, email, created_at
        FROM verifications
        ORDER BY created_at DESC
        LIMIT 100
        """)
        return [
            {
                "telegram_id": r[0],
                "verification_id": r[1],
                "state": r[2],
                "email": r[3],
                "created_at": r[4],
            }
            for r in cur.fetchall()
        ]

# ===================== MAIN =====================
async def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("verify", verify_cmd))
    app.add_handler(CommandHandler("stats", admin_stats))

    asyncio.create_task(worker(app))
    asyncio.create_task(
        asyncio.to_thread(
            uvicorn.run, api, host="0.0.0.0", port=8000
        )
    )

    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())