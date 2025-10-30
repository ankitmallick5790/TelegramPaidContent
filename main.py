import asyncio
import logging
import os

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import InputPaidMediaPhoto
from aiogram.webhook.aiohttp_server import SimpleRequestHandler
from aiohttp import web

# -------------------------- Setup --------------------------
logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN env var required")

IMAGE_FILE_ID = os.getenv("IMAGE_FILE_ID")  # Must set this – your photo file_id
if not IMAGE_FILE_ID:
    raise ValueError("IMAGE_FILE_ID env var required")

WEBHOOK_PATH = "/webhook"
WEBAPP_HOST = "0.0.0.0"
WEBAPP_PORT = int(os.getenv("PORT", "10000"))

# -------------------------- Bot & Dispatcher --------------------------
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()

# -------------------------- Core Handler: Any DM → Send Paid Media --------------------------
@dp.message(F.chat.type == "private")
async def auto_send_paid_media(message):
    logging.info(f"DM received from {message.from_user.id}: '{message.text[:50]}...'")

    paid_media = InputPaidMediaPhoto(media=IMAGE_FILE_ID)

    try:
        await bot.send_paid_media(
            chat_id=message.chat.id,
            media=paid_media,
            star_count=10,  # Cost to unlock
            payload="dm_exclusive",  # Trackable ID for this media
            caption="🔒 <b>Exclusive DM Unlock</b>\n\nPay 10 stars to view!",
        )
        logging.info(f"Paid media sent to {message.chat.id}")
    except Exception as e:
        logging.error(f"Send failed: {e}")
        await message.reply("Oops! Try again later. 😅")

# -------------------------- Webhook Lifecycle --------------------------
async def on_startup(app):
    hostname = os.getenv("RENDER_EXTERNAL_HOSTNAME")
    if hostname:
        webhook_url = f"https://{hostname}{WEBHOOK_PATH}"
        await bot.set_webhook(url=webhook_url)
        logging.info(f"Webhook set: {webhook_url}")
    else:
        logging.warning("No RENDER_EXTERNAL_HOSTNAME – webhook not set (local dev?)")

async def on_shutdown(app):
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.session.close()
    logging.info("Bot shutdown complete")

# -------------------------- Main --------------------------
async def main():
    app = web.Application()
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, WEBAPP_HOST, WEBAPP_PORT)
    await site.start()
    logging.info("🚀 Bot live – waiting for DMs...")

    try:
        await asyncio.Event().wait()  # Keep running forever
    finally:
        await runner.cleanup()

if __name__ == "__main__":
    asyncio.run(main())
