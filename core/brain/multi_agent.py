"""
🤝 Multi-Agent — چند Expert با هم.

سه Expert مختلف:
  1. NetworkExpert  — شبکه و پلتفرم
  2. CodeExpert     — کد و منطق
  3. ExperienceExpert — تجربه‌های قبلی

یه Judge نهایی جمع‌بندی می‌کنه.

نسخهٔ ۱:
  - ask(question) → پاسخ نهایی
  - parallel execution (gather)
  - timeout per expert
"""
from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

from core.kernel.logger import (
    log_error,
    log_info,
    log_success,
    log_warning,
)


class MultiAgent:
    """چند Expert با هم — تیم پزشکی.

    Features:
        - ask(question) → پاسخ نهایی
        - parallel execution
        - timeout per expert
        - fallback: اگه Judge کار نکرد، concatenate
    """

    def __init__(
        self,
        timeout_s: float = 120.0,
        max_experts: int = 3,
    ):
        self.timeout_s = timeout_s
        self.max_experts = max_experts
        log_info(
            f"🤝 [MULTI:init] آماده | "
            f"timeout={timeout_s}s | experts={max_experts}"
        )

    # ═══════════════════════════════════════
    # Expert 1: Network
    # ═══════════════════════════════════════
    async def _expert_network(
        self, question: str
    ) -> dict[str, Any]:
        """Expert شبکه — پلتفرم‌ها، اتصال."""
        try:
            from core.brain import get_brain
            brain = get_brain()
            if brain.client is None:
                return {
                    "expert": "network",
                    "error": "client offline",
                }

            prompt = (
                f"سؤال کاربر: {question}\n\n"
                "تو یه متخصص شبکه و پلتفرم هستی. "
                "از دید اتصال، شبکه، و پلتفرم‌های "
                "(بله، ایتا، تلگرام، روبیکا، ...) "
                "پاسخ بده.\n"
                "مختصر و دقیق (حداکثر ۲۰۰ کلمه).\n"
                "اگه چیزی نمی‌دونی، بگو «نمی‌دونم»."
            )

            result = await asyncio.wait_for(
                brain.agent(
                    prompt,
                    mode="precise",
                    bypass_busy=True,
                ),
                timeout=self.timeout_s,
            )
            return {
                "expert": "network",
                "answer": result or "",
            }
            
        except TimeoutError:
            log_warning("⏰ [MULTI:network] timeout")
            return {"expert": "network", "error": "timeout"}
        except Exception as e:
            log_warning(f"⚠️ [MULTI:network] {e}")
            return {"expert": "network", "error": str(e)}

    # ═══════════════════════════════════════
    # Expert 2: Code
    # ═══════════════════════════════════════
    async def _expert_code(
        self, question: str
    ) -> dict[str, Any]:
        """Expert کد — منطق، باگ، ساختار."""
        try:
            from core.brain import get_brain
            brain = get_brain()
            if brain.client is None:
                return {
                    "expert": "code",
                    "error": "client offline",
                }

            prompt = (
                f"سؤال کاربر: {question}\n\n"
                "تو یه متخصص کد و معماری نرم‌افزار هستی. "
                "از دید کد، منطق، باگ، و ساختار پروژه "
                "پاسخ بده.\n"
                "مختصر و دقیق (حداکثر ۲۰۰ کلمه).\n"
                "اگه چیزی نمی‌دونی، بگو «نمی‌دونم»."
            )

            result = await asyncio.wait_for(
                brain.agent(
                    prompt,
                    mode="precise",
                    bypass_busy=True,
                ),
                timeout=self.timeout_s,
            )
            return {
                "expert": "code",
                "answer": result or "",
            }
        except TimeoutError:
            log_warning("⏰ [MULTI:code] timeout")
            return {"expert": "code", "error": "timeout"}
        except Exception as e:
            log_warning(f"⚠️ [MULTI:code] {e}")
            return {"expert": "code", "error": str(e)}

    # ═══════════════════════════════════════
    # Expert 3: Experience
    # ═══════════════════════════════════════
    async def _expert_experience(
        self, question: str
    ) -> dict[str, Any]:
        """Expert تجربه — تجربه‌های قبلی و الگوها."""
        try:
            # ─── دریافت تجربه‌ها ───
            exp_text = ""
            try:
                from core.brain.experience_learner import (
                    get_experience,
                )
                exp = get_experience()
                exp_text = exp.build_context(limit=5)
            except Exception:
                pass

            from core.brain import get_brain
            brain = get_brain()
            if brain.client is None:
                return {
                    "expert": "experience",
                    "error": "client offline",
                }

            prompt = (
                f"سؤال کاربر: {question}\n\n"
                f"تجربه‌های قبلی:\n{exp_text or 'ندارد'}\n\n"
                "تو یه متخصص تجربه هستی. "
                "از دید تجربه‌های قبلی و الگوهای موفق/ناموفق "
                "پاسخ بده.\n"
                "مختصر و دقیق (حداکثر ۲۰۰ کلمه).\n"
                "اگه تجربه‌ای نداری، بگو «تجربه‌ای ندارم»."
            )

            result = await asyncio.wait_for(
                brain.agent(
                    prompt,
                    mode="precise",
                    bypass_busy=True,
                ),
                timeout=self.timeout_s,
            )
            return {
                "expert": "experience",
                "answer": result or "",
            }
        except TimeoutError:
            log_warning("⏰ [MULTI:experience] timeout")
            return {"expert": "experience", "error": "timeout"}
        except Exception as e:
            log_warning(f"⚠️ [MULTI:experience] {e}")
            return {"expert": "experience", "error": str(e)}

    # ═══════════════════════════════════════
    # Judge
    # ═══════════════════════════════════════
    async def _judge(
        self,
        question: str,
        expert_results: list[dict],
    ) -> str:
        """جمع‌بندی پاسخ‌های Expertها."""
        # ─── استخراج پاسخ‌های موفق ───
        valid = [
            r for r in expert_results
            if r.get("answer")
        ]

        if not valid:
            return "❌ هیچ Expertی پاسخ نداد."

        # ─── ساخت متن برای Judge ───
        expert_text = ""
        for r in expert_results:
            expert = r.get("expert", "?")
            ans = r.get("answer", "")
            err = r.get("error", "")
            if ans:
                expert_text += f"\n### {expert}:\n{ans}\n"
            elif err:
                expert_text += (
                    f"\n### {expert}: خطا — {err}\n"
                )

        # ─── Judge با Brain ───
        try:
            from core.brain import get_brain
            brain = get_brain()
            if brain.client is None:
                # ─── fallback: concatenate ───
                return self._fallback_combine(valid)

            prompt = (
                f"سؤال کاربر: {question}\n\n"
                f"نظر متخصص‌ها:{expert_text}\n\n"
                "تو یه Judge نهایی هستی. "
                "نظرهای متخصص‌ها رو جمع کن و یه "
                "پاسخ نهایی بده.\n"
                "اگه اختلاف دارن، توضیح بده.\n"
                "به فارسی روان (حداکثر ۳۰۰ کلمه)."
            )

            result = await asyncio.wait_for(
                brain.agent(
                    prompt,
                    mode="precise",
                    bypass_busy=True,
                ),
                timeout=self.timeout_s,
            )
            return result or self._fallback_combine(valid)
        except Exception as e:
            log_warning(f"⚠️ [MULTI:judge] {e}")
            return self._fallback_combine(valid)

    def _fallback_combine(
        self, valid: list[dict]
    ) -> str:
        """اگه Judge کار نکرد، پاسخ‌ها رو قاطی می‌کنه."""
        parts = []
        for r in valid:
            expert = r.get("expert", "?")
            ans = r.get("answer", "")
            parts.append(f"### {expert}:\n{ans}")
        return "\n\n".join(parts)

    # ═══════════════════════════════════════
    # Main API
    # ═══════════════════════════════════════
    async def ask(self, question: str) -> str:
        """پرسش از چند Expert و جمع‌بندی.

        Args:
            question: سؤال کاربر.

        Returns:
            پاسخ نهایی.
        """
        if not question or not question.strip():
            return "⚠️ سؤال خالی است."

        t0 = time.monotonic()
        log_info(
            f"🤝 [MULTI:ask] شروع | "
            f"q={question[:60]!r}"
        )

        # ─── اجرای موازی ───
        tasks = [
            self._expert_network(question),
            self._expert_code(question),
            self._expert_experience(question),
        ]

        raw_results = await asyncio.gather(
            *tasks,
            return_exceptions=True,
        )

        # ─── پاک‌سازی ───
        expert_results: list[dict] = []
        for r in raw_results:
            if isinstance(r, Exception):
                expert_results.append({
                    "error": f"{type(r).__name__}: {r}",
                })
            else:
                expert_results.append(r)

        # ─── Judge ───
        final = await self._judge(question, expert_results)

        elapsed = time.monotonic() - t0
        log_success(
            f"✅ [MULTI:ask] کامل | "
            f"experts={len(expert_results)} | "
            f"{elapsed:.1f}s"
        )
        return final


# ═══════════════════════════════════════════
# Singleton
# ═══════════════════════════════════════════
_shared_multi: MultiAgent | None = None
_multi_lock = threading.Lock()


def get_multi_agent() -> MultiAgent:
    """دریافت Singleton."""
    global _shared_multi
    if _shared_multi is None:
        with _multi_lock:
            if _shared_multi is None:
                _shared_multi = MultiAgent()
    return _shared_multi


def reset_multi_agent() -> None:
    """پاک کردن Singleton."""
    global _shared_multi
    with _multi_lock:
        _shared_multi = None
        log_info("🗑️ [MULTI:singleton] reset")


__all__ = [
    "MultiAgent",
    "get_multi_agent",
    "reset_multi_agent",
]