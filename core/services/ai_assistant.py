# core/services/ai_assistant.py
"""
AIAssistant — لایهٔ نازک برای فراخوانی مدل AI (Ollama لوکال یا DeepSeek آنلاین).

قابلیت‌ها:
  - chat(text, system=""): چت آزاد با system prompt
  - chat_with_status(text, system=""): چت با پرچم موفقیت/شکست
  - parse_intent(text): متن فارسی → {"action": ..., "args": {...}} با whitelist
  - available(): آیا هیچ providerی در دسترس است؟ (با cache TTL)
  - اولویت از .env: AI_PRIORITY=auto|deepseek|ollama
      auto     → اول DeepSeek (اگه کلید)، وگرنه Ollama
      deepseek → فقط DeepSeek
      ollama   → فقط Ollama

⚠️ این فایل روی اکشن‌های ChatOps کار نمی‌کند — فقط NLU + چت.
اکشن‌ها در admin_handler.py / safe_brain.py / brain.py پیاده شده‌اند.

نسخهٔ ۴ (این نسخه):
  - 🔴 FIX بحرانی: _extract_json کامل شد (قبلاً ناقص بود و فایل
    اصلاً parse نمی‌شد → هیچ‌چیز import نمی‌شد).
  - 🔴 FIX بحرانی: get_ai_assistant() singleton اضافه شد
    (قبلاً غایب بود → admin_handler / safe_brain / brain همه
    ImportError می‌گرفتن).
  - 🟠 FIX: enabled حالا با _env_bool (پشتیبانی از 1/yes/on/TRUE).
  - 🟠 FIX: آدرس DeepSeek حالا از env (DEEPSEEK_API_URL) با پیش‌فرض.
  - ✅ حفظ همهٔ FIXهای نسخهٔ ۳.
"""
from __future__ import annotations

import os
import re
import json
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from core.kernel.logger import log_info, log_warning, log_error

try:
    import aiohttp
except ImportError:
    aiohttp = None


# ═══════════════════════════════════════════
# env helpers
# ═══════════════════════════════════════════
_TRUE = {"true", "1", "yes", "on", "y", "t"}
_FALSE = {"false", "0", "no", "off", "n", "f"}


def _env_bool(name: str, default: bool = True) -> bool:
    """خواندن bool از env — پشتیبانی از 1/yes/on/TRUE/..."""
    raw = os.getenv(name)
    if raw is None:
        return default
    v = str(raw).strip().lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        return default


def _env_str(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


# ═══════════════════════════════════════════
# Persona (از data/prompts/persona.md)
# ═══════════════════════════════════════════
_DEFAULT_PERSONA = (
    "تو «WinDoctor» هستی؛ مغزِ هوشمندِ پروژهٔ پایشِ سرنخِ فروشِ سپاهان رویش. "
    "همیشه به فارسی، کوتاه، دقیق و حرفه‌ای جواب بده. "
    "هرگز نگو که «کلود» یا یک مدل هوش مصنوعی بیرونی هستی."
)


def _load_persona() -> str:
    try:
        from data import paths as data_paths
        p = str(data_paths.PERSONA)
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                return f.read().strip()
    except Exception:
        pass
    return _DEFAULT_PERSONA


def _load_intent_system() -> str:
    """system.md — راهنمای router برای parse_intent."""
    try:
        from data import paths as data_paths
        p = str(data_paths.SYSTEM)
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                return f.read().strip()
    except Exception:
        pass
    return ""


PERSONA = _load_persona()
_INTENT_SYSTEM = _load_intent_system()


# اکشن‌های مجاز — همخوان با data/prompts/system.md
_ALLOWED_ACTIONS = frozenset({
    "status", "report", "crm", "logs", "keywords",
    "add_keyword", "remove_keyword", "has_keyword",
    "platform_status", "health", "lead_details",
    "get_param", "set_param", "restart", "explain_logs",
    "feature_request", "ask", "help",
})

_LOG_PREVIEW_LEN = 80

# 🎯 fallback prefix — safe_brain این را می‌شناسد
_FALLBACK_PREFIX = "⚠️ هوش در دسترس نیست"


# ═══════════════════════════════════════════
# تنظیمات از env
# ═══════════════════════════════════════════
_AVAILABLE_CACHE_S = _env_float("AI_AVAILABLE_CACHE_S", 30.0)


# ═══════════════════════════════════════════
# AIAssistant
# ═══════════════════════════════════════════
class AIAssistant:
    """دستیار هوش مصنوعی — سبک، بدون DB، فقط AI."""

    def __init__(self):
        # ─── تنظیمات ───
        # 🟠 FIX: به‌جای .lower() == "true" از _env_bool استفاده می‌کنیم
        self.enabled = (
            _env_bool("AI_ENABLED", True)
            and _env_bool("AI_ASSISTANT_ENABLED", True)
        )
        self._priority = (
            _env_str("AI_PRIORITY", "auto").lower() or "auto"
        )

        # DeepSeek
        self._deepseek_key = _env_str("DEEPSEEK_API_KEY")
        # 🟠 FIX: URL از env با پیش‌فرض (قبلاً هاردکد بود)
        self._deepseek_url = _env_str(
            "DEEPSEEK_API_URL",
            "https://api.deepseek.com/v1/chat/completions",
        )
        self._deepseek_model = _env_str(
            "DEEPSEEK_MODEL", "deepseek-chat"
        ) or "deepseek-chat"

        # Ollama
        self._ollama_url = _env_str(
            "OLLAMA_URL", "http://localhost:11434"
        ).rstrip("/")
        self._ollama_model = _env_str(
            "OLLAMA_MODEL", "qwen2.5:3b"
        ) or "qwen2.5:3b"

        # Timeout
        self._timeout = _env_int("AI_TIMEOUT", 120)

        # 🎯 cache provider order (نه هر بار محاسبه)
        self._provider_cache: Optional[List[str]] = None

        # 🎯 cache available (TTL)
        self._available_cache: Optional[bool] = None
        self._available_cache_at: float = 0.0

        log_info(
            f"🔧 [AI:init] آماده | enabled={self.enabled} | "
            f"priority={self._priority} | "
            f"deepseek_key={'✅' if self._deepseek_key else '❌'} | "
            f"ollama={self._ollama_url} ({self._ollama_model}) | "
            f"available_cache={_AVAILABLE_CACHE_S}s"
        )

    # ═══════════════════════════════════════════
    # available (با cache)
    # ═══════════════════════════════════════════
    async def available(self) -> bool:
        """
        آیا هیچ providerی آماده است؟

        🔴 FIX: حالا نتیجه تا ۳۰ ثانیه cache می‌شود.
        🎯 FIX: حالا priority را در نظر می‌گیرد.
        """
        if not self.enabled or aiohttp is None:
            return False

        # 🎯 چک سریع بر اساس priority
        if self._priority == "deepseek" and not self._deepseek_key:
            return False

        # 🔴 cache check
        now = time.monotonic()
        if (
            self._available_cache is not None
            and (now - self._available_cache_at) < _AVAILABLE_CACHE_S
        ):
            return self._available_cache

        # ─── ping واقعی ───
        result = False

        # اگر priority=deepseek و کلید هست → مستقیم True (بدون ping)
        if self._priority == "deepseek" and self._deepseek_key:
            result = True
        else:
            # ping Ollama
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"{self._ollama_url}/api/tags",
                        timeout=aiohttp.ClientTimeout(total=3),
                    ) as resp:
                        if resp.status == 200:
                            result = True
            except Exception as e:
                log_info(
                    f"ℹ️ [AI:available] Ollama نیست | "
                    f"{type(e).__name__}: {e}"
                )

            # fallback: deepseek key
            if not result and self._deepseek_key:
                log_info(
                    "ℹ️ [AI:available] Ollama نیست ولی کلید DeepSeek هست"
                )
                result = True

        # 🔴 cache result
        self._available_cache = result
        self._available_cache_at = now
        return result

    # ═══════════════════════════════════════════
    # chat
    # ═══════════════════════════════════════════
    async def chat(self, text: str, system: str = "") -> str:
        """
        چت آزاد با متن.

        🟠 FIX: در صورت شکست، fallback با prefix مشخص
        (_FALLBACK_PREFIX) برمی‌گرداند که safe_brain می‌شناسد.
        """
        response, _ok = await self.chat_with_status(text, system)
        return response

    # ═══════════════════════════════════════════
    # chat_with_status (جدید)
    # ═══════════════════════════════════════════
    async def chat_with_status(
        self, text: str, system: str = ""
    ) -> Tuple[str, bool]:
        """
        چت با پرچم موفقیت/شکست.

        🟠 FIX: safe_brain/main.py از این استفاده می‌کند تا
        is_fallback را دقیق تشخیص دهد.

        Returns:
            (response_text, success_flag)
            success_flag=False → fallback (cache نباید بشود)
        """
        if not text or not self.enabled:
            return "", False

        full_prompt = f"{system}\n\n{text}" if system else text

        for name in self._provider_order():
            try:
                if name == "ollama":
                    result = await self._call_ollama(full_prompt)
                elif name == "deepseek":
                    result = await self._call_deepseek(full_prompt)
                else:
                    continue

                if result:
                    return result, True
            except Exception as e:
                log_warning(
                    f"⚠️ [AI:chat:{name}] {type(e).__name__}: {e}"
                )

        log_warning("⚠️ [AI:chat] هیچ providerی جواب نداد")

        # 🟠 FIX: prefix مشخص برای تشخیص fallback
        fallback_msg = (
            f"{_FALLBACK_PREFIX}\n"
            f"Ollama لوکال را روشن کن یا DEEPSEEK_API_KEY بگذار."
        )
        return fallback_msg, False

    # ═══════════════════════════════════════════
    # parse_intent
    # ═══════════════════════════════════════════
    async def parse_intent(self, text: str) -> Dict[str, Any]:
        """متن → {"action":..., "args":{...}} با whitelist."""
        if not text:
            return {"action": "unknown", "args": {}}

        prompt = text
        if _INTENT_SYSTEM:
            prompt = f"{_INTENT_SYSTEM}\n\nUser message: {text}"

        raw: Optional[str] = None
        for name in self._provider_order():
            try:
                if name == "ollama":
                    candidate = await self._call_ollama(
                        prompt, format_json=True
                    )
                elif name == "deepseek":
                    candidate = await self._call_deepseek(
                        prompt, format_json=True
                    )
                else:
                    continue
                if candidate:
                    raw = candidate
                    break
            except Exception as e:
                log_warning(
                    f"⚠️ [AI:intent:{name}] {type(e).__name__}: {e}"
                )

        if not raw:
            return {"action": "unknown", "args": {}}

        parsed = self._extract_json(raw)
        if not parsed or not isinstance(parsed, dict):
            log_warning(
                f"⚠️ [AI:intent] JSON نامعتبر | "
                f"raw={raw[:_LOG_PREVIEW_LEN]!r}"
            )
            return {"action": "unknown", "args": {}}

        action = str(parsed.get("action", "unknown")).strip().lower()
        args = parsed.get("args") or {}
        if not isinstance(args, dict):
            args = {}

        if action not in _ALLOWED_ACTIONS:
            log_warning(
                f"⚠️ [AI:intent] اکشن غیرمجاز | action={action!r}"
            )
            return {"action": "unknown", "args": {}}

        return {"action": action, "args": args}

    # ═══════════════════════════════════════════
    # provider order (با cache)
    # ═══════════════════════════════════════════
    def _provider_order(self) -> List[str]:
        """
        ترتیب providerها بر اساس priority.

        🎯 FIX: حالا cache می‌شود.
        """
        if self._provider_cache is not None:
            return self._provider_cache

        p = self._priority
        if p == "deepseek":
            result = ["deepseek"] if self._deepseek_key else []
        elif p == "ollama":
            if self._deepseek_key:
                result = ["ollama", "deepseek"]
            else:
                result = ["ollama"]
        else:  # auto
            if self._deepseek_key:
                result = ["deepseek", "ollama"]
            else:
                result = ["ollama"]

        self._provider_cache = result
        return result

    # ═══════════════════════════════════════════
    # Ollama
    # ═══════════════════════════════════════════
    async def _call_ollama(
        self, prompt: str, format_json: bool = False
    ) -> Optional[str]:
        if aiohttp is None:
            return None
        try:
            payload: Dict[str, Any] = {
                "model": self._ollama_model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
            }
            if format_json:
                payload["format"] = "json"

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self._ollama_url}/api/chat",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=self._timeout),
                ) as resp:
                    if resp.status != 200:
                        # 🎯 FIX: لاگ status
                        try:
                            body = (await resp.text())[:_LOG_PREVIEW_LEN]
                        except Exception:
                            body = ""
                        log_warning(
                            f"⚠️ [AI:ollama] status={resp.status} | "
                            f"body={body!r}"
                        )
                        return None
                    data = await resp.json()
                    return (data.get("message") or {}).get("content")
        except Exception as e:
            log_warning(f"⚠️ [AI:ollama] {type(e).__name__}: {e}")
            return None

    # ═══════════════════════════════════════════
    # DeepSeek
    # ═══════════════════════════════════════════
    async def _call_deepseek(
        self, prompt: str, format_json: bool = False
    ) -> Optional[str]:
        if not self._deepseek_key or aiohttp is None:
            return None
        try:
            payload: Dict[str, Any] = {
                "model": self._deepseek_model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
            }
            if format_json:
                payload["response_format"] = {"type": "json_object"}

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self._deepseek_url,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self._deepseek_key}",
                        "Content-Type": "application/json",
                    },
                    timeout=aiohttp.ClientTimeout(total=self._timeout),
                ) as resp:
                    if resp.status != 200:
                        # 🎯 FIX: لاگ status
                        try:
                            body = (await resp.text())[:_LOG_PREVIEW_LEN]
                        except Exception:
                            body = ""
                        log_warning(
                            f"⚠️ [AI:deepseek] status={resp.status} | "
                            f"body={body!r}"
                        )
                        return None
                    data = await resp.json()
                    return (
                        (data.get("choices") or [{}])[0]
                        .get("message", {})
                        .get("content")
                    )
        except Exception as e:
            log_warning(f"⚠️ [AI:deepseek] {type(e).__name__}: {e}")
            return None

    # ═══════════════════════════════════════════
    # JSON helper
    # ═══════════════════════════════════════════
    @staticmethod
    def _extract_json(text: str) -> Optional[Dict[str, Any]]:
        """
        استخراج اولین شیء JSON از متن.

        حالا markdown code block را هم پشتیبانی می‌کند (بلاک
        fenced با برچسب json یا بدون برچسب)، و اگر متن مستقیم
        JSON باشد هم کار می‌کند.

        Args:
            text: متن خام (ممکنه JSON خالص، داخل code block، یا
                  همراه با توضیح اضافه باشد).

        Returns:
            dict اگر JSON معتبر پیدا شد، وگرنه None.
        """
        if not text:
            return None

        s = str(text).strip()

        # ─── ۱) تلاش مستقیم ───
        try:
            data = json.loads(s)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, ValueError):
            pass

        # ─── ۲) markdown code block ───
        m = re.search(
            r"`{3}(?:json|JSON)?\s*\n?(.*?)\n?`{3}",
            s,
            re.DOTALL,
        )
        if m:
            inner = m.group(1).strip()
            try:
                data = json.loads(inner)
                if isinstance(data, dict):
                    return data
            except (json.JSONDecodeError, ValueError):
                pass

        # ─── ۳) اولین { ... } متوازن ───
        start = s.find("{")
        end = s.rfind("}")
        if start != -1 and end > start:
            candidate = s[start : end + 1]
            try:
                data = json.loads(candidate)
                if isinstance(data, dict):
                    return data
            except (json.JSONDecodeError, ValueError):
                pass

        # ─── ۴) regex غیرحریص (fallback) ───
        m = re.search(r"\{.*?\}", s, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(0))
                if isinstance(data, dict):
                    return data
            except (json.JSONDecodeError, ValueError):
                pass

        return None


# ═══════════════════════════════════════════
# Singleton
# ═══════════════════════════════════════════
_shared_ai: Optional[AIAssistant] = None
_shared_lock = threading.Lock()


def get_ai_assistant() -> AIAssistant:
    """
    دریافت نمونهٔ Singleton AIAssistant.

    🔴 FIX: این تابع قبلاً کاملاً غایب بود → همهٔ فایل‌هایی که
    get_ai_assistant را import می‌کردن (admin_handler, safe_brain,
    brain, ...) ImportError می‌گرفتن.

    Returns:
        AIAssistant: نمونهٔ مشترک (thread-safe).
    """
    global _shared_ai
    if _shared_ai is None:
        with _shared_lock:
            if _shared_ai is None:
                log_info("🏗️ [AI:singleton] ساخت نمونهٔ اول")
                _shared_ai = AIAssistant()
    return _shared_ai


def reset_ai_assistant() -> None:
    """
    پاک‌کردن singleton — فقط برای تست.

    ⚠️ در runtime صدا نزن — ممکنه referenceهای موجود بی‌اعتبار بشن.
    """
    global _shared_ai
    with _shared_lock:
        _shared_ai = None
        log_info("🗑️ [AI:singleton] reset شد")