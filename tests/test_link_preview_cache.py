from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from note_db.models import LinkPreviewCache
from note_ingestion.unfurl import LinkPreview, _get_cached_preview, _set_cached_preview, _url_hash


@pytest.mark.asyncio
async def test_link_preview_cache_round_trip():
    preview = LinkPreview(
        url="https://example.com/post",
        title="Cached",
        description="From cache",
        content_type="article",
    )
    stored: dict[str, LinkPreviewCache] = {}

    session = AsyncMock()

    async def scalar(stmt):
        url_hash = _url_hash(preview.url)
        row = stored.get(url_hash)
        return row

    async def get(model, key):
        return stored.get(key)

    session.scalar.side_effect = scalar
    session.get.side_effect = get
    session.add = MagicMock(side_effect=lambda row: stored.update({row.url_hash: row}))
    session.flush = AsyncMock()

    await _set_cached_preview(session, preview)
    cached = await _get_cached_preview(session, preview.url)
    assert cached is not None
    assert cached.title == "Cached"
    assert cached.description == "From cache"


@pytest.mark.asyncio
async def test_link_preview_cache_expires():
    preview = LinkPreview(url="https://example.com/old", title="Old")
    url_hash = _url_hash(preview.url)
    stored = {
        url_hash: LinkPreviewCache(
            url_hash=url_hash,
            url=preview.url,
            preview=preview.model_dump(),
            fetched_at=datetime.now(UTC) - timedelta(hours=72),
        )
    }

    session = AsyncMock()
    session.scalar.side_effect = lambda stmt: stored.get(url_hash)

    cached = await _get_cached_preview(session, preview.url)
    assert cached is None
