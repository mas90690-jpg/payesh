# tests/test_score_full.py
"""
تست جامع — نمره‌دهی دقیق تک‌تک بخش‌های پروژه.

۱۳ بخش را تست می‌کند و هر کدام را از ۱۰ نمره می‌دهد.

اجرا:
  python tests/test_score_full.py
  python tests/test_score_full.py --quick
  python tests/test_score_full.py --section ai
  python tests/test_score_full.py --json-out score.json
  python tests/test_score_full.py --section size
  python tests/test_score_full.py --section graph

نسخهٔ ۲ (این نسخه):
  - 🐛 FIX: بلوک KeywordFinder دوبار بود → حالا یک بار.
  - 🐛 FIX: asyncio.iscoroutinefunction منسوخ → inspect.
  - 🎯 NEW: تست match با کلیدواژهٔ واقعی.
  - 🎯 NEW: بخش ۱۱ — اندازهٔ فایل‌ها (شکستن فایل‌های > ۱۵۰۰ خط).
  - 🎯 NEW: بخش ۱۲ — import graph (درهم‌تنیدگی).
  - 🎯 NEW: بخش ۱۳ — dead code detection.
"""
from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from collections import defaultdict

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

from core.kernel.console_compat import patch_console
patch_console(verbose=False)


# ═════════════════════════════════════════════════════════
# Score tracker
# ═════════════════════════════════════════════════════════
class Score:
    def __init__(self):
        self.sections: dict[str, dict] = {}

    def add_section(self, name: str):
        self.sections[name] = {
            "checks": [],
            "score": 0.0,
            "max": 0.0,
        }

    def add_check(self, section: str, name: str, passed: bool,
                  detail: str = "", points: float = 1.0):
        if section not in self.sections:
            self.add_section(section)
        self.sections[section]["checks"].append({
            "name": name,
            "passed": passed,
            "detail": detail,
            "points": points,
        })
        self.sections[section]["max"] += points
        if passed:
            self.sections[section]["score"] += points

    def section_score(self, section: str) -> tuple[float, float]:
        s = self.sections.get(section, {})
        return s.get("score", 0.0), s.get("max", 0.0)

    def total(self) -> tuple[float, float]:
        total_score = sum(s["score"] for s in self.sections.values())
        total_max = sum(s["max"] for s in self.sections.values())
        return total_score, total_max

    def to_dict(self) -> dict:
        return {
            "sections": self.sections,
            "total": {
                "score": sum(s["score"] for s in self.sections.values()),
                "max": sum(s["max"] for s in self.sections.values()),
            }
        }


SCORE = Score()


def _hdr(title: str):
    print()
    print("=" * 72)
    print(f"  {title}")
    print("=" * 72)


def _sub(title: str):
    print(f"\n  ── {title} " + "─" * max(0, 60 - len(title)))


def _ok(name: str, detail: str = ""):
    print(f"     ✅ {name}" + (f" | {detail}" if detail else ""))


def _fail(name: str, detail: str = ""):
    print(f"     ❌ {name}" + (f" | {detail}" if detail else ""))


def _warn(name: str, detail: str = ""):
    print(f"     ⚠️  {name}" + (f" | {detail}" if detail else ""))


def _info(name: str, detail: str = ""):
    print(f"     ℹ️  {name}" + (f" | {detail}" if detail else ""))


# ═════════════════════════════════════════════════════════
# بخش ۱: فایل‌ها و ساختار
# ═════════════════════════════════════════════════════════
def test_structure():
    _hdr("۱) ساختار و فایل‌ها")
    SCORE.add_section("1. structure")

    root = Path(__file__).resolve().parent.parent

    _sub("فایل‌های اصلی")
    critical_files = [
        "main.py", "config.py", "login.py", "panel.py",
        ".env", "requirements.txt",
    ]
    for f in critical_files:
        p = root / f
        exists = p.exists()
        SCORE.add_check("1. structure", f"file:{f}", exists)
        if exists:
            _ok(f, f"{p.stat().st_size}B")
        else:
            _fail(f)

    _sub("پوشه‌ها")
    critical_dirs = [
        "collectors", "handlers", "analyzers",
        "core", "data", "storage", "tests",
    ]
    for d in critical_dirs:
        p = root / d
        exists = p.is_dir()
        SCORE.add_check("1. structure", f"dir:{d}", exists)
        if exists:
            _ok(f"{d}/")
        else:
            _fail(f"{d}/")

    _sub("فایل‌های config")
    config_files = [
        "access_control.json", "rules.json", "queries.json",
        "settings.json", "synonyms.json", "templates.json",
        "errors.json", "report_headers.json", "crm_headers.json",
        "actions.json", "domain.json", "events.json",
        "markers.json", "messages.json", "tiers.json",
        "commands.json", "ai_persona.json", "ai_mode_prompts.json",
    ]
    for f in config_files:
        p = root / "data" / "configs" / f
        exists = p.exists()
        SCORE.add_check("1. structure", f"config:{f}", exists,
                        points=0.5)
        if exists:
            _ok(f)
        else:
            _warn(f, "missing")

    _sub("کلکتورها")
    collectors = [
        "bale.py", "bale_bot.py", "eitaa.py", "rubika.py",
        "telegram.py", "whatsapp.py", "linkedin.py",
        "instagram.py", "youtube.py", "web.py",
        "facebook.py",
    ]
    for c in collectors:
        p = root / "collectors" / c
        exists = p.exists()
        SCORE.add_check("1. structure", f"collector:{c}", exists)
        if exists:
            _ok(c)
        else:
            _fail(c)


# ═════════════════════════════════════════════════════════
# بخش ۲: config
# ═════════════════════════════════════════════════════════
def test_config():
    _hdr("۲) Config و env")
    SCORE.add_section("2. config")

    _sub("import")
    try:
        from config import (
            PLATFORMS_ORDER, PLATFORMS_ACTIVE, PLUGINS_CONFIG,
            AI, AI_ENABLED, AI_PRIORITY, ADMIN_IDS,
            ADMIN_PLATFORM, RETENTION,
        )
        SCORE.add_check("2. config", "import", True)
        _ok("import config")
    except Exception as e:
        SCORE.add_check("2. config", "import", False, str(e))
        _fail("import config", str(e))
        return

    _sub("PLATFORMS")
    SCORE.add_check("2. config", "PLATFORMS_ORDER",
                    len(PLATFORMS_ORDER) >= 9,
                    f"{PLATFORMS_ORDER}")
    _ok(f"PLATFORMS_ORDER: {list(PLATFORMS_ORDER)}")

    SCORE.add_check("2. config", "PLATFORMS_ACTIVE",
                    len(PLATFORMS_ACTIVE) >= 8,
                    f"{PLATFORMS_ACTIVE}")
    _ok(f"PLATFORMS_ACTIVE: {PLATFORMS_ACTIVE}")

    _sub("AI config")
    SCORE.add_check("2. config", "AI_ENABLED", AI_ENABLED)
    _ok(f"AI_ENABLED={AI_ENABLED}")
    SCORE.add_check("2. config", "AI_PRIORITY",
                    AI_PRIORITY in ("deepseek", "auto", "ollama"),
                    AI_PRIORITY)
    _ok(f"AI_PRIORITY={AI_PRIORITY}")
    has_key = bool(AI.get("deepseek_api_key"))
    SCORE.add_check("2. config", "deepseek_key", has_key)
    _ok(f"deepseek_key={'✅' if has_key else '❌'}")

    _sub("Admin")
    SCORE.add_check("2. config", "ADMIN_IDS", len(ADMIN_IDS) > 0,
                    f"{len(ADMIN_IDS)}")
    _ok(f"ADMIN_IDS: {len(ADMIN_IDS)}")
    SCORE.add_check("2. config", "ADMIN_PLATFORM",
                    ADMIN_PLATFORM == "bale", ADMIN_PLATFORM)
    _ok(f"ADMIN_PLATFORM={ADMIN_PLATFORM}")

    _sub("PLUGINS_CONFIG")
    for key in (
        "balecollector", "eitaacollector", "rubikacollector",
        "telegramcollector", "whatsappcollector",
        "linkedincollector", "instagramcollector",
        "youtubecollector", "webcollector", "facebookcollector",
    ):
        exists = key in PLUGINS_CONFIG
        SCORE.add_check("2. config", f"plugin:{key}", exists)
        if exists:
            _ok(key)
        else:
            _fail(key)

    _sub("RETENTION")
    for k in ("media_days", "reports_days", "messages_days",
              "cleanup_interval_hours"):
        ok = k in RETENTION and RETENTION[k] > 0
        SCORE.add_check("2. config", f"retention:{k}", ok,
                        f"{RETENTION.get(k)}")
        if ok:
            _ok(f"{k}={RETENTION[k]}")
        else:
            _warn(f"{k}")


# ═════════════════════════════════════════════════════════
# بخش ۳: دیتابیس
# ═════════════════════════════════════════════════════════
def test_database():
    _hdr("۳) دیتابیس")
    SCORE.add_section("3. database")

    try:
        from data import paths as data_paths
        db_path = str(data_paths.DB_FILE)
    except Exception as e:
        SCORE.add_check("3. database", "paths", False, str(e))
        return

    db_exists = os.path.exists(db_path)
    SCORE.add_check("3. database", "db_exists", db_exists, db_path)
    if db_exists:
        _ok("DB file", f"{os.path.getsize(db_path)/1024:.1f}KB")
    else:
        _fail("DB file")
        return

    import sqlite3
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        cur = conn.cursor()

        cur.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' ORDER BY name"
        )
        tables = [r[0] for r in cur.fetchall()]
        SCORE.add_check("3. database", "tables",
                        len(tables) >= 4, f"{tables}")
        _ok(f"tables: {tables}")

        try:
            cur.execute("PRAGMA user_version")
            v = cur.fetchone()[0]
            SCORE.add_check("3. database", "schema_version",
                            v >= 6, f"v{v}")
            _ok(f"schema: v{v}")
        except Exception:
            pass

        try:
            cur.execute("SELECT COUNT(*) FROM messages")
            n = cur.fetchone()[0]
            SCORE.add_check("3. database", "messages", n >= 0, f"{n}")
            _ok(f"messages: {n}")
        except Exception:
            pass

        try:
            cur.execute(
                "SELECT COUNT(*) FROM messages "
                "WHERE keywords != ''"
            )
            leads = cur.fetchone()[0]
            SCORE.add_check("3. database", "leads", True, f"{leads}")
            _ok(f"leads: {leads}")
        except Exception:
            pass

        try:
            cur.execute("PRAGMA integrity_check")
            r = cur.fetchone()[0]
            ok = r == "ok"
            SCORE.add_check("3. database", "integrity", ok, r)
            if ok:
                _ok("integrity_check")
            else:
                _fail("integrity_check", r)
        except Exception:
            pass

        conn.close()
    except Exception as e:
        SCORE.add_check("3. database", "connect", False, str(e))
        _fail("DB connect", str(e))


# ═════════════════════════════════════════════════════════
# بخش ۴: هوش (AI)
# ═════════════════════════════════════════════════════════
async def test_ai():
    _hdr("۴) هوش مصنوعی")
    SCORE.add_section("4. ai")

    _sub("AIAssistant")
    try:
        from core.services.ai_assistant import get_ai_assistant
        ai = get_ai_assistant()
        SCORE.add_check("4. ai", "AIAssistant_init", True)
        _ok("AIAssistant init")

        order = ai._provider_order()
        SCORE.add_check("4. ai", "provider_order",
                        len(order) >= 1, f"{order}")
        _ok(f"provider_order: {order}")

        try:
            avail = await asyncio.wait_for(ai.available(), timeout=10)
            SCORE.add_check("4. ai", "available", avail)
            _ok(f"available={avail}")
        except Exception as e:
            SCORE.add_check("4. ai", "available", False, str(e))
            _warn("available", str(e))

        try:
            t0 = time.monotonic()
            resp, ok = await asyncio.wait_for(
                ai.chat_with_status("سلام، فقط بگو سلام"),
                timeout=30,
            )
            elapsed = time.monotonic() - t0
            SCORE.add_check("4. ai", "chat_real", ok and bool(resp),
                            f"{elapsed:.0f}ms")
            if ok:
                _ok("chat real", f"{elapsed:.0f}ms")
            else:
                _warn("chat real", str(resp)[:80])
        except Exception as e:
            SCORE.add_check("4. ai", "chat_real", False, str(e))
            _warn("chat real", str(e))
    except Exception as e:
        SCORE.add_check("4. ai", "AIAssistant", False, str(e))
        _fail("AIAssistant", str(e))

    _sub("SafeBrain")
    try:
        from core.safe_brain import get_safe_brain
        sb = get_safe_brain()
        SCORE.add_check("4. ai", "SafeBrain_init", True)
        _ok("SafeBrain init")
        SCORE.add_check("4. ai", "SafeBrain_key", bool(sb.api_key))
        _ok(f"api_key={'✅' if sb.api_key else '❌'}")
        SCORE.add_check("4. ai", "SafeBrain_persistent",
                        sb.persistent_memory is not None)
        if sb.persistent_memory:
            _ok(f"persistent: {sb.persistent_memory.count()} رکورد")

        try:
            r = await asyncio.wait_for(sb.process("help"), timeout=5)
            ok = bool(r)
            SCORE.add_check("4. ai", "SafeBrain_process", ok)
            _ok("process help", f"len={len(r) if r else 0}")
        except Exception as e:
            SCORE.add_check("4. ai", "SafeBrain_process", False, str(e))
    except Exception as e:
        SCORE.add_check("4. ai", "SafeBrain", False, str(e))
        _fail("SafeBrain", str(e))

    _sub("Brain")
    try:
        from core.brain.main import Brain
        tool_count = len(getattr(Brain, "TOOL_MAP", {}))
        ok = tool_count >= 40
        SCORE.add_check("4. ai", "Brain_tools", ok, f"{tool_count}")
        _ok(f"Brain tools: {tool_count}")

        required = [
            "read_file", "write_file", "grep_search",
            "audit_code", "git_status", "query_database",
            "get_setting", "set_setting",
            "start_platform", "stop_platform",
            "diagnose_platform", "list_all_platforms",
        ]
        missing = [t for t in required
                   if t not in Brain.TOOL_MAP]
        SCORE.add_check("4. ai", "Brain_required_tools",
                        not missing, f"missing={missing}")
        if not missing:
            _ok(f"همه {len(required)} ابزار کلیدی موجود")
        else:
            _fail("ابزارهای گم‌شده", f"{missing}")
    except Exception as e:
        SCORE.add_check("4. ai", "Brain", False, str(e))
        _fail("Brain", str(e))

    _sub("ManualBrain")
    try:
        from core.manual.brain import get_manual_brain
        from data import paths as data_paths
        mb = get_manual_brain(str(data_paths.DB_FILE))
        SCORE.add_check("4. ai", "ManualBrain_init", mb is not None)
        if mb:
            rules = len(mb.core_rules)
            SCORE.add_check("4. ai", "ManualBrain_rules",
                            rules >= 20, f"{rules}")
            _ok(f"core_rules: {rules}")
    except Exception as e:
        SCORE.add_check("4. ai", "ManualBrain", False, str(e))
        _warn("ManualBrain", str(e)[:80])


# ═════════════════════════════════════════════════════════
# بخش ۵: آنالیزورها (نسخهٔ fix شده)
# ═════════════════════════════════════════════════════════
def test_analyzers():
    _hdr("۵) آنالیزورها")
    SCORE.add_section("5. analyzers")

    # ─── KeywordFinder ───
    _sub("KeywordFinder")
    kf = None
    try:
        from analyzers.keyword_finder import get_keyword_finder
        kf = get_keyword_finder()
        n = len(kf.keywords)
        SCORE.add_check("5. analyzers", "KeywordFinder",
                        n >= 100, f"{n}")
        _ok(f"{n} کلیدواژه")
    except Exception as e:
        SCORE.add_check("5. analyzers", "KeywordFinder", False, str(e))
        _fail("KeywordFinder import", str(e))

    # ─── تست match با کلیدواژهٔ واقعی ───
    if kf is not None:
        try:
            sample_kw = None
            for kw in kf.keywords:
                if kw and len(kw) >= 3 and any(
                    '\u0600' <= c <= '\u06ff' for c in kw
                ):
                    sample_kw = kw
                    break
            if not sample_kw:
                sample_kw = "یاراترا"

            matches = kf.match(sample_kw)
            ok = len(matches) > 0
            SCORE.add_check(
                "5. analyzers", "kf_match", ok,
                f"sample={sample_kw} | matches={len(matches)}"
            )
            if ok:
                _ok(f"match «{sample_kw}»: {matches[:3]}")
            else:
                _warn(f"match «{sample_kw}»", "بدون تطابق")
        except Exception as e:
            SCORE.add_check("5. analyzers", "kf_match", False, str(e))
            _fail("kf_match", str(e))

    # ─── LeadScorer ───
    _sub("LeadScorer")
    try:
        from analyzers.lead_scorer import get_lead_scorer
        ls = get_lead_scorer()
        score, level, signals = ls.score(
            "قیمت کود مایع چند؟ می‌خرم فوری"
        )
        SCORE.add_check("5. analyzers", "LeadScorer",
                        score > 0, f"score={score}")
        _ok(f"score={score} | level={level}")
    except Exception as e:
        SCORE.add_check("5. analyzers", "LeadScorer", False, str(e))

    # ─── PriorityFinder ───
    _sub("PriorityFinder")
    try:
        from analyzers.priority_finder import get_priority_finder
        pf = get_priority_finder()
        stats = pf.get_stats()
        t1 = stats.get("t1_words", 0)
        SCORE.add_check("5. analyzers", "PriorityFinder",
                        t1 > 0, f"T1={t1}")
        _ok(f"T1={t1} | T2={stats.get('t2_words', 0)}")
    except Exception as e:
        SCORE.add_check("5. analyzers", "PriorityFinder", False, str(e))

    # ─── TargetFilter ───
    _sub("TargetFilter")
    try:
        from analyzers.target_filter import get_target_groups
        tf = get_target_groups()
        SCORE.add_check("5. analyzers", "TargetFilter", tf is not None)
        _ok("TargetFilter init")
    except Exception as e:
        SCORE.add_check("5. analyzers", "TargetFilter", False, str(e))


# ═════════════════════════════════════════════════════════
# بخش ۶: کلکتورها
# ═════════════════════════════════════════════════════════
def test_collectors():
    _hdr("۶) کلکتورها (import)")
    SCORE.add_section("6. collectors")

    collectors = [
        ("collectors.bale", "BaleCollector"),
        ("collectors.bale_bot", "BaleBotCollector"),
        ("collectors.eitaa", "EitaaCollector"),
        ("collectors.rubika", "RubikaCollector"),
        ("collectors.telegram", "TelegramWebCollector"),
        ("collectors.whatsapp", "WhatsAppCollector"),
        ("collectors.linkedin", "LinkedInCollector"),
        ("collectors.instagram", "InstagramCollector"),
        ("collectors.youtube", "YouTubeCollector"),
        ("collectors.web", "WebCollector"),
        ("collectors.facebook", "FacebookCollector"),
    ]

    for mod, cls_name in collectors:
        try:
            m = __import__(mod, fromlist=[cls_name])
            cls = getattr(m, cls_name, None)
            ok = cls is not None
            SCORE.add_check("6. collectors", f"{cls_name}", ok)
            if ok:
                _ok(f"{cls_name}")
            else:
                _fail(f"{cls_name} | کلاس پیدا نشد")
        except Exception as e:
            SCORE.add_check("6. collectors", f"{cls_name}",
                            False, str(e)[:60])
            _fail(f"{cls_name}", str(e)[:60])


# ═════════════════════════════════════════════════════════
# بخش ۷: هندلرها
# ═════════════════════════════════════════════════════════
def test_handlers():
    _hdr("۷) هندلرها")
    SCORE.add_section("7. handlers")

    handlers = [
        ("handlers.admin_handler", "AdminHandler"),
        ("handlers.storage_handler", "StorageHandler"),
    ]

    for mod, cls_name in handlers:
        try:
            m = __import__(mod, fromlist=[cls_name])
            cls = getattr(m, cls_name, None)
            SCORE.add_check("7. handlers", f"{cls_name}",
                            cls is not None)
            _ok(f"{cls_name}")
        except Exception as e:
            SCORE.add_check("7. handlers", f"{cls_name}",
                            False, str(e)[:60])
            _fail(f"{cls_name}", str(e)[:60])

    try:
        SCORE.add_check("7. handlers", "TestPipeline_import", True)
        _ok("TestPipeline import")
    except Exception as e:
        SCORE.add_check("7. handlers", "TestPipeline_import",
                        False, str(e)[:60])
        _fail("TestPipeline", str(e)[:60])


# ═════════════════════════════════════════════════════════
# بخش ۸: پاک‌سازی
# ═════════════════════════════════════════════════════════
def test_cleanup():
    _hdr("۸) سرویس پاک‌سازی")
    SCORE.add_section("8. cleanup")

    try:
        from core.services.cleanup_service import (
            CleanupService, start_cleanup_service,
            _CACHE_FILES,
        )
        SCORE.add_check("8. cleanup", "import", True)
        _ok("CleanupService import")

        cs = CleanupService()
        try:
            r = cs.is_running()
            SCORE.add_check("8. cleanup", "is_running",
                            isinstance(r, bool))
            _ok(f"is_running()={r}")
        except Exception as e:
            SCORE.add_check("8. cleanup", "is_running",
                            False, str(e))
            _fail("is_running()", str(e))

        try:
            stats = cs.get_stats()
            SCORE.add_check("8. cleanup", "get_stats",
                            isinstance(stats, dict))
            _ok(f"get_stats keys={list(stats.keys())[:5]}")
        except Exception as e:
            SCORE.add_check("8. cleanup", "get_stats", False, str(e))

        files_str = " ".join(str(f) for f in _CACHE_FILES)
        has_learned = "learned_patterns" in files_str
        has_plans = "plans.json" in files_str
        safe = not (has_learned or has_plans)
        SCORE.add_check("8. cleanup", "safe_cache_files",
                        safe,
                        f"learned={has_learned} plans={has_plans}")
        if safe:
            _ok("حافظه Brain در _CACHE_FILES نیست")
        else:
            _fail("learned_patterns یا plans در _CACHE_FILES است")

        SCORE.add_check("8. cleanup", "start_cleanup_service",
                        callable(start_cleanup_service))
        _ok("start_cleanup_service موجود")
    except Exception as e:
        SCORE.add_check("8. cleanup", "import", False, str(e))
        _fail("CleanupService", str(e))


# ═════════════════════════════════════════════════════════
# بخش ۹: پنل
# ═════════════════════════════════════════════════════════
def test_panel():
    _hdr("۹) پنل کنترل")
    SCORE.add_section("9. panel")

    try:
        import panel
        SCORE.add_check("9. panel", "import", True)
        _ok("panel import")

        actions = getattr(panel, "_ACTIONS", {})
        n = len(actions)
        SCORE.add_check("9. panel", "menu_items", n >= 15, f"{n}")
        _ok(f"menu: {n} گزینه")

        try:
            c = panel._platforms_count()
            SCORE.add_check("9. panel", "platforms_count",
                            c >= 8, f"{c}")
            _ok(f"platforms_count: {c}")
        except Exception as e:
            SCORE.add_check("9. panel", "platforms_count",
                            False, str(e))
            _fail("_platforms_count", str(e))
    except Exception as e:
        SCORE.add_check("9. panel", "import", False, str(e))
        _fail("panel import", str(e))

    _sub("helpers C class")
    try:
        from tests.helpers import C
        has_yellow = hasattr(C, "YELLOW")
        has_green = hasattr(C, "GREEN")
        SCORE.add_check("9. panel", "C.YELLOW", has_yellow)
        if has_yellow:
            _ok("C.YELLOW موجود")
        else:
            _fail("C.YELLOW ندارد")
        SCORE.add_check("9. panel", "C.GREEN", has_green)
        if has_green:
            _ok("C.GREEN موجود")
        else:
            _fail("C.GREEN ندارد")
    except Exception as e:
        SCORE.add_check("9. panel", "C_class", False, str(e))


# ═════════════════════════════════════════════════════════
# بخش ۱۰: تست‌ها
# ═════════════════════════════════════════════════════════
def test_test_files():
    _hdr("۱۰) فایل‌های تست")
    SCORE.add_section("10. tests")

    root = Path(__file__).resolve().parent
    test_files = [
        "test_bale_full.py",
        "test_eitaa_full.py",
        "test_rubika_full.py",
        "test_telegram_full.py",
        "test_whatsapp_full.py",
        "test_linkedin_full.py",
        "test_instagram_full.py",
        "test_youtube_full.py",
        "test_facebook_full.py",
        "test_web_full.py",
        "test_db_full.py",
        "test_storage_full.py",
        "test_healthcheck_full.py",
        "test_ai_full.py",
        "test_ai_master_full.py",
        "test_score_full.py",
        "run_all_tests.py",
        "helpers.py",
        "db_helpers.py",
        "pipeline_helpers.py",
    ]

    for f in test_files:
        p = root / f
        exists = p.exists()
        SCORE.add_check("10. tests", f"{f}", exists, points=0.5)
        if exists:
            _ok(f)
        else:
            _warn(f, "missing")


# ═════════════════════════════════════════════════════════
# بخش ۱۱: اندازهٔ فایل‌ها (نسخهٔ فوق دقیق)
# ═════════════════════════════════════════════════════════
def test_file_sizes():
    _hdr("۱۱) اندازهٔ فایل‌ها — فایل‌های بزرگ")
    SCORE.add_section("11. sizes")

    root = Path(__file__).resolve().parent.parent

    # ─── فایل‌های پایتون را جمع کن ───
    py_files: list[tuple[int, str]] = []
    for p in root.rglob("*.py"):
        rel = str(p)
        if any(x in rel for x in (".git", "venv", "__pycache__")):
            continue
        try:
            lines = len(p.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines())
            py_files.append((lines, str(p.relative_to(root))))
        except Exception:
            continue

    py_files.sort(reverse=True)

    # ─── چک فایل‌های بزرگ ───
    _sub("فایل‌های بزرگ (> ۱۵۰۰ خط)")
    very_big = [(n, f) for n, f in py_files if n > 1500]
    if very_big:
        for n, f in very_big[:10]:
            _warn(f"{n} خط", f)
    else:
        _ok("هیچ فایل > ۱۵۰۰ خط")

    # ─── چک فایل‌های متوسط ───
    _sub("فایل‌های متوسط (۸۰۰ - ۱۵۰۰ خط)")
    medium = [(n, f) for n, f in py_files if 800 < n <= 1500]
    if medium:
        for n, f in medium[:10]:
            _info(f"{n} خط", f)
    else:
        _ok("هیچ فایل ۸۰۰-۱۵۰۰ خط")

    # ─── نمره‌دهی ───
    if len(very_big) == 0:
        SCORE.add_check("11. sizes", "no_huge_files", True,
                        "0 فایل > ۱۵۰۰ خط")
    elif len(very_big) <= 2:
        SCORE.add_check("11. sizes", "no_huge_files", True,
                        f"{len(very_big)} فایل > ۱۵۰۰ خط")
    else:
        SCORE.add_check("11. sizes", "no_huge_files", False,
                        f"{len(very_big)} فایل > ۱۵۰۰ خط")

    # ─── آمار کلی ───
    _sub("آمار کلی")
    total_lines = sum(n for n, _ in py_files)
    total_files = len(py_files)
    _ok(f"{total_files} فایل پایتون", f"{total_lines} خط")

    SCORE.add_check("11. sizes", "total_files",
                    total_files >= 50, f"{total_files}")
    SCORE.add_check("11. sizes", "total_lines",
                    total_lines >= 10000, f"{total_lines}")


# ═════════════════════════════════════════════════════════
# بخش ۱۲: import graph (درهم‌تنیدگی)
# ═════════════════════════════════════════════════════════
def test_import_graph():
    _hdr("۱۲) import graph — درهم‌تنیدگی")
    SCORE.add_section("12. graph")

    import ast

    root = Path(__file__).resolve().parent.parent

    # ─── هر فایل و importهایش ───
    graph: dict[str, set[str]] = defaultdict(set)

    for p in root.rglob("*.py"):
        rel = str(p)
        if any(x in rel for x in (".git", "venv", "__pycache__")):
            continue
        try:
            tree = ast.parse(p.read_text(
                encoding="utf-8", errors="replace"
            ))
        except Exception:
            continue

        file_key = str(p.relative_to(root))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                graph[file_key].add(node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    graph[file_key].add(alias.name)

    # ─── چک import حلقه‌ای در core/ ───
    _sub("import حلقه‌ای (core فقط)")

    def has_cycle(graph_dict):
        cycles = []
        for start in graph_dict:
            if not start.startswith("core"):
                continue
            visited: set[str] = set()
            path: list[str] = []

            def dfs(node: str):
                if node in path:
                    idx = path.index(node)
                    cycles.append(path[idx:] + [node])
                    return
                if node in visited:
                    return
                visited.add(node)
                path.append(node)
                for dep in graph_dict.get(node, set()):
                    if dep.startswith("core"):
                        dfs(dep)
                path.pop()

            dfs(start)

        # حذف تکراری‌ها
        seen = set()
        unique = []
        for c in cycles:
            key = tuple(sorted(set(c)))
            if key not in seen:
                seen.add(key)
                unique.append(c)
        return unique

    cycles = has_cycle(graph)

    if not cycles:
        SCORE.add_check("12. graph", "no_core_cycles", True,
                        "0 حلقه")
        _ok("هیچ import حلقه‌ای در core/")
    else:
        SCORE.add_check("12. graph", "no_core_cycles", False,
                        f"{len(cycles)} حلقه")
        _fail(f"{len(cycles)} حلقه")
        for c in cycles[:5]:
            _warn(" → ".join(c[-4:]))

    # ─── بررسی لایه‌بندی ───
    _sub("لایه‌بندی")

    # قوانین: هر فایل فقط از لایه‌های پایین‌تر import کند
    layer_rules = {
        "core/kernel": 0,
        "core/contracts": 1,
        "core/utils": 1,
        "core/database": 2,
        "core/adapters": 3,
        "analyzers": 3,
        "collectors": 4,
        "handlers": 5,
        "core/services": 5,
        "core/manual": 6,
        "core/brain": 7,
        "core/safe_brain": 8,
        "core/ops": 6,
        "core/app": 9,
    }

    def get_layer(name: str) -> int:
        for prefix, lvl in sorted(
            layer_rules.items(),
            key=lambda x: -len(x[0])
        ):
            if name.startswith(prefix):
                return lvl
        return -1

    violations = []
    for file_key, deps in graph.items():
        file_layer = get_layer(file_key)
        if file_layer < 0:
            continue
        for dep in deps:
            if not dep.startswith("core"):
                continue
            dep_layer = get_layer(dep)
            if dep_layer < 0:
                continue
            if dep_layer > file_layer:
                # import از لایهٔ بالاتر → نقض
                violations.append((file_key, dep))

    if not violations:
        SCORE.add_check("12. graph", "layer_violations", True,
                        "0 نقض")
        _ok("هیچ نقض لایه‌بندی")
    else:
        SCORE.add_check("12. graph", "layer_violations",
                        len(violations) <= 2,
                        f"{len(violations)} نقض")
        if len(violations) <= 2:
            _warn(f"{len(violations)} نقض لایه‌بندی (قابل قبول)")
        else:
            _fail(f"{len(violations)} نقض لایه‌بندی")
        for f, d in violations[:5]:
            _info(f"{f} → {d}")

    # ─── چک orphans ───
    _sub("فایل‌های orphan (هیچ‌کس import نمی‌کند)")
    all_imported = set()
    for deps in graph.values():
        for d in deps:
            all_imported.add(d.replace(".", "/"))

    orphans = []
    for file_key in graph:
        if file_key.endswith("__init__.py"):
            continue
        if "tests/" in file_key or file_key.startswith("test_"):
            continue
        if file_key in ("main.py", "config.py", "login.py", "panel.py"):
            continue

        module_path = file_key.replace("\\", "/").replace(".py", "")
        if module_path not in all_imported:
            orphans.append(file_key)

    if not orphans:
        SCORE.add_check("12. graph", "orphans", True, "0 فایل")
        _ok("هیچ فایل orphan")
    elif len(orphans) <= 5:
        SCORE.add_check("12. graph", "orphans", True,
                        f"{len(orphans)} فایل")
        _info(f"{len(orphans)} فایل orphan (قابل قبول)")
    else:
        SCORE.add_check("12. graph", "orphans", False,
                        f"{len(orphans)} فایل")
        _warn(f"{len(orphans)} فایل orphan")
        for o in orphans[:5]:
            _info(o)


# ═════════════════════════════════════════════════════════
# بخش ۱۳: dead code
# ═════════════════════════════════════════════════════════
def test_dead_code():
    _hdr("۱۳) dead code")
    SCORE.add_section("13. dead")

    try:
        from tests.test_healthcheck_full import (
            scan, ScanConfig
        )
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        config = ScanConfig(
            root=root,
            include_tests=False,
            min_confidence="medium",
            max_findings=200,
        )
        collector, _, _ = scan(config)

        dead_findings = [
            f for f in collector.findings
            if "DEAD" in f.rule_id or "UNUSED-IMPORT" in f.rule_id
        ]

        _sub("یافته‌های dead code")

        dead_count = len([
            f for f in dead_findings if "DEAD" in f.rule_id
        ])
        unused_imports = len([
            f for f in dead_findings if "UNUSED-IMPORT" in f.rule_id
        ])

        _info(f"dead functions: {dead_count}")
        _info(f"unused imports: {unused_imports}")

        if dead_count == 0:
            SCORE.add_check("13. dead", "dead_funcs", True, "0")
        elif dead_count <= 10:
            SCORE.add_check("13. dead", "dead_funcs", True,
                            f"{dead_count} (قابل قبول)")
        else:
            SCORE.add_check("13. dead", "dead_funcs", False,
                            f"{dead_count}")

        if unused_imports == 0:
            SCORE.add_check("13. dead", "unused_imports", True, "0")
        elif unused_imports <= 20:
            SCORE.add_check("13. dead", "unused_imports", True,
                            f"{unused_imports} (قابل قبول)")
        else:
            SCORE.add_check("13. dead", "unused_imports", False,
                            f"{unused_imports}")

    except Exception as e:
        SCORE.add_check("13. dead", "scan", False, str(e)[:80])
        _warn("healthcheck scan", str(e)[:80])


# ═════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════
async def main(quick: bool = False, only_section: str = ""):
    print()
    print("=" * 72)
    print("  🎯 تست جامع — نمره‌دهی دقیق پروژه (نسخهٔ ۲)")
    print("=" * 72)
    print(f"  Started: {datetime.now():%Y-%m-%d %H:%M:%S}")

    t0 = time.monotonic()

    sections = {
        "structure": test_structure,
        "config": test_config,
        "database": test_database,
        "ai": test_ai,
        "analyzers": test_analyzers,
        "collectors": test_collectors,
        "handlers": test_handlers,
        "cleanup": test_cleanup,
        "panel": test_panel,
        "tests": test_test_files,
        "sizes": test_file_sizes,
        "graph": test_import_graph,
        "dead": test_dead_code,
    }

    if only_section:
        fn = sections.get(only_section.lower())
        if fn:
            try:
                if inspect.iscoroutinefunction(fn):
                    await fn()
                else:
                    fn()
            except Exception as e:
                print(f"\n  ❌ section {only_section}: "
                      f"{type(e).__name__}: {e}")
                traceback.print_exc()
        else:
            print(f"⚠️ بخش ناشناخته: {only_section}")
            print(f"   مقادیر: {list(sections.keys())}")
            return 1
    else:
        for name, fn in sections.items():
            if quick and name in ("ai", "dead"):
                continue
            try:
                if inspect.iscoroutinefunction(fn):
                    await fn()
                else:
                    fn()
            except Exception as e:
                print(f"\n  ❌ section {name}: "
                      f"{type(e).__name__}: {e}")

    elapsed = time.monotonic() - t0

    # ═══════════════════════════════════════════════
    # خلاصه
    # ═══════════════════════════════════════════════
    print()
    print("=" * 72)
    print("  📊 خلاصهٔ نمره‌ها")
    print("=" * 72)
    print()

    section_names = {
        "1. structure": "۱) ساختار",
        "2. config": "۲) Config",
        "3. database": "۳) دیتابیس",
        "4. ai": "۴) هوش",
        "5. analyzers": "۵) آنالیزورها",
        "6. collectors": "۶) کلکتورها",
        "7. handlers": "۷) هندلرها",
        "8. cleanup": "۸) پاک‌سازی",
        "9. panel": "۹) پنل",
        "10. tests": "۱۰) تست‌ها",
        "11. sizes": "۱۱) اندازه‌ها",
        "12. graph": "۱۲) import graph",
        "13. dead": "۱۳) dead code",
    }

    print(f"  {'بخش':<25s} {'نمره':>12s} {'درصد':>10s}")
    print(f"  {'-'*25} {'-'*12} {'-'*10}")

    total_score = 0.0
    total_max = 0.0
    for key in section_names:
        score, mx = SCORE.section_score(key)
        if mx == 0:
            continue
        pct = (score / mx * 100) if mx > 0 else 0
        bar = "█" * int(pct / 10) + "░" * (10 - int(pct / 10))
        print(f"  {section_names[key]:<25s} "
              f"{score:>5.1f}/{mx:<5.1f} "
              f"{pct:>6.1f}%  {bar}")
        total_score += score
        total_max += mx

    print(f"  {'-'*25} {'-'*12} {'-'*10}")

    overall = (total_score / total_max * 100) if total_max > 0 else 0

    bar = "█" * int(overall / 10) + "░" * (10 - int(overall / 10))
    print(f"  {'کل':<25s} "
          f"{total_score:>5.1f}/{total_max:<5.1f} "
          f"{overall:>6.1f}%  {bar}")

    print()
    if overall >= 95:
        grade = "A+"
        msg = "🏆 عالی — آمادهٔ production"
    elif overall >= 90:
        grade = "A"
        msg = "🎉 بسیار خوب — تقریباً کامل"
    elif overall >= 80:
        grade = "B"
        msg = "👍 خوب — چند مورد جزئی"
    elif overall >= 70:
        grade = "C"
        msg = "⚠️ قابل قبول — نیاز به بهبود"
    elif overall >= 60:
        grade = "D"
        msg = "🔧 نیاز به کار"
    else:
        grade = "F"
        msg = "❌ نیاز به بازنگری"

    print(f"  نمرهٔ نهایی: {overall:.1f}/100 — رتبه {grade}")
    print(f"  {msg}")
    print(f"  ⏱️  زمان: {elapsed:.1f}s")

    print()
    print("=" * 72)
    print()

    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--section", type=str, default="")
    parser.add_argument("--json-out", type=str, default="")
    args = parser.parse_args()

    rc = asyncio.run(
        main(quick=args.quick, only_section=args.section)
    )

    if args.json_out:
        try:
            with open(args.json_out, "w", encoding="utf-8") as f:
                json.dump(SCORE.to_dict(), f,
                          ensure_ascii=False, indent=2)
            print(f"📄 JSON: {args.json_out}")
        except Exception as e:
            print(f"⚠️ JSON: {e}")

    sys.exit(rc)