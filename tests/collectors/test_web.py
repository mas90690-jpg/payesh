# tests/collectors/test_web.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from collectors.web import WebCollector


def test_web_extract_text():
    """✅ تست استخراج متن از HTML"""
    html = """
    <html>
        <head><title>عنوان تست</title></head>
        <body>
            <p>متن یاراترا در اینجا</p>
            <script>alert('hi')</script>
            <style>.test{color:red}</style>
        </body>
    </html>
    """
    title, text = WebCollector._extract(html)
    assert "عنوان تست" in title
    assert "یاراترا" in text
    assert "alert" not in text
    assert "test{color" not in text


def test_web_extract_links():
    """✅ تست استخراج لینک‌ها از HTML"""
    html = """
    <html>
        <body>
            <a href="/page1">link1</a>
            <a href="https://example.com/page2">link2</a>
        </body>
    </html>
    """
    base = "https://example.com"
    links = WebCollector._extract_links(html, base)
    assert "https://example.com/page1" in links
    assert "https://example.com/page2" in links


def test_web_make_message_deduplication():
    """✅ تست جلوگیری از ذخیره‌ی تکراری"""
    collector = WebCollector()
    msg1 = collector._make_message("https://test.com", "title", "content یاراترا")
    msg2 = collector._make_message("https://test.com", "title", "content یاراترا")
    assert msg1 is not None
    assert msg2 is None  # تکراری


@pytest.mark.asyncio
async def test_web_fetch_messages_with_keyword():
    """✅ تست دریافت مطلب از وب با کلیدواژه"""
    collector = WebCollector()
    collector._connected = True
    collector._sources = [("page", "https://test.com", {})]
    
    # شبیه‌سازی fetch
    async def mock_fetch(session, url):
        return "<html><body>متن یاراترا</body></html>"
    collector._fetch = mock_fetch
    
    # شبیه‌سازی extract
    def mock_extract(html):
        return "عنوان", "متن یاراترا"
    collector._extract = mock_extract
    
    collector._seen_hashes = set()
    
    messages = await collector.fetch_messages()
    
    assert len(messages) >= 1
    assert messages[0]["platform"] == "web"
    assert "یاراترا" in messages[0]["text"]