# data/iran_cities.py
# لیست شهرهای ایران بر اساس استان

CITIES_BY_PROVINCE = {
    "تهران
}

# ==================================================
# لیست تخت همه شهرها (برای جستجوی سریع)
# ==================================================
ALL_CITIES = []
for cities in CITIES_BY_PROVINCE.values():
    ALL_CITIES.extend(cities)
ALL_CITIES = sorted(set(ALL_CITIES))