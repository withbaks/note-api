from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from arq import create_pool
from arq.connections import RedisSettings
from redis.asyncio import Redis

from note_core.config import get_settings

AI_PROCESS_JOB = "process_memory_ai"
SYNC_NOTIFY_JOB = "notify_sync_update"
CHANNEL_SYNC = "note:sync"
INTERACTIVE_QUEUE = "note:interactive"
BATCH_QUEUE = "note:batch"


def get_redis_settings() -> RedisSettings:
    settings = get_settings()
    return RedisSettings.from_dsn(settings.redis_url)


async def get_redis_pool():
    return await create_pool(get_redis_settings())


async def get_redis_client() -> Redis:
    return Redis.from_url(get_settings().redis_url, decode_responses=True)


async def enqueue_ai_job(pool, memory_object_id: str) -> None:
    await pool.enqueue_job(AI_PROCESS_JOB, memory_object_id, _queue_name=INTERACTIVE_QUEUE)


async def enqueue_batch_job(pool, job_name: str, *args) -> None:
    await pool.enqueue_job(job_name, *args, _queue_name=BATCH_QUEUE)


async def enqueue_sync_notify(pool, user_id: str, entity_type: str, entity_id: str) -> None:
    await pool.enqueue_job(SYNC_NOTIFY_JOB, user_id, entity_type, entity_id)


async def publish_sync_event(pool, user_id: str, payload: dict[str, Any]) -> None:
    message = json.dumps({"user_id": str(user_id), **payload})
    await pool.publish(CHANNEL_SYNC, message)


async def subscribe_sync_events(
    handler: Callable[[dict[str, Any]], Awaitable[None]],
) -> None:
    """Subscribe to CHANNEL_SYNC and invoke handler for each message.

    Reconnects on failure so a Redis blip cannot kill the API process.
    """
    while True:
        client: Redis | None = None
        pubsub = None
        try:
            client = await get_redis_client()
            pubsub = client.pubsub()
            await pubsub.subscribe(CHANNEL_SYNC)
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                data = message.get("data")
                if not data:
                    continue
                if isinstance(data, bytes):
                    data = data.decode()
                try:
                    payload = json.loads(data)
                except json.JSONDecodeError:
                    continue
                await handler(payload)
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(2)
        finally:
            if pubsub is not None:
                try:
                    await pubsub.unsubscribe(CHANNEL_SYNC)
                    await pubsub.aclose()
                except Exception:
                    pass
            if client is not None:
                try:
                    await client.aclose()
                except Exception:
                    pass
