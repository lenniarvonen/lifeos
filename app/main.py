from contextlib import asynccontextmanager

from fastapi import FastAPI

import scheduler
from routers import sync
from services import telegram_bot_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.start()
    telegram_bot_service.start()
    yield
    telegram_bot_service.stop()
    scheduler.shutdown()


app = FastAPI(title="lifeOS", lifespan=lifespan)
app.include_router(sync.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
