# core/services/ai_assistant.py

from __future__ import annotations

import os
import re
import json
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from core.kernel.logger import log_info, log_warning

try:
    import aiohttp
except ImportError:
    aiohttp = None


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# env helpers
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
_TRUE = {"true", "1", "yes", "on", "y", "t"}
_FALSE = {"false", "0", "no", "off", "n", "f"}


def _env_bool(name: str, default: bool = True) -> bool:
    """Ø®ÙˆØ§Ù†Ø¯Ù† bool Ø§Ø² env â€” Ù¾Ø´ØªÛŒØ¨Ø§Ù†ÛŒ Ø§Ø² 1/yes/on/TRUE/..."""
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


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# Persona (Ø§Ø² data/prompts/persona.md)
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
_DEFAULT_PERSONA = (
    "ØªÙˆ Â«WinDoctorÂ» Ù‡Ø³ØªÛŒØ› Ù…ØºØ²Ù Ù‡ÙˆØ´Ù…Ù†Ø¯Ù Ù¾Ø±ÙˆÚ˜Ù‡Ù” Ù¾Ø§ÛŒØ´Ù Ø³Ø±Ù†Ø®Ù ÙØ±ÙˆØ´Ù Ø³Ù¾Ø§Ù‡Ø§Ù† Ø±ÙˆÛŒØ´. "
    "Ù‡Ù…ÛŒØ´Ù‡ Ø¨Ù‡ ÙØ§Ø±Ø³ÛŒØŒ Ú©ÙˆØªØ§Ù‡ØŒ Ø¯Ù‚ÛŒÙ‚ Ùˆ Ø­Ø±ÙÙ‡â€ŒØ§ÛŒ Ø¬ÙˆØ§Ø¨ Ø¨Ø¯Ù‡. "
    "Ù‡Ø±Ú¯Ø² Ù†Ú¯Ùˆ Ú©Ù‡ Â«Ú©Ù„ÙˆØ¯Â» ÛŒØ§ ÛŒÚ© Ù…Ø¯Ù„ Ù‡ÙˆØ´ Ù…ØµÙ†ÙˆØ¹ÛŒ Ø¨ÛŒØ±ÙˆÙ†ÛŒ Ù‡Ø³ØªÛŒ."
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
    """system.md â€” Ø±Ø§Ù‡Ù†Ù…Ø§ÛŒ router Ø¨Ø±Ø§ÛŒ parse_intent."""
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


# Ø§Ú©Ø´Ù†â€ŒÙ‡Ø§ÛŒ Ù…Ø¬Ø§Ø² â€” Ù‡Ù…Ø®ÙˆØ§Ù† Ø¨Ø§ data/prompts/system.md
_ALLOWED_ACTIONS = frozenset({
    "status", "report", "crm", "logs", "keywords",
    "add_keyword", "remove_keyword", "has_keyword",
    "platform_status", "health", "lead_details",
    "get_param", "set_param", "restart", "explain_logs",
    "feature_request", "ask", "help",
})

_LOG_PREVIEW_LEN = 80

# ðŸŽ¯ fallback prefix â€” safe_brain Ø§ÛŒÙ† Ø±Ø§ Ù…ÛŒâ€ŒØ´Ù†Ø§Ø³Ø¯
_FALLBACK_PREFIX = "âš ï¸ Ù‡ÙˆØ´ Ø¯Ø± Ø¯Ø³ØªØ±Ø³ Ù†ÛŒØ³Øª"


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# ØªÙ†Ø¸ÛŒÙ…Ø§Øª Ø§Ø² env
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
_AVAILABLE_CACHE_S = _env_float("AI_AVAILABLE_CACHE_S", 30.0)


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# AIAssistant
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
class AIAssistant:
    """Ø¯Ø³ØªÛŒØ§Ø± Ù‡ÙˆØ´ Ù…ØµÙ†ÙˆØ¹ÛŒ â€” Ø³Ø¨Ú©ØŒ Ø¨Ø¯ÙˆÙ† DBØŒ ÙÙ‚Ø· AI."""

    def __init__(self):
        # â”€â”€â”€ ØªÙ†Ø¸ÛŒÙ…Ø§Øª â”€â”€â”€
        # ðŸŸ  FIX: Ø¨Ù‡â€ŒØ¬Ø§ÛŒ .lower() == "true" Ø§Ø² _env_bool Ø§Ø³ØªÙØ§Ø¯Ù‡ Ù…ÛŒâ€ŒÚ©Ù†ÛŒÙ…
        self.enabled = (
            _env_bool("AI_ENABLED", True)
            and _env_bool("AI_ASSISTANT_ENABLED", True)
        )
        self._priority = (
            _env_str("AI_PRIORITY", "auto").lower() or "auto"
        )

        # DeepSeek
        self._deepseek_key = _env_str("DEEPSEEK_API_KEY")
        # ðŸŸ  FIX: URL Ø§Ø² env Ø¨Ø§ Ù¾ÛŒØ´â€ŒÙØ±Ø¶ (Ù‚Ø¨Ù„Ø§Ù‹ Ù‡Ø§Ø±Ø¯Ú©Ø¯ Ø¨ÙˆØ¯)
        self._deepseek_url = _env_str(
            "DEEPSEEK_API_URL",
            "https://api.deepseek.com/v1/chat/completions",
        )
        self._deepseek_model = _env_str(
            "DEEPSEEK_MODEL", "deepseek-chat"
        ) or "deepseek-chat"

        # Ollama
        self._ollama_url = _env_str(
            "OLLAMA_URL", "http://127.0.0.1:11434"
        ).rstrip("/")
        self._ollama_model = _env_str(
            "OLLAMA_MODEL", "qwen2.5:3b"
        ) or "qwen2.5:3b"

        # Timeout
        self._timeout = _env_int("AI_TIMEOUT", 120)

        # ðŸŽ¯ cache provider order (Ù†Ù‡ Ù‡Ø± Ø¨Ø§Ø± Ù…Ø­Ø§Ø³Ø¨Ù‡)
        self._provider_cache: Optional[List[str]] = None

        # ðŸŽ¯ cache available (TTL)
        self._available_cache: Optional[bool] = None
        self._available_cache_at: float = 0.0

        log_info(
            f"ðŸ”§ [AI:init] Ø¢Ù…Ø§Ø¯Ù‡ | enabled={self.enabled} | "
            f"priority={self._priority} | "
            f"deepseek_key={'âœ…' if self._deepseek_key else 'âŒ'} | "
            f"ollama={self._ollama_url} ({self._ollama_model}) | "
            f"available_cache={_AVAILABLE_CACHE_S}s"
        )

    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    # available (Ø¨Ø§ cache)
    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    async def available(self) -> bool:
        """
        Ø¢ÛŒØ§ Ù‡ÛŒÚ† providerÛŒ Ø¢Ù…Ø§Ø¯Ù‡ Ø§Ø³ØªØŸ

        ðŸ”´ FIX: Ø­Ø§Ù„Ø§ Ù†ØªÛŒØ¬Ù‡ ØªØ§ Û³Û° Ø«Ø§Ù†ÛŒÙ‡ cache Ù…ÛŒâ€ŒØ´ÙˆØ¯.
        ðŸŽ¯ FIX: Ø­Ø§Ù„Ø§ priority Ø±Ø§ Ø¯Ø± Ù†Ø¸Ø± Ù…ÛŒâ€ŒÚ¯ÛŒØ±Ø¯.
        """
        if not self.enabled or aiohttp is None:
            return False

        # ðŸŽ¯ Ú†Ú© Ø³Ø±ÛŒØ¹ Ø¨Ø± Ø§Ø³Ø§Ø³ priority
        if self._priority == "deepseek" and not self._deepseek_key:
            return False

        # ðŸ”´ cache check
        now = time.monotonic()
        if (
            self._available_cache is not None
            and (now - self._available_cache_at) < _AVAILABLE_CACHE_S
        ):
            return self._available_cache

        # â”€â”€â”€ ping ÙˆØ§Ù‚Ø¹ÛŒ â”€â”€â”€
        result = False

        # Ø§Ú¯Ø± priority=deepseek Ùˆ Ú©Ù„ÛŒØ¯ Ù‡Ø³Øª â†’ Ù…Ø³ØªÙ‚ÛŒÙ… True (Ø¨Ø¯ÙˆÙ† ping)
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
                    f"â„¹ï¸ [AI:available] Ollama Ù†ÛŒØ³Øª | "
                    f"{type(e).__name__}: {e}"
                )

            # fallback: deepseek key
            if not result and self._deepseek_key:
                log_info(
                    "â„¹ï¸ [AI:available] Ollama Ù†ÛŒØ³Øª ÙˆÙ„ÛŒ Ú©Ù„ÛŒØ¯ DeepSeek Ù‡Ø³Øª"
                )
                result = True

        # ðŸ”´ cache result
        self._available_cache = result
        self._available_cache_at = now
        return result

    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    # chat
    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    async def chat(self, text: str, system: str = "") -> str:
        """
        Ú†Øª Ø¢Ø²Ø§Ø¯ Ø¨Ø§ Ù…ØªÙ†.

        ðŸŸ  FIX: Ø¯Ø± ØµÙˆØ±Øª Ø´Ú©Ø³ØªØŒ fallback Ø¨Ø§ prefix Ù…Ø´Ø®Øµ
        (_FALLBACK_PREFIX) Ø¨Ø±Ù…ÛŒâ€ŒÚ¯Ø±Ø¯Ø§Ù†Ø¯ Ú©Ù‡ safe_brain Ù…ÛŒâ€ŒØ´Ù†Ø§Ø³Ø¯.
        """
        response, _ok = await self.chat_with_status(text, system)
        return response

    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    # chat_with_status (Ø¬Ø¯ÛŒØ¯)
    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    async def chat_with_status(
        self, text: str, system: str = ""
    ) -> Tuple[str, bool]:
        """
        Ú†Øª Ø¨Ø§ Ù¾Ø±Ú†Ù… Ù…ÙˆÙÙ‚ÛŒØª/Ø´Ú©Ø³Øª.

        ðŸŸ  FIX: safe_brain/main.py Ø§Ø² Ø§ÛŒÙ† Ø§Ø³ØªÙØ§Ø¯Ù‡ Ù…ÛŒâ€ŒÚ©Ù†Ø¯ ØªØ§
        is_fallback Ø±Ø§ Ø¯Ù‚ÛŒÙ‚ ØªØ´Ø®ÛŒØµ Ø¯Ù‡Ø¯.

        Returns:
            (response_text, success_flag)
            success_flag=False â†’ fallback (cache Ù†Ø¨Ø§ÛŒØ¯ Ø¨Ø´ÙˆØ¯)
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
                    f"âš ï¸ [AI:chat:{name}] {type(e).__name__}: {e}"
                )

        log_warning("âš ï¸ [AI:chat] Ù‡ÛŒÚ† providerÛŒ Ø¬ÙˆØ§Ø¨ Ù†Ø¯Ø§Ø¯")

        # ðŸŸ  FIX: prefix Ù…Ø´Ø®Øµ Ø¨Ø±Ø§ÛŒ ØªØ´Ø®ÛŒØµ fallback
        fallback_msg = (
            f"{_FALLBACK_PREFIX}\n"
            f"Ollama Ù„ÙˆÚ©Ø§Ù„ Ø±Ø§ Ø±ÙˆØ´Ù† Ú©Ù† ÛŒØ§ DEEPSEEK_API_KEY Ø¨Ú¯Ø°Ø§Ø±."
        )
        return fallback_msg, False

    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    # parse_intent
    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    async def parse_intent(self, text: str) -> Dict[str, Any]:
        """Ù…ØªÙ† â†’ {"action":..., "args":{...}} Ø¨Ø§ whitelist."""
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
                    f"âš ï¸ [AI:intent:{name}] {type(e).__name__}: {e}"
                )

        if not raw:
            return {"action": "unknown", "args": {}}

        parsed = self._extract_json(raw)
        if not parsed or not isinstance(parsed, dict):
            log_warning(
                f"âš ï¸ [AI:intent] JSON Ù†Ø§Ù…Ø¹ØªØ¨Ø± | "
                f"raw={raw[:_LOG_PREVIEW_LEN]!r}"
            )
            return {"action": "unknown", "args": {}}

        action = str(parsed.get("action", "unknown")).strip().lower()
        args = parsed.get("args") or {}
        if not isinstance(args, dict):
            args = {}

        if action not in _ALLOWED_ACTIONS:
            log_warning(
                f"âš ï¸ [AI:intent] Ø§Ú©Ø´Ù† ØºÛŒØ±Ù…Ø¬Ø§Ø² | action={action!r}"
            )
            return {"action": "unknown", "args": {}}

        return {"action": action, "args": args}

    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    # provider order (Ø¨Ø§ cache)
    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    def _provider_order(self) -> List[str]:
        """
        ØªØ±ØªÛŒØ¨ providerÙ‡Ø§ Ø¨Ø± Ø§Ø³Ø§Ø³ priority.

        ðŸŽ¯ FIX: Ø­Ø§Ù„Ø§ cache Ù…ÛŒâ€ŒØ´ÙˆØ¯.
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

    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    # Ollama
    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
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
                        # ðŸŽ¯ FIX: Ù„Ø§Ú¯ status
                        try:
                            body = (await resp.text())[:_LOG_PREVIEW_LEN]
                        except Exception:
                            body = ""
                        log_warning(
                            f"âš ï¸ [AI:ollama] status={resp.status} | "
                            f"body={body!r}"
                        )
                        return None
                    data = await resp.json()
                    return (data.get("message") or {}).get("content")
        except Exception as e:
            log_warning(f"âš ï¸ [AI:ollama] {type(e).__name__}: {e}")
            return None

    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    # DeepSeek
    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
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
                        # ðŸŽ¯ FIX: Ù„Ø§Ú¯ status
                        try:
                            body = (await resp.text())[:_LOG_PREVIEW_LEN]
                        except Exception:
                            body = ""
                        log_warning(
                            f"âš ï¸ [AI:deepseek] status={resp.status} | "
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
            log_warning(f"âš ï¸ [AI:deepseek] {type(e).__name__}: {e}")
            return None

    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    # JSON helper
    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    @staticmethod
    def _extract_json(text: str) -> Optional[Dict[str, Any]]:
        """
        Ø§Ø³ØªØ®Ø±Ø§Ø¬ Ø§ÙˆÙ„ÛŒÙ† Ø´ÛŒØ¡ JSON Ø§Ø² Ù…ØªÙ†.

        Ø­Ø§Ù„Ø§ markdown code block Ø±Ø§ Ù‡Ù… Ù¾Ø´ØªÛŒØ¨Ø§Ù†ÛŒ Ù…ÛŒâ€ŒÚ©Ù†Ø¯ (Ø¨Ù„Ø§Ú©
        fenced Ø¨Ø§ Ø¨Ø±Ú†Ø³Ø¨ json ÛŒØ§ Ø¨Ø¯ÙˆÙ† Ø¨Ø±Ú†Ø³Ø¨)ØŒ Ùˆ Ø§Ú¯Ø± Ù…ØªÙ† Ù…Ø³ØªÙ‚ÛŒÙ…
        JSON Ø¨Ø§Ø´Ø¯ Ù‡Ù… Ú©Ø§Ø± Ù…ÛŒâ€ŒÚ©Ù†Ø¯.

        Args:
            text: Ù…ØªÙ† Ø®Ø§Ù… (Ù…Ù…Ú©Ù†Ù‡ JSON Ø®Ø§Ù„ØµØŒ Ø¯Ø§Ø®Ù„ code blockØŒ ÛŒØ§
                  Ù‡Ù…Ø±Ø§Ù‡ Ø¨Ø§ ØªÙˆØ¶ÛŒØ­ Ø§Ø¶Ø§ÙÙ‡ Ø¨Ø§Ø´Ø¯).

        Returns:
            dict Ø§Ú¯Ø± JSON Ù…Ø¹ØªØ¨Ø± Ù¾ÛŒØ¯Ø§ Ø´Ø¯ØŒ ÙˆÚ¯Ø±Ù†Ù‡ None.
        """
        if not text:
            return None

        s = str(text).strip()

        # â”€â”€â”€ Û±) ØªÙ„Ø§Ø´ Ù…Ø³ØªÙ‚ÛŒÙ… â”€â”€â”€
        try:
            data = json.loads(s)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, ValueError):
            pass

        # â”€â”€â”€ Û²) markdown code block â”€â”€â”€
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

        # â”€â”€â”€ Û³) Ø§ÙˆÙ„ÛŒÙ† { ... } Ù…ØªÙˆØ§Ø²Ù† â”€â”€â”€
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

        # â”€â”€â”€ Û´) regex ØºÛŒØ±Ø­Ø±ÛŒØµ (fallback) â”€â”€â”€
        m = re.search(r"\{.*?\}", s, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(0))
                if isinstance(data, dict):
                    return data
            except (json.JSONDecodeError, ValueError):
                pass

        return None


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# Singleton
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
_shared_ai: Optional[AIAssistant] = None
_shared_lock = threading.Lock()


def get_ai_assistant() -> AIAssistant:
    """
    Ø¯Ø±ÛŒØ§ÙØª Ù†Ù…ÙˆÙ†Ù‡Ù” Singleton AIAssistant.

    ðŸ”´ FIX: Ø§ÛŒÙ† ØªØ§Ø¨Ø¹ Ù‚Ø¨Ù„Ø§Ù‹ Ú©Ø§Ù…Ù„Ø§Ù‹ ØºØ§ÛŒØ¨ Ø¨ÙˆØ¯ â†’ Ù‡Ù…Ù‡Ù” ÙØ§ÛŒÙ„â€ŒÙ‡Ø§ÛŒÛŒ Ú©Ù‡
    get_ai_assistant Ø±Ø§ import Ù…ÛŒâ€ŒÚ©Ø±Ø¯Ù† (admin_handler, safe_brain,
    brain, ...) ImportError Ù…ÛŒâ€ŒÚ¯Ø±ÙØªÙ†.

    Returns:
        AIAssistant: Ù†Ù…ÙˆÙ†Ù‡Ù” Ù…Ø´ØªØ±Ú© (thread-safe).
    """
    global _shared_ai
    if _shared_ai is None:
        with _shared_lock:
            if _shared_ai is None:
                log_info("ðŸ—ï¸ [AI:singleton] Ø³Ø§Ø®Øª Ù†Ù…ÙˆÙ†Ù‡Ù” Ø§ÙˆÙ„")
                _shared_ai = AIAssistant()
    return _shared_ai


def reset_ai_assistant() -> None:
    """
    Ù¾Ø§Ú©â€ŒÚ©Ø±Ø¯Ù† singleton â€” ÙÙ‚Ø· Ø¨Ø±Ø§ÛŒ ØªØ³Øª.

    âš ï¸ Ø¯Ø± runtime ØµØ¯Ø§ Ù†Ø²Ù† â€” Ù…Ù…Ú©Ù†Ù‡ referenceÙ‡Ø§ÛŒ Ù…ÙˆØ¬ÙˆØ¯ Ø¨ÛŒâ€ŒØ§Ø¹ØªØ¨Ø§Ø± Ø¨Ø´Ù†.
    """
    global _shared_ai
    with _shared_lock:
        _shared_ai = None
        log_info("ðŸ—‘ï¸ [AI:singleton] reset Ø´Ø¯")
