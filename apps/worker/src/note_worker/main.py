from datetime import UTC, datetime
from uuid import UUID

from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, NotSupportedError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from note_ai.service import AIService
from note_core.config import get_settings
from note_core.logging import setup_logging
from note_db.models import AIJob, AIState, MemoryObject
from note_queue import AI_PROCESS_JOB, BATCH_QUEUE, INTERACTIVE_QUEUE, publish_sync_event

logger = setup_logging("note-worker")


def _is_stale_prepared_statement(exc: BaseException) -> bool:
    message = str(getattr(exc, "__cause__", None) or exc)
    return "InvalidCachedStatementError" in message or "cached statement plan is invalid" in message


def _create_db_engine():
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_recycle=1800,
    )


async def _reset_db_engine(ctx) -> None:
    engine = ctx.get("engine")
    if engine is not None:
        await engine.dispose()
    engine = _create_db_engine()
    ctx["engine"] = engine
    ctx["session_factory"] = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )


async def startup(ctx):
    engine = _create_db_engine()
    ctx["engine"] = engine
    ctx["session_factory"] = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    logger.info("Worker started")


async def shutdown(ctx):
    engine = ctx.get("engine")
    if engine is not None:
        await engine.dispose()
    logger.info("Worker shutting down")


async def process_memory_ai(ctx, memory_object_id: str):
    logger.info("Processing AI for memory %s", memory_object_id)
    user_id: str | None = None

    for attempt in range(2):
        try:
            async with ctx["session_factory"]() as session:
                await AIService(session).process_memory(memory_object_id)
                obj = await session.get(MemoryObject, UUID(memory_object_id))
                if obj:
                    user_id = str(obj.user_id)
                await session.commit()
            logger.info("AI processing complete for %s", memory_object_id)
            break
        except (NotSupportedError, DBAPIError) as exc:
            if attempt == 0 and _is_stale_prepared_statement(exc):
                logger.warning(
                    "Stale DB prepared statement for %s; reconnecting and retrying",
                    memory_object_id,
                )
                await _reset_db_engine(ctx)
                continue
            logger.error("AI processing failed for %s: %s", memory_object_id, exc)
            raise
        except Exception as exc:
            logger.error("AI processing failed for %s: %s", memory_object_id, exc)
            raise

    if user_id:
        await notify_sync_update(ctx, user_id, "memory", memory_object_id)


async def notify_sync_update(ctx, user_id: str, entity_type: str, entity_id: str):
    logger.info("Sync notify: user=%s entity=%s/%s", user_id, entity_type, entity_id)
    redis = ctx.get("redis")
    if redis is None:
        from note_queue import get_redis_pool

        redis = await get_redis_pool()
    await publish_sync_event(
        redis,
        user_id,
        {
            "type": "sync_update",
            "entity_type": entity_type,
            "entity_id": entity_id,
        },
    )


async def retry_failed_ai_jobs(ctx):
    now = datetime.now(UTC)
    async with ctx["session_factory"]() as session:
        jobs = (
            await session.scalars(
                select(AIJob).where(
                    AIJob.state == AIState.failed,
                    AIJob.next_retry_at.is_not(None),
                    AIJob.next_retry_at <= now,
                    AIJob.attempts < AIJob.max_attempts,
                )
            )
        ).all()
        memory_ids = [str(job.memory_object_id) for job in jobs]

    if not memory_ids:
        return

    redis = ctx.get("redis")
    if redis is None:
        from note_queue import get_redis_pool

        redis = await get_redis_pool()

    for memory_id in memory_ids:
        logger.info("Re-enqueueing failed AI job for memory %s", memory_id)
        await redis.enqueue_job(AI_PROCESS_JOB, memory_id, _queue_name=INTERACTIVE_QUEUE)


async def backfill_search_index(ctx):
    logger.info("Running search index backfill batch")
    async with ctx["session_factory"]() as session:
        from note_search.search_index import SearchIndexService

        indexed = await SearchIndexService(session).backfill_batch(limit=25)
        await session.commit()
    logger.info("Search index backfill indexed %s documents", indexed)


async def enqueue_fact_staleness_reviews(ctx):
    logger.info("Running fact staleness review batch")
    async with ctx["session_factory"]() as session:
        from note_ai.staleness import enqueue_staleness_reviews

        created = await enqueue_staleness_reviews(session, limit=50)
        await session.commit()
    logger.info("Staleness reviews created: %s", created)


class InteractiveWorkerSettings:
    functions = [process_memory_ai, notify_sync_update]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    queue_name = INTERACTIVE_QUEUE
    max_jobs = 10
    job_timeout = 120


class BatchWorkerSettings:
    functions = [backfill_search_index, retry_failed_ai_jobs, enqueue_fact_staleness_reviews]
    cron_jobs = [
        cron(retry_failed_ai_jobs, minute={0, 15, 30, 45}),
        cron(backfill_search_index, minute={2, 12, 22, 32, 42, 52}),
        cron(enqueue_fact_staleness_reviews, hour={9}, minute={5}),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    queue_name = BATCH_QUEUE
    max_jobs = 5
    job_timeout = 300


# Default worker runs interactive queue (batch worker: python -m note_worker.batch)
WorkerSettings = InteractiveWorkerSettings


def main():
    from arq import run_worker

    run_worker(InteractiveWorkerSettings)


def main_batch():
    from arq import run_worker

    run_worker(BatchWorkerSettings)


if __name__ == "__main__":
    main()
