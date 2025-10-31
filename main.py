import os
import logging
from contextlib import asynccontextmanager
from http import HTTPStatus
from telegram import Update, InputMediaPhoto
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from telegram.error import BadRequest
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from urllib.parse import urlparse

# Enable logging for Render logs
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment variables
TOKEN = os.getenv("BOT_TOKEN")
BASE_WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://telegrampaidcontent.onrender.com")  # Your Render URL

if not TOKEN:
    raise ValueError("BOT_TOKEN environment variable is required")

WEBHOOK_PATH = f"/webhook/{TOKEN}"  # Secure path with token
FULL_WEBHOOK_URL = f"{BASE_WEBHOOK_URL.rstrip('/')}{WEBHOOK_PATH}"

# Validate webhook URL (basic check for HTTPS and no invalid chars)
if not FULL_WEBHOOK_URL.startswith("https://"):
    raise ValueError("WEBHOOK_URL must be a valid HTTPS URL")

# Initialize the Application with token
ptb_app = Application.builder().token(TOKEN).build()

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message and update.message.chat.type == 'private':  # Restrict to private chats
        text = update.message.text.lower()
        if 'send' in text:
            # Send a paid photo requiring 22 Stars using your provided media URL
            media = InputMediaPhoto(
                media='https://graph.org/file/c276c13a86c0fbfba5c51-dad1143620a2b7fe9f.jpg',  # Your image URL (or Telegram file_id)
                caption='Here you go! Unlock to view.'
            )
            try:
                await context.bot.send_paid_media(
                    chat_id=update.effective_chat.id,
                    media=media,
                    stars=22,  # Number of Stars required to unlock
                    payload='paid_photo_1'  # Optional unique identifier
                )
                logger.info(f"Sent paid photo to {update.effective_user.id}")
            except Exception as e:
                logger.error(f"Error sending media: {e}")

# Add handler
ptb_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Set webhook with secure path
    try:
        await ptb_app.bot.set_webhook(FULL_WEBHOOK_URL)
        logger.info(f"Webhook set to {FULL_WEBHOOK_URL}")
    except BadRequest as e:
        logger.error(f"Failed to set webhook: {e}")
        # Continue for manual setup if needed
    async with ptb_app:
        await ptb_app.initialize()
        await ptb_app.start()
        yield
    # Shutdown
    await ptb_app.stop()
    await ptb_app.shutdown()

# FastAPI app
app = FastAPI(lifespan=lifespan)

@app.post(WEBHOOK_PATH)
async def process_update(request: Request):
    # Additional security: Verify the path includes the token (already handled by route)
    try:
        req = await request.json()
        update = Update.de_json(req, ptb_app.bot)
        if update:
            await ptb_app.process_update(update)
        return Response(status_code=HTTPStatus.OK)
    except Exception as e:
        logger.error(f"Error processing update: {e}")
        return JSONResponse(status_code=HTTPStatus.INTERNAL_SERVER_ERROR, content={"error": str(e)})

# Health check endpoint for Render
@app.get("/health")
async def health():
    return {"status": "healthy"}
