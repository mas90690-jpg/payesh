# core/manual/_brain_learning.py
"""
یادگیری + اجرای کد پایتون + فرمول‌ها.

این ماژول:
  - log_unknown (ثبت سؤالات ناشناخته)
  - count_repeats
  - learn_from_ai, promote_candidate, reject_candidate, list_candidates
  - handle_learn_command (یاد بگیر)
  - handle_python_command (اجرای کد)
  - handle_formula_command
  - process (نقطهٔ ورود اصلی) + _log_timing

نسخهٔ ۹ (این نسخه):
  - 🎯 FIX: `_handle_python_command` از CC=28 به CC≈4 کاهش یافت.
    با تقسیم به `_run_python_file` و `_run_python_inline`.
  - 🎯 FIX: `process` از CC=33 به CC≈6 کاهش یافت.
    با تقسیم به `_handle_learning_branch` و `_try_rule_match`.
  - ✅ حفظ همهٔ رفتار قبلی.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

from core.adapters.formula_manager import get_formula_manager
from core.kernel.logger import log_error, log_info, log_success, log_warning

from core.manual._brain_core import (
    MAX_TRIGGER_LEN,
    MIN_TRIGGER_LEN,
    MAX_UNKNOWN_LINES,
    SLOW_PROCESS_MS,
    UNKNOWN_FILE,
    UNKNOWN_MIN_REPEAT,
    UNKNOWN_TAIL_BYTES,
    LOG_PREVIEW_LEN,
    ROOT,
    md5_short,
    tail_lines,
)


# ═══════════════════════════════════════════════════════════
# ثابت‌های python sandbox
# ═══════════════════════════════════════════════════════════
_PY_SAFE_PREAMBLE = (
    "import builtins, os, io, sys\n"
    "from pathlib import Path as _P\n"
    "_ALLOWED = [\n"
    "    os.path.abspath('storage/reports'),\n"
    "]\n"
    "def _ok(p):\n"
    "    ap = os.path.abspath(str(p))\n"
    "    for d in _ALLOWED:\n"
    "        if ap == d or ap.startswith(d + os.sep): return True\n"
    "    return False\n"
    "def _deny(p):\n"
    "    raise PermissionError(\n"
    "        '\\u26d4 ONLY READ (or write in storage/reports/) "
    "— blocked: '\n"
    "        + str(p)\n"
    "    )\n"
    "_ro = builtins.open\n"
    "def _so(f, m='r', *a, **k):\n"
    "    if any(c in str(m) for c in 'wax+'):\n"
    "        if not _ok(f): _deny(f)\n"
    "        try:\n"
    "            os.makedirs(os.path.dirname("
    "os.path.abspath(str(f))), exist_ok=True)\n"
    "        except Exception: pass\n"
    "    return _ro(f, m, *a, **k)\n"
    "builtins.open = _so\n"
    "io.open = _so\n"
    "_wt = _P.write_text\n"
    "def _swt(self, *a, **k):\n"
    "    if not _ok(self): _deny(self)\n"
    "    self.parent.mkdir(parents=True, exist_ok=True)\n"
    "    return _wt(self, *a, **k)\n"
    "_P.write_text = _swt\n"
    "_wb = _P.write_bytes\n"
    "def _swb(self, *a, **k):\n"
    "    if not _ok(self): _deny(self)\n"
    "    self.parent.mkdir(parents=True, exist_ok=True)\n"
    "    return _wb(self, *a, **k)\n"
    "_P.write_bytes = _swb\n"
    "_t = _P.touch\n"
    "def _st(self, *a, **k):\n"
    "    if not _ok(self): _deny(self)\n"
    "    return _t(self, *a, **k)\n"
    "_P.touch = _st\n"
    "_u = _P.unlink\n"
    "def _su(self, *a, **k):\n"
    "    if not _ok(self): _deny(self)\n"
    "    return _u(self, *a, **k)\n"
    "_P.unlink = _su\n"
    "_r = _P.rmdir\n"
    "def _sr(self, *a, **k):\n"
    "    if not _ok(self): _deny(self)\n"
    "    return _r(self, *a, **k)\n"
    "_P.rmdir = _sr\n"
    "_mk = _P.mkdir\n"
    "def _smk(self, *a, **k):\n"
    "    if not _ok(self): _deny(self)\n"
    "    return _mk(self, *a, **k)\n"
    "_P.mkdir = _smk\n"
    "import runpy as _rp\n"
)

# الگوهای کد خطرناک (برای python_file و inline)
_DANGER_PATTERNS = (
    r"\bos\.(system|popen|fork|exec|remove|unlink|"
    r"rmdir|rename|chmod|makedirs|mkdir)\b",
    r"\bsubprocess\b",
    r"\b__import__\b",
    r"\beval\b", r"\bexec\b", r"\bcompile\b",
    r"\bshutil\.\w+\b",
    r"\bos\.replace\b",
    r"\bos\.truncate\b",
)

# SQL نوشتنی
_SQL_WRITE_RE = re.compile(
    r"\.execute\s*\(\s*['\"](?:\s*)"
    r"(DELETE|UPDATE|INSERT|DROP|ALTER|TRUNCATE|"
    r"CREATE|REPLACE)\b",
    re.IGNORECASE,
)


# ═══════════════════════════════════════════════════════════
# BrainLearningMixin
# ═══════════════════════════════════════════════════════════
class BrainLearningMixin:
    """Mixin یادگیری + python + formulas + process."""

    # ═══════════════════════════════════════════
    # Self-learning
    # ═══════════════════════════════════════════
    def _log_unknown(self, text: str, sender: str = None):
        try:
            UNKNOWN_FILE.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "ts": datetime.now().isoformat(),
                "text": text,
                "sender": sender or "unknown",
                "hash": md5_short(text),
            }
            with open(str(UNKNOWN_FILE), "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            try:
                with open(
                    str(UNKNOWN_FILE), encoding="utf-8"
                ) as f:
                    lines = f.readlines()
                if len(lines) > MAX_UNKNOWN_LINES:
                    with open(
                        str(UNKNOWN_FILE), "w", encoding="utf-8"
                    ) as f:
                        f.writelines(lines[-MAX_UNKNOWN_LINES:])
                    log_info(
                        f"🧹 [MBRAIN:unknown] cap | "
                        f"{len(lines)} → {MAX_UNKNOWN_LINES}"
                    )
            except Exception as e:
                log_warning(
                    f"⚠️ [MBRAIN:unknown:cap] "
                    f"{type(e).__name__}: {e}"
                )
        except Exception as e:
            log_warning(
                f"⚠️ [MBRAIN:unknown] {type(e).__name__}: {e}"
            )

    def _count_repeats(self, text: str) -> int:
        if not UNKNOWN_FILE.exists():
            return 0
        h = md5_short(text)
        lines = tail_lines(UNKNOWN_FILE, UNKNOWN_TAIL_BYTES)
        cnt = 0
        for ln in lines:
            try:
                if json.loads(ln).get("hash") == h:
                    cnt += 1
            except Exception:
                continue
        return cnt

    def learn_from_ai(
        self,
        question: str,
        answer: str,
        source: str = "ai",
        auto_promote: bool = False,
    ) -> bool:
        if not question or not answer:
            return False
        q = question.strip()
        a = answer.strip()

        if not self._is_valid_answer(a):
            log_warning(
                f"⚠️ [MBRAIN:learn] پاسخ نامعتبر | "
                f"{a[:LOG_PREVIEW_LEN]!r}"
            )
            return False

        if not self._is_valid_trigger(q):
            log_warning(
                f"⚠️ [MBRAIN:learn] trigger نامعتبر | "
                f"{q[:LOG_PREVIEW_LEN]!r}"
            )
            return False

        if self._trigger_exists(q):
            log_info(
                f"ℹ️ [MBRAIN:learn] trigger «{q[:40]}» "
                f"قبلاً وجود دارد — skip"
            )
            return False

        low = self._normalize(q)
        for c in self._candidates:
            for t in c.get("triggers", []):
                if (
                    isinstance(t, str)
                    and self._normalize(t) == low
                ):
                    return False

        rule = {
            "name": f"ai_learned_{int(datetime.now().timestamp())}",
            "triggers": [q],
            "action": {"type": "text", "text": a},
            "learned": True,
            "source": source,
            "created_at": datetime.now().isoformat(),
        }

        if auto_promote:
            self._save_learned(rule)
            log_success(
                f"✅ [MBRAIN:learn] promote مستقیم | "
                f"«{q[:40]}»"
            )
        else:
            self._candidates.append(rule)
            self._write_candidates()
            log_success(
                f"📝 [MBRAIN:learn] candidate | «{q[:40]}» | "
                f"total={len(self._candidates)}"
            )
        return True

    def promote_candidate(self, name: str) -> bool:
        try:
            promoted = None
            remaining: list[dict] = []
            for r in self._candidates:
                if r.get("name") == name:
                    promoted = r
                else:
                    remaining.append(r)
            if not promoted:
                log_info(f"ℹ️ [MBRAIN:promote] «{name}» نبود")
                return False
            if not self._is_rule_healthy(promoted):
                log_warning(
                    f"⛔ [MBRAIN:promote] «{name}» نامعتبر — حذف"
                )
                self._candidates = remaining
                self._write_candidates()
                return False
            self._candidates = remaining
            self._write_candidates()
            self._save_learned(promoted)
            log_success(
                f"✅ [MBRAIN:promote] «{name}» | "
                f"learned={len(self.learned_rules)} "
                f"candidates={len(self._candidates)}"
            )
            return True
        except Exception as e:
            log_warning(
                f"⚠️ [MBRAIN:promote] {type(e).__name__}: {e}"
            )
            return False

    def list_candidates(self) -> list[dict]:
        return list(self._candidates)

    def reject_candidate(self, name: str) -> bool:
        try:
            before = len(self._candidates)
            self._candidates = [
                c for c in self._candidates
                if c.get("name") != name
            ]
            if len(self._candidates) < before:
                self._write_candidates()
                log_info(f"🗑️ [MBRAIN:reject] «{name}» حذف شد")
                return True
        except Exception as e:
            log_warning(
                f"⚠️ [MBRAIN:reject] {type(e).__name__}: {e}"
            )
        return False

    def _handle_learn_command(self, text: str) -> str | None:
        """یادگیری از پیام کاربر — «یاد بگیر «سؤال» = «جواب»»."""
        m = re.search(
            r"یاد\s*بگیر[:\s]*(.+?)\s*=\s*(.+)",
            text,
            re.DOTALL,
        )
        if not m:
            return (
                "📝 قالب:\n"
                "• یاد بگیر «سؤال» = «جواب»\n"
                "• یاد بگیر «سؤال» = SQL: SELECT ...\n"
                "• تأیید <candidate_name>\n"
                "• رد <candidate_name>\n"
                "• لیست candidate ها"
            )

        trigger = m.group(1).strip().strip("«»\"'")
        answer = m.group(2).strip().strip("«»\"'")

        if not self._is_valid_trigger(trigger):
            return (
                f"⛔ trigger نامعتبر (طول باید بین "
                f"{MIN_TRIGGER_LEN} و {MAX_TRIGGER_LEN} باشد)."
            )

        is_sql = answer.lower().startswith("sql:")
        if is_sql:
            answer = answer[4:].strip()

        if not is_sql and not self._is_valid_answer(answer):
            return "⛔ پاسخ نامعتبر (خالی/کوتاه/خطا)."

        rule_name = (
            f"user_learned_{int(datetime.now().timestamp())}"
        )
        if is_sql:
            rule = {
                "name": rule_name,
                "triggers": [trigger],
                "action": {
                    "type": "sql_scalar",
                    "sql": answer,
                    "template": "📊 {v}",
                },
                "learned": True,
                "source": "user",
                "created_at": datetime.now().isoformat(),
            }
        else:
            rule = {
                "name": rule_name,
                "triggers": [trigger],
                "action": {"type": "text", "text": answer},
                "learned": True,
                "source": "user",
                "created_at": datetime.now().isoformat(),
            }

        self._save_learned(rule)
        log_success(
            f"✅ [MBRAIN:learn:manual] «{trigger[:40]}» | "
            f"type={'sql' if is_sql else 'text'}"
        )
        return f"✅ یاد گرفتم.\n📝 «{trigger}» → «{answer[:100]}»"

    # ═══════════════════════════════════════════
    # Python execution — dispatcher + helpers
    # ═══════════════════════════════════════════
    def _handle_python_command(self, text: str) -> str | None:
        """🎯 FIX: dispatcher — CC پایین.

        - python_file: / پایتون فایل: → _run_python_file
        - python: / پایتون: / >>> → _run_python_inline
        """
        low = self._normalize(text)

        # ─── branch 1: python_file ───
        if "python_file:" in low or "پایتون فایل:" in low:
            return self._run_python_file(text)

        # ─── branch 2: inline python ───
        triggers = (
            "python:", "پایتون:", ">>>",
            "اجرا کن پایتون", "python run",
        )
        if not any(t in low for t in triggers):
            return None
        return self._run_python_inline(text)

    def _run_python_file(self, text: str) -> str:
        """اجرای یک فایل .py — با sandbox."""
        m = re.search(
            r"(?:python_file|پایتون فایل)\s*:\s*(\S+)",
            text, re.IGNORECASE,
        )
        if not m:
            return "🐍 مثال: python_file: data/test.py"

        rel_path = m.group(1).strip()
        full_path = os.path.join(str(ROOT), rel_path)

        if not os.path.exists(full_path):
            return f"⚠️ فایل پیدا نشد: {rel_path}"
        if not full_path.endswith(".py"):
            return "⛔ فقط فایل‌های .py مجاز هستند."

        try:
            with open(full_path, encoding="utf-8") as f:
                file_code = f.read()
        except Exception as e:
            return f"❌ خطا در خواندن فایل: {e}"

        # چک الگوهای خطرناک
        for pat in _DANGER_PATTERNS:
            if re.search(pat, file_code, re.IGNORECASE):
                log_warning(f"⛔ [MBRAIN:pyfile] الگو | {pat}")
                return f"⛔ فایل شامل الگوی ممنوعه است: {pat}"

        full_code = (
            _PY_SAFE_PREAMBLE
            + f"_rp.run_path({full_path!r}, "
            f"run_name='__main__')\n"
        )

        try:
            t0 = time.monotonic()
            r = subprocess.run(
                [sys.executable, "-c", full_code],
                capture_output=True, text=True, timeout=30,
                encoding="utf-8", errors="replace",
                cwd=str(ROOT),
            )
            elapsed_ms = (time.monotonic() - t0) * 1000
            out = (r.stdout or "") + (r.stderr or "")
            if not out.strip():
                out = "(بدون خروجی)"
            log_success(
                f"🐍 [MBRAIN:pyfile] {rel_path} | "
                f"rc={r.returncode} | {elapsed_ms:.0f}ms"
            )
            icon = "✅" if r.returncode == 0 else "⚠️"
            return (
                f"{icon} فایل {rel_path} اجرا شد "
                f"(rc={r.returncode}):\n{out[:2000]}"
            )
        except subprocess.TimeoutExpired:
            return "⏰ اجرای فایل بیش از ۳۰ ثانیه طول کشید."
        except Exception as e:
            return f"❌ خطا: {e}"

    def _extract_python_code(self, text: str) -> str | None:
        """استخراج کد از ```block``` یا `python: ...`."""
        m = re.search(
            r"```(?:python|py)?\s*\n?(.+?)```",
            text, re.DOTALL,
        )
        if m:
            return m.group(1).strip()

        m = re.search(
            r"(?:python|پایتون)\s*:\s*(.+)",
            text, re.DOTALL | re.IGNORECASE,
        )
        if m:
            return m.group(1).strip()

        return None

    def _run_python_inline(self, text: str) -> str:
        """اجرای کد inline — با sandbox و فیلتر danger."""
        code = self._extract_python_code(text)
        if not code:
            return (
                "🐍 قواعد:\n"
                "• خواندن همه‌جا ✅\n"
                "• نوشتن فقط در storage/reports/ ✅\n"
                "• ویرایش/حذف فایل‌های اصلی ⛔\n\n"
                "مثال: python: print(open('core/brain.py').read()[:200])"
            )

        # چک الگوهای خطرناک
        for pat in _DANGER_PATTERNS:
            if re.search(pat, code, re.IGNORECASE):
                log_warning(
                    f"⛔ [MBRAIN:py] الگو | {pat} | "
                    f"code={code[:60]!r}"
                )
                return f"⛔ این کد ممنوع است (شامل «{pat}»)."

        # چک SQL نوشتنی
        sql_write = _SQL_WRITE_RE.search(code)
        if sql_write:
            log_warning(
                f"⛔ [MBRAIN:py] SQL نوشتنی | "
                f"keyword={sql_write.group(1)}"
            )
            return (
                f"⛔ کوئری نوشتنی ({sql_write.group(1)}) "
                f"روی دیتابیس ممنوع است."
            )

        full_code = _PY_SAFE_PREAMBLE + "\n" + code
        t0 = time.monotonic()
        try:
            r = subprocess.run(
                [sys.executable, "-c", full_code],
                capture_output=True, text=True, timeout=10,
                encoding="utf-8", errors="replace", cwd=str(ROOT),
            )
            elapsed_ms = (time.monotonic() - t0) * 1000
            out = (r.stdout or "") + (r.stderr or "")
            if not out.strip():
                out = "(بدون خروجی)"
            log_success(
                f"🐍 [MBRAIN:py] rc={r.returncode} | "
                f"{elapsed_ms:.0f}ms | len={len(code)}"
            )
            icon = "✅" if r.returncode == 0 else "⚠️"
            return (
                f"{icon} کد اجرا شد (rc={r.returncode}):\n"
                f"{out[:1500]}"
            )
        except subprocess.TimeoutExpired:
            log_warning("⏰ [MBRAIN:py] timeout")
            return "⏰ کد بیش از ۱۰ ثانیه طول کشید و متوقف شد."
        except Exception as e:
            log_error(f"❌ [MBRAIN:py] {type(e).__name__}: {e}")
            return f"❌ خطا در اجرا: {e}"

    # ═══════════════════════════════════════════
    # Formula commands
    # ═══════════════════════════════════════════
    def _handle_formula_command(self, text: str) -> str | None:
        low = self._normalize(text)
        fm = self._fm

        if "ذخیره کن" in text or "ذخیره فرمول" in text:
            names = self._extract_quoted(text)
            if not names:
                return "📝 قالب: «ذخیره کن «اسم» = SELECT ...»"
            m = re.search(r"=\s*(.+)", text, re.DOTALL)
            if not m:
                return "📝 قالب: «ذخیره کن «اسم» = SELECT ...»"
            return fm.save(names[0], m.group(1).strip())

        if text.startswith("تأیید "):
            names = self._extract_quoted(text)
            if names and fm.has_pending_confirm(names[0]):
                return fm.execute(names[0], confirm=True)

        if text.startswith("اجرا کن ") or text.startswith(
            "اجرا فرمول "
        ):
            names = self._extract_quoted(text)
            if not names:
                return "📝 قالب: «اجرا کن «اسم»»"
            confirm = ("تأیید" in text or "confirm" in low)
            return fm.execute(
                names[0], text=text, confirm=confirm
            )

        if any(w in low for w in [
            "لیست فرمول", "فرمول‌ها", "فرمولها",
            "همه فرمول", "فرمول های",
        ]):
            return fm.list_all()

        if text.startswith("نمایش فرمول "):
            names = self._extract_quoted(text)
            if names:
                return fm.show(names[0])

        if text.startswith("حذف فرمول "):
            names = self._extract_quoted(text)
            if names:
                return fm.delete(names[0])

        return None

    # ═══════════════════════════════════════════
    # process helpers — هر کدام تابع کوچک
    # ═══════════════════════════════════════════
    def _handle_learning_branch(
        self, text: str
    ) -> str | None:
        """یاد بگیر / تأیید / رد / لیست candidate.

        Returns:
            پاسخ متنی یا None (اگر هیچ‌کدام نبود).
        """
        # یاد بگیر
        if "یاد بگیر" in text or "یادگیری" in text[:10]:
            return self._handle_learn_command(text)

        # تأیید
        if text.startswith("تأیید ") or text.startswith("تایید "):
            name = text.split(" ", 1)[1].strip().strip("«»\"'")
            if self.promote_candidate(name):
                return f"✅ قانون «{name}» فعال شد."
            return (
                f"❌ قانون «{name}» در candidate ها "
                f"پیدا نشد یا نامعتبر بود."
            )

        # رد
        if text.startswith("رد "):
            name = text.split(" ", 1)[1].strip().strip("«»\"'")
            if self.reject_candidate(name):
                return f"🗑️ candidate «{name}» حذف شد."
            return f"❌ candidate «{name}» پیدا نشد."

        # لیست
        if (
            "لیست candidate" in text.lower()
            or "candidate ها" in text.lower()
        ):
            cands = self.list_candidates()
            if not cands:
                return "📭 هیچ candidate ای نیست."
            lines = [f"📋 {len(cands)} candidate:"]
            for c in cands[:20]:
                trg = (
                    c.get("triggers", [""])[0]
                    if c.get("triggers") else "?"
                )
                lines.append(
                    f"   • {c.get('name')} ← «{str(trg)[:50]}»"
                )
            return "\n".join(lines)

        return None

    def _try_set_param_branch(
        self, text: str
    ) -> str | None:
        """KEY=VALUE — ذخیره در .env."""
        m = re.search(r"\b([A-Z_]{4,})\s*[=:]\s*(\S+)", text)
        if not m:
            return None
        key, val = m.group(1), m.group(2)
        if not self._access_available:
            return None
        ok, reason = self._access_mgr.can_write_setting(key)
        if not ok:
            return f"⛔ {reason}"
        return self._set_setting(key, val)

    def _try_rule_match(
        self, text: str
    ) -> str | None:
        """تطبیق دقیق + فازی — با bump stats."""
        # تطبیق دقیق
        rule, score, layer = self._match_exact(text)
        if rule:
            reply = self._execute_action(
                rule.get("action", {}), text
            )
            if reply:
                self._bump_stat(
                    rule.get("name", "?"), "success"
                )
                log_success(
                    f"✅ [MBRAIN:match:exact] [{layer}] "
                    f"{rule.get('name')} | score={score}"
                )
                return reply
            self._bump_stat(rule.get("name", "?"), "fail")

        # تطبیق فازی
        fz = self._match_fuzzy(text)
        if fz:
            reply = self._execute_action(
                fz.get("action", {}), text
            )
            if reply:
                self._bump_stat(
                    fz.get("name", "?"), "success"
                )
                log_success(
                    f"✅ [MBRAIN:match:fuzzy] {fz.get('name')}"
                )
                return reply
        return None

    async def _try_ai_fallback(
        self, text: str, ai_fallback: Callable
    ) -> str | None:
        """AI fallback — با ثبت ناشناخته."""
        try:
            t_ai = time.monotonic()
            ai_reply = await ai_fallback(text)
            ai_ms = (time.monotonic() - t_ai) * 1000
            if not ai_reply:
                return None

            if not self._is_valid_answer(ai_reply):
                log_warning(
                    f"⚠️ [MBRAIN:ai] پاسخ نامعتبر — "
                    f"نادیده | "
                    f"{str(ai_reply)[:LOG_PREVIEW_LEN]!r}"
                )
                return None

            repeats = self._count_repeats(text)
            auto = (
                repeats >= UNKNOWN_MIN_REPEAT
                and not self._trigger_exists(text)
            )
            saved = self.learn_from_ai(
                text, ai_reply,
                source="ai",
                auto_promote=False,
            )
            if saved and auto:
                log_success(
                    f"🧠 [MBRAIN:ai] سؤال تکراری "
                    f"({repeats}×) → promote مستقیم"
                )
            elif saved:
                log_info(
                    "📝 [MBRAIN:ai] → candidate "
                    "(نیاز به تأیید)"
                )
            log_info(
                f"🎬 [MBRAIN:process] ai branch | "
                f"ai={ai_ms:.0f}ms"
            )
            return ai_reply
        except Exception as e:
            log_warning(
                f"⚠️ [MBRAIN:ai] {type(e).__name__}: {e}"
            )
            return None

    # ═══════════════════════════════════════════
    # process — نقطهٔ ورود (dispatcher)
    # ═══════════════════════════════════════════
    async def process(
        self,
        text: str,
        sender: str = None,
        ai_fallback: Callable | None = None,
    ) -> str | None:
        """🎯 FIX: dispatcher — CC از ۳۳ به ~۶ کاهش یافت.

        ترتیب branchها:
          ۱. formula
          ۲. learning (یاد بگیر / تأیید / رد / لیست)
          ۳. keyword
          ۴. error
          ۵. settings
          ۶. python
          ۷. set_param
          ۸. rule match (exact + fuzzy)
          ۹. AI fallback
          ۱۰. unknown
        """
        if not text:
            return None
        text = text.strip()
        t_all = time.monotonic()

        log_info(
            f"▶️ [MBRAIN:process] len={len(text)} | "
            f"in={text[:LOG_PREVIEW_LEN]!r}"
        )

        # ─── ۱) formula ───
        reply = self._handle_formula_command(text)
        if reply:
            self._log_timing(t_all, "formula")
            return reply

        # ─── ۲) learning ───
        reply = self._handle_learning_branch(text)
        if reply:
            self._log_timing(t_all, "learning")
            return reply

        # ─── ۳) keyword ───
        reply = self._handle_keyword_command(text)
        if reply:
            self._log_timing(t_all, "keyword")
            return reply

        # ─── ۴) error ───
        reply = self._handle_error_command(text)
        if reply:
            self._log_timing(t_all, "error")
            return reply

        # ─── ۵) settings ───
        reply = self._handle_settings_command(text)
        if reply:
            self._log_timing(t_all, "settings")
            return reply

        # ─── ۶) python ───
        reply = self._handle_python_command(text)
        if reply:
            self._log_timing(t_all, "python")
            return reply

        # ─── ۷) set_param ───
        reply = self._try_set_param_branch(text)
        if reply:
            self._log_timing(t_all, "set_param")
            return reply

        # ─── ۸) rule match (exact + fuzzy) ───
        reply = self._try_rule_match(text)
        if reply:
            self._log_timing(t_all, "match")
            return reply

        # ─── ۹) AI fallback ───
        if ai_fallback:
            reply = await self._try_ai_fallback(text, ai_fallback)
            if reply:
                self._log_timing(t_all, "ai")
                return reply

        # ─── ۱۰) unknown ───
        self._log_unknown(text, sender)
        log_info("🎬 [MBRAIN:process] ناشناخته — ثبت شد")
        self._log_timing(t_all, "unknown")
        return None

    def _log_timing(self, t0: float, branch: str):
        elapsed_ms = (time.monotonic() - t0) * 1000
        if elapsed_ms > SLOW_PROCESS_MS:
            log_warning(
                f"🐌 [MBRAIN:process] {branch} کند | "
                f"{elapsed_ms:.0f}ms"
            )