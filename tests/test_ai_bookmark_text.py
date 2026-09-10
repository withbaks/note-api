from types import SimpleNamespace
from unittest.mock import patch

from note_db.models import MemoryType


def test_extract_text_includes_link_metadata():
    from note_ai.service import AIService

    service = AIService(session=SimpleNamespace())
    obj = SimpleNamespace(
        type=MemoryType.bookmark,
        structured_title="Bookmark title",
        structured_value="https://example.com",
        content_text="Bookmark title\nhttps://example.com",
        link_metadata={
            "description": "Short card description",
            "extracted_text": "A" * 5000,
        },
    )
    with patch("note_ai.service.get_settings", return_value=SimpleNamespace(openai_api_key=None)):
        text = service._extract_text(obj)
    assert "Short card description" in text
    assert "A" * 4000 in text
    assert "A" * 4001 not in text
