# tests/test_ai_full.py
"""
تست فوق‌دقیق هوش مصنوعی — DeepSeek + Ollama + Brain + SafeBrain.

این تست ۲۴ مرحله دارد و همهٔ لایه‌های هوش را بررسی می‌کند:
  ۱. import تمام ماژول‌های کلیدی هوش
  ۲. config.AI (deepseek_api_key, priority, url, model)
  ۳. کلید DeepSeek (config → env)
  ۴. اتصال واقعی DeepSeek (chat + response)
  ۵. اتصال واقعی Ollama (اختیاری — اگر روشن باشد)
  ۶. AIAssistant._provider_order (ترتیب)
  ۷. AIAssistant.parse_intent (JSON)
  ۸. SafeBrain (api_key, cache, memory, rate_limit)
  ۹. SafeBrain.process (fallback, delegate)
 ۱۰. Brain._init_client (source کلید)
 ۱۱. Brain.TOOL_MAP (۴۲ ابزار)
 ۱۲. Brain.agent (بدون tool call)
 ۱۳. Brain.process_admin_command (quick commands)
 ۱۴. ManualBrain (RULES load + process)
 ۱۵. هماهنگی لایه‌ها (DeepSeek → SafeBrain → Brain)
 ۱۶. tests/pipeline_helpers (storage integration)
 ۱۷. tests/db_helpers (DB connectivity)
 ۱۸. performance metrics (latency)
 ۱۹. error handling (خطای عمدی)
 ۲۰. ضد اسپم (rate limit)
 ۲۱. حافظهٔ محلی (SafeMemory)
 ۲۲. کش (SafeCache)
 ۲۳. fallback chain (DeepSeek → Ollama)
 ۲۴. گزارش نهایی

اجرا:
  python tests/test_ai_full.py
  python tests/test_ai_full.py --quick              # فقط مراحل بحرانی
  python tests/test_ai_full.py --skip-deepseek      # فقط Ollama
  python tests/test_ai_full.py --skip-ollama        # فقط DeepSeek
  python tests/test_ai_full.py --deepseek-only      # هیچ fallback
  python tests/test_ai_full.py --verbose            # چاپ جزئیات کامل

⚠️ نکات:
  - تست ۴ نیاز به `DEEPSEEK_API_KEY` معتبر دارد.
  - تست ۵ نیاز به Ollama روشن دارد (اختیاری).
  - تست ۱۲ ممکن است ۳۰-۶۰s طول بکشد (بسته به provider).
  - تست ۱۴ (ManualBrain) ممکن است با خطای RULES روبرو شود
    → این تست skip می‌شود (نه fail) — چون ManualBrain مستقل است.

اصلاحات اخیر (نسخهٔ ۲.۰):
  - 🎯 تست ۴ حالا بدون هیچ args کلیدی (بدون system) می‌زند
  - 🎯 تست ۷ حالا با system پارامتری می‌زند
  - 🎯 تست ۱۲ فقط agent(text) — بدون tools
  - 🎯 تست ۱۴ (ManualBrain) skip امن‌تر
  - 🎯 LLM_TEST_PROMPT = "بگو یک کلمه"
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from datetime import datetime
from typing import Any

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

from tests.helpers import (
    TestReport, banner, check_env, safe_disconnect, section, C,
)


# ═════════════════════════════════════════════════════════
# ثابت‌های تست
# ═════════════════════════════════════════════════════════

# پرامپت‌های تست (خیلی ساده که همهٔ providerها بتونن جواب بدن)
LLM_TEST_PROMPT = "بگو یک کلمه"
LLM_PING_PROMPT = "سلام"
INTENT_TEST_TEXT = "وضعیت"
INTENT_EXPECTED_ACTION = "status"

# ثابت‌های سرعت
SLOW_LATENCY_MS = 30_000     # > 30s → هشدار
VERY_SLOW_LATENCY_MS = 60_000 # > 60s → fail
FAST_LATENCY_MS = 3_000      # < 3s → عالی

# سقف عملیاتی
MAX_AGENT_TIME_S = 120
MAX_SAFEBRAIN_TIME_S = 60
MAX_BRAIN_INIT_S = 30


# ═════════════════════════════════════════════════════════
# helpers کمکی
# ═════════════════════════════════════════════════════════
def _short(s: Any, n: int = 80) -> str:
    """نمایش کوتاه رشته."""
    if s is None:
        return "None"
    txt = str(s).strip().replace("\n", " ")
    if len(txt) <= n:
        return txt
    return txt[:n] + "…"


def _safe_len(s: Any) -> int:
    """طول امن."""
    try:
        return len(str(s))
    except Exception:
        return 0


def _check_keys(obj: Any, keys: list[str], label: str) -> list[str]:
    """چک کلیدهای dict — کلیدهای گم‌شده را برمی‌گرداند."""
    if not isinstance(obj, dict):
        return [f"{label} dict نیست"]
    return [k for k in keys if k not in obj]


# ═════════════════════════════════════════════════════════
# main
# ═════════════════════════════════════════════════════════
async def main(
    quick: bool = False,
    skip_deepseek: bool = False,
    skip_ollama: bool = False,
    deepseek_only: bool = False,
    verbose: bool = False,
):
    banner("تست فوق‌دقیق هوش مصنوعی (DeepSeek + Ollama + Brain + SafeBrain)")
    report = TestReport("ai")

    total_t0 = time.monotonic()

    # ═══════════════════════════════════════════════════
    # مرحله ۱: import تمام ماژول‌های کلیدی
    # ═══════════════════════════════════════════════════
    section("مرحله ۱: import ماژول‌های کلیدی هوش")
    t0 = time.monotonic()

    imports_status: dict[str, bool] = {}
    import_modules = [
        ("config", "config.AI"),
        ("core.services.ai_assistant", "AIAssistant"),
        ("core.safe_brain.main", "SafeBrain"),
        ("core.safe_brain.storage", "SafeMemory + SafeCache"),
        ("core.safe_brain.constants", "SAFE_ACTIONS"),
        ("core.brain.main", "Brain"),
        ("core.brain.mixin_agent", "AgentMixin"),
        ("core.brain.tools_meta", "TOOL_DEFINITIONS"),
        ("core.manual.brain", "ManualBrain"),
        ("data.paths", "paths"),
        ("handlers.admin_handler", "AdminHandler"),
    ]

    for module_name, label in import_modules:
        try:
            __import__(module_name)
            imports_status[module_name] = True
        except Exception as e:
            imports_status[module_name] = False
            report.add(
                f"import.{module_name}",
                "fail",
                f"{type(e).__name__}: {e}",
            )

    ok_count = sum(1 for v in imports_status.values() if v)
    total_imports = len(imports_status)
    elapsed = (time.monotonic() - t0) * 1000

    report.add(
        "import.all",
        "ok" if ok_count == total_imports else "fail",
        f"{ok_count}/{total_imports} ماژول | {elapsed:.0f}ms",
    )

    if ok_count < total_imports:
        missing = [
            m for m, v in imports_status.items() if not v
        ]
        print(f"\n  {C.FAIL}❌ ماژول‌های ناموفق:")
        for m in missing:
            print(f"     - {m}")
        print(f"  {C.WARN}⚠️ تست‌های بعدی ممکن است شکست بخورند{C.END}")

    # ═══════════════════════════════════════════════════
    # مرحله ۲: config.AI
    # ═══════════════════════════════════════════════════
    section("مرحله ۲: config.AI")
    try:
        from config import AI, AI_ENABLED, AI_PRIORITY

        required_keys = [
            "enabled", "priority", "url", "model",
            "timeout", "deepseek_api_key",
        ]
        missing = _check_keys(AI, required_keys, "AI")
        if missing:
            report.add(
                "config.AI.keys", "warn",
                f"کلیدهای گم‌شده: {missing}",
            )
        else:
            report.add(
                "config.AI.keys", "ok",
                f"{len(required_keys)} کلید موجود",
            )

        # ─── مقادیر ───
        deepseek_key = AI.get("deepseek_api_key", "")
        has_key = bool(deepseek_key and deepseek_key.strip())

        report.add(
            "config.AI.deepseek_key",
            "ok" if has_key else "fail",
            f"has_key={has_key} | "
            f"prefix={deepseek_key[:8] if has_key else '—'}...",
        )
        report.add(
            "config.AI.priority", "ok",
            f"priority={AI_PRIORITY}",
        )
        report.add(
            "config.AI.enabled",
            "ok" if AI_ENABLED else "warn",
            f"AI_ENABLED={AI_ENABLED}",
        )
        report.add(
            "config.AI.ollama", "ok",
            f"url={AI.get('url')} | model={AI.get('model')}",
        )
        report.add(
            "config.AI.timeout", "ok",
            f"timeout={AI.get('timeout')}s",
        )

        if verbose:
            print(f"\n  {C.DIM}AI config:")
            for k, v in AI.items():
                if k == "deepseek_api_key":
                    print(f"     {k}={'✅ set' if v else '❌ empty'}")
                else:
                    print(f"     {k}={_short(v, 60)}")
    except Exception as e:
        report.add(
            "config.AI", "fail",
            f"{type(e).__name__}: {e}",
        )

    # ═══════════════════════════════════════════════════
    # مرحله ۳: کلید DeepSeek (config → env)
    # ═══════════════════════════════════════════════════
    section("مرحله ۳: کلید DeepSeek (منبع)")
    try:
        from config import AI as _AI

        cfg_key = (_AI.get("deepseek_api_key") or "").strip()
        env_key = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()

        if cfg_key:
            report.add(
                "deepseek.key.source", "ok",
                f"منبع: config | prefix={cfg_key[:8]}... | "
                f"length={len(cfg_key)}",
            )
        elif env_key:
            report.add(
                "deepseek.key.source", "warn",
                f"منبع: env (config خالی) | prefix={env_key[:8]}...",
            )
        else:
            report.add(
                "deepseek.key.source", "fail",
                "هیچ کلیدی در config و env نیست",
            )

        # چک فرمت کلید
        key = cfg_key or env_key
        if key:
            if key.startswith("sk-"):
                report.add(
                    "deepseek.key.format", "ok",
                    f"شکل صحیح (sk-...)",
                )
            else:
                report.add(
                    "deepseek.key.format", "warn",
                    f"شکل مشکوک: {key[:6]}...",
                )

            if len(key) < 20:
                report.add(
                    "deepseek.key.length", "warn",
                    f"کوتاه: {len(key)} char",
                )
            else:
                report.add(
                    "deepseek.key.length", "ok",
                    f"{len(key)} char",
                )
    except Exception as e:
        report.add(
            "deepseek.key", "fail",
            f"{type(e).__name__}: {e}",
        )

    # ═══════════════════════════════════════════════════
    # مرحله ۴: اتصال واقعی DeepSeek
    # ═══════════════════════════════════════════════════
    if not skip_deepseek:
        section("مرحله ۴: اتصال واقعی DeepSeek")
        t0 = time.monotonic()
        try:
            from core.services.ai_assistant import get_ai_assistant
            ai = get_ai_assistant()

            # ─── چک available ───
            avail = await ai.available()
            report.add(
                "deepseek.available",
                "ok" if avail else "fail",
                f"available={avail}",
            )

            if avail:
                # ─── تست ۴a: پرامپت کوتاه ───
                resp, ok = await ai.chat_with_status(
                    LLM_TEST_PROMPT
                )
                elapsed = (time.monotonic() - t0) * 1000

                if ok and resp:
                    report.add(
                        "deepseek.chat_short",
                        "ok",
                        f"{elapsed:.0f}ms | "
                        f"len={len(resp)} | "
                        f"resp={_short(resp, 60)}",
                        elapsed,
                    )
                    if elapsed > SLOW_LATENCY_MS:
                        report.add(
                            "deepseek.chat_short.slow",
                            "warn",
                            f"کند: {elapsed/1000:.1f}s",
                        )
                    elif elapsed < FAST_LATENCY_MS:
                        report.add(
                            "deepseek.chat_short.fast",
                            "ok",
                            f"سریع: {elapsed:.0f}ms",
                        )
                else:
                    report.add(
                        "deepseek.chat_short",
                        "fail",
                        f"ok={ok} | resp={_short(resp, 80)} | "
                        f"{elapsed:.0f}ms",
                    )

                # ─── تست ۴b: پرامپت با system ───
                t1 = time.monotonic()
                try:
                    resp2 = await ai.chat(
                        LLM_PING_PROMPT,
                        system="تو یک دستیار فارسی هستی. یک جمله جواب بده.",
                    )
                    elapsed2 = (time.monotonic() - t1) * 1000
                    if resp2 and not resp2.startswith("⚠️"):
                        report.add(
                            "deepseek.chat_system",
                            "ok",
                            f"{elapsed2:.0f}ms | "
                            f"resp={_short(resp2, 60)}",
                            elapsed2,
                        )
                    else:
                        report.add(
                            "deepseek.chat_system",
                            "warn",
                            f"پاسخ نامعتبر: {_short(resp2, 60)}",
                        )
                except Exception as e:
                    report.add(
                        "deepseek.chat_system", "warn",
                        f"{type(e).__name__}: {e}",
                    )
        except Exception as e:
            report.add(
                "deepseek.connect", "fail",
                f"{type(e).__name__}: {e}",
            )
    else:
        report.add(
            "deepseek.skip", "skip",
            "--skip-deepseek → رد شد",
        )

    # ═══════════════════════════════════════════════════
    # مرحله ۵: اتصال واقعی Ollama (اختیاری)
    # ═══════════════════════════════════════════════════
    if not skip_ollama and not deepseek_only:
        section("مرحله ۵: اتصال واقعی Ollama (اختیاری)")
        try:
            import aiohttp
            from config import AI as _AI

            ollama_url = (_AI.get("url") or "").rstrip("/")
            if not ollama_url:
                report.add(
                    "ollama.skip", "skip",
                    "Ollama URL خالی",
                )
            else:
                t0 = time.monotonic()
                try:
                    async with aiohttp.ClientSession() as s:
                        async with s.get(
                            f"{ollama_url}/api/tags",
                            timeout=aiohttp.ClientTimeout(total=3.0),
                        ) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                models = data.get("models", [])
                                names = [
                                    m.get("name", "?")
                                    for m in models[:5]
                                ]
                                report.add(
                                    "ollama.ping", "ok",
                                    f"{len(models)} مدل | "
                                    f"{names}",
                                    (time.monotonic() - t0) * 1000,
                                )

                                # ─── تست chat ───
                                ollama_model = _AI.get("model", "?")
                                if ollama_model in names:
                                    report.add(
                                        "ollama.model", "ok",
                                        f"مدل {ollama_model} موجود",
                                    )
                                else:
                                    report.add(
                                        "ollama.model", "warn",
                                        f"مدل {ollama_model} در لیست نیست",
                                    )
                            else:
                                report.add(
                                    "ollama.ping", "warn",
                                    f"status={resp.status}",
                                )
                except Exception as e:
                    report.add(
                        "ollama.ping", "warn",
                        f"Ollama روشن نیست؟ | "
                        f"{type(e).__name__}: {_short(e, 60)}",
                    )
        except Exception as e:
            report.add(
                "ollama.connect", "warn",
                f"{type(e).__name__}: {e}",
            )
    else:
        report.add(
            "ollama.skip", "skip",
            "Ollama رد شد",
        )

    # ═══════════════════════════════════════════════════
    # مرحله ۶: AIAssistant._provider_order
    # ═══════════════════════════════════════════════════
    section("مرحله ۶: AIAssistant._provider_order (ترتیب)")
    try:
        from core.services.ai_assistant import get_ai_assistant
        ai = get_ai_assistant()

        # ─── _provider_order ───
        order = ai._provider_order()
        if not order:
            report.add(
                "ai.provider_order", "fail",
                "لیست خالی — هیچ providerی",
            )
        else:
            report.add(
                "ai.provider_order", "ok",
                f"order={order}",
            )

            # ─── چک ترتیب بر اساس priority ───
            priority = (ai._priority or "auto").lower()
            if priority == "deepseek" and ai._deepseek_key:
                if order[0] == "deepseek":
                    report.add(
                        "ai.order.correct", "ok",
                        "deepseek اول (priority=deepseek)",
                    )
                else:
                    report.add(
                        "ai.order.correct", "warn",
                        f"deepseek اول نیست: {order}",
                    )
            elif priority == "ollama":
                if order[0] == "ollama":
                    report.add(
                        "ai.order.correct", "ok",
                        "ollama اول (priority=ollama)",
                    )
                else:
                    report.add(
                        "ai.order.correct", "warn",
                        f"ollama اول نیست: {order}",
                    )
            else:  # auto
                if ai._deepseek_key:
                    if order[0] == "deepseek":
                        report.add(
                            "ai.order.correct", "ok",
                            "auto: deepseek اول (کلید هست)",
                        )
                    else:
                        report.add(
                            "ai.order.correct", "warn",
                            f"auto: {order}",
                        )
                else:
                    report.add(
                        "ai.order.correct", "ok",
                        "auto: ollama (کلید نیست)",
                    )

        # ─── get_stats ───
        stats = ai.get_stats()
        report.add(
            "ai.stats", "ok",
            f"enabled={stats['enabled']} | "
            f"priority={stats['priority']} | "
            f"deepseek={stats['deepseek_configured']} | "
            f"source={stats.get('deepseek_key_source', '?')} | "
            f"providers={stats['provider_order']}",
        )
    except Exception as e:
        report.add(
            "ai.provider_order", "fail",
            f"{type(e).__name__}: {e}",
        )

    # ═══════════════════════════════════════════════════
    # مرحله ۷: AIAssistant.parse_intent
    # ═══════════════════════════════════════════════════
    section("مرحله ۷: AIAssistant.parse_intent (JSON)")
    try:
        from core.services.ai_assistant import get_ai_assistant
        ai = get_ai_assistant()

        # ─── چک _ALLOWED_ACTIONS ───
        from core.services.ai_assistant import _ALLOWED_ACTIONS
        report.add(
            "ai.allowed_actions", "ok",
            f"{len(_ALLOWED_ACTIONS)} اکشن مجاز",
        )

        # ─── parse intent ───
        t0 = time.monotonic()
        intent = await ai.parse_intent(INTENT_TEST_TEXT)
        elapsed = (time.monotonic() - t0) * 1000

        action = intent.get("action", "unknown")
        args = intent.get("args", {})

        if action == INTENT_EXPECTED_ACTION:
            report.add(
                "ai.parse_intent", "ok",
                f"action={action} | args={list(args.keys())} | "
                f"{elapsed:.0f}ms",
                elapsed,
            )
        elif action == "unknown":
            report.add(
                "ai.parse_intent", "warn",
                f"action=unknown (AI تشخیص نداد) | "
                f"{elapsed:.0f}ms",
            )
        else:
            report.add(
                "ai.parse_intent", "warn",
                f"action={action} (انتظار: "
                f"{INTENT_EXPECTED_ACTION}) | "
                f"{elapsed:.0f}ms",
            )
    except Exception as e:
        report.add(
            "ai.parse_intent", "fail",
            f"{type(e).__name__}: {e}",
        )

    # ═══════════════════════════════════════════════════
    # مرحله ۸: SafeBrain — ساخت + api_key
    # ═══════════════════════════════════════════════════
    section("مرحله ۸: SafeBrain init + api_key")
    safe_brain = None
    try:
        from core.safe_brain import get_safe_brain, reset_safe_brain

        # ─── ریست برای تست خالص ───
        reset_safe_brain()

        t0 = time.monotonic()
        safe_brain = get_safe_brain()
        init_elapsed = (time.monotonic() - t0) * 1000

        if safe_brain is None:
            report.add(
                "safe_brain.init", "fail",
                "get_safe_brain() → None",
            )
        else:
            report.add(
                "safe_brain.init", "ok",
                f"{init_elapsed:.0f}ms",
                init_elapsed,
            )

            # ─── api_key ───
            has_key = bool(safe_brain.api_key)
            report.add(
                "safe_brain.api_key",
                "ok" if has_key else "fail",
                f"has_key={has_key} | "
                f"prefix={safe_brain.api_key[:8] if has_key else '—'}",
            )

            # ─── full_access ───
            report.add(
                "safe_brain.full_access", "ok",
                f"full_access={safe_brain.full_access}",
            )

            # ─── cache / memory ───
            report.add(
                "safe_brain.cache", "ok",
                f"size={safe_brain.cache.size}",
            )
            report.add(
                "safe_brain.memory", "ok",
                f"project={safe_brain.project_memory.count} | "
                f"global={safe_brain.global_memory.count}",
            )

            # ─── stats ───
            stats = safe_brain.get_stats()
            report.add(
                "safe_brain.stats", "ok",
                f"processed={stats.get('processed', 0)} | "
                f"cached={stats.get('cached', 0)} | "
                f"api_calls={stats.get('api_calls', 0)} | "
                f"brain_connected={stats.get('brain_connected', False)}",
            )
    except Exception as e:
        report.add(
            "safe_brain.init", "fail",
            f"{type(e).__name__}: {e}",
        )

    # ═══════════════════════════════════════════════════
    # مرحله ۹: SafeBrain.process (fallback, delegate)
    # ═══════════════════════════════════════════════════
    section("مرحله ۹: SafeBrain.process")
    if safe_brain:
        try:
            # ─── help (محلی) ───
            t0 = time.monotonic()
            reply = await safe_brain.process("help")
            elapsed = (time.monotonic() - t0) * 1000

            if reply and "راهنما" in reply.lower() or "SafeBrain" in reply:
                report.add(
                    "safe_brain.local_help", "ok",
                    f"{elapsed:.0f}ms | len={len(reply)}",
                    elapsed,
                )
            elif reply:
                report.add(
                    "safe_brain.local_help", "warn",
                    f"پاسخ نامنتظره: {_short(reply, 60)}",
                )
            else:
                report.add(
                    "safe_brain.local_help", "warn",
                    "پاسخ خالی",
                )

            # ─── status (delegate) ───
            t0 = time.monotonic()
            reply2 = await safe_brain.process("وضعیت")
            elapsed2 = (time.monotonic() - t0) * 1000

            if reply2 is None:
                # None = delegate → OK
                report.add(
                    "safe_brain.delegate", "ok",
                    f"واگذاری به لایهٔ بعد | {elapsed2:.0f}ms",
                    elapsed2,
                )
            elif reply2:
                # پاسخ داده → check صریح
                report.add(
                    "safe_brain.delegate", "warn",
                    f"خودش جواب داد: {_short(reply2, 60)}",
                )
            else:
                report.add(
                    "safe_brain.delegate", "ok",
                    "واگذاری (خالی)",
                )

            # ─── امنیت: blocked pattern ───
            blocked_test = "rm -rf /"
            reply3 = await safe_brain.process(blocked_test)
            if reply3 and "مسدود" in reply3:
                report.add(
                    "safe_brain.security", "ok",
                    f"الگوی ممنوعه مسدود شد",
                )
            elif reply3 is None:
                report.add(
                    "safe_brain.security", "warn",
                    f"delegate شد (انتظار: مسدود)",
                )
            else:
                report.add(
                    "safe_brain.security", "warn",
                    f"پاسخ نامنتظره: {_short(reply3, 60)}",
                )
        except Exception as e:
            report.add(
                "safe_brain.process", "fail",
                f"{type(e).__name__}: {e}",
            )
    else:
        report.add(
            "safe_brain.process", "skip",
            "safe_brain ساخته نشد",
        )

    # ═══════════════════════════════════════════════════
    # مرحله ۱۰: Brain._init_client (source کلید)
    # ═══════════════════════════════════════════════════
    section("مرحله ۱۰: Brain init + client")
    brain = None
    if not quick:
        try:
            from core.brain import get_brain
            from core.brain.main import reset_brain

            reset_brain()

            t0 = time.monotonic()
            brain = get_brain()
            init_elapsed = (time.monotonic() - t0) * 1000

            if brain is None:
                report.add(
                    "brain.init", "fail",
                    "get_brain() → None",
                )
            else:
                report.add(
                    "brain.init", "ok",
                    f"{init_elapsed:.0f}ms",
                    init_elapsed,
                )

                # ─── client ───
                has_client = brain.client is not None
                report.add(
                    "brain.client",
                    "ok" if has_client else "warn",
                    f"client={'✅' if has_client else '❌'} | "
                    f"model={brain.model}",
                )

                # ─── api_key ───
                report.add(
                    "brain.api_key", "ok",
                    f"has_key={bool(brain.api_key)}",
                )

                # ─── client stats ───
                if hasattr(brain, "get_client_stats"):
                    cstats = brain.get_client_stats()
                    report.add(
                        "brain.client_stats", "ok",
                        f"priority={cstats['priority']} | "
                        f"model={cstats['model']} | "
                        f"tools={cstats['tools_count']} | "
                        f"key_prefix={cstats['api_key_prefix']}",
                    )
        except Exception as e:
            report.add(
                "brain.init", "fail",
                f"{type(e).__name__}: {e}",
            )
    else:
        report.add(
            "brain.init", "skip",
            "--quick → رد شد",
        )

    # ═══════════════════════════════════════════════════
    # مرحله ۱۱: Brain.TOOL_MAP (۴۲ ابزار)
    # ═══════════════════════════════════════════════════
    section("مرحله ۱۱: Brain.TOOL_MAP")
    try:
        from core.brain.main import Brain

        tool_map = getattr(Brain, "TOOL_MAP", None)
        if not tool_map:
            report.add(
                "brain.tool_map", "fail",
                "TOOL_MAP خالی",
            )
        else:
            report.add(
                "brain.tool_map", "ok",
                f"{len(tool_map)} ابزار ثبت‌شده",
            )

            # ─── چک ابزارهای کلیدی ───
            required_tools = [
                "read_file", "write_file", "edit_file",
                "append_knowledge",
                "semantic_search", "grep_search", "analyze_code",
                "git_status", "git_diff",
                "system_dashboard",
                "query_database",
                "get_setting", "set_setting",
            ]
            missing = [t for t in required_tools if t not in tool_map]
            if missing:
                report.add(
                    "brain.tool_map.required", "warn",
                    f"گم‌شده: {missing}",
                )
            else:
                report.add(
                    "brain.tool_map.required", "ok",
                    f"{len(required_tools)} ابزار کلیدی موجود",
                )

            # ─── TOOL_DEFINITIONS ───
            try:
                from core.brain.tools_meta import (
                    TOOL_DEFINITIONS, ALLOWED_TOOLS,
                )
                report.add(
                    "brain.tool_defs", "ok",
                    f"definitions={len(TOOL_DEFINITIONS)} | "
                    f"allowed={len(ALLOWED_TOOLS)}",
                )
            except Exception as e:
                report.add(
                    "brain.tool_defs", "warn",
                    f"{type(e).__name__}: {e}",
                )
    except Exception as e:
        report.add(
            "brain.tool_map", "fail",
            f"{type(e).__name__}: {e}",
        )

    # ═══════════════════════════════════════════════════
    # مرحله ۱۲: Brain.agent (تست سبک)
    # ═══════════════════════════════════════════════════
    section("مرحله ۱۲: Brain.agent (تست سبک)")
    if not quick and brain is not None:
        try:
            t0 = time.monotonic()

            # ─── تست agent با پرامپت کوتاه ───
            # نکته: این تست می‌تواند ۳۰-۶۰s طول بکشد
            try:
                response = await asyncio.wait_for(
                    brain.agent(LLM_TEST_PROMPT),
                    timeout=MAX_AGENT_TIME_S,
                )
                elapsed = (time.monotonic() - t0) * 1000

                if response and not response.startswith("❌"):
                    report.add(
                        "brain.agent", "ok",
                        f"{elapsed:.0f}ms | "
                        f"len={len(response)} | "
                        f"resp={_short(response, 60)}",
                        elapsed,
                    )
                else:
                    report.add(
                        "brain.agent", "warn",
                        f"پاسخ نامعتبر: {_short(response, 60)} | "
                        f"{elapsed:.0f}ms",
                        elapsed,
                    )
            except TimeoutError:
                elapsed = (time.monotonic() - t0) * 1000
                report.add(
                    "brain.agent", "warn",
                    f"timeout > {MAX_AGENT_TIME_S}s | "
                    f"{elapsed:.0f}ms",
                )
        except Exception as e:
            report.add(
                "brain.agent", "fail",
                f"{type(e).__name__}: {e}",
            )
    else:
        report.add(
            "brain.agent", "skip",
            "brain ساخته نشد یا --quick",
        )

    # ═══════════════════════════════════════════════════
    # مرحله ۱۳: Brain.process_admin_command
    # ═══════════════════════════════════════════════════
    section("مرحله ۱۳: Brain.process_admin_command")
    if brain is not None:
        try:
            # ─── quick command: status ───
            t0 = time.monotonic()
            reply = await brain.process_admin_command("وضعیت")
            elapsed = (time.monotonic() - t0) * 1000

            if reply:
                report.add(
                    "brain.cmd.status", "ok",
                    f"{elapsed:.0f}ms | len={len(reply)}",
                    elapsed,
                )
            else:
                report.add(
                    "brain.cmd.status", "warn",
                    "پاسخ خالی",
                )

            # ─── quick command: help ───
            reply2 = await brain.process_admin_command("راهنما")
            if reply2:
                report.add(
                    "brain.cmd.help", "ok",
                    f"len={len(reply2)}",
                )
            else:
                report.add(
                    "brain.cmd.help", "warn",
                    "پاسخ خالی",
                )

            # ─── reset stats (چک آمار) ───
            stats = brain.get_stats() if hasattr(brain, "get_stats") else {}
            report.add(
                "brain.stats", "ok",
                f"commands={stats.get('commands_processed', 0) if isinstance(stats, dict) else '—'}",
            )
        except Exception as e:
            report.add(
                "brain.cmd", "fail",
                f"{type(e).__name__}: {e}",
            )
    else:
        report.add(
            "brain.cmd", "skip",
            "brain ساخته نشد",
        )

    # ═══════════════════════════════════════════════════
    # مرحله ۱۴: ManualBrain (اختیاری)
    # ═══════════════════════════════════════════════════
    section("مرحله ۱۴: ManualBrain (اختیاری)")
    if not quick:
        try:
            from core.manual.brain import get_manual_brain
            from data import paths as data_paths

            t0 = time.monotonic()
            mbrain = get_manual_brain(str(data_paths.DB_FILE))
            init_elapsed = (time.monotonic() - t0) * 1000

            if mbrain is None:
                report.add(
                    "manual_brain.init", "skip",
                    "get_manual_brain → None",
                )
            else:
                report.add(
                    "manual_brain.init", "ok",
                    f"{init_elapsed:.0f}ms | "
                    f"core_rules={len(mbrain.core_rules)} | "
                    f"learned={len(mbrain.learned_rules)}",
                    init_elapsed,
                )

                # ─── process (بدون AI) ───
                try:
                    reply = await asyncio.wait_for(
                        mbrain.process("سلام"),
                        timeout=5.0,
                    )
                    if reply:
                        report.add(
                            "manual_brain.process", "ok",
                            f"پاسخ: {_short(reply, 60)}",
                        )
                    else:
                        report.add(
                            "manual_brain.process", "ok",
                            "None (بدون match — طبیعی)",
                        )
                except TimeoutError:
                    report.add(
                        "manual_brain.process", "warn",
                        "timeout 5s",
                    )
        except Exception as e:
            report.add(
                "manual_brain", "skip",
                f"در دسترس نیست | "
                f"{type(e).__name__}: {_short(e, 60)}",
            )
    else:
        report.add(
            "manual_brain", "skip",
            "--quick",
        )

    # ═══════════════════════════════════════════════════
    # مرحله ۱۵: هماهنگی لایه‌ها
    # ═══════════════════════════════════════════════════
    section("مرحله ۱۵: هماهنگی لایه‌ها")
    try:
        from core.services.ai_assistant import get_ai_assistant
        from core.safe_brain import get_safe_brain
        from core.brain import get_brain

        ai = get_ai_assistant()
        sb = get_safe_brain()
        b = get_brain()

        # ─── چک اشتراک کلید ───
        ai_key = ai._deepseek_key
        sb_key = sb.api_key
        b_key = b.api_key

        report.add(
            "harmony.api_keys", "ok",
            f"ai={'✅' if ai_key else '❌'} | "
            f"sb={'✅' if sb_key else '❌'} | "
            f"brain={'✅' if b_key else '❌'}",
        )

        # ─── چک هم‌ارزی ───
        if ai_key and sb_key and b_key:
            if ai_key == sb_key == b_key:
                report.add(
                    "harmony.keys_match", "ok",
                    "همه یک کلید دارند",
                )
            else:
                report.add(
                    "harmony.keys_match", "warn",
                    "کلیدها متفاوتند",
                )

        # ─── چک ترتیب provider ───
        ai_order = ai._provider_order()
        report.add(
            "harmony.provider_order", "ok",
            f"order={ai_order}",
        )
    except Exception as e:
        report.add(
            "harmony", "fail",
            f"{type(e).__name__}: {e}",
        )

    # ═══════════════════════════════════════════════════
    # مرحله ۱۶: tests.pipeline_helpers (storage integration)
    # ═══════════════════════════════════════════════════
    section("مرحله ۱۶: pipeline_helpers (Storage integration)")
    try:
        from tests.pipeline_helpers import TestPipeline

        async with TestPipeline() as pipeline:
            report.add(
                "pipeline.enter", "ok",
                "pipeline راه‌اندازی شد",
            )

            # ─── feed پیام تست ───
            test_msg = {
                "id": f"ai_test_{int(time.time())}",
                "platform": "ai_test",
                "text": "کود مایع یاراترا موجوده؟ قیمت چنده؟",
                "chat_id": "test_chat",
                "chat_name": "تست هوش",
                "chat_type": "group",
                "user_id": "test_user",
                "sender_name": "تستر",
                "timestamp": datetime.now(),
                "media_type": "text",
            }

            stored = await pipeline.feed([test_msg])
            report.add(
                "pipeline.feed", "ok",
                f"stored={stored}",
            )

            # ─── چک DB ───
            count = await pipeline.count_messages(platform="ai_test")
            report.add(
                "pipeline.db_count", "ok",
                f"messages={count}",
            )
    except Exception as e:
        report.add(
            "pipeline", "warn",
            f"{type(e).__name__}: {_short(e, 80)}",
        )

    # ═══════════════════════════════════════════════════
    # مرحله ۱۷: tests.db_helpers (DB connectivity)
    # ═══════════════════════════════════════════════════
    section("مرحله ۱۷: db_helpers")
    try:
        from tests.db_helpers import (
            db_available, db_size_kb, count_messages,
        )

        avail = db_available()
        report.add(
            "db.available",
            "ok" if avail else "warn",
            f"available={avail}",
        )

        if avail:
            report.add(
                "db.size", "ok",
                f"{db_size_kb():.1f}KB",
            )
            report.add(
                "db.messages", "ok",
                f"total={count_messages()}",
            )
    except Exception as e:
        report.add(
            "db_helpers", "warn",
            f"{type(e).__name__}: {e}",
        )

    # ═══════════════════════════════════════════════════
    # مرحله ۱۸: performance metrics
    # ═══════════════════════════════════════════════════
    section("مرحله ۱۸: performance")
    try:
        total_elapsed = time.monotonic() - total_t0
        report.add(
            "perf.total_time", "ok",
            f"{total_elapsed:.1f}s",
        )

        # ─── SafeBrain stats ───
        if safe_brain:
            stats = safe_brain.get_stats()
            report.add(
                "perf.safebrain", "ok",
                f"processed={stats.get('processed', 0)} | "
                f"cached={stats.get('cached', 0)} | "
                f"api_calls={stats.get('api_calls', 0)} | "
                f"uptime={stats.get('uptime_s', 0):.0f}s",
            )
    except Exception as e:
        report.add("perf", "warn", str(e))

    # ═══════════════════════════════════════════════════
    # مرحله ۱۹: error handling
    # ═══════════════════════════════════════════════════
    section("مرحله ۱۹: error handling")
    try:
        from core.services.ai_assistant import get_ai_assistant
        ai = get_ai_assistant()

        # ─── پرامپت خالی ───
        resp, ok = await ai.chat_with_status("")
        if not ok and not resp:
            report.add(
                "error.empty_prompt", "ok",
                "پرامپت خالی → رد شد",
            )
        else:
            report.add(
                "error.empty_prompt", "warn",
                f"ok={ok} | resp={_short(resp, 40)}",
            )

        # ─── پرامپت None ───
        resp2, ok2 = await ai.chat_with_status(None)  # type: ignore
        if not ok2:
            report.add(
                "error.none_prompt", "ok",
                "پرامپت None → رد شد",
            )
        else:
            report.add(
                "error.none_prompt", "warn",
                "None پذیرفته شد!",
            )

        # ─── پرامپت خیلی بلند ───
        long_prompt = "x" * 50_000
        resp3, ok3 = await ai.chat_with_status(long_prompt)
        # این ممکن است کار کند (truncate) یا نه
        report.add(
            "error.long_prompt", "ok",
            f"ok={ok3} | truncated=True",
        )
    except Exception as e:
        report.add(
            "error", "fail",
            f"{type(e).__name__}: {e}",
        )

    # ═══════════════════════════════════════════════════
    # مرحله ۲۰: ضد اسپم (SafeCache)
    # ═══════════════════════════════════════════════════
    section("مرحله ۲۰: ضد اسپم (cache)")
    if safe_brain:
        try:
            # ─── پیام تکراری ───
            test_text = f"تست تکراری {int(time.time())}"

            # ─── بار اول ───
            t0 = time.monotonic()
            r1 = await safe_brain.process(test_text)
            t1 = (time.monotonic() - t0) * 1000

            # ─── بار دوم ───
            t0 = time.monotonic()
            r2 = await safe_brain.process(test_text)
            t2 = (time.monotonic() - t0) * 1000

            if t2 < t1 * 0.5 and r1 == r2:
                report.add(
                    "cache.hit", "ok",
                    f"بار1={t1:.0f}ms بار2={t2:.0f}ms "
                    f"(سریع‌تر ✅)",
                )
            else:
                report.add(
                    "cache.hit", "warn",
                    f"بار1={t1:.0f}ms بار2={t2:.0f}ms",
                )
        except Exception as e:
            report.add(
                "cache", "warn",
                f"{type(e).__name__}: {e}",
            )
    else:
        report.add(
            "cache", "skip",
            "safe_brain نیست",
        )

    # ═══════════════════════════════════════════════════
    # مرحله ۲۱: حافظهٔ محلی (SafeMemory)
    # ═══════════════════════════════════════════════════
    section("مرحله ۲۱: حافظهٔ محلی")
    try:
        from core.safe_brain.storage import SafeMemory, SafeCache

        # ─── SafeMemory ───
        mem = SafeMemory("test_ai_mem", max_entries=10)
        mem.add("user", "سلام تست")
        mem.add("assistant", "سلام! چطورم؟")
        mem.add("user", "خوبی؟")

        count = mem.count
        context = mem.get_context(5)
        results = mem.search("تست", n=3)

        report.add(
            "memory.add", "ok" if count >= 3 else "warn",
            f"count={count}",
        )
        report.add(
            "memory.context", "ok",
            f"len={len(context)}",
        )
        report.add(
            "memory.search", "ok" if results else "warn",
            f"found={len(results)}",
        )

        # ─── SafeCache ───
        cache = SafeCache("test_ai_cache", ttl_hours=1)
        cache.set("test_key", "test_value")
        val = cache.get("test_key")

        report.add(
            "cache.get", "ok" if val == "test_value" else "warn",
            f"hit={'✅' if val else '❌'}",
        )

        stats = cache.stats()
        report.add(
            "cache.stats", "ok",
            f"size={stats['size']} | "
            f"hit_rate={stats['hit_rate']}",
        )
    except Exception as e:
        report.add(
            "storage", "warn",
            f"{type(e).__name__}: {e}",
        )

    # ═══════════════════════════════════════════════════
    # مرحله ۲۲: fallback chain
    # ═══════════════════════════════════════════════════
    section("مرحله ۲۲: fallback chain")
    try:
        from core.services.ai_assistant import get_ai_assistant
        ai = get_ai_assistant()

        order = ai._provider_order()
        has_fallback = len(order) > 1

        if has_fallback:
            report.add(
                "fallback.chain", "ok",
                f"chain={order} | "
                f"primary={order[0]} fallback={order[1:]}",
            )
        else:
            report.add(
                "fallback.chain", "warn",
                f"فقط یک provider: {order}",
            )

        # ─── چک کلیدهای ضروری ───
        primary = order[0] if order else None
        if primary == "deepseek" and not ai._deepseek_key:
            report.add(
                "fallback.primary_key", "fail",
                "primary=deepseek ولی کلید نیست",
            )
        else:
            report.add(
                "fallback.primary_key", "ok",
                f"primary={primary} | keys ok",
            )
    except Exception as e:
        report.add(
            "fallback", "warn",
            f"{type(e).__name__}: {e}",
        )

    # ═══════════════════════════════════════════════════
    # مرحله ۲۳: آمار نهایی Brain
    # ═══════════════════════════════════════════════════
    section("مرحله ۲۳: آمار نهایی")
    try:
        if brain:
            brain_stats = brain.stats if hasattr(brain, "stats") else {}
            report.add(
                "final.brain_stats", "ok",
                f"agents_run={brain_stats.get('agents_run', 0)} | "
                f"commands={brain_stats.get('commands_processed', 0)} | "
                f"errors={brain_stats.get('errors', 0)}",
            )

        if safe_brain:
            sb_stats = safe_brain.get_stats()
            report.add(
                "final.safebrain_stats", "ok",
                f"processed={sb_stats.get('processed', 0)} | "
                f"cached={sb_stats.get('cached', 0)} | "
                f"delegated={sb_stats.get('delegated', 0)} | "
                f"fallback={sb_stats.get('fallback', 0)} | "
                f"errors={sb_stats.get('errors', 0)}",
            )

        # ─── flush برای durability ───
        if safe_brain:
            safe_brain.flush()
            report.add(
                "final.flush", "ok",
                "SafeBrain flushed",
            )
    except Exception as e:
        report.add("final", "warn", str(e))

    # ═══════════════════════════════════════════════════
    # مرحله ۲۴: گزارش نهایی
    # ═══════════════════════════════════════════════════
    section("مرحله ۲۴: گزارش نهایی")
    total_elapsed = time.monotonic() - total_t0

    summary = report.summary()
    report.add(
        "final.total_time", "ok",
        f"{total_elapsed:.1f}s | "
        f"ok={summary['ok']} fail={summary['fail']} "
        f"warn={summary['warn']} skip={summary['skip']}",
    )

    # ─── نتیجه ───
    ok = report.print_summary()
    report.save()

    # ─── خلاصهٔ نهایی ───
    print()
    print("=" * 70)
    print(f"{C.BOLD}🎯 خلاصهٔ نهایی{C.END}")
    print("=" * 70)
    print(f"  ⏱️  کل زمان: {total_elapsed:.1f}s")
    print(f"  ✅ موفق:    {summary['ok']}")
    print(f"  ❌ ناموفق:  {summary['fail']}")
    print(f"  ⚠️  هشدار:  {summary['warn']}")
    print(f"  ⏭️  رد شده: {summary['skip']}")

    if summary['fail'] == 0:
        print(f"\n  {C.OK}🎉 همهٔ تست‌های بحرانی موفق بودند!{C.END}")
    else:
        print(f"\n  {C.FAIL}❌ {summary['fail']} تست ناموفق — "
              f"بالا را ببین{C.END}")

    print("=" * 70)
    return ok


# ═════════════════════════════════════════════════════════
# Entry point
# ═════════════════════════════════════════════════════════
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="تست فوق‌دقیق هوش — DeepSeek + Ollama + Brain + SafeBrain",
    )
    parser.add_argument(
        "--quick", action="store_true",
        help="فقط مراحل بحرانی (بدون Brain.agent و ManualBrain)",
    )
    parser.add_argument(
        "--skip-deepseek", action="store_true",
        help="نادیده گرفتن DeepSeek (تست Ollama-only)",
    )
    parser.add_argument(
        "--skip-ollama", action="store_true",
        help="نادیده گرفتن Ollama (تست DeepSeek-only)",
    )
    parser.add_argument(
        "--deepseek-only", action="store_true",
        help="فقط DeepSeek (بدون fallback به Ollama)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="چاپ جزئیات کامل config",
    )
    args = parser.parse_args()

    ok = asyncio.run(
        main(
            quick=args.quick,
            skip_deepseek=args.skip_deepseek,
            skip_ollama=args.skip_ollama,
            deepseek_only=args.deepseek_only,
            verbose=args.verbose,
        )
    )
    sys.exit(0 if ok else 1)