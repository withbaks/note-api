"""LLM provider abstraction with circuit breaker, timeouts, and usage tracking."""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from note_core.circuit import circuit_is_open, record_llm_failure, record_llm_success
from note_core.config import get_settings

logger = logging.getLogger(__name__)

LLM_TIMEOUT_SECONDS = 45.0
EMBED_TIMEOUT_SECONDS = 30.0


async def record_usage(
    session,
    *,
    user_id: UUID | None,
    operation: str,
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    memory_id: UUID | None = None,
) -> None:
    if session is None or user_id is None:
        return
    try:
        from note_db.models import AIUsageEvent

        cost = (input_tokens * 0.000_000_15) + (output_tokens * 0.000_000_60)
        session.add(
            AIUsageEvent(
                user_id=user_id,
                operation=operation,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost,
                memory_object_id=memory_id,
            )
        )
    except Exception:
        logger.debug("Failed to record AI usage", exc_info=True)


async def user_over_monthly_cap(session, user_id: UUID) -> bool:
    try:
        from datetime import UTC, datetime

        from sqlalchemy import func, select

        from note_db.models import AIUsageEvent, User

        user = await session.get(User, user_id)
        if not user or not user.ai_monthly_cap_usd:
            return False
        month_start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        total = await session.scalar(
            select(func.coalesce(func.sum(AIUsageEvent.cost_usd), 0.0)).where(
                AIUsageEvent.user_id == user_id,
                AIUsageEvent.created_at >= month_start,
            )
        )
        return float(total or 0) >= float(user.ai_monthly_cap_usd)
    except Exception:
        return False


async def complete_json(
    prompt: str,
    *,
    session=None,
    user_id: UUID | None = None,
    memory_id: UUID | None = None,
    operation: str = "llm_complete",
) -> str:
    settings = get_settings()
    if not settings.openai_api_key or circuit_is_open():
        raise RuntimeError("LLM unavailable")
    if session and user_id and await user_over_monthly_cap(session, user_id):
        raise RuntimeError("Monthly AI cap exceeded")

    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=settings.openai_api_key, timeout=LLM_TIMEOUT_SECONDS)
    try:
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model=settings.ai_model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
            ),
            timeout=LLM_TIMEOUT_SECONDS,
        )
        record_llm_success()
        usage = getattr(response, "usage", None)
        await record_usage(
            session,
            user_id=user_id,
            operation=operation,
            model=settings.ai_model,
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
            memory_id=memory_id,
        )
        return response.choices[0].message.content or "{}"
    except Exception:
        record_llm_failure()
        raise


async def embed_texts(
    texts: list[str],
    *,
    session=None,
    user_id: UUID | None = None,
    memory_id: UUID | None = None,
    operation: str = "embed",
) -> list[list[float] | None]:
    if not texts:
        return []
    settings = get_settings()
    if not settings.openai_api_key or circuit_is_open():
        return [None for _ in texts]
    if session and user_id and await user_over_monthly_cap(session, user_id):
        return [None for _ in texts]

    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=settings.openai_api_key, timeout=EMBED_TIMEOUT_SECONDS)
    try:
        response = await asyncio.wait_for(
            client.embeddings.create(model="text-embedding-3-small", input=texts),
            timeout=EMBED_TIMEOUT_SECONDS,
        )
        record_llm_success()
        usage = getattr(response, "usage", None)
        await record_usage(
            session,
            user_id=user_id,
            operation=operation,
            model="text-embedding-3-small",
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=0,
            memory_id=memory_id,
        )
        return [item.embedding for item in response.data]
    except Exception:
        record_llm_failure()
        return [None for _ in texts]
