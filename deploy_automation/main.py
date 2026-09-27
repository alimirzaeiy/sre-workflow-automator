import logging
import asyncio
from typing import Optional
from contextlib import asynccontextmanager
from fastapi import FastAPI
from deploy_automation.config import settings
from deploy_automation.database import init_db
from deploy_automation.bot.telegram_service import TelegramService
from deploy_automation.api.routes import router, set_telegram_service

from deploy_automation.bot.sre_manager_bot import SREManagerBot
from deploy_automation.integrations.clickup_dispatcher import SREClickUpDispatcher

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

sre_bot_instance: SREManagerBot = SREManagerBot()
dispatcher_instance: SREClickUpDispatcher = SREClickUpDispatcher()
monitor_task: Optional[asyncio.Task] = None

async def clickup_monitor_loop():
    """Background polling loop for SRE tasks dispatch and monitoring."""
    logger.info("ClickUp SRE Task Monitor loop started.")
    while True:
        try:
            await dispatcher_instance.check_and_dispatch_new_tasks(
                telegram_proposal_notifier=sre_bot_instance.notify_task_dispatched
            )
        except Exception as e:
            logger.error(f"Error in ClickUp task monitoring loop: {e}")
        await asyncio.sleep(60)

@asynccontextmanager
async def lifespan(app: FastAPI):
    global monitor_task
    logger.info("Initializing Deploy Automation Database...")
    await init_db()

    # Start Telegram Bot if token provided
    if settings.TELEGRAM_BOT_TOKEN:
        logger.info("Starting SRE Manager Telegram Bot...")
        tg_app = sre_bot_instance.build_application()
        set_telegram_service(sre_bot_instance)
        await tg_app.initialize()
        await tg_app.start()
        await tg_app.updater.start_polling()
        logger.info("SRE Manager Telegram Bot polling started successfully.")
        
        # Start background ClickUp monitoring
        monitor_task = asyncio.create_task(clickup_monitor_loop())
    else:
        logger.warning("TELEGRAM_BOT_TOKEN not provided; Telegram Bot disabled.")

    yield

    # Cancel monitor
    if monitor_task:
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass

    # Shutdown Telegram Bot
    if settings.TELEGRAM_BOT_TOKEN and sre_bot_instance.app:
        logger.info("Stopping Telegram Bot...")
        await sre_bot_instance.app.updater.stop()
        await sre_bot_instance.app.stop()
        await sre_bot_instance.app.shutdown()


app = FastAPI(
    title=settings.APP_NAME,
    description="Automated deployment checklist & verification for ClickUp, GitLab, and Telegram",
    version="1.0.0",
    lifespan=lifespan
)

import os
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
@app.get("/dashboard", include_in_schema=False)
async def serve_dashboard():
    index_file = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return {"message": "Deploy Automation Dashboard is active"}


app.include_router(router)



if __name__ == "__main__":
    import uvicorn
    uvicorn.run("deploy_automation.main:app", host=settings.HOST, port=settings.PORT, reload=settings.DEBUG)
