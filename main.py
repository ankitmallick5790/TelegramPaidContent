import os
from contextlib import asynccontextmanager
from http import HTTPStatus
from telegram import Update, InputMediaPhoto
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from fastapi import FastAPI, Request, Response

# Bot token from environment variable
TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://telegrampaidcontent.onrender.com")  # Replace with your Render URL

# Initialize the Application with token
ptb_app = Application.builder().token(TOKEN).build()

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message and update.message.chat.type == 'private':  # Restrict to private chats
        text = update.message.text.lower()
        if 'send' in text:
            # Send a paid photo requiring 22 Stars
            media = InputMediaPhoto(
                media='https://graph.org/file/c276c13a86c0fbfba5c51-dad1143620a2b7fe9f.jpg',  # Or use file_id from uploaded media
                caption='Here you go! Unlock to view.'
            )
            await context.bot.send_paid_media(
                chat_id=update.effective_chat.id,
                media=media,
                stars=299,  # Number of Stars required to unlock
                payload='paid_photo_1'  # Optional unique identifier for the media
            )
            # Add logging if needed
            print(f"Sent paid photo to {update.effective_user.id}")

# Add handler
ptb_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Set webhook
    await ptb_app.bot.set_webhook(WEBHOOK_URL + TOKEN)  # Secure path with token
    async with ptb_app:
        await ptb_app.start()
        yield
    # Shutdown
    await ptb_app.stop()

# FastAPI app
app = FastAPI(lifespan=lifespan)

@app.post("/")
async def process_update(request: Request):
    # Verify path includes token for security (optional, based on webhook setup)
    if TOKEN and TOKEN not in str(request.url):
        return Response(status_code=HTTPStatus.UNAUTHORIZED)
    req = await request.json()
    update = Update.de_json(req, ptb_app.bot)
    await ptb_app.process_update(update)
    return Response(status_code=HTTPStatus.OK)
