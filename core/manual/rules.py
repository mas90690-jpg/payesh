# core/manual_rules.py
"""
قوانین هوش دستی — لودر هوشمند.

استراتژی:
  ۱) اول تلاش می‌کنه از data/intelligence/core/rules.json بخونه
  ۲) اگه فایل نبود یا خراب بود → fallback به hardcoded
  ۳) فرمت یکسان: همیشه لیست دیکشنری

سازگاری عقب‌رو:
  - `RULES` (module-level constant) حفظ شده — `manual_brain.py` ازش استفاده می‌کنه.
  - برای reload در runtime، از `reload_rules()` استفاده کن.

نسخهٔ ۲ (این نسخه):
  - 🎯 لاگ دقیق و ساخت‌یافته با پیشوند [RULES:...]:
      * load: منبع (JSON/fallback) + تعداد + زمان
      * resolve: تعداد queries resolve شده + drop شده
      * خطاها: type(e).__name__ + line برای JSONDecodeError
  - 🐛 FIX: _FALLBACK_RULES حالا deep-copy می‌شه (mutation leak رفع)
  - 🐛 FIX: data dict نبود → log + پیش‌فرض
  - 🐛 FIX: mutation in-place حذف شد (کپی هر rule قبل از تغییر)
  - 🎯 validation: هر rule چک می‌شه که trigger + action داره
  - 🎯 reload_rules() برای hot-reload
  - 🧹 from __future__ import annotations
  - 🧹 type hints دقیق
"""
from __future__ import annotations

import copy
import json
import time
from typing import Any

from core.kernel.logger import log_error, log_info, log_success, log_warning

# ═══════════════════════════════════════════════════════
# مسیرهای JSON (منبع یکتا: data/paths.py)
# ═══════════════════════════════════════════════════════
from data import paths as data_paths

_RULES_FILE = data_paths.RULES
_QUERIES_FILE = data_paths.QUERIES

# آستانهٔ کندی (میلی‌ثانیه)
_SLOW_LOAD_MS = 500

# ═══════════════════════════════════════════════════════
# hardcoded fallback
# ═══════════════════════════════════════════════════════
_FALLBACK_RULES: list[dict[str, Any]] = [
    {
        "name": "today_count",
        "triggers": [
            "امروز چند سرنخ",
            "چند سرنخ امروز",
            "سرنخ امروز",
            ["امروز", "سرنخ"],
        ],
        "action": {
            "type": "sql_scalar",
            "sql": (
                "SELECT COUNT(*) FROM messages "
                "WHERE keywords!='' AND date(timestamp)=date('now')"
            ),
            "template": "📊 امروز: {v} سرنخ",
        },
    },
    # نمونه — بقیه در rules.json
]


# ═══════════════════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════════════════
def _is_valid_rule(rule: Any) -> bool:
    """validation ساده برای rule."""
    if not isinstance(rule, dict):
        return False
    if not rule.get("name"):
        return False
    triggers = rule.get("triggers")
    if not triggers or not isinstance(triggers, list):
        return False
    action = rule.get("action")
    if not isinstance(action, dict):
        return False
    if not action.get("type"):
        return False
    return True


def _sanitize_rule(rule: dict[str, Any]) -> dict[str, Any] | None:
    """کپی امن از rule + آماده‌سازی action (تبدیل sql → sql_scalar)."""
    if not _is_valid_rule(rule):
        return None

    # deep-copy (جلوگیری از mutation در input)
    r = copy.deepcopy(rule)
    act = r.get("action") or {}

    if act.get("type") == "sql":
        if "sql" in act:
            # direct SQL — تبدیل به sql_scalar
            act["type"] = "sql_scalar"
        else:
            # query نام‌دار — pending resolve
            act["_pending_resolve"] = True
    return r


# ═══════════════════════════════════════════════════════
# load from JSON
# ═══════════════════════════════════════════════════════
def _load_rules_from_json() -> list[dict[str, Any]]:
    """از JSON بخون. اگه خطا داد → []."""
    if not _RULES_FILE.exists():
        log_warning(
            f"⚠️ [RULES:load] rules.json پیدا نشد: {_RULES_FILE}"
        )
        return []

    t0 = time.monotonic()
    try:
        with open(str(_RULES_FILE), encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        log_error(
            f"❌ [RULES:load] JSON نامعتبر | "
            f"line={e.lineno} col={e.colno} | {e.msg}"
        )
        return []
    except Exception as e:
        log_error(
            f"❌ [RULES:load] خواندن | {type(e).__name__}: {e}"
        )
        return []

    if not isinstance(data, dict):
        log_warning(
            f"⚠️ [RULES:load] ساختار نامعتبر | "
            f"type={type(data).__name__} — انتظار dict"
        )
        return []

    raw_rules = data.get("rules", [])
    if not isinstance(raw_rules, list):
        log_warning(
            f"⚠️ [RULES:load] rules باید لیست باشه | "
            f"type={type(raw_rules).__name__}"
        )
        return []

    # sanitize + filter
    cleaned: list[dict[str, Any]] = []
    dropped = 0
    converted_sql = 0
    pending = 0

    for r in raw_rules:
        s = _sanitize_rule(r)
        if s is None:
            dropped += 1
            continue
        act = s.get("action") or {}
        if act.get("_pending_resolve"):
            pending += 1
        elif act.get("type") == "sql_scalar" and "sql" in act:
            converted_sql += 1
        cleaned.append(s)

    elapsed_ms = (time.monotonic() - t0) * 1000
    log_success(
        f"✅ [RULES:load] {len(cleaned)} از {len(raw_rules)} قانون "
        f"بارگذاری شد | sql_direct={converted_sql} "
        f"pending={pending} dropped={dropped} | {elapsed_ms:.0f}ms"
    )
    if elapsed_ms > _SLOW_LOAD_MS:
        log_warning(
            f"🐌 [RULES:load] کند | {elapsed_ms:.0f}ms"
        )
    return cleaned


# ═══════════════════════════════════════════════════════
# resolve named queries
# ═══════════════════════════════════════════════════════
def _load_queries() -> dict[str, Any]:
    """بارگذاری queries.json — dict از {name: {sql, ...}}."""
    if not _QUERIES_FILE.exists():
        log_info(
            f"ℹ️ [RULES:queries] فایل نیست: {_QUERIES_FILE.name}"
        )
        return {}
    try:
        with open(str(_QUERIES_FILE), encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            log_warning(
                f"⚠️ [RULES:queries] ساختار نامعتبر | "
                f"type={type(data).__name__}"
            )
            return {}
        queries = data.get("queries", {})
        if not isinstance(queries, dict):
            return {}
        log_info(
            f"📚 [RULES:queries] {len(queries)} کوئری بارگذاری شد"
        )
        return queries
    except json.JSONDecodeError as e:
        log_warning(
            f"⚠️ [RULES:queries] JSON نامعتبر | "
            f"line={e.lineno} | {e.msg}"
        )
        return {}
    except Exception as e:
        log_warning(
            f"⚠️ [RULES:queries] {type(e).__name__}: {e}"
        )
        return {}


def _resolve_pending_queries(
    rules: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """قوانینی که SQL نام‌دار دارن رو از queries.json resolve می‌کنه."""
    # اول چک: آیا اصلاً rule pending داریم؟
    has_pending = any(
        (r.get("action") or {}).get("_pending_resolve")
        for r in rules
    )
    if not has_pending:
        return rules

    queries = _load_queries()
    if not queries:
        log_warning(
            "⚠️ [RULES:resolve] ruleهای pending هستن ولی "
            "queries.json خالیه — همه drop می‌شن"
        )
        # حذف ruleهای pending (چون بدون SQL کار نمی‌کنن)
        return [
            r for r in rules
            if not (r.get("action") or {}).get("_pending_resolve")
        ]

    resolved: list[dict[str, Any]] = []
    ok_count = 0
    drop_count = 0

    for r in rules:
        act = r.get("action") or {}
        if not act.get("_pending_resolve"):
            resolved.append(r)
            continue

        qname = act.get("query")
        qdef = queries.get(qname) if qname else None

        if qdef and isinstance(qdef, dict) and "sql" in qdef:
            act.pop("_pending_resolve", None)
            # نوع رو از query یا پیش‌فرض sql_scalar
            act["type"] = qdef.get("type", "sql_scalar")
            act["sql"] = qdef["sql"]
            ok_count += 1
            resolved.append(r)
        else:
            drop_count += 1
            log_warning(
                f"⚠️ [RULES:resolve] rule «{r.get('name')}» | "
                f"query=«{qname}» پیدا نشد — drop"
            )

    if ok_count or drop_count:
        log_success(
            f"✅ [RULES:resolve] {ok_count} resolve شد | "
            f"{drop_count} drop"
        )
    return resolved


# ═══════════════════════════════════════════════════════
# main loader
# ═══════════════════════════════════════════════════════
def _load_rules() -> list[dict[str, Any]]:
    """لود قوانین با fallback خودکار."""
    t0 = time.monotonic()
    log_info("▶️ [RULES:main] شروع بارگذاری")

    json_rules = _load_rules_from_json()
    if json_rules:
        json_rules = _resolve_pending_queries(json_rules)
        if json_rules:
            elapsed_ms = (time.monotonic() - t0) * 1000
            log_success(
                f"✅ [RULES:main] {len(json_rules)} rule از JSON | "
                f"{elapsed_ms:.0f}ms"
            )
            return json_rules

    # fallback
    log_warning(
        "⚠️ [RULES:main] JSON در دسترس نبود — fallback hardcoded"
    )
    fb = copy.deepcopy(_FALLBACK_RULES)
    elapsed_ms = (time.monotonic() - t0) * 1000
    log_success(
        f"✅ [RULES:main] {len(fb)} rule از fallback | "
        f"{elapsed_ms:.0f}ms"
    )
    return fb


# ═══════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════
RULES: list[dict[str, Any]] = _load_rules()


def reload_rules() -> int:
    """بارگذاری مجدد — برای hot-reload. Returns: تعداد ruleها."""
    global RULES
    log_info("🔄 [RULES:reload] بارگذاری مجدد...")
    RULES = _load_rules()
    log_success(f"✅ [RULES:reload] {len(RULES)} rule")
    return len(RULES)


def get_rules_count() -> int:
    """تعداد ruleهای بارگذاری‌شده."""
    return len(RULES)
