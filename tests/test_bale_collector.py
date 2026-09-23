"""تست‌های کلکتور بله (Bale) — تمرکز روی رفع باگِ «file_id عددی»."""

import asyncio

import pytest

from collectors.bale import BaleCollector


@pytest.fixture
def collector() -> BaleCollector:
    """یک نمونهٔ کلکتور بدون نیاز به اتصال واقعی (به‌جای __init__ مستقیم)."""
    bc = BaleCollector.__new__(BaleCollector)
    bc._client = None
    bc._media_dir = "."
    return bc


def run(coro):
    """اجرای coroutine در یک حلقهٔ رویداد جدید (جدا از حلقهٔ pytest)."""
    return asyncio.new_event_loop().run_until_complete(coro)


class TestDownloadFileIntBug:
    """باگ واقعی: `'int' object is not subscriptable` هنگام دانلود فایل با file_id عددی."""

    async def _download(self, bc: BaleCollector, file_id):
        return await bc._download_file(file_id, "photo")

    def test_int_file_id_no_crash(self, collector: BaleCollector) -> None:
        """file_id از نوع int (مثل ۱۲۳۴۵ در لاگ) نباید کرش کند."""
        result = run(self._download(collector, 12345))
        assert result is None  # بدون کلاینت → دانلود نمی‌شود ولی خطا هم نمی‌دهد

    def test_str_file_id_no_crash(self, collector: BaleCollector) -> None:
        """file_id رشته‌ای هم مثل قبل کار می‌کند."""
        result = run(self._download(collector, "abc123"))
        assert result is None

    def test_none_file_id_returns_none(self, collector: BaleCollector) -> None:
        """file_id تهی باید بی‌خطا None برگرداند."""
        assert run(self._download(collector, None)) is None

    def test_media_type_from_document_int(self) -> None:
        """تبدیل file_id عددیِ document به رشته، پیش از استفاده در دانلود."""
        class _Doc:
            file_id = 12345
            access_hash = None
            caption = None
            mime_type = "image/jpeg"
            name = "pic.jpg"

        class _Msg:
            text = None
            document = _Doc()

        media_type, file_id, caption = BaleCollector._get_media_type_from_message(
            BaleCollector, _Msg()
        )
        assert media_type == "photo"
        assert file_id == "12345"  # عدد → رشته (پیش از برش)
        assert caption == ""
