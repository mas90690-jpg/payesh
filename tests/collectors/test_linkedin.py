# tests/collectors/test_linkedin.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime
from collectors.linkedin import LinkedInCollector
from analyzers.keyword_finder import get_keyword_finder


@pytest.mark.asyncio
async def test_linkedin_match_logic():
    """✅ تست منطق تطبیق کلیدواژه در لینکدین"""
    collector = LinkedInCollector(email="test@test.com", password="pass")
    collector._kw = get_keyword_finder()

    # پست با کلیدواژه
    post_text = "کود یاراترا بهترین است"
    matched = collector._match_post(post_text, "کود")
    assert len(matched) >= 1
    assert "یاراترا" in matched or "کود" in matched

    # پست با عبارت جستجو (حتی اگر محصول نباشد)
    post_text = "کود کشاورزی وارداتی"
    matched = collector._match_post(post_text, "کود")
    assert "کود" in matched

    # پست بی‌ربط
    post_text = "سلام این یک پست معمولی است"
    matched = collector._match_post(post_text, "کود")
    assert len(matched) == 0


@pytest.mark.asyncio
async def test_linkedin_load_searches():
    """✅ تست بارگذاری عبارت‌های جستجو از فایل"""
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', delete=False) as f:
        f.write("# کامنت\n")
        f.write("کود کشاورزی\n")
        f.write("یاراترا\n")
        f.write("\n")
        f.write("رونزا\n")
        f.flush()
        f.close()

        collector = LinkedInCollector(searches_file=f.name)
        searches = collector._load_searches()
        assert len(searches) == 3
        assert "کود کشاورزی" in searches
        assert "یاراترا" in searches
        assert "رونزا" in searches


@pytest.mark.asyncio
async def test_linkedin_scrape_search_with_keyword(temp_db, keyword_finder):
    """✅ تست اسکرپ جستجوی لینکدین با کلیدواژه"""
    collector = LinkedInCollector(email="test@test.com", password="pass")
    collector._kw = keyword_finder
    collector._seen_ids = set()
    collector._messages_queue = []
    
    # شبیه‌سازی صفحه
    mock_page = AsyncMock()
    mock_post = AsyncMock()
    mock_post.inner_text = AsyncMock(return_value="یاراترا بهترین کود است")
    mock_post.get_attribute = AsyncMock(return_value="urn:li:activity:123")
    mock_page.query_selector_all = AsyncMock(return_value=[mock_post])
    mock_page.goto = AsyncMock()
    
    collector._page = mock_page
    collector._connected = True
    
    await collector._scrape_search("کود")
    
    assert len(collector._messages_queue) == 1
    assert collector._messages_queue[0]["platform"] == "linkedin"
    assert "یاراترا" in collector._messages_queue[0]["text"]