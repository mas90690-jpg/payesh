# scripts/fix_ai_assistant.py
"""جایگزینی متد chat در ai_assistant.py."""
import argparse
import sys
from pathlib import Path


NEW_CHAT = '''    async def chat(self, text: str, system: str = "") -> str:
        """چت آزاد — اول Brain.agent()، بعد fallback به DeepSeek."""
        if not text:
            return ""

        # ۱) Brain.agent()
        try:
            from core.brain import get_brain
            brain = get_brain()

            if brain.client is not None:
                log_info(f"🧠 [AI:brain] Brain.agent | tools={len(brain.TOOL_MAP)}")
                result = await brain.agent(text, mode="precise")

                if result and not result.startswith("⚠️"):
                    return result

                log_warning(f"⚠️ [AI:brain] پاسخ نامعتبر | preview={result[:100] if result else 'خالی'}")
            else:
                log_info("ℹ️ [AI:brain] client آماده نیست — fallback")
        except ImportError as e:
            log_warning(f"⚠️ [AI:brain] import | {e}")
        except Exception as e:
            log_warning(f"⚠️ [AI:brain] {type(e).__name__}: {e}")

        # ۲) Fallback: DeepSeek
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
                get_brain_memory().remember(text, response, importance=0.6)
            except Exception as e:
                log_warning(f"⚠️ [AI:bm:save] {e}")

        return response


'''


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", default="core/services/ai_assistant.py")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    file_path = Path(args.file)
    if not file_path.exists():
        print(f"❌ فایل پیدا نشد: {file_path}")
        return 1

    content = file_path.read_text(encoding="utf-8")
    lines = content.splitlines(keepends=True)

    start_idx = None
    for i, line in enumerate(lines):
        if line.strip().startswith("async def chat("):
            start_idx = i
            break

    if start_idx is None:
        print("❌ متد chat پیدا نشد!")
        return 1

    end_idx = None
    for j in range(start_idx + 1, len(lines)):
        if lines[j].startswith(("    async def ", "    def ")):
            end_idx = j
            break

    if end_idx is None:
        print("❌ پایان متد chat پیدا نشد!")
        return 1

    print(f"📍 متد chat: خطوط {start_idx + 1} تا {end_idx}")

    new_lines = lines[:start_idx] + [NEW_CHAT] + lines[end_idx:]
    new_content = "".join(new_lines)

    if args.dry_run:
        print(f"🔍 dry-run | قبل: {len(lines)} | بعد: {len(new_lines)}")
        return 0

    backup = file_path.with_suffix(file_path.suffix + ".bak")
    if not backup.exists():
        backup.write_text(content, encoding="utf-8")
        print(f"📦 backup: {backup}")

    file_path.write_text(new_content, encoding="utf-8")
    print(f"✅ جایگزین شد | قبل: {len(lines)} | بعد: {len(new_lines)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())