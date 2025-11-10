import os
import logging
import asyncio
import json
from contextlib import asynccontextmanager
from http import HTTPStatus
from telegram import Update, InputPaidMediaPhoto
from telegram.ext import Application, MessageHandler, filters, ContextTypes, CommandHandler
from telegram.error import BadRequest
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from urllib.parse import urlparse
import aiohttp

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment variables for your deployment
TOKEN = os.getenv("BOT_TOKEN")
BASE_WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://telegrampaidcontent.onrender.com")
AI_API_KEY = os.getenv("AI_API_KEY")  # Your Grok API key starting with "xai-"
DEFAULT_FILE_ID = os.getenv("DEFAULT_FILE_ID", "AgACAgUAAxkBAAMTaQU-em6X2nceQKfORhFTTOQPfvEAAkQNaxvRCShU60Ue_Do0OekBAAMCAAN4AAM2BA")
STAR_COUNT = int(os.getenv("STAR_COUNT", "22"))
COOLDOWN_TIME = int(os.getenv("COOLDOWN_TIME", "60"))  # seconds cooldown between user msgs

if not TOKEN or not AI_API_KEY:
    raise ValueError("BOT_TOKEN and AI_API_KEY are required environment variables")

WEBHOOK_PATH = f"/webhook/{TOKEN}"
FULL_WEBHOOK_URL = f"{BASE_WEBHOOK_URL.rstrip('/')}{WEBHOOK_PATH}"

if not FULL_WEBHOOK_URL.startswith("https://"):
    raise ValueError("WEBHOOK_URL must start with https://")

ptb_app = Application.builder().token(TOKEN).build()

# User session storage for messages and rate limit
user_sessions = {}

SYSTEM_PROMPT = """
You are Kira Bloom, a playful 25yo OnlyFans model. Be flirty, teasing, and confident.
Build rapport naturally.
After 5-7 messages, or if the user shows interest in pics/nudes/content/uploads, decide to send media.
Respond ONLY in JSON:
{
  "response": "flirty reply text (short, <100 words)",
  "action": "chat" or "send_media",
  "content_type": "tease" or "nudes" (if send_media)
}
No explicit content; keep teasing and fun.
"""

async def generate_ai_response(user_text: str, user_id: int, history: list, msg_count: int) -> dict:
    session = user_sessions.get(user_id, {})
    current_time = asyncio.get_event_loop().time()
    last_time = session.get('last_time', 0)
    elapsed = current_time - last_time

    logger.info(f"Cooldown check for {user_id}: {elapsed:.1f}s elapsed; threshold {COOLDOWN_TIME}s, msg count {msg_count}")

    if msg_count > 3 and elapsed < COOLDOWN_TIME:
        logger.info(f"Cooldown active for {user_id}; sending cooldown message")
        session['last_time'] = current_time
        user_sessions[user_id] = session
        return {"response": "Slow down, babe—let's savor this! What's next? 💋", "action": "chat", "content_type": ""}

    session['last_time'] = current_time
    user_sessions[user_id] = session

    if len(user_text.strip()) < 2:
        return {"response": "😘", "action": "chat", "content_type": ""}

    history_str = "\n".join([f"User: {h['user']}\nAI: {h['ai']}" for h in history[-6:]]) if history else ""
    full_prompt = f"{SYSTEM_PROMPT}\nHistory:\n{history_str}\nUser: {user_text}\nRespond in JSON only."

    headers = {
        "Authorization": f"Bearer {AI_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": "grok-beta",
        "messages": [
            {"role": "system", "content": full_prompt},
            {"role": "user", "content": user_text}
        ],
        "max_tokens": 150,
        "temperature": 0.8
    }

    async with aiohttp.ClientSession() as session:
        async with session.post("https://api.x.ai/v1/chat/completions", json=data, headers=headers) as resp:
            if resp.status == 200:
                result = await resp.json()
                ai_text = result["choices"][0]["message"]["content"].strip()
                logger.info(f"AI raw response for {user_id}: {ai_text[:100]}...")
                try:
                    parsed = json.loads(ai_text)
                    return {
                        "response": parsed.get("response", ai_text),
                        "action": parsed.get("action", "chat"),
                        "content_type": parsed.get("content_type", "")
                    }
                except json.JSONDecodeError:
                    logger.warning("Failed to parse AI JSON response; sending raw response")
                    return {"response": ai_text, "action": "chat", "content_type": ""}
            else:
                logger.error(f"AI API error {resp.status}: {await resp.text()}")
                return {"response": "Oops, AI is busy now. Try again? 😘", "action": "chat", "content_type": ""}

async def handle_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.business_message or update.message
    if not msg or msg.chat.type != 'private':
        return

    chat_id = msg.chat.id
    user_id = msg.from_user.id if msg.from_user else update.effective_user.id
    business_connection_id = update.business_message.business_connection_id if update.business_message else None

    text = msg.text or ""

    if user_id not in user_sessions:
        user_sessions[user_id] = {'msgs': [], 'count': 0, 'last_time': 0}

    session = user_sessions[user_id]
    session['count'] += 1
    session['msgs'].append({"user": text, "ai": ""})
    if len(session['msgs']) > 7:
        session['msgs'].pop(0)

    if msg.photo:
        photo_file = msg.photo[-1]
        file_id = photo_file.file_id
        text += f" (User sent photo with ID: {file_id})"

    ai_output = await generate_ai_response(text, user_id, session['msgs'], session['count'])

    if ai_output["action"] == "send_media":
        logger.info(f"AI intent detected: send_media for user {user_id}")
        paid_photo = InputPaidMediaPhoto(media=DEFAULT_FILE_ID)
        try:
            await context.bot.send_paid_media(
                chat_id=chat_id,
                media=[paid_photo],
                star_count=STAR_COUNT,
                caption=f"Unlock exclusive just for you! 😘",
                business_connection_id=business_connection_id
            )
            logger.info(f"Paid media sent to user {user_id}")
        except Exception as e:
            logger.error(f"Error sending paid media: {e}")
            try:
                await context.bot.send_message(chat_id=chat_id, text=ai_output["response"], business_connection_id=business_connection_id)
            except Exception as e2:
                logger.error(f"Fallback AI message sending failed: {e2}")
    else:
        try:
            await context.bot.send_message(chat_id=chat_id, text=ai_output["response"], business_connection_id=business_connection_id)
            session['msgs'][-1]["ai"] = ai_output["response"]
            logger.info(f"AI response sent to user {user_id}")
        except Exception as e:
            logger.error(f"Error sending AI response: {e}")

    if ai_output["action"] == "send_media":
        session['msgs'] = []
        session['count'] = 0

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.business_message or update.message
    if not msg or msg.chat.type != 'private':
        return

    chat_id = msg.chat.id
    user_id = msg.from_user.id if msg.from_user else update.effective_user.id
    business_connection_id = update.business_message.business_connection_id if update.business_message else None

    if user_id not in user_sessions:
        user_sessions[user_id] = {'msgs': [], 'count': 0, 'last_time': asyncio.get_event_loop().time()}

    welcome_msg = ("Hey gorgeous! I'm Kira, your AI OnlyFans tease. Chat with me, "
                   "and I'll know when you're ready for my exclusive content! "
                   "Enable sensitive content in your privacy settings for full fun. 😘")
    try:
        await context.bot.send_message(chat_id=chat_id, text=welcome_msg, business_connection_id=business_connection_id)
    except Exception as e:
        logger.error(f"Error sending welcome message: {e}")

ptb_app.add_handler(CommandHandler("start", start_command))
ptb_app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_update))
try:
    from telegram.ext.filters import BusinessMessage
    ptb_app.add_handler(MessageHandler(BusinessMessage.ALL & ~filters.COMMAND, handle_update))
except ImportError:
    pass

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await ptb_app.bot.set_webhook(FULL_WEBHOOK_URL)
        logger.info(f"Webhook set at {FULL_WEBHOOK_URL}")
    except BadRequest as e:
        logger.error(f"Webhook setup failed: {e}")
    async with ptb_app:
        await ptb_app.initialize()
        await ptb_app.start()
        yield
    await ptb_app.stop()
    await ptb_app.shutdown()

app = FastAPI(lifespan=lifespan)

@app.post(WEBHOOK_PATH)
async def process_update(request: Request):
    logger.info("Incoming webhook update received")
    try:
        req = await request.json()
        update = Update.de_json(req, ptb_app.bot)
        if update:
            await ptb_app.process_update(update)
            logger.info(f"Update {update.update_id} processed")
        return Response(status_code=HTTPStatus.OK)
    except Exception as e:
        logger.error(f"Error processing update: {e}")
        return JSONResponse(status_code=HTTPStatus.INTERNAL_SERVER_ERROR, content={"error": str(e)})

@app.get("/health")
async def health():
    return {"status": "healthy"}
