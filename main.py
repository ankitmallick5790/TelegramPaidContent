import os
import logging
from contextlib import asynccontextmanager
from http import HTTPStatus
from telegram import Update, InputPaidMediaPhoto
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

async def handle_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Check for business_message or message
    msg = update.business_message or update.message
    if not msg:
        logger.warning("No message or business_message in update")
        return
    
    chat_type = msg.chat.type
    text = msg.text.lower() if msg.text else ""
    user_id = msg.from_user.id if msg.from_user else update.effective_user.id
    chat_id = update.effective_chat.id if hasattr(update, 'effective_chat') else msg.chat.id
    
    # Extract business_connection_id if business message
    business_connection_id = update.business_message.business_connection_id if update.business_message else None
    
    logger.info(f"Received {'business' if update.business_message else ''} message from {user_id} in {chat_type}: '{text}'")
    
    if chat_type == 'private':  # Restrict to private chats
        if 'send' in text:
            logger.info(f"Trigger matched for user {user_id} in private chat {chat_id}")
            
            # Create InputPaidMediaPhoto for the paid photo
            paid_photo = InputPaidMediaPhoto(
                media='AgACAgUAAxkBAAMTaQU-em6X2nceQKfORhFTTOQPfvEAAkQNaxvRCShU60Ue_Do0OekBAAMCAAN4AAM2BA',  # Your file_id as string
                caption='Here you go! Unlock to view.'
            )
            
            try:
                await context.bot.send_paid_media(
                    chat_id=chat_id,
                    media=[paid_photo],  # List of InputPaidMediaPhoto (even for one)
                    star_count=22,  # Number of Stars required to unlock (passed to method, not object)
                    business_connection_id=business_connection_id  # Required for Business mode proxying
                )
                logger.info(f"Successfully sent paid photo to {user_id} in {chat_id}")
            except Exception as e:
                logger.error(f"Error sending paid media to {user_id}: {e}")
                
                # Fallback: Test with non-paid send_photo (uncomment for debugging)
                # await context.bot.send_photo(chat_id=chat_id, photo=paid_photo.media, caption=paid_photo.caption)
                # logger.info(f"Fallback non-paid photo sent to {user_id}")
        else:
            logger.info(f"No trigger match for message '{text}' from {user_id}")
    else:
        logger.info(f"Ignoring non-private message from {user_id} in {chat_type}")

async def handle_photo_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Check for business_message.photo or message.photo
    msg = update.business_message or update.message
    if not msg or not msg.photo:
        return
    
    if msg.chat.type == 'private':
        user_id = msg.from_user.id if msg.from_user else update.effective_user.id
        chat_id = update.effective_chat.id if hasattr(update, 'effective_chat') else msg.chat.id
        photo_file = msg.photo[-1]  # Largest photo size
        file_id = photo_file.file_id
        
        # Extract business_connection_id for consistency (though not needed for reply)
        business_connection_id = update.business_message.business_connection_id if update.business_message else None
        
        logger.info(f"{'Business ' if update.business_message else ''}Photo received from {user_id} in private chat {chat_id}, file_id: {file_id}")
        
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"File ID: {file_id}\n\nUse this in your paid media code!",
                business_connection_id=business_connection_id  # Proxy reply through account if business
            )
            logger.info(f"File ID sent to {user_id}")
        except Exception as e:
            logger.error(f"Error sending file ID to {user_id}: {e}")

# Add handlers for both message and business_message
ptb_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_update))
ptb_app.add_handler(MessageHandler(filters.PHOTO, handle_photo_update))
# For business messages (if library supports; fallback via unified handler above)
try:
    from telegram.ext.filters import BusinessMessage
    ptb_app.add_handler(MessageHandler(BusinessMessage.TEXT & ~filters.COMMAND, handle_update))
    ptb_app.add_handler(MessageHandler(BusinessMessage.PHOTO, handle_photo_update))
    logger.info("Business message filters added")
except ImportError:
    logger.info("Business filters not available; using unified handler")

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
    logger.info("Incoming webhook update received")
    try:
        req = await request.json()
        logger.info(f"Update JSON: {req}")  # Log full update for debugging (remove in prod for privacy)
        update = Update.de_json(req, ptb_app.bot)
        if update:
            logger.info(f"Processing update ID: {update.update_id}")
            await ptb_app.process_update(update)
            logger.info(f"Update {update.update_id} processed successfully")
        else:
            logger.warning("No valid update in JSON")
        return Response(status_code=HTTPStatus.OK)
    except Exception as e:
        logger.error(f"Error processing update: {e}")
        return JSONResponse(status_code=HTTPStatus.INTERNAL_SERVER_ERROR, content={"error": str(e)})

# Health check endpoint for Render
@app.get("/health")
async def health():
    return {"status": "healthy"}
