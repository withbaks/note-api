import hashlib
import ipaddress
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

MAX_HTML_BYTES = 512_000
FETCH_TIMEOUT = 10.0
MAX_EXTRACTED_TEXT = 12_000
SHORT_DESCRIPTION_THRESHOLD = 80
USER_AGENT = "NoteBot/1.0 (+https://withbaks.com/note)"
CACHE_TTL = timedelta(hours=48)

ARTICLE_TYPES = frozenset(
    {
        "Article",
        "NewsArticle",
        "BlogPosting",
        "ScholarlyArticle",
        "TechArticle",
        "Report",
    }
)
VIDEO_TYPES = frozenset({"VideoObject", "Movie"})
PRODUCT_TYPES = frozenset({"Product", "IndividualProduct"})


class LinkPreview(BaseModel):
    url: str
    title: str | None = None
    description: str | None = None
    image_url: str | None = None
    site_name: str | None = None
    favicon_url: str | None = None
    extracted_text: str | None = None
    content_type: str | None = Field(default=None, pattern="^(article|video|product|generic)$")
    author: str | None = None
    published_at: str | None = None
    language: str | None = None


class UnfurlError(Exception):
    pass


def _normalize_url(url: str) -> str:
    raw = url.strip()
    if not raw:
        raise UnfurlError("URL is required")
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    if parsed.scheme not in ("http", "https"):
        raise UnfurlError("Only http and https URLs are supported")
    if not parsed.netloc:
        raise UnfurlError("Invalid URL")
    return parsed.geturl()


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


def _host_blocked(hostname: str) -> bool:
    host = hostname.lower().strip(".")
    if host in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
        return True
    if host.endswith(".local") or host.endswith(".internal"):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
    except ValueError:
        return False


def _meta_content(html: str, *, prop: str | None = None, name: str | None = None) -> str | None:
    keys = []
    if prop:
        keys.extend([("property", prop), ("name", prop)])
    if name:
        keys.append(("name", name))
    for attr, value in keys:
        patterns = [
            rf'<meta[^>]+{attr}=["\']{re.escape(value)}["\'][^>]+content=["\']([^"\']+)["\']',
            rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+{attr}=["\']{re.escape(value)}["\']',
        ]
        for pattern in patterns:
            match = re.search(pattern, html, flags=re.IGNORECASE)
            if match:
                return _decode_entities(match.group(1).strip())
    return None


def _decode_entities(text: str) -> str:
    return (
        text.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )


def _title_tag(html: str) -> str | None:
    match = re.search(r"<title[^>]*>([^<]+)</title>", html, flags=re.IGNORECASE)
    if not match:
        return None
    return _decode_entities(match.group(1).strip())


def _html_lang(html: str) -> str | None:
    match = re.search(r"<html[^>]+lang=[\"']([^\"']+)[\"']", html, flags=re.IGNORECASE)
    if not match:
        return None
    return match.group(1).strip()[:16]


def _favicon_url(html: str, base_url: str) -> str | None:
    for pattern in (
        r'<link[^>]+rel=["\'](?:shortcut icon|icon)["\'][^>]+href=["\']([^"\']+)["\']',
        r'<link[^>]+href=["\']([^"\']+)["\'][^>]+rel=["\'](?:shortcut icon|icon)["\']',
    ):
        match = re.search(pattern, html, flags=re.IGNORECASE)
        if match:
            return urljoin(base_url, match.group(1).strip())
    parsed = urlparse(base_url)
    return f"{parsed.scheme}://{parsed.netloc}/favicon.ico"


def _abs_url(base_url: str, maybe_relative: str | None) -> str | None:
    if not maybe_relative:
        return None
    return urljoin(base_url, maybe_relative.strip())


def _json_ld_blocks(html: str) -> list[Any]:
    blocks: list[Any] = []
    for match in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        raw = match.group(1).strip()
        if not raw:
            continue
        try:
            blocks.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return blocks


def _flatten_json_ld(node: Any) -> list[dict[str, Any]]:
    if isinstance(node, list):
        items: list[dict[str, Any]] = []
        for entry in node:
            items.extend(_flatten_json_ld(entry))
        return items
    if not isinstance(node, dict):
        return []
    items = [node]
    graph = node.get("@graph")
    if isinstance(graph, list):
        for entry in graph:
            items.extend(_flatten_json_ld(entry))
    return items


def _json_ld_type(node: dict[str, Any]) -> str | None:
    raw_type = node.get("@type")
    if isinstance(raw_type, list):
        raw_type = raw_type[0] if raw_type else None
    if isinstance(raw_type, str):
        return raw_type.split("/")[-1]
    return None


def _json_ld_author(node: dict[str, Any]) -> str | None:
    author = node.get("author")
    if isinstance(author, str):
        return author[:256]
    if isinstance(author, dict):
        name = author.get("name")
        return str(name)[:256] if name else None
    if isinstance(author, list) and author:
        first = author[0]
        if isinstance(first, str):
            return first[:256]
        if isinstance(first, dict) and first.get("name"):
            return str(first["name"])[:256]
    return None


def _parse_json_ld(html: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    nodes: list[dict[str, Any]] = []
    for block in _json_ld_blocks(html):
        nodes.extend(_flatten_json_ld(block))

    for node in nodes:
        node_type = _json_ld_type(node)
        if not node_type:
            continue
        if node_type in ARTICLE_TYPES and result.get("content_type") != "article":
            result["content_type"] = "article"
            result["title"] = node.get("headline") or node.get("name") or result.get("title")
            result["description"] = node.get("description") or result.get("description")
            result["author"] = _json_ld_author(node) or result.get("author")
            result["published_at"] = node.get("datePublished") or result.get("published_at")
            if node.get("articleBody") and not result.get("extracted_text"):
                result["extracted_text"] = str(node["articleBody"])
        elif node_type in VIDEO_TYPES and not result.get("content_type"):
            result["content_type"] = "video"
            result["title"] = node.get("name") or result.get("title")
            result["description"] = node.get("description") or result.get("description")
        elif node_type in PRODUCT_TYPES and not result.get("content_type"):
            result["content_type"] = "product"
            result["title"] = node.get("name") or result.get("title")
            result["description"] = node.get("description") or result.get("description")
        elif node_type == "WebPage" and not result.get("content_type"):
            result["content_type"] = "generic"
            result["title"] = node.get("name") or result.get("title")
            result["description"] = node.get("description") or result.get("description")

    return result


def _extract_article_text(html: str, url: str) -> str | None:
    try:
        import trafilatura
    except ImportError:
        return None

    text = trafilatura.extract(
        html,
        url=url,
        include_comments=False,
        include_tables=False,
        favor_precision=True,
    )
    if not text:
        return None
    cleaned = re.sub(r"\s+", " ", text).strip()
    return cleaned[:MAX_EXTRACTED_TEXT] if cleaned else None


def _build_preview(final_url: str, html: str) -> LinkPreview:
    json_ld = _parse_json_ld(html)

    title = (
        _meta_content(html, prop="og:title")
        or _meta_content(html, name="twitter:title")
        or json_ld.get("title")
        or _title_tag(html)
    )
    description = (
        _meta_content(html, prop="og:description")
        or _meta_content(html, name="description")
        or _meta_content(html, name="twitter:description")
        or json_ld.get("description")
    )
    image_url = _abs_url(
        final_url,
        _meta_content(html, prop="og:image") or _meta_content(html, name="twitter:image"),
    )
    site_name = _meta_content(html, prop="og:site_name")
    favicon_url = _favicon_url(html, final_url) if html else f"{urlparse(final_url).scheme}://{urlparse(final_url).netloc}/favicon.ico"
    language = _html_lang(html) or _meta_content(html, name="language")

    extracted_text = json_ld.get("extracted_text")
    if isinstance(extracted_text, str):
        extracted_text = extracted_text[:MAX_EXTRACTED_TEXT]
    else:
        extracted_text = None

    description_text = description.strip() if isinstance(description, str) else ""
    if not extracted_text and (
        not description_text or len(description_text) < SHORT_DESCRIPTION_THRESHOLD
    ):
        extracted_text = _extract_article_text(html, final_url)

    content_type = json_ld.get("content_type")
    if not content_type:
        content_type = "article" if extracted_text and len(extracted_text) > 400 else "generic"

    if not title:
        title = urlparse(final_url).netloc

    author = json_ld.get("author")
    published_at = json_ld.get("published_at")
    if isinstance(published_at, str):
        published_at = published_at[:64]

    return LinkPreview(
        url=final_url,
        title=title[:512] if title else None,
        description=description[:1024] if description else None,
        image_url=image_url,
        site_name=site_name[:256] if site_name else None,
        favicon_url=favicon_url,
        extracted_text=extracted_text[:MAX_EXTRACTED_TEXT] if extracted_text else None,
        content_type=content_type,
        author=author[:256] if isinstance(author, str) else None,
        published_at=published_at if isinstance(published_at, str) else None,
        language=language[:16] if language else None,
    )


async def _get_cached_preview(session: AsyncSession, url: str) -> LinkPreview | None:
    from note_db.models import LinkPreviewCache

    row = await session.scalar(
        select(LinkPreviewCache).where(LinkPreviewCache.url_hash == _url_hash(url))
    )
    if not row:
        return None
    if row.fetched_at < datetime.now(UTC) - CACHE_TTL:
        return None
    return LinkPreview.model_validate(row.preview)


async def _set_cached_preview(session: AsyncSession, preview: LinkPreview) -> None:
    from note_db.models import LinkPreviewCache

    url_hash = _url_hash(preview.url)
    row = await session.get(LinkPreviewCache, url_hash)
    payload = preview.model_dump()
    now = datetime.now(UTC)
    if row:
        row.url = preview.url
        row.preview = payload
        row.fetched_at = now
    else:
        session.add(
            LinkPreviewCache(
                url_hash=url_hash,
                url=preview.url,
                preview=payload,
                fetched_at=now,
            )
        )


async def unfurl_url(
    url: str,
    *,
    session: AsyncSession | None = None,
    force: bool = False,
) -> LinkPreview:
    normalized = _normalize_url(url)
    parsed = urlparse(normalized)
    if _host_blocked(parsed.hostname or ""):
        raise UnfurlError("URL host is not allowed")

    if session and not force:
        cached = await _get_cached_preview(session, normalized)
        if cached:
            return cached

    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=FETCH_TIMEOUT,
        headers=headers,
    ) as client:
        response = await client.get(normalized)
        for hop in response.history:
            hop_host = urlparse(str(hop.url)).hostname or ""
            if _host_blocked(hop_host):
                raise UnfurlError("Redirect target is not allowed")

        final_url = str(response.url)
        final_host = urlparse(final_url).hostname or ""
        if _host_blocked(final_host):
            raise UnfurlError("URL host is not allowed")

        content_type = response.headers.get("content-type", "")
        html = ""
        if "text/html" in content_type or "application/xhtml" in content_type or not content_type:
            html = response.text[:MAX_HTML_BYTES]

    preview = _build_preview(final_url, html)

    if session:
        await _set_cached_preview(session, preview)
        await session.flush()

    return preview
