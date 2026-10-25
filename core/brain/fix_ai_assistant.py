"""جایگزینی متد chat در ai_assistant.py با Brain.agent()."""
from pathlib import Path

FILE = Path("core/services/ai_assistant.py")

# ─── بخوان ───
content = FILE.read_text(encoding="utf-8")
lines = content.splitlines(keepends=True)

# ─── پیدا کن خط شروع `async def chat` ───
start_idx = None
for i, line in enumerate(lines):
    if line.strip().startswith("async def chat("):
        start_idx = i
        break

if start_idx is None:
    print("❌ متد chat پیدا نشد!")
    raise SystemExit(1)

# ─── پیدا کن خط پایان (next def در same indent) ───
end_idx = None
for j in range(start_idx + 1, len(lines)):
    line = lines[j]
    # ─── چک کن indent و شروع تعریف ───
    if line.startswith(("    async def ", "    def ")):
        end_idx = j
        break

if end_idx is None:
    print("❌ پایان متد chat پیدا نشد!")
    raise SystemExit(1)

print(f"📍 متد chat: خطوط {start_idx + 1} تا {end_idx}")

# ─── کد جدید ───
NEW_CHAT = '''    async def chat(self, text: str, system: str = "") -> str:
        """چت آزاد — اول Brain.agent()، بعد fallback به DeepSeek.

        نسخهٔ ۶ (دکتر فوق دقیق):
          - 🆕 اول Brain.agent() رو صدا می‌زنه (۶۷ ابزار + CoT)
          - ✅ fallback به DeepSeek اگه Brain آماده نبود
        """
        if not text:
            return ""

        # ═══ ۱) Brain.agent() ═══
        try:
            from core.brain import get_brain
            brain = get_brain()

            if brain.client is not None:
                log_info(
                    f"🧠 [AI:brain] Brain.agent | "
                    f"tools={len(brain.TOOL_MAP)}"
                )

                result = await brain.agent(text, mode="precise")

                if result and not result.startswith("⚠️"):
                    return result

                log_warning(
                    f"⚠️ [AI:brain] پاسخ نامعتبر | "
                    f"preview={result[:100] if result else 'خالی'}"
                )
            else:
                log_info(
                    "ℹ️ [AI:brain] client آماده نیست — fallback"
                )
        except ImportError as e:
            log_warning(f"⚠️ [AI:brain] import | {e}")
        except Exception as e:
            log_warning(
                f"⚠️ [AI:brain] {type(e).__name__}: {e}"
            )

        # ═══ ۲) Fallback: DeepSeek ═══
        log_info("🔁 [AI:fallback] DeepSeek مستقیم")

        if not system:
            try:
                from config import get_ai_system_prompt
                system = get_ai_system_prompt("chat")
            except Exception:
                system = ""

        try:
            from core.brain.brain_memory import get_brain_memory
            bm = get_brain_memory()

            if bm.is_task_done(text, within_hours=1):
                return "ℹ️ این کار در ساعت اخیر انجام شد."

            context = bm.build_context(text)
            if context:
                system = f"{system}\\n\\n# دانش پروژه:\\n{context}"
        except Exception as e:
            log_warning(f"⚠️ [AI:bm] {e}")

        response, _ok = await self.chat_with_status(text, system)

        if response and not response.startswith("⚠️"):
            try:
                from core.brain.brain_memory import get_brain_memory
                get_brain_memory().remember(
                    text, response, importance=0.6
                )
            except Exception as e:
                log_warning(f"⚠️ [AI:bm:save] {e}")

        return response


'''

# ─── جایگزینی ───
new_lines = (
    lines[:start_idx]
    + [NEW_CHAT]
    + lines[end_idx:]
)

new_content = "".join(new_lines)
FILE.write_text(new_content, encoding="utf-8")

print("✅ متد chat جایگزین شد!")
print(f"   فایل: {FILE}")
print(f"   خطوط قبل: {len(lines)}")
print(f"   خطوط بعد: {len(new_lines)}")
