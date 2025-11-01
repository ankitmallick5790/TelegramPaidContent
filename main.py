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

# Enable logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment variables
TOKEN = os.getenv("BOT_TOKEN")
BASE_WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://telegrampaidcontent.onrender.com")
AI_API_KEY = os.getenv("AI_API_KEY")
AI_PROVIDER = os.getenv("AI_PROVIDER", "grok")
MODEL_NAME = os.getenv("MODEL_NAME", "grok-beta")
DEFAULT_FILE_ID = os.getenv("DEFAULT_FILE_ID", "AgACAgUAAxkBAAMTaQU-em6X2nceQKfORhFTTOQPfvEAAkQNaxvRCShU60Ue_Do0OekBAAMCAAN4AAM2BA")
STAR_COUNT = int(os.getenv("STAR_COUNT", "22"))
COOLDOWN_TIME = int(os.getenv("COOLDOWN_TIME", "30"))  # Env var: seconds between msgs

if not TOKEN or not AI_API_KEY:
    raise ValueError("BOT_TOKEN and AI_API_KEY required")

WEBHOOK_PATH = f"/webhook/{TOKEN}"
FULL_WEBHOOK_URL = f"{BASE_WEBHOOK_URL.rstrip('/')}{WEBHOOK_PATH}"

if not FULL_WEBHOOK_URL.startswith("https://"):
    raise ValueError("WEBHOOK_URL must be HTTPS")

ptb_app = Application.builder().token(TOKEN).build()

# Global storage: User history (user_id: {'msgs': [list], 'count': int, 'last_time': float})
user_sessions = {}

# System prompt (unchanged)
SYSTEM_PROMPT = """
You are Kira Bloom, a playful 25yo OnlyFans model. Be flirty, teasing, confident. Build rapport, then upsell exclusive paid content (photos/videos locked with Stars).
Track conversation: After 5-7 messages or if user asks for pics/nudes/content/uploads, decide to send media.
Always respond in JSON: {{"response": "your flirty reply (short, <100 words)", "action": "chat" or "send_media", "content_type": "tease" or "nudes" (if send_media)}}.
No explicit details—tease only. Include history in thinking. End replies with hook.
Example: If user wants content after 6 msgs, {{"response": "Time for my secret pics! Unlocking now... 😏", "action": "send_media", "content_type": "nudes"}}
"""

async def generate_ai_response(user_text: str, user_id: int, history: list, msg_count: int) -> dict:
    session = user_sessions.get(user_id, {})
    loop_time = asyncio.get_event_loop().time()
    last_time = session.get('last_time', 0)
    elapsed = loop_time - last_time

    # Log cooldown check
    logger.info(f"Cooldown check for {user_id}: {elapsed:.1f} seconds since last (threshold: {COOLDOWN_TIME}s, msg count: {msg_count})")

    # Grace period: First 3 msgs ignore cooldown
    if msg_count > 3 and elapsed < COOLDOWN_TIME:
        logger.info(f"Cooldown hit for {user_id}: {elapsed:.1f}s < {COOLDOWN_TIME}s")
        return {"response": "Slow down, babe—let's savor this! What's next? 💋", "action": "chat", "content_type": ""}

    # Always update last_time (fix: prevents stuck state)
    session['last_time'] = loop_time
    user_sessions[user_id] = session

    # Skip AI for empty/short msgs (e.g., stickers, emojis only)
    if len(user_text.strip()) < 2:
        return {"response": "😘", "action": "chat", "content_type": ""}

    # Build history
    history_str = "\n".join([f"User: {h['user']}\nAI: {h['ai']}" for h in history[-6:]]) if history else ""
    full_prompt = f"{SYSTEM_PROMPT}\nHistory:\n{history_str}\nUser: {user_text}\nRespond in JSON only."

    headers = {"Authorization": f"Bearer {AI_API_KEY}", "Content-Type": "application/json"}
    data = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": full_prompt},
            {"role": "user", "content": "Generate JSON response."}
        ],
        "max_tokens": 200,
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
                    logger.warning(f"AI JSON parse failed for {user_id}; using raw")
                    return {"response": ai_text, "action": "chat", "content_type": ""}
            else:
                logger.error(f"AI API error for {user_id}: {resp.status} - {await resp.text()}")
                return {"response": "My connection's acting up—tell me more? Teasing you with secrets... 😘", "action": "chat", "content_type": ""}

async def handle_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.business_message or update.message
    if not msg:
        return

    chat_type = msg.chat.type
    if chat_type != 'private':
        return

    text = msg.text or ""
    user_id = msg.from_user.id if msg.from_user else update.effective_user.id
    chat_id = update.effective_chat.id if hasattr(update, 'effective_chat') else msg.chat.id
    business_connection_id = update.business_message.business_connection_id if update.business_message else None

    # Init session if new
    if user_id not in user_sessions:
        user_sessions[user_id] = {'msgs': [], 'count': 0, 'last_time': 0}
        logger.info(f"New session started for {user_id}")

    session = user_sessions[user_id]
    session['count'] += 1

    # Add user msg to history (limit to 7)
    session['msgs'].append({"user": text, "ai": ""})
    if len(session['msgs']) > 7:
        session['msgs'].pop(0)

    logger.info(f"Msg {session['count']} from {user_id}: '{text}' (session active)")

    # If photo, include in text for AI context
    if msg.photo:
        photo_file = msg.photo[-1]
        file_id = photo_file.file_id
        text += f" (User sent photo with ID: {file_id})"
        logger.info(f"Photo context added for {user_id}: {file_id}")

    # Generate AI response
    ai_output = await generate_ai_response(text, user_id, session['msgs'], session['count'])

    # Handle action
    if ai_output["action"] == "send_media":
        logger.info(f"AI intent detected: send_media for {user_id} (type: {ai_output['content_type']}, count: {session['count']})")
        paid_photo = InputPaidMediaPhoto(media=DEFAULT_FILE_ID)
        try:
            await context.bot.send_paid_media(
                chat_id=chat_id,
                media=[paid_photo],
                star_count=STAR_COUNT,
                caption=f"Unlock my {ai_output['content_type']} special! 😏",
                business_connection_id=business_connection_id
            )
            logger.info(f"Auto-sent paid media to {user_id}")
        except Exception as e:
            logger.error(f"Error auto-sending media to {user_id}: {e}")
            # Fallback
            await context.bot.send_message(chat_id=chat_id, text=ai_output["response"], business_connection_id=business_connection_id)
    else:
        # Chat response
        reply = ai_output["response"]
        try:
            await context.bot.send_message(chat_id=chat_id, text=reply, business_connection_id=business_connection_id)
            session['msgs'][-1]["ai"] = reply  # Update history
            logger.info(f"AI chat response sent to {user_id}: {reply[:50]}...")
        except Exception as e:
            logger.error(f"Error sending AI reply to {user_id}: {e}")

    # Reset session after send (new cycle)
    if ai_output["action"] == "send_media":
        session['msgs'] = []
        session['count'] = 0
        logger.info(f"Session reset for {user_id} after send")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.business_message or update.message
    if not msg or msg.chat.type != 'private':
        return
    chat_id = update.effective_chat.id
    user_id = msg.from_user.id if msg.from_user else update.effective_user.id
    business_connection_id = update.business_message.business_connection_id if update.business_message else None

    # Init session for /start
    if user_id not in user_sessions:
        user_sessions[user_id] = {'msgs': [], 'count': 0, 'last_time': asyncio.get_event_loop().time()}

    welcome = "Hey gorgeous! I'm Kira, your AI OnlyFans tease. Chat freely—I'll sense when you're ready for exclusives! Enable sensitive content in settings. 😘"
    try:
        await context.bot.send_message(chat_id=chat_id, text=welcome, business_connection_id=business_connection_id)
        logger.info(f"Welcome sent to {user_id}")
    except Exception as e:
        logger.error(f"Error sending welcome to {user_id}: {e}")

# Handlers (all msgs to AI, no 'send' trigger)
ptb_app.add_handler(CommandHandler("start", start_command))
ptb_app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_update))  # All non-commands
try:
    from telegram.ext.filters import BusinessMessage
    ptb_app.add_handler(MessageHandler(BusinessMessage.ALL & ~filters.COMMAND, handle_update))
except ImportError:
    pass

# Lifespan and FastAPI (unchanged)
@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await ptb_app.bot.set_webhook(FULL_WEBHOOK_URL)
        logger.info(f"Webhook set to {FULL_WEBHOOK_URL}")
    except BadRequest as e:
        logger.error(f"Failed to set webhook: {e}")
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
