import asyncio
import logging
import os
from typing import Any

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import (
    InputPaidMediaPhoto,
    BusinessConnection,
    PaidMediaPurchased,
)
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.webhook.aiohttp_server import SimpleRequestHandler
from aiohttp import web

# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #
logging.basicConfig(level=logging.INFO)
logging.getLogger("aiogram").setLevel(logging.DEBUG)

# --------------------------------------------------------------------------- #
# Environment Variables
# --------------------------------------------------------------------------- #
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN must be set in the environment")

BUSINESS_CONNECTION_ID = os.getenv("BUSINESS_CONNECTION_ID", "")
IMAGE_FILE_ID = os.getenv(
    "IMAGE_FILE_ID", "AgACAgIAAxkBAAIB..."  # ← replace with real file_id
)
WEBHOOK_PATH = "/webhook"
WEBAPP_HOST = "0.0.0.0"
WEBAPP_PORT = int(os.getenv("PORT", "10000"))

# --------------------------------------------------------------------------- #
# Router & Message Handlers
# --------------------------------------------------------------------------- #
router = Router()


@router.message(Command("start"))
async def cmd_start(message, bot: Bot):
    await message.reply("Hi! Send **send stuff** to get exclusive paid content.")


@router.message(F.text.lower() == "send stuff")
async def send_paid_content(message, bot: Bot):
    logging.info(f"Sending paid media to user {message.from_user.id}")

    paid_media = InputPaidMediaPhoto(media=IMAGE_FILE_ID)

    try:
        await bot.send_paid_media(
            chat_id=message.chat.id,
            media=paid_media,
            star_count=10,
            payload="fan_unlock_001",
            caption="Unlock this fan exclusive!",
            business_connection_id=BUSINESS_CONNECTION_ID or None,
        )
        logging.info("Paid media sent")
    except Exception as exc:
        logging.error(f"Send failed: {exc}")
        await message.reply(f"Error: {exc}")


@router.message()
async def catch_all(message):
    await message.reply("Try **/start** or **send stuff**!")


# --------------------------------------------------------------------------- #
# Special Event Handlers
# --------------------------------------------------------------------------- #
def register_special_handlers(dp: Dispatcher, bot: Bot):
    @dp.business_connection()
    async def handle_business_connection(conn: BusinessConnection):
        logging.info(
            f"Business connection: id={conn.id} user={conn.user.id} enabled={conn.is_enabled}"
        )

    @dp.paid_media_purchased()
    async def handle_purchase(purchase: PaidMediaPurchased):
        logging.info(
            f"Purchase: user={purchase.user_id} stars={purchase.star_count} payload={purchase.payload}"
        )
        await bot.send_message(
            chat_id=purchase.user_id,
            text="Thanks for unlocking! More soon?",
            business_connection_id=BUSINESS_CONNECTION_ID or None,
        )


# --------------------------------------------------------------------------- #
# Webhook Lifecycle
# --------------------------------------------------------------------------- #
async def on_startup(app: web.Application):
    bot: Bot = app["bot"]
    hostname = os.getenv("RENDER_EXTERNAL_HOSTNAME")
    if not hostname:
        logging.error("RENDER_EXTERNAL_HOSTNAME not set. Webhook cannot be configured.")
        return
    webhook_url = f"https://{hostname}{WEBHOOK_PATH}"
    await bot.set_webhook(url=webhook_url)
    logging.info(f"Webhook set: {webhook_url}")


async def on_shutdown(app: web.Application):
    bot: Bot = app["bot"]
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.session.close()
    logging.info("Webhook removed and session closed")


# --------------------------------------------------------------------------- #
# Main Application
# --------------------------------------------------------------------------- #
async def main():
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    dp = Dispatcher()
    dp.include_router(router)
    register_special_handlers(dp, bot)

    app = web.Application()
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)

    app["bot"] = bot
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, WEBAPP_HOST, WEBAPP_PORT)
    await site.start()
    logging.info(f"Bot running on {WEBAPP_HOST}:{WEBAPP_PORT}")

    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
