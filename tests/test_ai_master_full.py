# tests/test_ai_master_full.py
"""
تست فوق‌دقیق و جامع هوش مصنوعی — همه چیز در یک فایل.

پوشش کامل:
  📦 بخش A: Import + Config (۳ تست)
  🔌 بخش B: DeepSeek + Ollama (اتصال واقعی) (۵ تست)
  🤖 بخش C: AIAssistant (ترتیب + intent) (۴ تست)
  🧠 بخش D: SafeBrain (گارد + کش + حافظه) (۶ تست)
  🎯 بخش E: Brain (Agent + ۴۲ ابزار + commands) (۷ تست)
  📚 بخش F: ManualBrain (RULES + process) (۳ تست)
  💾 بخش G: حافظه‌ها (SafeMemory + BrainMemory + APIMemory) (۵ تست)
  🛠️  بخش H: ۴۲ ابزار Brain (تک‌تک) (۱۰ تست)
  🎯 بخش I: Intent Parsing (۲۰ پرامپت) (۳ تست)
  🌐 بخش J: Pipeline + DB + Storage (۳ تست)
  ⚡ بخش K: Performance + Error Handling (۳ تست)
  🎉 بخش L: گزارش نهایی (۱ تست)

مجموع: ~۵۳ تست در یک فایل

اجرا:
  python tests/test_ai_master_full.py                # همه
  python tests/test_ai_master_full.py --quick        # سریع (بدون Brain.agent)
  python tests/test_ai_master_full.py --section B    # فقط بخش B
  python tests/test_ai_master_full.py --skip-heavy   # بدون تست‌های سنگین
  python tests/test_ai_master_full.py --verbose

⚠️ زمان تخمینی: ۶۰-۱۲۰ ثانیه (بسته به provider و network)
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
    C,
    TestReport,
    banner,
    section,
)


# ═════════════════════════════════════════════════════════
# ثابت‌ها
# ═════════════════════════════════════════════════════════
LLM_TEST_PROMPT = "بگو یک کلمه"
LLM_PING_PROMPT = "سلام"
INTENT_TEST_TEXT = "وضعیت"
INTENT_EXPECTED_ACTION = "status"

SLOW_LATENCY_MS = 30_000
VERY_SLOW_LATENCY_MS = 60_000
FAST_LATENCY_MS = 3_000

MAX_AGENT_TIME_S = 120
MAX_SAFEBRAIN_TIME_S = 60
MAX_BRAIN_INIT_S = 30

# ۲۰ پرامپت برای intent
INTENT_PROMPTS = [
    ("وضعیت", "status"),
    ("آمار", "status"),
    ("گزارش بده", "report"),
    ("گزارش ایتا", "report"),
    ("crm", "crm"),
    ("خروجی اکسل", "crm"),
    ("لاگ بده", "logs"),
    ("سلامت بده", "health"),
    ("راهنما", "help"),
    ("اولین سرنخ", "lead_details"),
    ("آخرین سرنخ", "lead_details"),
    ("کلیدواژه «کود مایع» را اضافه کن", "add_keyword"),
    ("کلیدواژه «سم» را حذف کن", "remove_keyword"),
    ("کلیدواژه «کود» را داری؟", "has_keyword"),
    ("کلیدواژه‌ها", "keywords"),
    ("ایتا کار می‌کند؟", "platform_status"),
    ("get LOG_LEVEL", "get_param"),
    ("set BALE_POLL_SECONDS=120", "set_param"),
    ("ری‌استارت کن", "restart"),
    ("چیکار بلدی؟", "ask"),
]


# ═════════════════════════════════════════════════════════
# helpers
# ═════════════════════════════════════════════════════════
def _short(s: Any, n: int = 80) -> str:
    if s is None:
        return "None"
    txt = str(s).strip().replace("\n", " ")
    return txt if len(txt) <= n else txt[:n] + "…"


def _safe_len(s: Any) -> int:
    try:
        return len(str(s))
    except Exception:
        return 0


def _check_keys(obj: Any, keys: list[str], label: str) -> list[str]:
    if not isinstance(obj, dict):
        return [f"{label} dict نیست"]
    return [k for k in keys if k not in obj]


# ═════════════════════════════════════════════════════════
# بخش A: Import + Config
# ═════════════════════════════════════════════════════════
async def section_A_imports(report: TestReport, **kw):
    section("بخش A: Import + Config")

    # ─── A1: import همه ماژول‌های کلیدی ───
    t0 = time.monotonic()
    import_modules = [
        ("config", "config.AI"),
        ("core.services.ai_assistant", "AIAssistant"),
        ("core.safe_brain.main", "SafeBrain"),
        ("core.safe_brain.storage", "SafeMemory+SafeCache"),
        ("core.safe_brain.constants", "SAFE_ACTIONS"),
        ("core.brain.main", "Brain"),
        ("core.brain.mixin_agent", "AgentMixin"),
        ("core.brain.tools_meta", "TOOL_DEFINITIONS"),
        ("core.brain.memory", "BrainMemory"),
        ("core.brain.learning", "LearningEngine"),
        ("core.manual.brain", "ManualBrain"),
        ("data.paths", "paths"),
        ("handlers.admin_handler", "AdminHandler"),
    ]
    imports_status: dict[str, bool] = {}
    for module_name, label in import_modules:
        try:
            __import__(module_name)
            imports_status[module_name] = True
        except Exception as e:
            imports_status[module_name] = False
            report.add(f"A1.import.{module_name}", "fail",
                       f"{type(e).__name__}: {e}")

    ok_count = sum(1 for v in imports_status.values() if v)
    total = len(imports_status)
    elapsed = (time.monotonic() - t0) * 1000
    report.add("A1.import.all",
               "ok" if ok_count == total else "fail",
               f"{ok_count}/{total} ماژول | {elapsed:.0f}ms", elapsed)

    # ─── A2: config.AI ───
    try:
        from config import AI, AI_ENABLED, AI_PRIORITY
        required = ["enabled", "priority", "url", "model",
                    "timeout", "deepseek_api_key"]
        missing = _check_keys(AI, required, "AI")
        if missing:
            report.add("A2.config.keys", "warn", f"گم‌شده: {missing}")
        else:
            report.add("A2.config.keys", "ok",
                       f"{len(required)} کلید موجود")

        key = AI.get("deepseek_api_key", "")
        has_key = bool(key and key.strip())
        report.add("A2.config.deepseek_key",
                   "ok" if has_key else "fail",
                   f"has_key={has_key} | prefix={key[:8] if has_key else '—'}...")

        report.add("A2.config.priority", "ok",
                   f"priority={AI_PRIORITY}")
        report.add("A2.config.enabled",
                   "ok" if AI_ENABLED else "warn",
                   f"enabled={AI_ENABLED}")
        report.add("A2.config.ollama", "ok",
                   f"url={AI.get('url')} | model={AI.get('model')}")
    except Exception as e:
        report.add("A2.config", "fail", f"{type(e).__name__}: {e}")

    # ─── A3: کلید DeepSeek (منبع + شکل) ───
    try:
        from config import AI as _AI
        cfg_key = (_AI.get("deepseek_api_key") or "").strip()
        env_key = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()

        if cfg_key:
            report.add("A3.key.source", "ok",
                       f"config | prefix={cfg_key[:8]}... | len={len(cfg_key)}")
        elif env_key:
            report.add("A3.key.source", "warn",
                       f"env فقط | prefix={env_key[:8]}...")
        else:
            report.add("A3.key.source", "fail",
                       "هیچ کلیدی در config و env نیست")

        key = cfg_key or env_key
        if key:
            if key.startswith("sk-"):
                report.add("A3.key.format", "ok", "شکل صحیح (sk-...)")
            else:
                report.add("A3.key.format", "warn",
                           f"شکل مشکوک: {key[:6]}...")
            if len(key) < 20:
                report.add("A3.key.length", "warn",
                           f"کوتاه: {len(key)} char")
            else:
                report.add("A3.key.length", "ok", f"{len(key)} char")
    except Exception as e:
        report.add("A3.key", "fail", f"{type(e).__name__}: {e}")


# ═════════════════════════════════════════════════════════
# بخش B: DeepSeek + Ollama
# ═════════════════════════════════════════════════════════
async def section_B_connections(report: TestReport,
                                 skip_deepseek: bool = False,
                                 skip_ollama: bool = False,
                                 deepseek_only: bool = False,
                                 **kw):
    section("بخش B: اتصال واقعی DeepSeek + Ollama")

    # ─── B1: DeepSeek available ───
    if not skip_deepseek:
        try:
            from core.services.ai_assistant import get_ai_assistant
            ai = get_ai_assistant()

            t0 = time.monotonic()
            avail = await ai.available()
            elapsed = (time.monotonic() - t0) * 1000
            report.add("B1.deepseek.available",
                       "ok" if avail else "fail",
                       f"available={avail} | {elapsed:.0f}ms", elapsed)

            # ─── B2: DeepSeek chat (بدون system) ───
            if avail:
                t0 = time.monotonic()
                resp, ok = await ai.chat_with_status(LLM_TEST_PROMPT)
                elapsed = (time.monotonic() - t0) * 1000

                if ok and resp:
                    report.add("B2.deepseek.chat",
                               "ok",
                               f"{elapsed:.0f}ms | len={len(resp)} | "
                               f"resp={_short(resp, 60)}",
                               elapsed)
                    if elapsed > SLOW_LATENCY_MS:
                        report.add("B2.deepseek.slow", "warn",
                                   f"کند: {elapsed/1000:.1f}s")
                    elif elapsed < FAST_LATENCY_MS:
                        report.add("B2.deepseek.fast", "ok",
                                   f"سریع: {elapsed:.0f}ms")
                else:
                    report.add("B2.deepseek.chat", "fail",
                               f"ok={ok} | resp={_short(resp, 80)}")

                # ─── B3: DeepSeek chat با system ───
                t0 = time.monotonic()
                try:
                    resp2 = await ai.chat(
                        LLM_PING_PROMPT,
                        system="تو یک دستیار فارسی هستی. یک جمله جواب بده.",
                    )
                    elapsed2 = (time.monotonic() - t0) * 1000
                    if resp2 and not resp2.startswith("⚠️"):
                        report.add("B3.deepseek.system", "ok",
                                   f"{elapsed2:.0f}ms | "
                                   f"resp={_short(resp2, 60)}",
                                   elapsed2)
                    else:
                        report.add("B3.deepseek.system", "warn",
                                   f"نامعتبر: {_short(resp2, 60)}")
                except Exception as e:
                    report.add("B3.deepseek.system", "warn",
                               f"{type(e).__name__}: {e}")
        except Exception as e:
            report.add("B1.deepseek", "fail",
                       f"{type(e).__name__}: {e}")
    else:
        report.add("B1.deepseek", "skip", "--skip-deepseek")

    # ─── B4: Ollama ping ───
    if not skip_ollama and not deepseek_only:
        try:
            import aiohttp
            from config import AI as _AI
            ollama_url = (_AI.get("url") or "").rstrip("/")

            if not ollama_url:
                report.add("B4.ollama", "skip", "URL خالی")
            else:
                t0 = time.monotonic()
                async with aiohttp.ClientSession() as s, s.get(
                    f"{ollama_url}/api/tags",
                    timeout=aiohttp.ClientTimeout(total=3.0),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        models = data.get("models", [])
                        names = [m.get("name", "?") for m in models[:5]]
                        report.add("B4.ollama.ping", "ok",
                                   f"{len(models)} مدل | {names}",
                                   (time.monotonic() - t0) * 1000)
                        # ─── B5: مدل مورد نظر ───
                        wanted = _AI.get("model", "?")
                        if wanted in names:
                            report.add("B5.ollama.model", "ok",
                                       f"{wanted} موجود")
                        else:
                            report.add("B5.ollama.model", "warn",
                                       f"{wanted} نیست")
                    else:
                        report.add("B4.ollama.ping", "warn",
                                   f"status={resp.status}")
        except Exception as e:
            report.add("B4.ollama.ping", "warn",
                       f"روشن نیست؟ | {type(e).__name__}")
    else:
        report.add("B4.ollama", "skip", "رد شد")


# ═════════════════════════════════════════════════════════
# بخش C: AIAssistant
# ═════════════════════════════════════════════════════════
async def section_C_assistant(report: TestReport, **kw):
    section("بخش C: AIAssistant (ترتیب + intent)")

    # ─── C1: _provider_order ───
    try:
        from core.services.ai_assistant import get_ai_assistant
        ai = get_ai_assistant()

        order = ai._provider_order()
        if not order:
            report.add("C1.order", "fail", "لیست خالی")
        else:
            report.add("C1.order", "ok", f"order={order}")

            # ─── C2: چک ترتیب بر اساس priority ───
            priority = (ai._priority or "auto").lower()
            if priority == "deepseek" and ai._deepseek_key:
                ok = order[0] == "deepseek"
            elif priority == "ollama":
                ok = order[0] == "ollama"
            else:  # auto
                ok = (order[0] == "deepseek" if ai._deepseek_key
                      else order[0] == "ollama")
            report.add("C2.order.correct",
                       "ok" if ok else "warn",
                       f"priority={priority} | order={order}")

        # ─── C3: get_stats ───
        stats = ai.get_stats()
        report.add("C3.stats", "ok",
                   f"enabled={stats['enabled']} | "
                   f"priority={stats['priority']} | "
                   f"deepseek={stats['deepseek_configured']} | "
                   f"source={stats.get('deepseek_key_source', '?')}")

        # ─── C4: parse_intent ───
        t0 = time.monotonic()
        intent = await ai.parse_intent(INTENT_TEST_TEXT)
        elapsed = (time.monotonic() - t0) * 1000
        action = intent.get("action", "unknown")
        if action == INTENT_EXPECTED_ACTION:
            report.add("C4.intent", "ok",
                       f"action={action} | {elapsed:.0f}ms", elapsed)
        else:
            report.add("C4.intent", "warn",
                       f"action={action} (انتظار={INTENT_EXPECTED_ACTION})")
    except Exception as e:
        report.add("C1.assistant", "fail", f"{type(e).__name__}: {e}")


# ═════════════════════════════════════════════════════════
# بخش D: SafeBrain
# ═════════════════════════════════════════════════════════
async def section_D_safebrain(report: TestReport, **kw):
    section("بخش D: SafeBrain (گارد + کش + حافظه)")

    safe_brain = None
    try:
        from core.safe_brain import get_safe_brain, reset_safe_brain
        reset_safe_brain()

        t0 = time.monotonic()
        safe_brain = get_safe_brain()
        init_ms = (time.monotonic() - t0) * 1000

        # ─── D1: init ───
        report.add("D1.init", "ok",
                   f"{init_ms:.0f}ms | "
                   f"api_key={'✅' if safe_brain.api_key else '❌'}",
                   init_ms)

        # ─── D2: api_key ───
        report.add("D2.api_key",
                   "ok" if safe_brain.api_key else "fail",
                   f"prefix={safe_brain.api_key[:8] if safe_brain.api_key else '—'}")

        # ─── D3: cache/memory ───
        report.add("D3.components", "ok",
                   f"cache={safe_brain.cache.size} | "
                   f"project={safe_brain.project_memory.count} | "
                   f"global={safe_brain.global_memory.count}")

        # ─── D4: process help (محلی) ───
        t0 = time.monotonic()
        reply = await safe_brain.process("help")
        elapsed = (time.monotonic() - t0) * 1000
        if reply and ("SafeBrain" in reply or "راهنما" in reply):
            report.add("D4.help", "ok",
                       f"{elapsed:.0f}ms | len={len(reply)}", elapsed)
        else:
            report.add("D4.help", "warn",
                       f"پاسخ: {_short(reply, 60)}")

        # ─── D5: process status (delegate) ───
        reply2 = await safe_brain.process("وضعیت")
        if reply2 is None:
            report.add("D5.delegate", "ok", "واگذاری ✅")
        else:
            report.add("D5.delegate", "warn",
                       f"خودش جواب داد: {_short(reply2, 60)}")

        # ─── D6: امنیت ───
        reply3 = await safe_brain.process("rm -rf /")
        if reply3 and "مسدود" in reply3:
            report.add("D6.security", "ok", "مسدود شد")
        else:
            report.add("D6.security", "warn",
                       f"پاسخ: {_short(reply3, 60)}")
    except Exception as e:
        report.add("D1.safebrain", "fail", f"{type(e).__name__}: {e}")

    return safe_brain


# ═════════════════════════════════════════════════════════
# بخش E: Brain
# ═════════════════════════════════════════════════════════
async def section_E_brain(report: TestReport,
                          quick: bool = False,
                          skip_heavy: bool = False,
                          **kw):
    section("بخش E: Brain (Agent + ۴۲ ابزار)")

    brain = None
    try:
        from core.brain import get_brain
        from core.brain.main import reset_brain
        reset_brain()

        # ─── E1: init ───
        t0 = time.monotonic()
        brain = get_brain()
        init_ms = (time.monotonic() - t0) * 1000
        report.add("E1.init", "ok",
                   f"{init_ms:.0f}ms | "
                   f"client={'✅' if brain.client else '❌'} | "
                   f"model={brain.model}",
                   init_ms)

        # ─── E2: api_key ───
        report.add("E2.api_key",
                   "ok" if brain.api_key else "fail",
                   f"has_key={bool(brain.api_key)}")

        # ─── E3: client stats ───
        if hasattr(brain, "get_client_stats"):
            cs = brain.get_client_stats()
            report.add("E3.client_stats", "ok",
                       f"priority={cs['priority']} | "
                       f"model={cs['model']} | "
                       f"tools={cs['tools_count']} | "
                       f"key_source={cs.get('api_key_prefix', '?')}")

        # ─── E4: TOOL_MAP ───
        from core.brain.main import Brain
        tool_map = getattr(Brain, "TOOL_MAP", None)
        if not tool_map:
            report.add("E4.tool_map", "fail", "خالی")
        else:
            report.add("E4.tool_map", "ok",
                       f"{len(tool_map)} ابزار")
            required = [
                "read_file", "write_file", "edit_file",
                "append_knowledge", "semantic_search",
                "grep_search", "analyze_code", "git_status",
                "system_dashboard", "query_database",
                "get_setting", "set_setting",
            ]
            missing = [t for t in required if t not in tool_map]
            report.add("E4.tool_map.required",
                       "ok" if not missing else "warn",
                       f"گم‌شده: {missing}" if missing else "همه موجود")

        # ─── E5: commands (سریع) ───
        reply = await brain.process_admin_command("وضعیت")
        if reply:
            report.add("E5.cmd.status", "ok", f"len={len(reply)}")
        else:
            report.add("E5.cmd.status", "warn", "خالی")

        reply2 = await brain.process_admin_command("راهنما")
        if reply2:
            report.add("E5.cmd.help", "ok", f"len={len(reply2)}")
        else:
            report.add("E5.cmd.help", "warn", "خالی")

        # ─── E6: agent (سنگین) ───
        if not quick and not skip_heavy:
            t0 = time.monotonic()
            try:
                response = await asyncio.wait_for(
                    brain.agent(LLM_TEST_PROMPT),
                    timeout=MAX_AGENT_TIME_S,
                )
                elapsed = (time.monotonic() - t0) * 1000
                if response and not response.startswith("❌"):
                    report.add("E6.agent", "ok",
                               f"{elapsed:.0f}ms | "
                               f"len={len(response)}",
                               elapsed)
                else:
                    report.add("E6.agent", "warn",
                               f"نامعتبر: {_short(response, 60)}")
            except TimeoutError:
                report.add("E6.agent", "warn",
                           f"timeout > {MAX_AGENT_TIME_S}s")
        else:
            report.add("E6.agent", "skip", "--quick یا --skip-heavy")

        # ─── E7: stats ───
        try:
            stats = brain.get_stats() if hasattr(brain, "get_stats") else {}
            if isinstance(stats, dict):
                report.add("E7.stats", "ok",
                           f"commands={stats.get('commands_processed', 0)} | "
                           f"agents={stats.get('agents_run', 0)} | "
                           f"errors={stats.get('errors', 0)}")
        except Exception as e:
            report.add("E7.stats", "warn", str(e))
    except Exception as e:
        report.add("E1.brain", "fail", f"{type(e).__name__}: {e}")

    return brain


# ═════════════════════════════════════════════════════════
# بخش F: ManualBrain
# ═════════════════════════════════════════════════════════
async def section_F_manual(report: TestReport,
                           quick: bool = False, **kw):
    section("بخش F: ManualBrain (RULES + process)")

    if quick:
        report.add("F.manual", "skip", "--quick")
        return

    try:
        from core.manual.brain import get_manual_brain
        from data import paths as data_paths

        t0 = time.monotonic()
        mbrain = get_manual_brain(str(data_paths.DB_FILE))
        init_ms = (time.monotonic() - t0) * 1000

        if mbrain is None:
            report.add("F1.init", "skip", "get_manual_brain → None")
            return

        report.add("F1.init", "ok",
                   f"{init_ms:.0f}ms | "
                   f"core={len(mbrain.core_rules)} | "
                   f"learned={len(mbrain.learned_rules)}",
                   init_ms)

        # ─── F2: process ───
        try:
            reply = await asyncio.wait_for(
                mbrain.process("سلام"),
                timeout=5.0,
            )
            if reply:
                report.add("F2.process", "ok",
                           f"پاسخ: {_short(reply, 60)}")
            else:
                report.add("F2.process", "ok",
                           "None (بدون match — طبیعی)")
        except TimeoutError:
            report.add("F2.process", "warn", "timeout 5s")

        # ─── F3: keywords API ───
        report.add("F3.keywords", "ok",
                   "custom_count="
                   f"{len(mbrain._load_custom_kw()) if hasattr(mbrain, '_load_custom_kw') else '?'}")
    except Exception as e:
        report.add("F1.manual", "skip",
                   f"دسترس نیست | {type(e).__name__}: {_short(e, 60)}")


# ═════════════════════════════════════════════════════════
# بخش G: حافظه‌ها
# ═════════════════════════════════════════════════════════
async def section_G_memory(report: TestReport, **kw):
    section("بخش G: حافظه‌ها (SafeMemory + BrainMemory + Cache)")

    # ─── G1: SafeMemory ───
    try:
        from core.safe_brain.storage import SafeMemory
        mem = SafeMemory("test_master_mem", max_entries=10)
        mem.add("user", "سلام تست")
        mem.add("assistant", "سلام! چطورم؟")
        mem.add("user", "خوبی؟")

        report.add("G1.memory.add", "ok",
                   f"count={mem.count}")

        ctx = mem.get_context(5)
        report.add("G2.memory.context", "ok",
                   f"len={len(ctx)}")

        results = mem.search("تست", n=3)
        report.add("G3.memory.search",
                   "ok" if results else "warn",
                   f"found={len(results)}")
    except Exception as e:
        report.add("G1.memory", "fail", f"{type(e).__name__}: {e}")

    # ─── G4: SafeCache ───
    try:
        from core.safe_brain.storage import SafeCache
        cache = SafeCache("test_master_cache", ttl_hours=1)
        cache.set("test_key", "test_value")
        val = cache.get("test_key")

        report.add("G4.cache.get",
                   "ok" if val == "test_value" else "warn",
                   f"hit={'✅' if val else '❌'}")

        stats = cache.stats()
        report.add("G4.cache.stats", "ok",
                   f"size={stats['size']} | "
                   f"hit_rate={stats['hit_rate']}")
    except Exception as e:
        report.add("G4.cache", "warn", f"{type(e).__name__}: {e}")

    # ─── G5: BrainMemory ───
    try:
        from core.brain.memory import BrainMemory
        bm = BrainMemory(max_tokens=10000, use_api=False)
        bm.add("user", "سلام به BrainMemory")
        bm.add("assistant", "سلام! آماده‌ام.")

        report.add("G5.brain_memory", "ok",
                   f"short_term={len(bm.short_term)} | "
                   f"summaries={len(bm.summaries)}")

        if hasattr(bm, "get_stats"):
            st = bm.get_stats()
            report.add("G5.brain_memory.stats", "ok",
                       f"short={st.get('short_term', 0)} | "
                       f"chroma={st.get('chroma', False)}")
    except Exception as e:
        report.add("G5.brain_memory", "warn",
                   f"{type(e).__name__}: {e}")


# ═════════════════════════════════════════════════════════
# بخش H: ۴۲ ابزار Brain
# ═════════════════════════════════════════════════════════
async def section_H_tools(report: TestReport,
                          brain=None,
                          skip_heavy: bool = False,
                          **kw):
    section("بخش H: ۴۲ ابزار Brain (تک‌تک)")

    if brain is None:
        report.add("H.tools", "skip", "brain نیست")
        return

    # ─── H1-H5: ابزارهای فایل (فقط read) ───
    try:
        r = await brain._read_file("config.py")
        if r and "AI_ENABLED" in r:
            report.add("H1.read_file", "ok",
                       f"len={len(r)} | config.py خوانده شد")
        else:
            report.add("H1.read_file", "warn",
                       "خوانده شد ولی محتوا نامنتظره")
    except Exception as e:
        report.add("H1.read_file", "fail", str(e))

    # ─── H2: list_directory ───
    try:
        r = await brain._list_directory("core")
        if r and len(r) > 10:
            report.add("H2.list_directory", "ok",
                       f"len={len(r)}")
        else:
            report.add("H2.list_directory", "warn", "خالی")
    except Exception as e:
        report.add("H2.list_directory", "fail", str(e))

    # ─── H3: analyze_code ───
    try:
        r = await brain._analyze_code("config.py")
        if r and "تحلیل" in r:
            report.add("H3.analyze_code", "ok", "OK")
        else:
            report.add("H3.analyze_code", "warn", _short(r, 60))
    except Exception as e:
        report.add("H3.analyze_code", "fail", str(e))

    # ─── H4: grep_search ───
    try:
        r = await brain._grep_search("def main", "*.py")
        if r and len(r) > 5:
            report.add("H4.grep_search", "ok",
                       f"len={len(r)}")
        else:
            report.add("H4.grep_search", "warn", "خالی")
    except Exception as e:
        report.add("H4.grep_search", "fail", str(e))

    # ─── H5: git_status ───
    try:
        r = await brain._git_status()
        report.add("H5.git_status", "ok",
                   f"len={len(r) if r else 0}")
    except Exception as e:
        report.add("H5.git_status", "warn", str(e))

    # ─── H6: get_system_info ───
    try:
        r = await brain._get_system_info()
        if r and "CPU" in r:
            report.add("H6.system_info", "ok", "OK")
        else:
            report.add("H6.system_info", "warn", _short(r, 60))
    except Exception as e:
        report.add("H6.system_info", "warn", str(e))

    # ─── H7: get_setting ───
    try:
        r = await brain._get_setting("AI_ENABLED")
        report.add("H7.get_setting", "ok",
                   f"{_short(r, 60)}")
    except Exception as e:
        report.add("H7.get_setting", "warn", str(e))

    # ─── H8: get_access_summary ───
    try:
        r = await brain._get_access_summary()
        report.add("H8.access", "ok",
                   f"len={len(r) if r else 0}")
    except Exception as e:
        report.add("H8.access", "warn", str(e))

    # ─── H9: system_dashboard ───
    if not skip_heavy:
        try:
            r = await brain._system_dashboard()
            if r and "داشبورد" in r:
                report.add("H9.dashboard", "ok", "OK")
            else:
                report.add("H9.dashboard", "warn", _short(r, 60))
        except Exception as e:
            report.add("H9.dashboard", "warn", str(e))
    else:
        report.add("H9.dashboard", "skip", "--skip-heavy")

    # ─── H10: append_knowledge (safe) ───
    # ⚠️ این تست واقعاً می‌نویسه — می‌خوایم مطمئن شویم قفل کار می‌کنه
    try:
        r = await brain._write_file(
            "data/prompts/bot_knowledge.md",
            "test overwrite",
        )
        if r and "قفل" in r:
            report.add("H10.append_knowledge.lock", "ok",
                       "قفل فعال ✅")
        else:
            report.add("H10.append_knowledge.lock", "fail",
                       f"قفل غیرفعال! r={_short(r, 60)}")
    except Exception as e:
        report.add("H10.append_knowledge.lock", "warn", str(e))


# ═════════════════════════════════════════════════════════
# بخش I: Intent Parsing (۲۰ پرامپت)
# ═════════════════════════════════════════════════════════
async def section_I_intent(report: TestReport,
                            skip_heavy: bool = False, **kw):
    section("بخش I: Intent Parsing (۲۰ پرامپت)")

    if skip_heavy:
        report.add("I.intent", "skip", "--skip-heavy")
        return

    try:
        from core.services.ai_assistant import get_ai_assistant
        ai = get_ai_assistant()

        passed = 0
        failed = 0
        unknown_count = 0
        t_all = time.monotonic()

        for prompt, expected_action in INTENT_PROMPTS:
            try:
                intent = await ai.parse_intent(prompt)
                action = intent.get("action", "unknown")

                if action == expected_action:
                    passed += 1
                elif action == "unknown":
                    unknown_count += 1
                else:
                    failed += 1
            except Exception:
                failed += 1

        total = len(INTENT_PROMPTS)
        elapsed = (time.monotonic() - t_all) * 1000

        # ─── امتیاز ───
        score = passed / total if total else 0

        if score >= 0.7:
            status = "ok"
        elif score >= 0.4:
            status = "warn"
        else:
            status = "fail"

        report.add("I.intent.all", status,
                   f"{passed}/{total} صحیح | "
                   f"unknown={unknown_count} | "
                   f"wrong={failed} | "
                   f"score={score:.0%} | "
                   f"{elapsed:.0f}ms",
                   elapsed)

        print("\n  📊 نتایج:")
        print(f"     ✅ صحیح:   {passed}/{total}")
        print(f"     ❓ unknown: {unknown_count}")
        print(f"     ❌ اشتباه:  {failed}")
        print(f"     🎯 امتیاز:  {score:.0%}")
    except Exception as e:
        report.add("I.intent", "fail", f"{type(e).__name__}: {e}")


# ═════════════════════════════════════════════════════════
# بخش J: Pipeline + DB
# ═════════════════════════════════════════════════════════
async def section_J_pipeline(report: TestReport, **kw):
    section("بخش J: Pipeline + DB + Storage")

    # ─── J1: pipeline ───
    try:
        from tests.pipeline_helpers import TestPipeline
        async with TestPipeline() as pipeline:
            report.add("J1.pipeline.enter", "ok", "راه افتاد")

            test_msg = {
                "id": f"ai_master_test_{int(time.time())}",
                "platform": "ai_master_test",
                "text": "کود مایع یاراترا موجوده؟ قیمت چنده؟",
                "chat_id": "test_chat",
                "chat_name": "تست مستر",
                "chat_type": "group",
                "user_id": "test_user",
                "sender_name": "تستر",
                "timestamp": datetime.now(),
                "media_type": "text",
            }

            stored = await pipeline.feed([test_msg])
            report.add("J1.pipeline.feed", "ok",
                       f"stored={stored}")

            count = await pipeline.count_messages(platform="ai_master_test")
            report.add("J1.pipeline.count", "ok",
                       f"messages={count}")
    except Exception as e:
        report.add("J1.pipeline", "warn",
                   f"{type(e).__name__}: {_short(e, 80)}")

    # ─── J2: db_helpers ───
    try:
        from tests.db_helpers import (
            count_messages,
            db_available,
            db_size_kb,
        )
        avail = db_available()
        report.add("J2.db.available",
                   "ok" if avail else "warn",
                   f"available={avail}")
        if avail:
            report.add("J2.db.size", "ok",
                       f"{db_size_kb():.1f}KB")
            report.add("J2.db.messages", "ok",
                       f"total={count_messages()}")
    except Exception as e:
        report.add("J2.db_helpers", "warn", str(e))

# ═════════════════════════════════════════════════════════
# بخش K: Performance + Error
# ═════════════════════════════════════════════════════════
async def section_K_perf(report: TestReport, **kw):
    section("بخش K: Performance + Error Handling")

    # ─── K1: error handling ───
    try:
        from core.services.ai_assistant import get_ai_assistant
        ai = get_ai_assistant()

        # پرامپت خالی
        resp, ok = await ai.chat_with_status("")
        if not ok and not resp:
            report.add("K1.empty_prompt", "ok", "رد شد")
        else:
            report.add("K1.empty_prompt", "warn",
                       f"ok={ok} | resp={_short(resp, 40)}")

        # پرامپت None
        resp2, ok2 = await ai.chat_with_status(None)  # type: ignore
        if not ok2:
            report.add("K1.none_prompt", "ok", "رد شد")
        else:
            report.add("K1.none_prompt", "warn", "None پذیرفته شد!")

        # پرامپت بلند
        resp3, ok3 = await ai.chat_with_status("x" * 50_000)
        report.add("K1.long_prompt", "ok",
                   f"ok={ok3} | truncated")
    except Exception as e:
        report.add("K1.error_handling", "fail",
                   f"{type(e).__name__}: {e}")

    # ─── K2: fallback chain ───
    try:
        from core.services.ai_assistant import get_ai_assistant
        ai = get_ai_assistant()
        order = ai._provider_order()
        report.add("K2.fallback",
                   "ok" if len(order) > 1 else "warn",
                   f"chain={order}")

        primary = order[0] if order else None
        if primary == "deepseek" and not ai._deepseek_key:
            report.add("K2.primary_key", "fail",
                       "primary=deepseek ولی کلید نیست")
        else:
            report.add("K2.primary_key", "ok",
                       f"primary={primary}")
    except Exception as e:
        report.add("K2.fallback", "warn", str(e))

    # ─── K3: cache hit ───
    try:
        from core.safe_brain import get_safe_brain
        sb = get_safe_brain()

        if sb:
            test_text = f"تست cache master {int(time.time())}"
            t0 = time.monotonic()
            r1 = await sb.process(test_text)
            t1 = (time.monotonic() - t0) * 1000

            t0 = time.monotonic()
            r2 = await sb.process(test_text)
            t2 = (time.monotonic() - t0) * 1000

            if t2 < t1 * 0.5 and r1 == r2:
                report.add("K3.cache.hit", "ok",
                           f"بار1={t1:.0f}ms بار2={t2:.0f}ms")
            else:
                report.add("K3.cache.hit", "warn",
                           f"بار1={t1:.0f}ms بار2={t2:.0f}ms")
    except Exception as e:
        report.add("K3.cache", "warn", str(e))


# ═════════════════════════════════════════════════════════
# بخش L: گزارش نهایی
# ═════════════════════════════════════════════════════════
async def section_L_final(report: TestReport,
                           safe_brain=None,
                           brain=None,
                           total_t0: float = 0, **kw):
    section("بخش L: گزارش نهایی")

    total_elapsed = time.monotonic() - total_t0

    # ─── L1: SafeBrain stats ───
    if safe_brain:
        try:
            safe_brain.flush()
            st = safe_brain.get_stats()
            report.add("L1.safebrain_stats", "ok",
                       f"processed={st.get('processed', 0)} | "
                       f"cached={st.get('cached', 0)} | "
                       f"delegated={st.get('delegated', 0)} | "
                       f"fallback={st.get('fallback', 0)} | "
                       f"errors={st.get('errors', 0)}")
        except Exception as e:
            report.add("L1.safebrain_stats", "warn", str(e))

    # ─── L2: Brain stats ───
    if brain:
        try:
            st = brain.stats if hasattr(brain, "stats") else {}
            report.add("L2.brain_stats", "ok",
                       f"commands={st.get('commands_processed', 0)} | "
                       f"agents={st.get('agents_run', 0)} | "
                       f"errors={st.get('errors', 0)}")
        except Exception as e:
            report.add("L2.brain_stats", "warn", str(e))

    # ─── L3: کل زمان ───
    report.add("L3.total_time", "ok",
               f"{total_elapsed:.1f}s",
               total_elapsed * 1000)


# ═════════════════════════════════════════════════════════
# main
# ═════════════════════════════════════════════════════════
async def main(
    quick: bool = False,
    skip_deepseek: bool = False,
    skip_ollama: bool = False,
    deepseek_only: bool = False,
    skip_heavy: bool = False,
    only_section: str = "",
    verbose: bool = False,
):
    banner("تست جامع هوش مصنوعی — همه چیز در یک فایل")
    report = TestReport("ai_master")
    total_t0 = time.monotonic()

    # ─── تعیین بخش‌ها ───
    all_sections = {
        "A": section_A_imports,
        "B": section_B_connections,
        "C": section_C_assistant,
        "D": section_D_safebrain,
        "E": section_E_brain,
        "F": section_F_manual,
        "G": section_G_memory,
        "H": section_H_tools,
        "I": section_I_intent,
        "J": section_J_pipeline,
        "K": section_K_perf,
        "L": section_L_final,
    }

    sections_to_run = (
        [only_section.upper()] if only_section
        else list(all_sections.keys())
    )

    # ─── متغیرهای اشتراکی ───
    shared = {
        "quick": quick,
        "skip_deepseek": skip_deepseek,
        "skip_ollama": skip_ollama,
        "deepseek_only": deepseek_only,
        "skip_heavy": skip_heavy,
        "verbose": verbose,
    }
    safe_brain = None
    brain = None

    # ─── اجرای بخش‌ها ───
    for sec_key in sections_to_run:
        sec_fn = all_sections.get(sec_key)
        if not sec_fn:
            continue
        try:
            # ─── D → return safe_brain ───
            if sec_key == "D":
                safe_brain = await sec_fn(report, **shared)
                continue
            # ─── E → return brain ───
            if sec_key == "E":
                brain = await sec_fn(report, **shared)
                continue
            # ─── H → نیاز به brain ───
            if sec_key == "H":
                await sec_fn(report, brain=brain, **shared)
                continue
            # ─── L → نیاز به safe_brain + brain ───
            if sec_key == "L":
                await sec_fn(report, safe_brain=safe_brain,
                             brain=brain, total_t0=total_t0, **shared)
                continue
            # ─── بقیه ───
            await sec_fn(report, **shared)
        except Exception as e:
            report.add(f"section.{sec_key}", "fail",
                       f"{type(e).__name__}: {e}")
            import traceback
            if verbose:
                for ln in traceback.format_exc().splitlines()[-5:]:
                    print(f"  {C.DIM}↳ {ln}{C.END}")

    # ─── نتیجه ───
    summary = report.summary()
    total_elapsed = time.monotonic() - total_t0

    ok = report.print_summary()
    report.save()

    # ─── خلاصه ───
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
        print(f"\n  {C.OK}🎉 همهٔ تست‌ها موفق!{C.END}")
    else:
        print(f"\n  {C.FAIL}❌ {summary['fail']} تست ناموفق{C.END}")

    print("=" * 70)
    return ok


# ═════════════════════════════════════════════════════════
# Entry point
# ═════════════════════════════════════════════════════════
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="تست جامع هوش مصنوعی — همه چیز در یک فایل",
    )
    parser.add_argument("--quick", action="store_true",
                        help="حالت سریع (بدون Brain.agent + Intent)")
    parser.add_argument("--skip-deepseek", action="store_true")
    parser.add_argument("--skip-ollama", action="store_true")
    parser.add_argument("--deepseek-only", action="store_true")
    parser.add_argument("--skip-heavy", action="store_true",
                        help="بدون تست‌های سنگین (agent, intent, dashboard)")
    parser.add_argument("--section", type=str, default="",
                        help="فقط یک بخش (A/B/C/D/E/F/G/H/I/J/K/L)")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    ok = asyncio.run(
        main(
            quick=args.quick,
            skip_deepseek=args.skip_deepseek,
            skip_ollama=args.skip_ollama,
            deepseek_only=args.deepseek_only,
            skip_heavy=args.skip_heavy,
            only_section=args.section,
            verbose=args.verbose,
        )
    )
    sys.exit(0 if ok else 1)