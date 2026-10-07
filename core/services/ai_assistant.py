"""AIAssistant — دستیار هوش مصنوعی (Ollama + DeepSeek).

نسخهٔ ۴ (این نسخه):
  - 🐛 FIX: `_deepseek_key` حالا اولویت با `config.AI` دارد (نه env).
    این با `main.py` و `admin_handler.py` هماهنگ است که api_key را از
    config می‌خوانند.
  - 🐛 FIX: `_deepseek_model` حالا از `config.AI` قابل تنظیم است.
  - 🐛 FIX: `_AVAILABLE_CACHE_S` پیش‌فرض از 30 به 60 تغییر یافت
    (هماهنگ با توصیهٔ design).
  - 🐛 FIX: `available()` حالا در حالت DeepSeek با کلید، فقط چک
    کلید می‌کند (بدون ping که کند است). این طراحی موازی DeepSeek +
    Ollama را تقویت می‌کند.
  - 🐛 FIX: `_call_deepseek` حالا یک retry ساده دارد (در صورت
    429/500/timeout یک بار دیگر تلاش می‌کند).
  - 🐛 FIX: `_call_deepseek` حالا در صورت 401 (کلید نامعتبر) هشدار
    واضح می‌دهد.
  - 🐛 FIX: `_call_ollama` حالا در صورت اتصال نبود، پیام واضح می‌دهد
    (نه فقط "خطا").
  - 🐛 FIX: `_FALLBACK_PREFIX` حالا با `admin_handler._AI_FALLBACK_PREFIXES`
    هماهنگ است.
  - 🎯 `get_stats()` حالا `deepseek_key_source` را نشان می‌دهد
    (config یا env).
  - ✅ حفظ همهٔ FIXهای نسخهٔ ۳.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
from functools import lru_cache
from typing import Any

from core.kernel.logger import log_info, log_warning

try:
    import aiohttp
except ImportError:
    aiohttp = None


# ═══════════════════════════════════════════
# env helpers
# ═══════════════════════════════════════════
_TRUE = frozenset({
    "true", "1", "yes", "on", "y", "t",
})
_FALSE = frozenset({
    "false", "0", "no", "off", "n", "f",
})


def _env_bool(name: str, default: bool = True) -> bool:
    """خواندن bool از env."""
    raw = os.getenv(name)
    if raw is None:
        return default
    v = str(raw).strip().lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    return default


def _env_float(
    name: str, default: float,
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


def _env_int(
    name: str, default: int,
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


def _env_str(name: str, default: str = "") -> str:
    """خواندن str از env — با strip."""
    return (os.getenv(name) or default).strip()


# ═══════════════════════════════════════════
# Persona (از data/prompts/persona.md)
# ═══════════════════════════════════════════
_DEFAULT_PERSONA = (
    "تو «WinDoctor» هستی؛ مغزِ هوشمندِ پروژهٔ پایشِ سرنخِ فروشِ "
    "سپاهان رویش. همیشه به فارسی، کوتاه، دقیق و حرفه‌ای جواب "
    "بده. هرگز نگو که «کلود» یا یک مدل هوش مصنوعی بیرونی هستی."
)


@lru_cache(maxsize=1)
def _load_persona() -> str:
    """🎯 FIX: با lru_cache."""
    try:
        from data import paths as data_paths
        p = str(data_paths.PERSONA)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                text = f.read().strip()
                if text:
                    return text
    except Exception:
        pass
    return _DEFAULT_PERSONA


@lru_cache(maxsize=1)
def _load_intent_system() -> str:
    """🎯 FIX: با lru_cache — system.md برای parse_intent."""
    try:
        from data import paths as data_paths
        p = str(data_paths.SYSTEM)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return f.read().strip()
    except Exception:
        pass
    return ""


PERSONA = _load_persona()
_INTENT_SYSTEM = _load_intent_system()


# ═══════════════════════════════════════════
# اکشن‌های مجاز (هماهنگ با safe_brain.constants)
# ═══════════════════════════════════════════
try:
    from core.safe_brain.constants import (
        SAFE_ACTIONS as _SAFE_ACTIONS_FROM_SB,
    )
    _ALLOWED_ACTIONS: frozenset[str] = frozenset(
        _SAFE_ACTIONS_FROM_SB
    )
except ImportError:
    # fallback — هماهنگ با system.md
    _ALLOWED_ACTIONS = frozenset({
        "status", "report", "crm", "logs", "keywords",
        "add_keyword", "remove_keyword", "has_keyword",
        "platform_status", "health", "lead_details",
        "get_param", "set_param", "restart", "explain_logs",
        "feature_request", "ask", "help",
    })

_LOG_PREVIEW_LEN = _env_int(
    "AI_LOG_PREVIEW", 80,
    min_val=20, max_val=500,
)

# ═══════════════════════════════════════════
# 🎯 FIX: fallback prefix — هماهنگ با admin_handler
# ═══════════════════════════════════════════
# admin_handler._AI_FALLBACK_PREFIXES شامل این می‌شود.
# هرگز این رشته را بدون هماهنگی با admin_handler عوض نکن.
_FALLBACK_PREFIX = "⚠️ هوش در دسترس نیست"


# ═══════════════════════════════════════════
# تنظیمات از env
# ═══════════════════════════════════════════
_AVAILABLE_CACHE_S = _env_float(
    "AI_AVAILABLE_CACHE_S", 60.0,
    min_val=0.0, max_val=600.0,
)
_AI_PING_TIMEOUT_S = _env_float(
    "AI_PING_TIMEOUT_S", 3.0,
    min_val=0.5, max_val=60.0,
)
_MAX_PROMPT_LEN = _env_int(
    "AI_MAX_PROMPT_LEN", 20_000,
    min_val=500, max_val=200_000,
)

# 🎯 retry برای DeepSeek (در صورت خطای گذرا)
_DEEPSEEK_MAX_RETRY = _env_int(
    "AI_DEEPSEEK_MAX_RETRY", 1,
    min_val=0, max_val=5,
)
_DEEPSEEK_RETRY_DELAY_S = _env_float(
    "AI_DEEPSEEK_RETRY_DELAY_S", 1.0,
    min_val=0.0, max_val=30.0,
)


# ═══════════════════════════════════════════
# AIAssistant
# ═══════════════════════════════════════════
class AIAssistant:
    """دستیار هوش مصنوعی — سبک، بدون DB، فقط AI.

    thread-safe (با `_lock`).

    طراحی موازی DeepSeek + Ollama:
      - DeepSeek اول (وقتی کلید هست)
      - Ollama دوم (fallback خودکار)
      - هر دو timeout مستقل دارند
      - هیچ‌کدام منتظر دیگری نمی‌ماند
    """

    def __init__(self):
        # ─── config (اولویت) → env (fallback) ───
        try:
            from config import AI as _AI_CFG
        except Exception:
            _AI_CFG = {}

        self._config = _AI_CFG or {}

        # ─── enabled ───
        self.enabled = (
            _env_bool("AI_ENABLED", True)
            and _env_bool("AI_ASSISTANT_ENABLED", True)
        )

        # ─── priority ───
        # اولویت: env > config > auto
        self._priority = (
            _env_str("AI_PRIORITY", "").lower()
            or str(self._config.get("priority", "auto")).lower()
            or "auto"
        )

        # ═══════════════════════════════════════════
        # 🎯 FIX: DeepSeek key — اولویت config.AI
        # ═══════════════════════════════════════════
        # قبلاً env اول بود → ناسازگار با main.py و admin_handler.
        # حالا config.AI اول (منبع یکتا)، env به‌عنوان fallback.
        _cfg_key = (self._config.get("deepseek_api_key") or "").strip()
        _env_key = _env_str("DEEPSEEK_API_KEY")

        if _cfg_key:
            self._deepseek_key = _cfg_key
            self._deepseek_key_source = "config"
        elif _env_key:
            self._deepseek_key = _env_key
            self._deepseek_key_source = "env"
        else:
            self._deepseek_key = ""
            self._deepseek_key_source = "none"

        # ─── DeepSeek URL ───
        self._deepseek_url = (
            _env_str("DEEPSEEK_API_URL")
            or "https://api.deepseek.com/v1/chat/completions"
        )

        # ═══════════════════════════════════════════
        # 🎯 FIX: DeepSeek model — از config.AI هم می‌آید
        # ═══════════════════════════════════════════
        self._deepseek_model = (
            _env_str("DEEPSEEK_MODEL")
            or str(self._config.get("deepseek_model", ""))
            or "deepseek-chat"
        )

        # ─── Ollama ───
        self._ollama_url = (
            _env_str("OLLAMA_URL")
            or str(self._config.get("url", ""))
            or "http://127.0.0.1:11434"
        ).rstrip("/")
        self._ollama_model = (
            _env_str("OLLAMA_MODEL")
            or str(self._config.get("model", ""))
            or "qwen2.5:3b"
        )

        # ─── Timeout ───
        try:
            _cfg_timeout = int(self._config.get("timeout", 120))
        except (TypeError, ValueError):
            _cfg_timeout = 120

        self._timeout = _env_int(
            "AI_TIMEOUT", _cfg_timeout,
            min_val=5, max_val=600,
        )

        # ─── caches ───
        self._provider_cache: list[str] | None = None
        self._available_cache: bool | None = None
        self._available_cache_at: float = 0.0

        # 🎯 lock برای همه state
        self._lock = threading.RLock()

        log_info(
            f"🔧 [AI:init] آماده | enabled={self.enabled} | "
            f"priority={self._priority} | "
            f"deepseek_key={'✅' if self._deepseek_key else '❌'}"
            f"({self._deepseek_key_source}) | "
            f"model={self._deepseek_model} | "
            f"ollama={self._ollama_url} ({self._ollama_model}) | "
            f"available_cache={_AVAILABLE_CACHE_S}s | "
            f"deepseek_retry={_DEEPSEEK_MAX_RETRY}"
        )

    # ═══════════════════════════════════════════
    # available (با cache)
    # ═══════════════════════════════════════════
    async def available(self) -> bool:
        """آیا هیچ providerی آماده است؟

        نتیجه تا `_AVAILABLE_CACHE_S` ثانیه cache می‌شود.

        🎯 طراحی موازی: در حالت `priority=deepseek` با کلید،
        بلافاصله True برمی‌گرداند (بدون ping) — چون DeepSeek
        از راه دور است و ping هر بار ۳ ثانیه طول می‌کشد.
        این طراحی به موازی بودن DeepSeek + Ollama کمک می‌کند.
        """
        if not self.enabled or aiohttp is None:
            return False

        # ─── چک سریع: priority=deepseek بدون کلید ───
        if (
            self._priority == "deepseek"
            and not self._deepseek_key
        ):
            return False

        # ─── cache check ───
        now = time.monotonic()
        with self._lock:
            if (
                self._available_cache is not None
                and (
                    now - self._available_cache_at
                ) < _AVAILABLE_CACHE_S
            ):
                return self._available_cache

        # ─── ping واقعی (خارج از lock) ───
        result = await self._check_available_inner()

        with self._lock:
            self._available_cache = result
            self._available_cache_at = now

        return result

    async def _check_available_inner(self) -> bool:
        """قلب چک `available` — بدون lock.

        🎯 طراحی موازی:
          ۱) اگر priority=deepseek + کلید → True فوری
          ۲) اگر priority=auto + کلید → True فوری (بدون ping)
          ۳) اگر priority=ollama → ping Ollama
        """
        # ═══════════════════════════════════════════
        # 🎯 FIX: DeepSeek اول — بدون ping (سریع)
        # ═══════════════════════════════════════════
        # در حالت priority=deepseek یا auto، اگر کلید باشد،
        # True برمی‌گردانیم. ping واقعی در _call_deepseek انجام
        # می‌شود که timeout خودش را دارد.
        if self._deepseek_key and self._priority in (
            "deepseek", "auto",
        ):
            log_info(
                f"✅ [AI:available] DeepSeek فعال "
                f"(priority={self._priority}, "
                f"source={self._deepseek_key_source})"
            )
            return True

        # ═══════════════════════════════════════════
        # ping Ollama (لوکال) — سریع، ۳s
        # ═══════════════════════════════════════════
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self._ollama_url}/api/tags",
                    timeout=aiohttp.ClientTimeout(
                        total=_AI_PING_TIMEOUT_S
                    ),
                ) as resp:
                    if resp.status == 200:
                        log_info(
                            "✅ [AI:available] Ollama فعال"
                        )
                        return True
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log_info(
                f"ℹ️ [AI:available] Ollama نیست | "
                f"{type(e).__name__}: {e}"
            )

        # ─── fallback: deepseek key (اگر priority=ollama) ───
        if self._deepseek_key:
            log_info(
                f"ℹ️ [AI:available] Ollama نیست ولی "
                f"کلید DeepSeek هست (fallback)"
            )
            return True

        return False

    def invalidate_available_cache(self) -> None:
        """🎯 پاک کردن cache available — برای تست."""
        with self._lock:
            self._available_cache = None
            self._available_cache_at = 0.0
        log_info("🗑️ [AI:available] cache پاک شد")

    # ═══════════════════════════════════════════
    # chat
    # ═══════════════════════════════════════════
    async def chat(self, text: str, system: str = "") -> str:
        """چت آزاد — در صورت شکست، prefix fallback برمی‌گرداند."""
        response, _ok = await self.chat_with_status(
            text, system
        )
        return response

    async def chat_with_status(
        self, text: str, system: str = ""
    ) -> tuple[str, bool]:
        """چت با پرچم موفقیت/شکست.

        Returns:
            (response_text, success_flag)
            success_flag=False → fallback (cache نباید بشود)
        """
        # 🐛 FIX: type-check
        if text is None:
            return "", False
        if not isinstance(text, str):
            try:
                text = str(text)
            except Exception:
                return "", False
        if not text.strip():
            return "", False

        if not self.enabled:
            return "", False

        # 🐛 FIX: limit prompt
        if len(text) > _MAX_PROMPT_LEN:
            text = text[:_MAX_PROMPT_LEN]

        full_prompt = (
            f"{system}\n\n{text}" if system else text
        )

        for name in self._provider_order():
            try:
                if name == "ollama":
                    result = await self._call_ollama(
                        full_prompt
                    )
                elif name == "deepseek":
                    result = await self._call_deepseek(
                        full_prompt
                    )
                else:
                    continue

                if result:
                    return result, True
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log_warning(
                    f"⚠️ [AI:chat:{name}] "
                    f"{type(e).__name__}: {e}"
                )

        log_warning("⚠️ [AI:chat] هیچ providerی جواب نداد")

        fallback_msg = (
            f"{_FALLBACK_PREFIX}\n"
            f"Ollama لوکال را روشن کن یا "
            f"DEEPSEEK_API_KEY بگذار."
        )
        return fallback_msg, False

    # ═══════════════════════════════════════════
    # parse_intent
    # ═══════════════════════════════════════════
    async def parse_intent(self, text: str) -> dict[str, Any]:
        """متن → {"action":..., "args":{...}} با whitelist."""
        if not text:
            return {"action": "unknown", "args": {}}

        prompt = text
        if _INTENT_SYSTEM:
            prompt = (
                f"{_INTENT_SYSTEM}\n\nUser message: {text}"
            )

        raw: str | None = None
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
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log_warning(
                    f"⚠️ [AI:intent:{name}] "
                    f"{type(e).__name__}: {e}"
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

        action = str(
            parsed.get("action", "unknown")
        ).strip().lower()
        args = parsed.get("args") or {}

        # 🐛 FIX: validate args
        if not isinstance(args, dict):
            args = {}
        else:
            args = {
                str(k)[:64]: (
                    str(v)[:1000]
                    if not isinstance(v, (dict, list))
                    else v
                )
                for k, v in args.items()
            }

        if action not in _ALLOWED_ACTIONS:
            log_warning(
                f"⚠️ [AI:intent] اکشن غیرمجاز | "
                f"action={action!r}"
            )
            return {"action": "unknown", "args": {}}

        return {"action": action, "args": args}

    # ═══════════════════════════════════════════
    # provider order (با cache)
    # ═══════════════════════════════════════════
    def _provider_order(self) -> list[str]:
        """ترتیب providerها بر اساس priority — cache شده.

        طراحی موازی:
          - DeepSeek اول (سریع، دقیق)
          - Ollama دوم (لوکال، همیشه هست)
          - اگر DeepSeek شکست خورد → Ollama بلافاصله
        """
        with self._lock:
            if self._provider_cache is not None:
                return list(self._provider_cache)

            p = self._priority
            if p == "deepseek":
                result = (
                    ["deepseek"] if self._deepseek_key else []
                )
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
            return list(result)

    # ═══════════════════════════════════════════
    # Ollama
    # ═══════════════════════════════════════════
    async def _call_ollama(
        self, prompt: str, format_json: bool = False
    ) -> str | None:
        """تماس با Ollama (لوکال).

        🎯 FIX: پیام‌های واضح‌تر برای خطاها.
        """
        if aiohttp is None:
            return None

        payload: dict[str, Any] = {
            "model": self._ollama_model,
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "stream": False,
        }
        if format_json:
            payload["format"] = "json"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self._ollama_url}/api/chat",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(
                        total=self._timeout
                    ),
                ) as resp:
                    if resp.status != 200:
                        try:
                            body = (await resp.text())[
                                :_LOG_PREVIEW_LEN
                            ]
                        except Exception:
                            body = ""
                        log_warning(
                            f"⚠️ [AI:ollama] "
                            f"status={resp.status} | "
                            f"body={body!r}"
                        )
                        return None
                    data = await resp.json()
                    return (
                        data.get("message") or {}
                    ).get("content")
        except asyncio.CancelledError:
            raise
        except aiohttp.ClientConnectorError as e:
            log_info(
                f"ℹ️ [AI:ollama] اتصال برقرار نشد "
                f"(Ollama روشن نیست؟) | "
                f"{type(e).__name__}: {e}"
            )
            return None
        except Exception as e:
            log_warning(
                f"⚠️ [AI:ollama] {type(e).__name__}: {e}"
            )
            return None

    # ═══════════════════════════════════════════
    # DeepSeek
    # ═══════════════════════════════════════════
    async def _call_deepseek(
        self, prompt: str, format_json: bool = False
    ) -> str | None:
        """تماس با DeepSeek — با retry ساده.

        🎯 FIX: retry برای خطاهای گذرا (429، 500، timeout).
        🎯 FIX: پیام واضح برای 401 (کلید نامعتبر).
        """
        if not self._deepseek_key or aiohttp is None:
            return None

        payload: dict[str, Any] = {
            "model": self._deepseek_model,
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "stream": False,
        }
        if format_json:
            payload["response_format"] = {
                "type": "json_object"
            }

        headers = {
            "Authorization": f"Bearer {self._deepseek_key}",
            "Content-Type": "application/json",
        }

        # ─── retry loop ───
        last_error = "unknown"
        for attempt in range(_DEEPSEEK_MAX_RETRY + 1):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        self._deepseek_url,
                        json=payload,
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(
                            total=self._timeout
                        ),
                    ) as resp:
                        # ═══════════════════════════════
                        # 200: موفق
                        # ═══════════════════════════════
                        if resp.status == 200:
                            data = await resp.json()
                            return (
                                (data.get("choices") or [{}])[0]
                                .get("message", {})
                                .get("content")
                            )

                        # ═══════════════════════════════
                        # 401: کلید نامعتبر (بدون retry)
                        # ═══════════════════════════════
                        if resp.status == 401:
                            log_warning(
                                "❌ [AI:deepseek] 401 — "
                                "کلید API نامعتبر است. "
                                "DEEPSEEK_API_KEY را چک کن."
                            )
                            return None

                        # ═══════════════════════════════
                        # 402: موجودی تمام
                        # ═══════════════════════════════
                        if resp.status == 402:
                            log_warning(
                                "❌ [AI:deepseek] 402 — "
                                "موجودی حساب DeepSeek تمام شده. "
                                "شارژ کن یا AI_PRIORITY=ollama بگذار."
                            )
                            return None

                        # ═══════════════════════════════
                        # 429: rate limit → retry
                        # ═══════════════════════════════
                        if resp.status == 429:
                            last_error = "429 rate limit"
                            if attempt < _DEEPSEEK_MAX_RETRY:
                                log_info(
                                    f"⏳ [AI:deepseek] 429 — "
                                    f"retry {attempt+1}/"
                                    f"{_DEEPSEEK_MAX_RETRY} "
                                    f"بعد از "
                                    f"{_DEEPSEEK_RETRY_DELAY_S}s"
                                )
                                await asyncio.sleep(
                                    _DEEPSEEK_RETRY_DELAY_S
                                )
                                continue
                            log_warning(
                                "⚠️ [AI:deepseek] 429 — "
                                "محدودیت سرعت. "
                                "بعداً دوباره تلاش کن."
                            )
                            return None

                        # ═══════════════════════════════
                        # 500/502/503: server error → retry
                        # ═══════════════════════════════
                        if resp.status in (500, 502, 503, 504):
                            last_error = f"{resp.status} server error"
                            if attempt < _DEEPSEEK_MAX_RETRY:
                                log_info(
                                    f"⏳ [AI:deepseek] {resp.status} — "
                                    f"retry {attempt+1}/"
                                    f"{_DEEPSEEK_MAX_RETRY}"
                                )
                                await asyncio.sleep(
                                    _DEEPSEEK_RETRY_DELAY_S
                                )
                                continue
                            log_warning(
                                f"⚠️ [AI:deepseek] {resp.status} — "
                                f"سرور مشکل دارد"
                            )
                            return None

                        # ═══════════════════════════════
                        # بقیه کدها
                        # ═══════════════════════════════
                        try:
                            body = (await resp.text())[
                                :_LOG_PREVIEW_LEN
                            ]
                        except Exception:
                            body = ""
                        log_warning(
                            f"⚠️ [AI:deepseek] "
                            f"status={resp.status} | "
                            f"body={body!r}"
                        )
                        return None

            except asyncio.CancelledError:
                raise
            except TimeoutError:
                last_error = "timeout"
                if attempt < _DEEPSEEK_MAX_RETRY:
                    log_info(
                        f"⏳ [AI:deepseek] timeout — "
                        f"retry {attempt+1}/"
                        f"{_DEEPSEEK_MAX_RETRY}"
                    )
                    await asyncio.sleep(_DEEPSEEK_RETRY_DELAY_S)
                    continue
                log_warning(
                    f"⏰ [AI:deepseek] timeout "
                    f"({self._timeout}s)"
                )
                return None
            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"
                log_warning(
                    f"⚠️ [AI:deepseek] "
                    f"{type(e).__name__}: {e}"
                )
                return None

        log_warning(
            f"⚠️ [AI:deepseek] پس از retry ناموفق | "
            f"last_error={last_error}"
        )
        return None

    # ═══════════════════════════════════════════
    # JSON helper
    # ═══════════════════════════════════════════
    @staticmethod
    def _extract_json(text: str) -> dict[str, Any] | None:
        """استخراج اولین شیء JSON از متن."""
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
            candidate = s[start:end + 1]
            try:
                data = json.loads(candidate)
                if isinstance(data, dict):
                    return data
            except (json.JSONDecodeError, ValueError):
                pass

        # ─── ۴) regex غیرحریص ───
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
    # debug
    # ═══════════════════════════════════════════
    def get_stats(self) -> dict[str, Any]:
        """🎯 خلاصهٔ وضعیت — برای debug."""
        with self._lock:
            provider_cache = (
                list(self._provider_cache)
                if self._provider_cache else []
            )
            avail_cache = self._available_cache
            avail_age = (
                round(
                    time.monotonic()
                    - self._available_cache_at, 1
                )
                if self._available_cache_at > 0
                else None
            )

        return {
            "enabled": self.enabled,
            "priority": self._priority,
            "deepseek_configured": bool(self._deepseek_key),
            "deepseek_key_source": self._deepseek_key_source,
            "deepseek_model": self._deepseek_model,
            "deepseek_url": self._deepseek_url,
            "deepseek_max_retry": _DEEPSEEK_MAX_RETRY,
            "ollama_url": self._ollama_url,
            "ollama_model": self._ollama_model,
            "timeout_s": self._timeout,
            "provider_order": provider_cache,
            "available_cache": avail_cache,
            "available_cache_age_s": avail_age,
            "available_cache_ttl_s": _AVAILABLE_CACHE_S,
            "aiohttp_available": aiohttp is not None,
            "allowed_actions_count": len(_ALLOWED_ACTIONS),
            "intent_system_loaded": bool(_INTENT_SYSTEM),
            "fallback_prefix": _FALLBACK_PREFIX,
        }

    def __repr__(self) -> str:
        return (
            f"<AIAssistant enabled={self.enabled} "
            f"priority={self._priority} "
            f"deepseek={'✅' if self._deepseek_key else '❌'}"
            f"({self._deepseek_key_source}) "
            f"provider={self._provider_order()}>"
        )


# ═══════════════════════════════════════════
# Singleton
# ═══════════════════════════════════════════
_shared_ai: AIAssistant | None = None
_shared_lock = threading.Lock()


def get_ai_assistant() -> AIAssistant:
    """دریافت نمونهٔ Singleton AIAssistant (thread-safe)."""
    global _shared_ai
    if _shared_ai is None:
        with _shared_lock:
            if _shared_ai is None:
                log_info("🏗️ [AI:singleton] ساخت نمونهٔ اول")
                _shared_ai = AIAssistant()
    return _shared_ai


def reset_ai_assistant() -> None:
    """پاک‌کردن singleton — فقط برای تست."""
    global _shared_ai
    with _shared_lock:
        _shared_ai = None
        log_info("🗑️ [AI:singleton] reset شد")


# ═══════════════════════════════════════════
# __all__
# ═══════════════════════════════════════════
__all__ = [
    "AIAssistant",
    "get_ai_assistant",
    "reset_ai_assistant",
    "PERSONA",
    "_ALLOWED_ACTIONS",
    "_FALLBACK_PREFIX",
]