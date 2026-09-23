# tests/test_keyword_finder.py


def test_keywords_loaded(keyword_finder):
    """بررسی اینکه حداقل ۲۹۰ کلیدواژه از محصولات بارگذاری شده باشد"""
    assert (
        len(keyword_finder.keywords) >= 290
    ), f"فقط {len(keyword_finder.keywords)} کلیدواژه بارگذاری شد!"


def test_persian_match(keyword_finder):
    """تطبیق نام فارسی محصولات"""
    assert keyword_finder.has_match("قیمت یاراترا چنده؟")
    assert keyword_finder.has_match("رونزا نکست بهترین است")
    assert not keyword_finder.has_match("سلام خوبی؟")
    assert not keyword_finder.has_match("کود NPK")  # چون NPK در لیست نیست


def test_english_match(keyword_finder):
    """تطبیق نام انگلیسی محصولات (حساس به بزرگ/کوچکی نیست)"""
    assert keyword_finder.has_match("Yara Rexolin Zn")
    assert keyword_finder.has_match("Albatros")
    assert keyword_finder.has_match("Yaratera")
    assert not keyword_finder.has_match("Hello World")


def test_match_returns_list(keyword_finder):
    """بررسی اینکه متد match لیست کلیدواژه‌های یافت‌شده را برمی‌گرداند"""
    result = keyword_finder.match("خرید Yara و رونزا")
    # حداقل یکی از اینها باید پیدا شود
    found = [kw for kw in result if kw.lower() in ["yara", "یارا", "رونزا"]]
    assert len(found) >= 1, f"کلیدواژه‌ای پیدا نشد: {result}"


def test_match_multiple_keywords(keyword_finder):
    """بررسی پیدا کردن چند کلیدواژه در یک متن"""
    text = "یاراترا و رونزا نکست و یارالیوا"
    result = keyword_finder.match(text)
    assert len(result) >= 3, f"فقط {len(result)} کلیدواژه پیدا شد: {result}"
