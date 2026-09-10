import json

import pytest

from note_ingestion.rate_limit import RateLimitExceeded, check_unfurl_rate_limit, _buckets
from note_ingestion.unfurl import (
    UnfurlError,
    _build_preview,
    _host_blocked,
    _normalize_url,
    _parse_json_ld,
)


def test_normalize_url_adds_https():
    assert _normalize_url("example.com/path") == "https://example.com/path"


def test_host_blocked_localhost():
    assert _host_blocked("localhost") is True
    assert _host_blocked("example.com") is False


def test_build_preview_uses_open_graph():
    html = """
    <html lang="en">
      <head>
        <meta property="og:title" content="OG Title" />
        <meta property="og:description" content="OG Description" />
        <meta property="og:image" content="https://cdn.example.com/cover.jpg" />
        <meta property="og:site_name" content="Example" />
        <link rel="icon" href="/favicon.ico" />
      </head>
      <body><p>ignored</p></body>
    </html>
    """
    preview = _build_preview("https://example.com/article", html)
    assert preview.title == "OG Title"
    assert preview.description == "OG Description"
    assert preview.image_url == "https://cdn.example.com/cover.jpg"
    assert preview.site_name == "Example"
    assert preview.favicon_url == "https://example.com/favicon.ico"
    assert preview.language == "en"


def test_parse_json_ld_article():
    payload = {
        "@context": "https://schema.org",
        "@type": "NewsArticle",
        "headline": "JSON-LD Headline",
        "description": "JSON-LD summary",
        "datePublished": "2026-01-15",
        "author": {"@type": "Person", "name": "Alex Chen"},
        "articleBody": "Long article body text.",
    }
    html = f"""
    <html>
      <head>
        <script type="application/ld+json">
        {json.dumps(payload)}
        </script>
      </head>
    </html>
    """
    data = _parse_json_ld(html)
    assert data["content_type"] == "article"
    assert data["title"] == "JSON-LD Headline"
    assert data["author"] == "Alex Chen"
    assert data["published_at"] == "2026-01-15"
    assert "Long article body" in data["extracted_text"]


def test_build_preview_falls_back_to_title_tag():
    html = "<html><head><title>Plain Title</title></head></html>"
    preview = _build_preview("https://example.com", html)
    assert preview.title == "Plain Title"


def test_normalize_url_rejects_invalid_scheme():
    with pytest.raises(UnfurlError):
        _normalize_url("ftp://example.com")


def test_unfurl_rate_limit():
    from uuid import uuid4

    _buckets.clear()
    user_id = uuid4()
    for _ in range(30):
        check_unfurl_rate_limit(user_id)
    with pytest.raises(RateLimitExceeded):
        check_unfurl_rate_limit(user_id)
