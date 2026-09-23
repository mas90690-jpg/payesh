"""حافظهٔ گفتگوی ChatOps — JSONL با cap."""
from __future__ import annotations

import json
import os
from datetime import datetime

from core.kernel.logger import log_warning


class AdminMemory:
    """ذخیره/خواندنِ آخرین گفتگوها برای context دادن به AI."""

    def __init__(self, path: str, max_entries: int = 200):
        self.path = path
        self.max_entries = max_entries

    def recent(self, n: int = 6) -> str:
        """متن آماده برای درج در system prompt."""
        if not os.path.exists(self.path):
            return ""
        try:
            with open(self.path, encoding="utf-8") as f:
                lines = f.readlines()[-n:]
            items = []
            for ln in lines:
                try:
                    o = json.loads(ln)
                    items.append(
                        f"س: {o.get('q','')[:120]}\n"
                        f"ج: {o.get('a','')[:160]}"
                    )
                except Exception:
                    continue
            if not items:
                return ""
            return "\n\n# گفتگوی اخیر (حافظه):\n" + "\n".join(items)
        except Exception as e:
            log_warning(f"⚠️ [MEM:read] {type(e).__name__}: {e}")
            return ""

    def remember(self, q: str, a: str):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(
                    json.dumps(
                        {"ts": datetime.now().isoformat(), "q": q, "a": a},
                        ensure_ascii=False,
                    )
                    + "\n"
                )
            with open(self.path, encoding="utf-8") as f:
                lines = f.readlines()
            if len(lines) > self.max_entries:
                with open(self.path, "w", encoding="utf-8") as f:
                    f.writelines(lines[-self.max_entries:])
        except Exception as e:
            log_warning(f"⚠️ [MEM:write] {type(e).__name__}: {e}")
