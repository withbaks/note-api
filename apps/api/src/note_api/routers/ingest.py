from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from note_api.deps import get_current_user_id, get_db
from note_core.config import get_settings
from note_ingestion.rate_limit import RateLimitExceeded, check_unfurl_rate_limit
from note_ingestion.service import BookmarkRequest, IngestItemResponse, IngestService
from note_ingestion.unfurl import LinkPreview, UnfurlError

router = APIRouter(tags=["ingest"])


class UnfurlRequest(BaseModel):
    url: str = Field(min_length=1, max_length=1024)


@router.post("/ingest/unfurl", response_model=LinkPreview)
async def unfurl_link(
    req: UnfurlRequest,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
    force: bool = Query(default=False),
):
    try:
        check_unfurl_rate_limit(user_id)
    except RateLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    try:
        return await IngestService(db).unfurl(req.url, force=force)
    except UnfurlError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/ingest/unfurl", response_model=LinkPreview)
async def get_unfurled_link(
    url: str = Query(min_length=1, max_length=1024),
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
    force: bool = Query(default=False),
):
    try:
        check_unfurl_rate_limit(user_id)
    except RateLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    try:
        return await IngestService(db).unfurl(url, force=force)
    except UnfurlError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/ingest/bookmarks", response_model=IngestItemResponse)
async def create_bookmark(
    req: BookmarkRequest,
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    result = await IngestService(db).create_bookmark(
        user_id,
        url=req.url,
        title=req.title,
        content=req.content,
        source_type=req.source_type,
    )
    await db.commit()
    return result


@router.get("/ingest/items", response_model=list[IngestItemResponse])
async def list_ingest_items(
    limit: int = Query(default=50, le=200),
    user_id: UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    return await IngestService(db).list_ingest_items(user_id, limit)


class WhatsAppWebhookBody(BaseModel):
    from_: str = Field(alias="from", min_length=1)
    text: str | None = None
    media_url: str | None = None

    model_config = {"populate_by_name": True}


WHATSAPP_WEBHOOK_DOCS = {
    "status": "not_configured",
    "detail": "WhatsApp ingest requires WHATSAPP_SECRET and WHATSAPP_DEFAULT_USER_ID",
    "expected_headers": {"X-WhatsApp-Secret": "<shared secret>"},
    "expected_body": {
        "from": "+15551234567",
        "text": "optional message text",
        "media_url": "optional https://...",
    },
    "behavior": "When configured, creates an ingest item (source_type=whatsapp) for WHATSAPP_DEFAULT_USER_ID",
}


@router.get("/whatsapp/health")
async def whatsapp_health():
    settings = get_settings()
    return {
        "status": "ok",
        "service": "whatsapp",
        "configured": bool(settings.whatsapp_secret and settings.whatsapp_default_user_id),
    }


@router.post("/whatsapp/webhook")
async def whatsapp_webhook(
    body: WhatsAppWebhookBody,
    db: AsyncSession = Depends(get_db),
    x_whatsapp_secret: str | None = Header(default=None, alias="X-WhatsApp-Secret"),
):
    settings = get_settings()
    if not settings.whatsapp_secret or not settings.whatsapp_default_user_id:
        raise HTTPException(status_code=501, detail=WHATSAPP_WEBHOOK_DOCS)

    if not x_whatsapp_secret or x_whatsapp_secret != settings.whatsapp_secret:
        raise HTTPException(status_code=401, detail="Invalid WhatsApp secret")

    try:
        mapped_user_id = UUID(settings.whatsapp_default_user_id)
    except ValueError as exc:
        raise HTTPException(status_code=501, detail=WHATSAPP_WEBHOOK_DOCS) from exc

    url = body.media_url or f"whatsapp://{body.from_}"
    result = await IngestService(db).create_bookmark(
        mapped_user_id,
        url=url,
        title=f"WhatsApp from {body.from_}",
        content=body.text,
        source_type="whatsapp",
        raw_payload={"from": body.from_, "text": body.text, "media_url": body.media_url},
    )
    await db.commit()
    return result

