from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from note_db.models import AIJob, AIState, IngestItem, Lifecycle, MemoryObject, MemoryType, Visibility
from note_queue import enqueue_ai_job, get_redis_pool
from note_ingestion.unfurl import LinkPreview, UnfurlError, unfurl_url


class BookmarkRequest(BaseModel):
    url: str = Field(min_length=1, max_length=1024)
    title: str | None = Field(default=None, max_length=512)
    content: str | None = None
    source_type: str = Field(default="bookmark", pattern="^(bookmark|url|image|whatsapp|ig)$")


class IngestItemResponse(BaseModel):
    id: UUID
    user_id: UUID
    memory_object_id: UUID | None
    source_type: str
    source_url: str | None
    status: str
    title: str | None
    content_text: str | None
    media_type: str | None
    preview: LinkPreview | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


def _preview_from_metadata(metadata: dict | None, url: str) -> LinkPreview | None:
    if not metadata:
        return None
    preview_data = metadata.get("preview")
    if isinstance(preview_data, dict):
        return LinkPreview.model_validate({**preview_data, "url": preview_data.get("url", url)})
    return None


def _response_from_item(item: IngestItem) -> IngestItemResponse:
    data = IngestItemResponse.model_validate(item)
    if item.source_url:
        data.preview = _preview_from_metadata(item.source_metadata, item.source_url)
    return data


class IngestService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _link_metadata_from_preview(
        preview: LinkPreview | None, url: str
    ) -> dict | None:
        if not preview:
            return None
        return {
            "url": preview.url or url,
            "title": preview.title,
            "description": preview.description,
            "image_url": preview.image_url,
            "site_name": preview.site_name,
            "favicon_url": preview.favicon_url,
            "extracted_text": preview.extracted_text,
            "content_type": preview.content_type,
            "author": preview.author,
            "published_at": preview.published_at,
            "language": preview.language,
            "enriched_at": datetime.now(UTC).isoformat(),
        }

    async def unfurl(self, url: str, *, force: bool = False) -> LinkPreview:
        return await unfurl_url(url, session=self.session, force=force)

    async def create_bookmark(
        self,
        user_id: UUID,
        url: str,
        title: str | None = None,
        content: str | None = None,
        source_type: str = "bookmark",
        raw_payload: dict | None = None,
        preview: LinkPreview | None = None,
    ) -> IngestItemResponse:
        resolved_preview = preview
        if resolved_preview is None and source_type in ("bookmark", "url"):
            try:
                resolved_preview = await unfurl_url(url, session=self.session)
            except UnfurlError:
                resolved_preview = None

        resolved_title = title or (resolved_preview.title if resolved_preview else None)
        resolved_url = resolved_preview.url if resolved_preview else url
        parts = [p for p in (resolved_title, resolved_url, content) if p]
        content_text = "\n".join(parts)

        if source_type == "image":
            media_uri = url
            media_type = "image"
        else:
            media_uri = resolved_preview.image_url if resolved_preview else None
            media_type = "link_preview" if media_uri else None

        memory_type = MemoryType.bookmark if source_type in ("bookmark", "url") else MemoryType.text

        memory = MemoryObject(
            id=uuid4(),
            user_id=user_id,
            type=memory_type,
            origin="ingest",
            content_text=content_text,
            structured_title=resolved_title or resolved_url,
            structured_value=resolved_url,
            media_uri=media_uri,
            media_type=media_type,
            link_metadata=self._link_metadata_from_preview(resolved_preview, resolved_url),
            visibility=Visibility.private,
            lifecycle=Lifecycle.active,
            ai_state=AIState.captured,
        )
        self.session.add(memory)
        await self.session.flush()

        metadata: dict | None = None
        if resolved_preview:
            metadata = {
                "title": resolved_title,
                "preview": resolved_preview.model_dump(),
            }
        elif resolved_title:
            metadata = {"title": resolved_title}

        item = IngestItem(
            user_id=user_id,
            memory_object_id=memory.id,
            source_type=source_type,
            source_url=resolved_url,
            source_metadata=metadata,
            status="processed",
            title=resolved_title,
            content_text=content_text,
            media_type=media_type,
            raw_payload=raw_payload,
        )
        self.session.add(item)
        self.session.add(AIJob(memory_object_id=memory.id, state=AIState.captured))
        await self.session.flush()

        try:
            pool = await get_redis_pool()
            await enqueue_ai_job(pool, str(memory.id))
        except Exception:
            pass

        return _response_from_item(item)

    async def list_ingest_items(self, user_id: UUID, limit: int = 50) -> list[IngestItemResponse]:
        stmt = (
            select(IngestItem)
            .where(IngestItem.user_id == user_id)
            .order_by(IngestItem.created_at.desc())
            .limit(limit)
        )
        items = (await self.session.scalars(stmt)).all()
        return [_response_from_item(i) for i in items]
