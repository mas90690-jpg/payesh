# core/manual/rules.py
"""
قوانین هوش دستی — لودر هوشمند.

استراتژی:
  ۱) اول تلاش می‌کنه از `data/configs/rules.json` بخونه
  ۲) اگه فایل نبود یا خراب بود → fallback به hardcoded
  ۳) فرمت یکسان: همیشه لیست دیکشنری

سازگاری عقب‌رو:
  - `RULES` (module-level constant) حفظ شده.
  - برای reload در runtime، از `reload_rules()` استفاده کن.

نسخهٔ ۳ (این نسخه):
  - 🎯 `_SLOW_LOAD_MS` حالا env-based (RULES_SLOW_LOAD_MS).
  - 🎯 `RULES` حالا **lazy-load** می‌شود (به‌جای load در import).
    این کار باعث می‌شود import سریع‌تر شود و مشکل circular import
    کاهش یابد.
  - 🎯 `get_rules()` تابع جدید — lazy access با mtime check.
  - 🎯 `_RULES_CACHE` با mtime-aware hot-reload (مثل keyword_finder).
  - 🐛 FIX: `_resolve_pending_queries` حالا در صورت نبود queries.json،
    ruleهای non-pending را حفظ می‌کند (قبلاً همه را drop می‌کرد).
  - 🐛 FIX: `_sanitize_rule` حالا `action._pending_resolve` را فقط
    برای type "sql" بدون کلید sql اضافه می‌کند.
  - 🎯 `get_stats()` متد جدید برای debug.
  - ✅ حفظ همهٔ رفتار نسخهٔ ۲.
"""

from __future__ import annotations

import copy
import json
import os
import threading
import time
from typing import Any

from core.kernel.logger import log_error, log_info, log_success, log_warning

# ═══════════════════════════════════════════════════════════
# مسیرها (منبع یکتا: data/paths.py)
# ═══════════════════════════════════════════════════════════
from data import paths as data_paths

_RULES_FILE = data_paths.RULES
_QUERIES_FILE = data_paths.QUERIES


# ═══════════════════════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════════════════════
def _env_int(
    name: str,
    default: int,
    min_val: int | None = None,
    max_val: int | None = None,
) -> int:
    """خواندن int از env — با sanitize."""
    try:
        v = int(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        return default
    if min_val is not None and v < min_val:
        return min_val
    if max_val is not None and v > max_val:
        return max_val
    return v


def _env_float(
    name: str,
    default: float,
    min_val: float | None = None,
    max_val: float | None = None,
) -> float:
    """خواندن float از env — با sanitize."""
    try:
        v = float(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        return default
    if min_val is not None and v < min_val:
        return min_val
    if max_val is not None and v > max_val:
        return max_val
    return v


# 🎯 FIX: env-based
_SLOW_LOAD_MS = _env_int("RULES_SLOW_LOAD_MS", 500, min_val=50, max_val=60_000)
_RELOAD_CHECK_SECONDS = _env_float(
    "RULES_RELOAD_SECONDS", 10.0, min_val=1.0, max_val=3600.0
)


# ═══════════════════════════════════════════════════════════
# hardcoded fallback
# ═══════════════════════════════════════════════════════════
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
                "WHERE keywords!='' "
                "AND date(timestamp)=date('now')"
            ),
            "template": "📊 امروز: {v} سرنخ",
        },
    },
    # نمونه — بقیه در rules.json
]


# ═══════════════════════════════════════════════════════════
# validation / sanitize
# ═══════════════════════════════════════════════════════════
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
    return action.get("type")


def _sanitize_rule(rule: dict[str, Any]) -> dict[str, Any] | None:
    """کپی امن از rule + آماده‌سازی action.

    - `type: "sql"` + کلید sql → تبدیل به `sql_scalar`
    - `type: "sql"` بدون کلید sql → pending resolve
    - بقیه: بدون تغییر
    """
    if not _is_valid_rule(rule):
        return None

    r = copy.deepcopy(rule)
    act = r.get("action") or {}

    if act.get("type") == "sql":
        if "sql" in act:
            act["type"] = "sql_scalar"
        else:
            act["_pending_resolve"] = True

    return r


# ═══════════════════════════════════════════════════════════
# load from JSON
# ═══════════════════════════════════════════════════════════
def _load_rules_from_json() -> list[dict[str, Any]]:
    """از JSON بخون. اگه خطا داد → []."""
    if not _RULES_FILE.exists():
        log_warning(f"⚠️ [RULES:load] rules.json نبود: {_RULES_FILE}")
        return []

    t0 = time.monotonic()
    try:
        with open(str(_RULES_FILE), encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        log_error(
            f"❌ [RULES:load] JSON نامعتبر | line={
                e.lineno} col={
                e.colno} | {
                e.msg}")
        return []
    except Exception as e:
        log_error(f"❌ [RULES:load] خواندن | {type(e).__name__}: {e}")
        return []

    if not isinstance(data, dict):
        log_warning(
            f"⚠️ [RULES:load] ساختار نامعتبر | type={
                type(data).__name__} — انتظار dict")
        return []

    raw_rules = data.get("rules", [])
    if not isinstance(raw_rules, list):
        log_warning(
            f"⚠️ [RULES:load] rules باید لیست باشه | type={
                type(raw_rules).__name__}")
        return []

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
        f"✅ [RULES:load] {len(cleaned)} از {len(raw_rules)} "
        f"قانون | sql_direct={converted_sql} pending={pending} "
        f"dropped={dropped} | {elapsed_ms:.0f}ms"
    )
    if elapsed_ms > _SLOW_LOAD_MS:
        log_warning(f"🐌 [RULES:load] کند | {elapsed_ms:.0f}ms")
    return cleaned


# ═══════════════════════════════════════════════════════════
# resolve named queries
# ═══════════════════════════════════════════════════════════
def _load_queries() -> dict[str, Any]:
    """بارگذاری queries.json — dict از {name: {...}}."""
    if not _QUERIES_FILE.exists():
        log_info(f"ℹ️ [RULES:queries] فایل نیست: {_QUERIES_FILE.name}")
        return {}
    try:
        with open(str(_QUERIES_FILE), encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            log_warning(
                f"⚠️ [RULES:queries] ساختار نامعتبر | type={
                    type(data).__name__}")
            return {}
        queries = data.get("queries", {})
        if not isinstance(queries, dict):
            return {}
        log_info(f"📚 [RULES:queries] {len(queries)} کوئری بارگذاری شد")
        return queries
    except json.JSONDecodeError as e:
        log_warning(
            f"⚠️ [RULES:queries] JSON نامعتبر | line={
                e.lineno} | {
                e.msg}")
        return {}
    except Exception as e:
        log_warning(f"⚠️ [RULES:queries] {type(e).__name__}: {e}")
        return {}


def _resolve_pending_queries(
    rules: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """قوانینی که SQL نام‌دار دارن رو resolve می‌کنه.

    🐛 FIX: اگر queries.json نبود، ruleهای non-pending حفظ می‌شن
    (قبلاً همه drop می‌شدند).
    """
    has_pending = any((r.get("action") or {}).get(
        "_pending_resolve") for r in rules)
    if not has_pending:
        return rules

    queries = _load_queries()
    if not queries:
        log_warning(
            "⚠️ [RULES:resolve] pending هست ولی queries.json "
            "خالیه — ruleهای pending drop می‌شن (بقیه حفظ)"
        )
        # 🎯 FIX: فقط pendingها را drop کن
        return [
            r for r in rules if not (
                r.get("action") or {}).get("_pending_resolve")]

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
            f"✅ [RULES:resolve] {ok_count} resolve | {drop_count} drop")
    return resolved


# ═══════════════════════════════════════════════════════════
# main loader
# ═══════════════════════════════════════════════════════════
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
                f"✅ [RULES:main] {
                    len(json_rules)} rule از JSON | {
                    elapsed_ms:.0f}ms")
            return json_rules

    # fallback
    log_warning("⚠️ [RULES:main] JSON در دسترس نبود — fallback")
    fb = copy.deepcopy(_FALLBACK_RULES)
    elapsed_ms = (time.monotonic() - t0) * 1000
    log_success(
        f"✅ [RULES:main] {
            len(fb)} rule از fallback | {
            elapsed_ms:.0f}ms")
    return fb


# ═══════════════════════════════════════════════════════════
# Cache + Lazy access
# ═══════════════════════════════════════════════════════════
_RULES_CACHE: list[dict[str, Any]] | None = None
_RULES_CACHE_MTIME: tuple[float | None, float | None] = (None, None)
_RULES_LOCK = threading.Lock()
_LAST_CHECK: float = 0.0


def _get_files_mtime() -> tuple[float | None, float | None]:
    """mtime rules.json و queries.json."""
    try:
        r_mtime = _RULES_FILE.stat().st_mtime if _RULES_FILE.exists() else None
    except OSError:
        r_mtime = None
    try:
        q_mtime = _QUERIES_FILE.stat().st_mtime if _QUERIES_FILE.exists() else None
    except OSError:
        q_mtime = None
    return r_mtime, q_mtime


def _load_with_cache() -> list[dict[str, Any]]:
    """لود با cache mtime-aware."""
    global _RULES_CACHE, _RULES_CACHE_MTIME

    current_mtime = _get_files_mtime()

    with _RULES_LOCK:
        if _RULES_CACHE is not None and current_mtime == _RULES_CACHE_MTIME:
            return _RULES_CACHE

    # load (خارج از lock برای جلوگیری از block)
    new_rules = _load_rules()

    with _RULES_LOCK:
        _RULES_CACHE = new_rules
        _RULES_CACHE_MTIME = current_mtime

    return new_rules


def _maybe_reload() -> None:
    """چک mtime — اگه تغییر کرده reload کن."""
    global _LAST_CHECK

    now = time.monotonic()
    if now - _LAST_CHECK < _RELOAD_CHECK_SECONDS:
        return
    _LAST_CHECK = now

    current_mtime = _get_files_mtime()
    if current_mtime == _RULES_CACHE_MTIME:
        return

    # تغییر داشته → reload
    with _RULES_LOCK:
        changed = current_mtime != _RULES_CACHE_MTIME
    if changed:
        log_info("🔄 [RULES:hot-reload] تغییر شناسایی شد")
        _load_with_cache()


# ═══════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════
def get_rules() -> list[dict[str, Any]]:
    """🎯 دریافت ruleها با cache + hot-reload."""
    _maybe_reload()
    return _load_with_cache()


def reload_rules() -> int:
    """بارگذاری مجدد — برای hot-reload دستی.

    Returns:
        تعداد ruleها.
    """
    global _RULES_CACHE, _RULES_CACHE_MTIME
    log_info("🔄 [RULES:reload] بارگذاری مجدد...")

    # invalidate cache
    with _RULES_LOCK:
        _RULES_CACHE = None
        _RULES_CACHE_MTIME = (None, None)

    rules = _load_with_cache()
    log_success(f"✅ [RULES:reload] {len(rules)} rule")
    return len(rules)


def get_rules_count() -> int:
    """تعداد ruleهای بارگذاری‌شده."""
    return len(get_rules())


# ═══════════════════════════════════════════════════════════
# Backward-compat: RULES constant
# ═══════════════════════════════════════════════════════════
# ⚠️ برای سازگاری با کد قدیمی که `from core.manual.rules import RULES`
# استفاده می‌کند. حالا lazy-init می‌شود.
#
# اگر می‌خواهی hot-reload داشته باشی، `get_rules()` صدا بزن.


class _LazyRulesList(list):
    """🎯 لیست lazy برای سازگاری با `RULES` (list-like).

    هر دسترسی به طول/آیتم، از `get_rules()` استفاده می‌کند.
    """

    def __iter__(self):
        return iter(get_rules())

    def __len__(self):
        return len(get_rules())

    def __getitem__(self, idx):
        return get_rules()[idx]

    def __contains__(self, item):
        return item in get_rules()

    def __repr__(self):
        return f"<LazyRulesList len={len(self)}>"


RULES = _LazyRulesList()


# ═══════════════════════════════════════════════════════════
# Debug stats
# ═══════════════════════════════════════════════════════════
def get_stats() -> dict[str, Any]:
    """🎯 خلاصهٔ وضعیت — برای debug."""
    rules = get_rules()
    mtime_r, mtime_q = _RULES_CACHE_MTIME

    pending_count = sum(
        1 for r in rules if (r.get("action") or {}).get("_pending_resolve")
    )

    # شمارش انواع action
    action_types: dict[str, int] = {}
    for r in rules:
        t = (r.get("action") or {}).get("type", "?")
        action_types[t] = action_types.get(t, 0) + 1

    return {
        "total_rules": len(rules),
        "pending_resolve": pending_count,
        "action_types": action_types,
        "rules_file": str(_RULES_FILE),
        "queries_file": str(_QUERIES_FILE),
        "rules_mtime": mtime_r,
        "queries_mtime": mtime_q,
        "reload_check_seconds": _RELOAD_CHECK_SECONDS,
        "slow_load_ms": _SLOW_LOAD_MS,
    }
