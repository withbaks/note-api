import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from note_api.routers import (
    admin,
    auth,
    features,
    ingest,
    memories,
    notifications,
    public,
    realtime,
    social,
    sync,
    users,
)
from note_api.routers.realtime import broadcast_to_user
from note_core.config import get_settings
from note_core.logging import setup_logging
from note_queue import subscribe_sync_events

logger = setup_logging("note-api")


async def _handle_sync_event(payload: dict) -> None:
    user_id = payload.get("user_id")
    if not user_id:
        return
    await broadcast_to_user(str(user_id), payload)


async def _run_sync_subscriber() -> None:
    try:
        await subscribe_sync_events(_handle_sync_event)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Sync Redis subscriber stopped unexpectedly")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting note-api")
    subscriber_task = asyncio.create_task(_run_sync_subscriber())
    # Don't let a broken subscriber crash request handling
    subscriber_task.add_done_callback(
        lambda t: None
        if t.cancelled() or t.exception() is None
        else logger.error("Sync subscriber died: %s", t.exception())
    )
    yield
    subscriber_task.cancel()
    try:
        await subscriber_task
    except asyncio.CancelledError:
        pass
    logger.info("Shutting down note-api")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Note API",
        description="Memory app backend",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth.router, prefix="/v1")
    app.include_router(users.router, prefix="/v1")
    app.include_router(admin.router, prefix="/v1")
    app.include_router(memories.router, prefix="/v1")
    app.include_router(sync.router, prefix="/v1")
    app.include_router(features.router, prefix="/v1")
    app.include_router(social.router, prefix="/v1")
    app.include_router(public.router, prefix="/v1")
    app.include_router(ingest.router, prefix="/v1")
    app.include_router(notifications.router, prefix="/v1")
    app.include_router(realtime.router, prefix="/v1")

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "note-api"}

    return app


app = create_app()


def run():
    import uvicorn

    uvicorn.run("note_api.main:app", host="0.0.0.0", port=8000, reload=True)
