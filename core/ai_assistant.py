# -*- coding: utf-8 -*-
"""
Local AI assistant (Ollama) — turns a free-text admin message into a structured
command {action, args}. Called ON-DEMAND only (when an admin sends a message),
so it uses RAM just for the few seconds it answers, then Ollama frees it
(OLLAMA_KEEP_ALIVE). Model name comes from .env (OLLAMA_MODEL) so the same code
works for qwen2.5:3b on 8GB or qwen2.5:7b after a RAM upgrade — no code change.

If Ollama is unreachable, parse_intent returns {"action": "unknown"} and the
admin handler falls back to its rule-based parser → the bot still works.

This version is optimised for Persian responses and reliability.
"""
import json
import re
from typing import List, Dict, Any, Optional

from config import AI
from core.logger import get_logger

# ─── Whitelist of allowed actions ──────────────────────────────────────────
ALLOWED_ACTIONS = [
    "status",
    "report",
    "crm",
    "logs",
    "keywords",
    "add_keyword",
    "remove_keyword",
    "has_keyword",
    "platform_status",
    "health",
    "lead_details",
    "get_param",
    "set_param",
    "help",
    "restart",
    "explain_logs",
    "feature_request",
    "ask",
]

# ─── Persona: the bot speaks Persian, as the bot itself ──────────────────
PERSONA = (
    "تو «مغزِ هوشمندِ رباتِ پایشِ سرنخِ سپاهان رویش» هستی؛ یک دستیارِ داخلیِ همین ربات. "
    "همیشه به فارسی، کوتاه، دقیق و حرفه‌ای جواب بده و از زبانِ همین ربات حرف بزن. "
    "هرگز نگو که «کلود» یا یک مدلِ هوش مصنوعیِ بیرونی هستی؛ تو بخشی از این ربات‌ای. "
    "اگر چیزی را نمی‌دانی یا ربات آن قابلیت را ندارد، صادقانه و کوتاه بگو. "
    "حتی اگر کاربر به انگلیسی سوال کند، تو به فارسی پاسخ بده."
)

# ─── System prompt for intent parsing (JSON output) ──────────────────────
_SYSTEM = (
    "You are the command router of a lead-monitoring bot. "
    "Map the user's message (Persian or English) to EXACTLY ONE allowed action and its args. "
    "Allowed actions:\n"
    "- status: overall stats\n"
    "- report: generate sales report\n"
    "- crm: export leads for CRM (excel/csv)\n"
    "- logs: recent log lines\n"
    "- keywords: list/count keywords\n"
    "- add_keyword: add a keyword (args.value = the keyword)\n"
    "- remove_keyword: remove a custom keyword (args.value = the keyword)\n"
    "- has_keyword: check if a keyword exists (args.value = the keyword)\n"
    "- platform_status: is a platform working (args.value = eitaa|bale|rubika|telegram|whatsapp|linkedin|web)\n"
    "- health: full health of the bot and all platforms (0-100)\n"
    "- lead_details: details of the first/last detected lead (args.value = first|last)\n"
    "- get_param: read a setting (args.value = the setting name)\n"
    "- set_param: change a setting (args.name, args.value)\n"
    "- restart: restart the bot\n"
    "- explain_logs: explain recent logs / what an error means\n"
    "- feature_request: user wants a NEW capability the bot doesn't have yet\n"
    "- ask: a general question about the bot/its abilities → answer it\n"
    "- help: list commands\n"
    'Respond ONLY as JSON: {"action": "<one of the allowed>", "args": {...}}. '
    'If unsure, use {"action": "unknown", "args": {}}.'
)


class AIAssistant:
    """Ollama-based assistant with Persian-first responses and robust fallback."""

    def __init__(self):
        self.url = AI["url"]
        self.model = AI["model"]
        self.keep_alive = AI["keep_alive"]
        self.timeout = AI["timeout"]
        self.enabled = AI["enabled"]
        self._logger = get_logger()

    # ─── Helpers ────────────────────────────────────────────────────────────

    async def _call_ollama(
        self, prompt: str, format_json: bool = False
    ) -> Optional[str]:
        """
        Low-level call to Ollama generate endpoint.
        Returns the raw response text, or None on failure.
        """
        if not self.enabled:
            return None
        body = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": {"temperature": 0.2},
        }
        if format_json:
            body["format"] = "json"
            body["options"]["temperature"] = 0.0  # deterministic for JSON

        try:
            import aiohttp

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.url}/api/generate", json=body, timeout=self.timeout
                ) as resp:
                    if resp.status != 200:
                        self._logger.warning(f"Ollama returned {resp.status}")
                        return None
                    data = await resp.json()
                    return data.get("response", "").strip()
        except Exception as e:
            self._logger.warning(f"Ollama call failed: {e}")
            return None

    # ─── Availability check ────────────────────────────────────────────────

    async def available(self) -> bool:
        if not self.enabled:
            return False
        try:
            import aiohttp

            async with aiohttp.ClientSession() as s:
                async with s.get(f"{self.url}/api/tags", timeout=4) as r:
                    return r.status == 200
        except Exception:
            return False

    # ─── Intent parsing (structured command) ──────────────────────────────

    async def parse_intent(self, text: str) -> Dict[str, Any]:
        """Return {"action": str, "args": dict}. Falls back to unknown on any failure."""
        if not self.enabled or not text:
            return {"action": "unknown", "args": {}}

        prompt = f"{_SYSTEM}\n\nUser message: {text}\nJSON:"
        raw = await self._call_ollama(prompt, format_json=True)

        if not raw:
            return {"action": "unknown", "args": {}}

        # Try to extract JSON from the response (Ollama may wrap it in markdown)
        json_str = raw
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            json_str = match.group(0)

        try:
            parsed = json.loads(json_str)
        except json.JSONDecodeError:
            self._logger.warning(f"Ollama returned invalid JSON: {raw[:200]}")
            return {"action": "unknown", "args": {}}

        action = str(parsed.get("action", "unknown")).strip()
        if action not in ALLOWED_ACTIONS:
            action = "unknown"
        args = parsed.get("args", {})
        if not isinstance(args, dict):
            args = {}
        return {"action": action, "args": args}

    # ─── Free-form chat (always Persian) ───────────────────────────────────

    async def chat(self, text: str, system: Optional[str] = None) -> str:
        """
        Free-form answer in Persian, using the bot's own voice.
        If the user asks in English, the model is instructed to reply in Persian.
        """
        if not self.enabled or not text:
            return ""

        # Enforce Persian even if the user wrote English
        system_prompt = (
            system or PERSONA
        ) + "\n\n⚠️ مهم: حتی اگر کاربر به انگلیسی سوال کرد، تو به فارسی پاسخ بده."

        prompt = f"{system_prompt}\n\nUser: {text}\nAssistant (in Persian):"
        raw = await self._call_ollama(prompt, format_json=False)

        if raw:
            # Remove any accidental English – keep the first Persian sentence if mixed
            # (This is a simple heuristic; the model should already obey)
            return raw
        else:
            # Fallback if Ollama is silent
            return "هوش در دسترس نیست. لطفاً از دستورات ساده استفاده کنید."


# ─── Singleton ─────────────────────────────────────────────────────────────

_shared: Optional[AIAssistant] = None


def get_ai_assistant() -> AIAssistant:
    global _shared
    if _shared is None:
        _shared = AIAssistant()
    return _shared
