"""
تست‌های صفر تا صد — AI فقط DeepSeek.

چک می‌کند:
  ✅ AI_PRIORITY=deepseek
  ✅ Ollama غیرفعال است
  ✅ DeepSeek API کار می‌کند
  ✅ Fallback به Ollama نمی‌رود
  ✅ Brain کامل فعال است

اجرا:
    python -m pytest tests/test_ai_deepseek_only.py -v
    # یا
    python tests/test_ai_deepseek_only.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

# ─── اضافه کردن root به path ───
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ═══════════════════════════════════════════
# ابزار تست
# ═══════════════════════════════════════════
class TestRunner:
    """Runner ساده با آمار."""

    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors: list[str] = []

    def ok(self, name: str, msg: str = "") -> None:
        self.passed += 1
        print(f"  ✅ {name}" + (f" — {msg}" if msg else ""))

    def fail(self, name: str, reason: str) -> None:
        self.failed += 1
        self.errors.append(f"{name}: {reason}")
        print(f"  ❌ {name} — {reason}")

    def warn(self, name: str, msg: str) -> None:
        print(f"  ⚠️  {name} — {msg}")

    def section(self, title: str) -> None:
        print()
        print(f"╭─ {title} " + "─" * (60 - len(title)))

    def end_section(self) -> None:
        print("╰" + "─" * 60)

    def summary(self) -> int:
        print()
        print("=" * 62)
        total = self.passed + self.failed
        if self.failed == 0:
            print(f"🎉 همه {total} تست PASS شد!")
        else:
            print(
                f"📊 نتیجه: {self.passed}/{total} pass | "
                f"{self.failed} fail"
            )
            print()
            print("❌ خطاها:")
            for err in self.errors:
                print(f"   • {err}")
        print("=" * 62)
        return 0 if self.failed == 0 else 1


runner = TestRunner()


# ═══════════════════════════════════════════
# تست ۱: config
# ═══════════════════════════════════════════
def test_config_env() -> None:
    """چک می‌کند config درست بار شده."""
    runner.section("۱) config و env")

    try:
        from config import (
            AI_PRIORITY,
            AI_ENABLED,
            AI,
            FULL_ACCESS,
        )
        runner.ok("import config", "موفق")
    except Exception as e:
        runner.fail("import config", f"{type(e).__name__}: {e}")
        return

    # ─── AI_PRIORITY ───
    if AI_PRIORITY == "deepseek":
        runner.ok("AI_PRIORITY", "deepseek ✅")
    else:
        runner.fail(
            "AI_PRIORITY",
            f"انتظار: deepseek | واقعی: {AI_PRIORITY}",
        )

    # ─── AI_ENABLED ───
    if AI_ENABLED:
        runner.ok("AI_ENABLED", "true")
    else:
        runner.fail("AI_ENABLED", "false است")

    # ─── DeepSeek key ───
    ds_key = (AI or {}).get("deepseek_api_key", "")
    if ds_key and len(ds_key) > 10:
        runner.ok(
            "DEEPSEEK_API_KEY",
            f"موجود ({len(ds_key)} char)",
        )
    else:
        runner.fail(
            "DEEPSEEK_API_KEY",
            "خالی یا کوتاه — در .env تنظیم کن",
        )

    # ─── Full Access ───
    if FULL_ACCESS:
        runner.ok("FULL_ACCESS", "true")
    else:
        runner.warn("FULL_ACCESS", "false — Brain کامل نیست")

    runner.end_section()


# ═══════════════════════════════════════════
# تست ۲: AIAssistant — Ollama غیرفعال
# ═══════════════════════════════════════════
def test_ai_assistant_providers() -> None:
    """چک می‌کند فقط DeepSeek در لیست providerها هست."""
    runner.section("۲) AIAssistant — provider order")

    try:
        from core.services.ai_assistant import (
            get_ai_assistant,
            reset_ai_assistant,
        )
        reset_ai_assistant()
        ai = get_ai_assistant()
        runner.ok("import AIAssistant", "موفق")
    except Exception as e:
        runner.fail("import AIAssistant", f"{type(e).__name__}: {e}")
        return

    # ─── لیست providerها ───
    providers = ai._provider_order()
    runner.ok("provider_order", f"{providers}")

    if providers == ["deepseek"]:
        runner.ok("فقط DeepSeek", "✅ درست")
    elif "ollama" in providers:
        runner.fail(
            "Ollama هنوز در لیست",
            f"providers={providers}",
        )
    else:
        runner.fail("provider_order", f"غیرمنتظره: {providers}")

    # ─── priority ───
    if ai._priority == "deepseek":
        runner.ok("priority", "deepseek")
    else:
        runner.fail("priority", f"واقعی: {ai._priority}")

    # ─── DeepSeek key در assistant ───
    if ai._deepseek_key:
        runner.ok(
            "DeepSeek key",
            f"موجود ({ai._deepseek_key_source})",
        )
    else:
        runner.fail("DeepSeek key", "خالی")

    runner.end_section()


# ═══════════════════════════════════════════
# تست ۳: DeepSeek API — واقعی
# ═══════════════════════════════════════════
async def test_deepseek_api_real() -> None:
    """تماس واقعی با DeepSeek API."""
    runner.section("۳) DeepSeek API — تماس واقعی")

    try:
        from core.services.ai_assistant import get_ai_assistant
        ai = get_ai_assistant()
    except Exception as e:
        runner.fail("import", f"{type(e).__name__}: {e}")
        return

    if not ai._deepseek_key:
        runner.fail("تست DeepSeek", "کلید خالی")
        return

    # ─── available ───
    t0 = time.monotonic()
    try:
        available = await asyncio.wait_for(
            ai.available(), timeout=10
        )
        elapsed = (time.monotonic() - t0) * 1000
        if available:
            runner.ok("available()", f"{elapsed:.0f}ms")
        else:
            runner.fail("available()", "False برگشت")
            return
    except Exception as e:
        runner.fail("available()", f"{type(e).__name__}: {e}")
        return

    # ─── chat واقعی ───
    t0 = time.monotonic()
    try:
        reply, ok = await asyncio.wait_for(
            ai.chat_with_status("سلام، ۱+۱ چند است؟"),
            timeout=60,
        )
        elapsed = (time.monotonic() - t0) * 1000

        if ok and reply and len(reply) > 0:
            runner.ok(
                "chat واقعی",
                f"{elapsed:.0f}ms | پاسخ: {reply[:60]}",
            )
        else:
            runner.fail(
                "chat واقعی",
                f"ok={ok} | reply={reply[:100] if reply else 'خالی'}",
            )
    except asyncio.TimeoutError:
        runner.fail("chat واقعی", "timeout 60s")
    except Exception as e:
        runner.fail("chat واقعی", f"{type(e).__name__}: {e}")

    runner.end_section()


# ═══════════════════════════════════════════
# تست ۴: Brain — TOOL_MAP
# ═══════════════════════════════════════════
def test_brain_tool_map() -> None:
    """چک می‌کند همه ابزارها در TOOL_MAP ثبت شده‌اند."""
    runner.section("۴) Brain — TOOL_MAP")

    try:
        from core.brain.main import _TOOL_METHODS, Brain
        runner.ok(
            "_TOOL_METHODS import",
            f"{len(_TOOL_METHODS)} ابزار",
        )
    except Exception as e:
        runner.fail("import _TOOL_METHODS", f"{type(e).__name__}: {e}")
        return

    # ─── ۳ ابزار جدید ───
    new_tools = [
        "diagnose_platform",
        "run_platform_as_user",
        "list_all_platforms",
    ]

    for tool in new_tools:
        if tool in _TOOL_METHODS:
            method_name = _TOOL_METHODS[tool]
            if hasattr(Brain, method_name):
                runner.ok(f"ابزار «{tool}»", f"→ {method_name}")
            else:
                runner.fail(
                    f"ابزار «{tool}»",
                    f"متد {method_name} در Brain نیست",
                )
        else:
            runner.fail(
                f"ابزار «{tool}»",
                "در _TOOL_METHODS ثبت نشده",
            )

    # ─── TOOL_MAP واقعی ───
    try:
        tool_map = Brain.TOOL_MAP
        runner.ok(
            "Brain.TOOL_MAP",
            f"{len(tool_map)} ابزار ثبت‌شده",
        )

        for tool in new_tools:
            if tool in tool_map:
                runner.ok(f"TOOL_MAP[{tool}]", "موجود")
            else:
                runner.fail(
                    f"TOOL_MAP[{tool}]",
                    "در TOOL_MAP نیست",
                )
    except Exception as e:
        runner.fail("Brain.TOOL_MAP", f"{type(e).__name__}: {e}")

    runner.end_section()


# ═══════════════════════════════════════════
# تست ۵: tools_meta — ALLOWED_TOOLS
# ═══════════════════════════════════════════
def test_tools_meta() -> None:
    """چک می‌کند ALLOWED_TOOLS و TOOL_DEFINITIONS درست است."""
    runner.section("۵) tools_meta — ALLOWED + DEFINITIONS")

    try:
        from core.brain.tools_meta import (
            ALLOWED_TOOLS,
            TOOL_DEFINITIONS,
        )
        runner.ok(
            "import",
            f"allowed={len(ALLOWED_TOOLS)} | "
            f"definitions={len(TOOL_DEFINITIONS)}",
        )
    except Exception as e:
        runner.fail("import tools_meta", f"{type(e).__name__}: {e}")
        return

    new_tools = {
        "diagnose_platform": "low",
        "run_platform_as_user": "medium",
        "list_all_platforms": "low",
    }

    # ─── در ALLOWED_TOOLS ───
    for tool, expected_risk in new_tools.items():
        if tool in ALLOWED_TOOLS:
            info = ALLOWED_TOOLS[tool]
            risk = info.get("risk")
            if risk == expected_risk:
                runner.ok(
                    f"ALLOWED_TOOLS[{tool}]",
                    f"risk={risk}",
                )
            else:
                runner.fail(
                    f"ALLOWED_TOOLS[{tool}]",
                    f"risk انتظار={expected_risk} | "
                    f"واقعی={risk}",
                )
        else:
            runner.fail(
                f"ALLOWED_TOOLS[{tool}]",
                "ثبت نشده",
            )

    # ─── در TOOL_DEFINITIONS ───
    defined_names = {
        t.get("function", {}).get("name")
        for t in TOOL_DEFINITIONS
    }

    for tool in new_tools.keys():
        if tool in defined_names:
            runner.ok(f"TOOL_DEFINITIONS[{tool}]", "تعریف شده")
        else:
            runner.fail(
                f"TOOL_DEFINITIONS[{tool}]",
                "تعریف نشده",
            )

    runner.end_section()


# ═══════════════════════════════════════════
# تست ۶: mixin_platforms — متدهای جدید
# ═══════════════════════════════════════════
def test_mixin_platforms() -> None:
    """چک می‌کند متدهای جدید در PlatformMixin هستند."""
    runner.section("۶) mixin_platforms — متدهای جدید")

    try:
        from core.brain.mixin_platforms import (
            PlatformMixin,
            PlatformManager,
        )
        runner.ok("import PlatformMixin", "موفق")
    except Exception as e:
        runner.fail("import", f"{type(e).__name__}: {e}")
        return

    # ─── متدهای جدید ───
    new_methods = [
        "_diagnose_platform",
        "_run_platform_as_user",
        "_start_as_user",
        "_stop_as_user",
        "_run_once_as_user",
        "_status_as_user",
        "_list_all_platforms",
    ]

    for method in new_methods:
        if hasattr(PlatformMixin, method):
            runner.ok(f"متد «{method}»", "موجود")
        else:
            runner.fail(f"متد «{method}»", "نیست")

    runner.end_section()


# ═══════════════════════════════════════════
# تست ۷: diagnose_platform — واقعی
# ═══════════════════════════════════════════
async def test_diagnose_platform_real() -> None:
    """تماس واقعی با diagnose_platform."""
    runner.section("۷) diagnose_platform — تماس واقعی")

    # ─── ساخت Brain سبک بدون AI ───
    try:
        from core.brain.main import Brain
        brain = Brain(
            read_only=False,
            full_access=False,
        )
        runner.ok("Brain ساخت", "موفق")
    except Exception as e:
        runner.fail("Brain ساخت", f"{type(e).__name__}: {e}")
        return

    # ─── تست diagnose برای bale ───
    for platform in ["bale", "eitaa", "whatsapp"]:
        try:
            t0 = time.monotonic()
            result = await asyncio.wait_for(
                brain._diagnose_platform(platform),
                timeout=30,
            )
            elapsed = (time.monotonic() - t0) * 1000

            if result and "ریشه‌یابی" in result:
                runner.ok(
                    f"diagnose({platform})",
                    f"{elapsed:.0f}ms | {len(result)} char",
                )
                print(f"     ─── خروجی ───")
                for line in result.split("\n")[:8]:
                    print(f"     {line}")
            else:
                runner.fail(
                    f"diagnose({platform})",
                    f"خروجی نامعتبر: {result[:100] if result else 'خالی'}",
                )
        except asyncio.TimeoutError:
            runner.fail(f"diagnose({platform})", "timeout 30s")
        except Exception as e:
            runner.fail(
                f"diagnose({platform})",
                f"{type(e).__name__}: {e}",
            )

    # ─── پلتفرم نامعتبر ───
    try:
        result = await brain._diagnose_platform("blah")
        if "نامعتبر" in result or "ناشناخته" in result:
            runner.ok("diagnose پلتفرم نامعتبر", "به درستی رد شد")
        else:
            runner.fail(
                "diagnose پلتفرم نامعتبر",
                f"باید رد کند: {result[:80]}",
            )
    except Exception as e:
        runner.fail("diagnose نامعتبر", f"{type(e).__name__}: {e}")

    runner.end_section()


# ═══════════════════════════════════════════
# تست ۸: list_all_platforms — واقعی
# ═══════════════════════════════════════════
async def test_list_all_platforms() -> None:
    """تست list_all_platforms."""
    runner.section("۸) list_all_platforms")

    try:
        from core.brain.main import Brain
        brain = Brain(read_only=False, full_access=False)
    except Exception as e:
        runner.fail("Brain ساخت", f"{type(e).__name__}: {e}")
        return

    try:
        result = await brain._list_all_platforms()
        if result and "پلتفرم" in result:
            lines = result.split("\n")
            runner.ok(
                "list_all_platforms",
                f"{len(lines)} خط",
            )
            for line in lines[:6]:
                print(f"     {line}")
        else:
            runner.fail(
                "list_all_platforms",
                f"خروجی نامعتبر: {result[:80] if result else 'خالی'}",
            )
    except Exception as e:
        runner.fail(
            "list_all_platforms",
            f"{type(e).__name__}: {e}",
        )

    runner.end_section()


# ═══════════════════════════════════════════
# تست ۹: SafeBrain — rate limit + memory
# ═══════════════════════════════════════════
def test_safe_brain() -> None:
    """تست SafeBrain با api_key."""
    runner.section("۹) SafeBrain")

    try:
        from core.safe_brain import get_safe_brain
        from config import AI as _AI

        api_key = (_AI or {}).get("deepseek_api_key", "")
        sb = get_safe_brain(api_key=api_key)
        runner.ok("SafeBrain ساخت", "موفق")
    except Exception as e:
        runner.fail("SafeBrain", f"{type(e).__name__}: {e}")
        return

    # ─── api_key ───
    if sb.api_key:
        runner.ok("api_key", f"{len(sb.api_key)} char")
    else:
        runner.fail("api_key", "خالی")

    # ─── persistent memory ───
    if sb.persistent_memory:
        try:
            count = sb.persistent_memory.count()
            runner.ok("PersistentMemory", f"{count} رکورد")
        except Exception as e:
            runner.fail("PersistentMemory", f"{e}")
    else:
        runner.warn("PersistentMemory", "غیرفعال")

    runner.end_section()


# ═══════════════════════════════════════════
# تست ۱۰: Domain Guard
# ═══════════════════════════════════════════
def test_domain_guard() -> None:
    """تست DomainGuard."""
    runner.section("۱۰) DomainGuard")

    try:
        from handlers.admin_handler import DomainGuard
        dg = DomainGuard()
        runner.ok("DomainGuard ساخت", "موفق")
    except Exception as e:
        runner.fail("DomainGuard", f"{type(e).__name__}: {e}")
        return

    # ─── in-domain ───
    in_domain_tests = [
        "وضعیت ربات",
        "چند سرنخ",
        "کلیدواژه‌ها",
        "گزارش",
    ]
    for text in in_domain_tests:
        if dg.is_in_domain(text):
            runner.ok(f"in-domain: «{text}»", "✅")
        else:
            runner.fail(f"in-domain: «{text}»", "باید True باشد")

    # ─── out-of-domain ───
    out_domain_tests = [
        "هوا چطوره",
        "دلار چند شد",
        "فوتبال",
    ]
    for text in out_domain_tests:
        if not dg.is_in_domain(text):
            runner.ok(f"out-domain: «{text}»", "✅")
        else:
            runner.fail(f"out-domain: «{text}»", "باید False باشد")

    runner.end_section()


# ═══════════════════════════════════════════
# تست ۱۱: ManualBrain — rules
# ═══════════════════════════════════════════
def test_manual_brain_rules() -> None:
    """تست ManualBrain و rules.json."""
    runner.section("۱۱) ManualBrain — قوانین")

    try:
        from core.manual.brain import get_manual_brain
        from data import paths

        mb = get_manual_brain(str(paths.DB_FILE))
        runner.ok("ManualBrain ساخت", "موفق")

        rules_count = len(mb.core_rules)
        runner.ok("core_rules", f"{rules_count} قانون")

        learned_count = len(mb.learned_rules)
        runner.ok("learned_rules", f"{learned_count} قانون")

        candidates_count = len(mb._candidates)
        if candidates_count > 5:
            runner.warn(
                "candidate_rules",
                f"{candidates_count} — پاک‌سازی توصیه می‌شود",
            )
        else:
            runner.ok(
                "candidate_rules",
                f"{candidates_count}",
            )
    except Exception as e:
        runner.fail("ManualBrain", f"{type(e).__name__}: {e}")

    runner.end_section()


# ═══════════════════════════════════════════
# تست ۱۲: KeywordFinder + PriorityFinder
# ═══════════════════════════════════════════
def test_analyzers() -> None:
    """تست آنالیزورها."""
    runner.section("۱۲) آنالیزورها")

    # ─── KeywordFinder ───
    try:
        from analyzers.keyword_finder import (
            get_keyword_finder,
        )
        kf = get_keyword_finder()
        count = len(kf.keywords)
        runner.ok("KeywordFinder", f"{count} کلیدواژه")

        # تست match
        test_text = "کود مایع یاراترا"
        matches = kf.match(test_text)
        if matches:
            runner.ok(
                "match «کود مایع»",
                f"{len(matches)} تطابق",
            )
        else:
            runner.warn(
                "match «کود مایع»",
                "بدون تطابق (شاید محصول نیست)",
            )
    except Exception as e:
        runner.fail("KeywordFinder", f"{type(e).__name__}: {e}")

    # ─── PriorityFinder ───
    try:
        from analyzers.priority_finder import (
            get_priority_finder,
        )
        pf = get_priority_finder()
        stats = pf.get_stats()
        runner.ok(
            "PriorityFinder",
            f"T1={stats['t1_words']} T2={stats['t2_words']}",
        )
    except Exception as e:
        runner.fail("PriorityFinder", f"{type(e).__name__}: {e}")

    # ─── LeadScorer ───
    try:
        from analyzers.lead_scorer import get_lead_scorer
        ls = get_lead_scorer()
        score, level, signals = ls.score(
            "قیمت کود مایع چند؟ می‌خرم فوری"
        )
        runner.ok(
            "LeadScorer",
            f"score={score} level={level} signals={signals}",
        )
        if score > 0:
            runner.ok("score > 0", "✅")
        else:
            runner.fail("score > 0", f"score={score}")
    except Exception as e:
        runner.fail("LeadScorer", f"{type(e).__name__}: {e}")

    runner.end_section()


# ═══════════════════════════════════════════
# تست ۱۳: Database
# ═══════════════════════════════════════════
async def test_database() -> None:
    """تست دیتابیس."""
    runner.section("۱۳) دیتابیس")

    try:
        from core.database.factory import create_database
        db = create_database()
        await db.init()
        runner.ok("دیتابیس ساخت", "موفق")

        # ─── total count ───
        try:
            counts = await db.get_total_count()
            runner.ok(
                "get_total_count",
                f"messages={counts.get('messages', 0)} "
                f"leads={counts.get('leads', 0)}",
            )
        except Exception as e:
            runner.fail("get_total_count", f"{e}")

        await db.close()
        runner.ok("دیتابیس بسته شد", "✅")
    except Exception as e:
        runner.fail("دیتابیس", f"{type(e).__name__}: {e}")

    runner.end_section()


# ═══════════════════════════════════════════
# تست ۱۴: Puzzle — بررسی مشکل قبلی
# ═══════════════════════════════════════════
async def test_by_platform_query() -> None:
    """تست query که قبلاً خطا داشت."""
    runner.section("۱۴) query مشکل‌دار — by_platform")

    try:
        from core.manual.brain import get_manual_brain
        from data import paths

        mb = get_manual_brain(str(paths.DB_FILE))

        t0 = time.monotonic()
        try:
            result = await mb.process("تفکیک پلتفرم")
            elapsed = (time.monotonic() - t0) * 1000

            if result and "خطا" not in str(result).lower():
                runner.ok(
                    "by_platform",
                    f"{elapsed:.0f}ms | {str(result)[:60]}",
                )
            else:
                runner.fail(
                    "by_platform",
                    f"خطا در نتیجه: {str(result)[:100]}",
                )
        except Exception as e:
            runner.fail("by_platform", f"{type(e).__name__}: {e}")
    except Exception as e:
        runner.fail("setup", f"{type(e).__name__}: {e}")

    runner.end_section()
    

# ═══════════════════════════════════════════
# تست ۱۵: Persona و System Prompt
# ═══════════════════════════════════════════
def test_persona() -> None:
    """تست persona و system prompt."""
    runner.section("۱۵) Persona و Prompt")

    try:
        from config import (
            AI_PERSONA,
            AI_SYSTEM_PROMPT,
            AI_MODE_PROMPTS,
        )
        runner.ok("import persona", "موفق")

        # ─── persona ───
        if AI_PERSONA and AI_PERSONA.get("name"):
            runner.ok(
                "persona.name",
                AI_PERSONA.get("name"),
            )
        else:
            runner.fail("persona.name", "خالی")

        # ─── system prompt ───
        if AI_SYSTEM_PROMPT and len(AI_SYSTEM_PROMPT) > 50:
            runner.ok(
                "system_prompt",
                f"{len(AI_SYSTEM_PROMPT)} char",
            )
        else:
            runner.fail(
                "system_prompt",
                f"کوتاه: {len(AI_SYSTEM_PROMPT)}",
            )

        # ─── mode prompts ───
        if AI_MODE_PROMPTS:
            runner.ok(
                "mode_prompts",
                f"{len(AI_MODE_PROMPTS)} حالت",
            )
        else:
            runner.warn("mode_prompts", "خالی")
    except Exception as e:
        runner.fail("persona", f"{type(e).__name__}: {e}")

    runner.end_section()


# ═══════════════════════════════════════════
# Main
# ═══════════════════════════════════════════
async def main() -> int:
    print("=" * 62)
    print("🧪 تست جامع صفر تا صد — AI + Brain + Platform")
    print("=" * 62)

    # ─── تست‌های sync ───
    test_config_env()
    test_ai_assistant_providers()
    test_brain_tool_map()
    test_tools_meta()
    test_mixin_platforms()
    test_safe_brain()
    test_domain_guard()
    test_manual_brain_rules()
    test_analyzers()
    test_persona()
    await test_by_platform_query()

    # ─── تست‌های async ───
    await test_deepseek_api_real()
    await test_diagnose_platform_real()
    await test_list_all_platforms()
    await test_database()

    return runner.summary()


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)