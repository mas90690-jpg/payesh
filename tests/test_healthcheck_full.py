# tests/test_healthcheck_full.py
"""
🔬 Health Check فوق‌دقیق — تحلیل استاتیک پروژه.

بدون اجرای scrape، فقط کد را می‌خواند و تحلیل می‌کند.
~۱۵-۳۰ ثانیه، مناسب برای CI و اجرای مرتب.

۱۲ محور:
  ۱) ساختار پروژه
  ۲) ساختار کد (کلاس‌ها و متدهای اجباری)
  ۳) استاندارد (type hints, docstrings, naming)
  ۴) منطق (except لخت، broad except، return مخلوط)
  ۵) یکپارچگی (config ↔ collectors ↔ .env)
  ۶) تمیزی (unused imports/vars/args، dead code، کد تکراری)
  ۷) به‌روز بودن (requirements، نسخه‌ها)
  ۸) ایمپورت‌ها (ترتیب، سنگین، حلقه‌ای)
  ۹) پیچیدگی (تابع بلند، if تودرتو، پارامتر زیاد)
  ۱۰) امنیت (hardcoded path، eval/exec، رمز در کد)
  ۱۱) اسناد (README، docstrings)
  ۱۲) آمار کلی

نسخهٔ ۲ (این نسخه):
  - 🐛 FIX: تابع تکراری _returns_in_own_body حذف شد
    (قبلاً دو بار تعریف شده بود → دومی اولی را override می‌کرد).
  - 🎯 MOVE: _returns_in_own_body از میان check_standards به
    بخش «ابزارهای AST» منتقل شد (قبل از اولین استفاده).
  - 🎯 ADD: test_db_full به _EXPECTED_TESTS اضافه شد
    (پنل گزینهٔ ۱۰ به آن ارجاع می‌دهد).
  - 🎯 ADD: analyzer_files به ساختار پروژه اضافه شد
    (بررسی ۴ فایل analyzers).
  - 🎯 MODERN: تایپ‌هینت‌ها با | (Python 3.10+)، بدون Optional.
  - 🎯 CLEAN: بخش‌ها با کامنت‌های واضح از هم جدا شدند.
  - ✅ حفظ همهٔ ۱۲ محور و منطق نسخهٔ ۱.

اجرا:
    python tests/test_healthcheck_full.py
    python tests/test_healthcheck_full.py --json-only
    python tests/test_healthcheck_full.py --no-color
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import time
import tokenize
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


# ═════════════════════════════════════════════════════════
# رنگ‌ها
# ═════════════════════════════════════════════════════════
class C:
    OK = "\033[92m"
    FAIL = "\033[91m"
    WARN = "\033[93m"
    INFO = "\033[96m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    END = "\033[0m"


def _disable_color() -> None:
    for attr in ("OK", "FAIL", "WARN", "INFO", "BOLD", "DIM", "END"):
        setattr(C, attr, "")


# ═════════════════════════════════════════════════════════
# ثابت‌ها
# ═════════════════════════════════════════════════════════
_IGNORE_PARTS = {
    "__pycache__", ".venv", "venv", "myenv", ".env",
    "env", ".git", ".idea", ".vscode", "node_modules",
    "backups", "backup", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "site-packages",
}

_EXPECTED_PLATFORMS: dict[str, str] = {
    "bale": "balecollector",
    "eitaa": "eitaacollector",
    "rubika": "rubikacollector",
    "telegram": "telegramcollector",
    "whatsapp": "whatsappcollector",
    "linkedin": "linkedincollector",
    "instagram": "instagramcollector", 
    "web": "webcollector",
}

_EXPECTED_COLLECTOR_FILES: set[str] = {
    "bale", "eitaa", "rubika", "telegram",
    "whatsapp", "linkedin", "instagram",   # ← اضافه
    "web",
}

# 🎯 ADD: analyzers
_EXPECTED_ANALYZER_FILES: set[str] = {
    "keyword_finder", "lead_scorer",
    "priority_finder", "target_filter",
}

# 🎯 FIX: test_db_full اضافه شد (پنل گزینهٔ ۱۰)
_EXPECTED_TESTS: set[str] = {
    "test_bale_full", "test_eitaa_full", "test_rubika_full",
    "test_telegram_full", "test_whatsapp_full",
    "test_linkedin_full", "test_instagram_full",   # ← اضافه
    "test_web_full",
    "test_storage_full", "test_healthcheck_full",
    "test_db_full",
    "run_all_tests", "helpers", "pipeline_helpers",
    "db_helpers",
}

_KEY_LIBS: list[tuple[str, str]] = [
    ("playwright", "playwright"),
    ("rubpy", "rubpy"),
    ("telethon", "telethon"),
    ("bs4", "beautifulsoup4"),
    ("dotenv", "python-dotenv"),
    ("pytest", "pytest"),
    ("psutil", "psutil"),
    ("aiohttp", "aiohttp"),
    ("aiosqlite", "aiosqlite"),
    ("fastapi", "fastapi"),
    ("uvicorn", "uvicorn"),
    ("openai", "openai"),
    ("yaml", "PyYAML"),
    ("markdownify", "markdownify"),
    ("rich", "rich"),
    ("tabulate", "tabulate"),
    ("tqdm", "tqdm"),
]

_CRITICAL_REQ_PKGS: set[str] = {
    "playwright", "rubpy", "beautifulsoup4",
    "python-dotenv", "pytest", "psutil",
}

# ─── handlerهای واقعی (بقیه helper هستند) ───
_REAL_HANDLERS: set[str] = {"storage_handler", "admin_handler"}

# ─── ایمپورت‌هایی که خودشان استفاده‌اند ولی regex نمی‌بیند ───
_IMPORT_SPECIAL: set[str] = {
    "annotations",
    "print_function",
    "division",
    "absolute_import",
    "unicode_literals",
}

# ─── متدهای جادویی که در unused var حساب نمی‌شوند ───
_MAGIC_METHODS: set[str] = {
    "__init__", "__repr__", "__str__", "__eq__", "__hash__",
    "__enter__", "__exit__", "__aenter__", "__aexit__",
    "__iter__", "__next__", "__len__", "__getitem__",
    "__setitem__", "__call__", "__bool__", "__int__",
    "__float__", "__contains__", "__add__", "__sub__",
    "__mul__", "__truediv__", "__floordiv__", "__mod__",
    "__pow__", "__and__", "__or__", "__xor__", "__get__",
    "__set__", "__delete__", "__set_name__", "__init_subclass__",
    "__class_getitem__", "__post_init__",
}

# ─── stdlib (برای تشخیص ترتیب ایمپورت) ───
_STDLIB: set[str] = {
    "os", "sys", "re", "json", "time", "ast", "typing",
    "pathlib", "datetime", "collections", "asyncio",
    "subprocess", "sqlite3", "shutil", "tempfile",
    "functools", "itertools", "argparse", "signal",
    "atexit", "socket", "hashlib", "uuid", "logging",
    "traceback", "contextlib", "dataclasses", "enum",
    "io", "math", "random", "string", "copy", "abc",
    "inspect", "importlib", "warnings", "urllib",
    "http", "email", "base64", "csv", "glob", "platform",
    "tokenize", "statistics", "textwrap", "unicodedata",
    "queue", "threading", "multiprocessing", "concurrent",
}

_LOCAL_TOP: set[str] = {
    "core", "collectors", "handlers", "analyzers",
    "tests", "config", "main", "login", "panel",
}


# ═════════════════════════════════════════════════════════
# گزارش
# ═════════════════════════════════════════════════════════
class HealthReport:
    """جمع‌آوری و چاپ نتایج Health Check."""

    def __init__(self, quiet: bool = False) -> None:
        self.started = datetime.now()
        self.t0 = time.monotonic()
        self.sections: dict[str, list[dict[str, Any]]] = {}
        self.current_section = "?"
        self.quiet = quiet

    def section(self, name: str) -> None:
        self.current_section = name
        self.sections.setdefault(name, [])
        if not self.quiet:
            print()
            print(f"{C.BOLD}═══ {name} ═══{C.END}")

    def add(
        self,
        name: str,
        status: str,
        detail: str = "",
        items: list[str] | None = None,
    ) -> None:
        """اضافه کردن نتیجه. items = لیست نمونه برای نمایش."""
        item: dict[str, Any] = {
            "name": name,
            "status": status,
            "detail": detail[:500],
            "section": self.current_section,
            "items": (items or [])[:20],
        }
        self.sections.setdefault(self.current_section, []).append(item)

        if self.quiet:
            return
        icon = {
            "ok": f"{C.OK}✅{C.END}",
            "fail": f"{C.FAIL}❌{C.END}",
            "warn": f"{C.WARN}⚠️{C.END}",
            "skip": f"{C.INFO}⏭️{C.END}",
            "info": f"{C.INFO}ℹ️{C.END}",
        }.get(status, "?")
        print(f"  {icon} [{name}] {detail}")

        if items and status in ("warn", "fail"):
            for x in items[:3]:
                print(f"       {C.DIM}• {x}{C.END}")
            if len(items) > 3:
                print(
                    f"       {C.DIM}... و {len(items) - 3} مورد دیگر"
                    f"{C.END}"
                )

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {
            "ok": 0, "fail": 0, "warn": 0, "skip": 0, "info": 0,
        }
        for items in self.sections.values():
            for it in items:
                counts[it["status"]] = counts.get(it["status"], 0) + 1
        return counts

    def print_summary(self) -> bool:
        counts = self.summary()
        elapsed = time.monotonic() - self.t0
        print()
        print("=" * 70)
        print(f"{C.BOLD}📊 گزارش نهایی Health Check{C.END}")
        print("=" * 70)
        for sec_name, items in self.sections.items():
            ok = sum(1 for i in items if i["status"] == "ok")
            fail = sum(1 for i in items if i["status"] == "fail")
            warn = sum(1 for i in items if i["status"] == "warn")
            icon = "✅" if fail == 0 else "❌"
            print(
                f"  {icon} {sec_name:42s} "
                f"ok={ok:3d} fail={fail:2d} warn={warn:2d}"
            )
        print()
        print(f"  ✅ موفق:    {counts['ok']}")
        print(f"  ❌ ناموفق:  {counts['fail']}")
        print(f"  ⚠️  هشدار:  {counts['warn']}")
        print(f"  ⏭️  رد شده: {counts['skip']}")
        print(f"  ℹ️  اطلاعات: {counts['info']}")
        print(f"  ⏱️  زمان:    {elapsed:.1f}s")
        print("=" * 70)

        if counts["fail"] > 0:
            print(f"\n{C.FAIL}❌ موارد ناموفق:{C.END}")
            for sec_name, items in self.sections.items():
                for it in items:
                    if it["status"] == "fail":
                        print(
                            f"  • [{sec_name}] {it['name']}: "
                            f"{it['detail']}"
                        )

        if counts["warn"] > 0 and not self.quiet:
            print(f"\n{C.WARN}⚠️  خلاصهٔ هشدارها:{C.END}")
            for sec_name, items in self.sections.items():
                for it in items:
                    if it["status"] == "warn":
                        line = (
                            f"  • [{sec_name}] {it['name']}: "
                            f"{it['detail']}"
                        )
                        print(line)
                        for x in it.get("items", [])[:3]:
                            print(f"       {C.DIM}• {x}{C.END}")

        return counts["fail"] == 0

    def save(self) -> str:
        ts = self.started.strftime("%Y%m%d_%H%M%S")
        out_dir = ROOT / "storage" / "test_reports"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"healthcheck_{ts}.json"
        data = {
            "type": "healthcheck_deep",
            "version": 2,
            "started_at": self.started.isoformat(),
            "elapsed_s": round(time.monotonic() - self.t0, 2),
            "summary": self.summary(),
            "sections": self.sections,
        }
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n📁 گزارش ذخیره شد: {path}")
        return str(path)


# ═════════════════════════════════════════════════════════
# ابزارهای فایل + AST
# ═════════════════════════════════════════════════════════
def _is_ignored(path: Path) -> bool:
    return bool(set(path.parts) & _IGNORE_PARTS)


def _py_files(
    skip_tests: bool = True,
    include_backups: bool = False,
) -> list[Path]:
    out: list[Path] = []
    for p in ROOT.rglob("*.py"):
        if _is_ignored(p):
            continue
        if not include_backups and "backup" in str(p).lower():
            continue
        if skip_tests and "tests" in p.parts:
            continue
        out.append(p)
    return out


def _parse(path: Path) -> ast.Module | None:
    try:
        with tokenize.open(path) as f:
            return ast.parse(f.read())
    except (SyntaxError, OSError, UnicodeDecodeError,
            LookupError, ValueError):
        return None


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _src(path: Path) -> str:
    try:
        with tokenize.open(path) as f:
            return f.read()
    except (SyntaxError, OSError, UnicodeDecodeError,
            LookupError, ValueError):
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""


def _funcs(tree: ast.Module):
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield n


def _classes(tree: ast.Module):
    for n in ast.walk(tree):
        if isinstance(n, ast.ClassDef):
            yield n


def _names_used_in_tree(tree: ast.Module) -> set[str]:
    """همهٔ اسم‌هایی که در AST استفاده می‌شوند (نه فقط تعریف)."""
    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            n = node
            while isinstance(n, ast.Attribute):
                n = n.value
            if isinstance(n, ast.Name):
                used.add(n.id)
        elif isinstance(node, ast.Constant):
            if isinstance(node.value, str):
                for m in re.finditer(
                    r"\b([A-Za-z_][A-Za-z0-9_]*)\b", node.value
                ):
                    used.add(m.group(1))
    return used


# ─── ابزار AST (مشترک بین check_standards و check_logic) ───
def _returns_in_own_body(node: ast.AST) -> tuple[int, int]:
    """
    تعداد returnهای مستقیم یک تابع (بدون نفوذ به توابع تودرتو).

    Returns:
        (تعداد return با مقدار، تعداد return بدون مقدار)
    """
    with_val = 0
    without = 0

    def _walk(n: ast.AST) -> None:
        nonlocal with_val, without
        for child in ast.iter_child_nodes(n):
            # 🐛 FIX: به توابع تودرتو نفوذ نکن
            if isinstance(
                child,
                (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda),
            ):
                continue
            if isinstance(child, ast.Return):
                if child.value is None:
                    without += 1
                else:
                    with_val += 1
            _walk(child)

    _walk(node)
    return with_val, without


def _max_depth(node: ast.AST, current: int = 0) -> int:
    """حداکثر عمق تو در توی بلوک‌های کنترلی."""
    max_d = current
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.If, ast.For, ast.While,
                              ast.With, ast.AsyncWith, ast.AsyncFor)):
            max_d = max(max_d, _max_depth(child, current + 1))
        else:
            max_d = max(max_d, _max_depth(child, current))
    return max_d


# ═════════════════════════════════════════════════════════
# ۱) ساختار پروژه
# ═════════════════════════════════════════════════════════
def check_project_structure(r: HealthReport) -> None:
    r.section("۱) ساختار پروژه")

    for d in [
        "collectors", "handlers", "analyzers",
        "core", "core/database", "core/kernel",
        "core/services", "core/adapters",
        "tests", "storage", "data",
    ]:
        p = ROOT / d
        r.add(f"dir.{d}", "ok" if p.is_dir() else "fail",
              "موجود" if p.is_dir() else "پیدا نشد")

    for f in [
        "config.py", "main.py", "login.py", "panel.py",
        "requirements.txt", ".gitignore",
    ]:
        p = ROOT / f
        ok = p.is_file()
        r.add(
            f"file.{f}",
            "ok" if ok else "warn",
            f"{p.stat().st_size}B" if ok else "پیدا نشد",
        )

    # ─── collectors ───
    col_dir = ROOT / "collectors"
    if col_dir.is_dir():
        found = {
            p.stem for p in col_dir.glob("*.py")
            if p.stem != "__init__"
        }
        missing = _EXPECTED_COLLECTOR_FILES - found
        r.add(
            "structure.collectors",
            "ok" if not missing else "warn",
            f"{len(found)} فایل"
            + (f" | missing={sorted(missing)}" if missing else ""),
            items=sorted(missing),
        )

    # 🎯 NEW: analyzers
    an_dir = ROOT / "analyzers"
    if an_dir.is_dir():
        found = {
            p.stem for p in an_dir.glob("*.py")
            if p.stem != "__init__"
        }
        missing = _EXPECTED_ANALYZER_FILES - found
        r.add(
            "structure.analyzers",
            "ok" if not missing else "warn",
            f"{len(found)} فایل"
            + (f" | missing={sorted(missing)}" if missing else ""),
            items=sorted(missing),
        )

    # ─── tests ───
    t_dir = ROOT / "tests"
    if t_dir.is_dir():
        found = {p.stem for p in t_dir.glob("*.py")}
        missing = _EXPECTED_TESTS - found
        r.add(
            "structure.tests",
            "ok" if not missing else "warn",
            f"{len(found)} فایل"
            + (f" | missing={sorted(missing)}" if missing else ""),
            items=sorted(missing),
        )


# ═════════════════════════════════════════════════════════
# ۲) ساختار کد
# ═════════════════════════════════════════════════════════
_COLLECTOR_METHODS: set[str] = {
    "connect", "fetch_messages", "disconnect",
}
_HANDLER_METHODS: set[str] = {"handle"}


def check_code_structure(r: HealthReport) -> None:
    r.section("۲) ساختار کد (کلاس‌ها و متدهای اجباری)")

    # ─── collectors ───
    col_dir = ROOT / "collectors"
    if col_dir.is_dir():
        for py in sorted(col_dir.glob("*.py")):
            if py.stem.startswith("_"):
                continue
            tree = _parse(py)
            if tree is None:
                r.add(
                    f"structure.collectors.{py.stem}",
                    "warn",
                    "پارس نشد (SyntaxError یا encoding)",
                )
                continue

            classes = list(_classes(tree))
            if not classes:
                r.add(
                    f"structure.collectors.{py.stem}",
                    "warn", "هیچ کلاسی ندارد",
                )
                continue

            main_cls = next(
                (c for c in classes if c.name.endswith("Collector")),
                classes[0],
            )
            methods = {
                n.name for n in main_cls.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            missing = _COLLECTOR_METHODS - methods
            r.add(
                f"structure.collectors.{py.stem}",
                "ok" if not missing else "warn",
                f"{main_cls.name}: {len(methods)} متد"
                + (f" | missing={sorted(missing)}" if missing else " ✅"),
                items=sorted(missing),
            )

    # ─── handlers (فقط واقعی‌ها) ───
    h_dir = ROOT / "handlers"
    if h_dir.is_dir():
        for py in sorted(h_dir.glob("*.py")):
            if py.stem.startswith("_"):
                continue
            if py.stem not in _REAL_HANDLERS:
                r.add(
                    f"structure.handlers.{py.stem}",
                    "info",
                    "helper (handler واقعی نیست)",
                )
                continue

            tree = _parse(py)
            if tree is None:
                r.add(
                    f"structure.handlers.{py.stem}",
                    "warn", "پارس نشد",
                )
                continue

            classes = list(_classes(tree))
            if not classes:
                r.add(
                    f"structure.handlers.{py.stem}",
                    "warn", "هیچ کلاسی ندارد",
                )
                continue

            main_cls = next(
                (c for c in classes if c.name.endswith("Handler")),
                classes[0],
            )
            methods = {
                n.name for n in main_cls.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            missing = _HANDLER_METHODS - methods
            r.add(
                f"structure.handlers.{py.stem}",
                "ok" if not missing else "warn",
                f"{main_cls.name}: {len(methods)} متد"
                + (f" | missing={sorted(missing)}" if missing else " ✅"),
                items=sorted(missing),
            )


# ═════════════════════════════════════════════════════════
# ۳) استاندارد
# ═════════════════════════════════════════════════════════
def check_standards(r: HealthReport) -> None:
    r.section("۳) استاندارد (type hints، docstrings، naming)")

    total = 0
    with_hints = 0
    with_doc = 0
    mods_with_doc = 0
    mods_total = 0
    bad_naming: list[str] = []

    for py in _py_files(skip_tests=True):
        tree = _parse(py)
        if tree is None:
            continue
        mods_total += 1
        rel = _rel(py)
        if ast.get_docstring(tree):
            mods_with_doc += 1

        for node in _funcs(tree):
            if node.name.startswith("_"):
                continue
            total += 1

            has_ret = node.returns is not None
            args = node.args.args + node.args.kwonlyargs
            has_args = all(
                a.annotation is not None for a in args
                if a.arg not in ("self", "cls")
            )
            if has_ret and has_args:
                with_hints += 1
            if ast.get_docstring(node):
                with_doc += 1

            # ─── naming (snake_case) ───
            if (
                any(c.isupper() for c in node.name)
                and "_" not in node.name
                and not node.name.isupper()
            ):
                bad_naming.append(f"{rel}:{node.name}")

    if total:
        pct_h = with_hints / total * 100
        pct_d = with_doc / total * 100
        r.add(
            "std.type_hints",
            "ok" if pct_h >= 60 else "warn",
            f"{with_hints}/{total} ({pct_h:.1f}%)",
        )
        r.add(
            "std.docstrings_funcs",
            "ok" if pct_d >= 40 else "warn",
            f"{with_doc}/{total} ({pct_d:.1f}%)",
        )

    if mods_total:
        pct_m = mods_with_doc / mods_total * 100
        r.add(
            "std.docstrings_modules",
            "ok" if pct_m >= 50 else "warn",
            f"{mods_with_doc}/{mods_total} ({pct_m:.1f}%)",
        )

    r.add(
        "std.naming_functions",
        "ok" if not bad_naming else "warn",
        f"{len(bad_naming)} تابع با نام غیر snake_case",
        items=bad_naming,
    )


# ═════════════════════════════════════════════════════════
# ۴) منطق
# ═════════════════════════════════════════════════════════
def check_logic(r: HealthReport) -> None:
    r.section("۴) منطق (except، return)")

    bare: list[str] = []
    broad_swallow: list[str] = []      # except Exception بدون log/raise
    broad_log: list[str] = []          # except Exception با log
    broad_reraise: list[str] = []      # except Exception با raise
    mixed_returns: list[str] = []

    for py in _py_files():
        tree = _parse(py)
        if tree is None:
            continue
        rel = _rel(py)

        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                if node.type is None:
                    bare.append(f"{rel}:{node.lineno}")
                    continue
                if (
                    isinstance(node.type, ast.Name)
                    and node.type.id == "Exception"
                ):
                    has_raise = any(
                        isinstance(x, ast.Raise)
                        for x in ast.walk(node)
                    )
                    has_log = any(
                        isinstance(x, ast.Call)
                        and isinstance(x.func, ast.Attribute)
                        and x.func.attr in (
                            "error", "warning", "exception",
                            "critical", "info", "debug",
                        )
                        for x in ast.walk(node)
                    )
                    label = f"{rel}:{node.lineno}"
                    if has_raise:
                        broad_reraise.append(label)
                    elif has_log:
                        broad_log.append(label)
                    else:
                        broad_swallow.append(label)

            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                wv, wo = _returns_in_own_body(node)
                if wv and wo:
                    mixed_returns.append(f"{rel}:{node.name}")

    r.add(
        "logic.bare_except",
        "ok" if not bare else "warn",
        f"{len(bare)} مورد except لخت",
        items=bare,
    )
    r.add(
        "logic.broad_except_swallow",
        "ok" if not broad_swallow else "warn",
        f"{len(broad_swallow)} مورد except Exception بدون log/raise",
        items=broad_swallow,
    )
    r.add(
        "logic.broad_except_log",
        "info",
        f"{len(broad_log)} مورد except Exception با log (خوب)",
    )
    r.add(
        "logic.broad_except_reraise",
        "info",
        f"{len(broad_reraise)} مورد except Exception با re-raise (خوب)",
    )
    r.add(
        "logic.mixed_returns",
        "ok" if not mixed_returns else "warn",
        f"{len(mixed_returns)} تابع return مخلوط",
        items=mixed_returns,
    )


# ═════════════════════════════════════════════════════════
# ۵) یکپارچگی
# ═════════════════════════════════════════════════════════
def check_integration(r: HealthReport) -> None:
    r.section("۵) یکپارچگی (config ↔ collectors ↔ .env)")

    try:
        from config import PLUGINS_CONFIG
    except Exception as e:
        r.add("integ.config", "fail", f"{type(e).__name__}: {e}")
        return

    r.add("integ.config", "ok", f"{len(PLUGINS_CONFIG)} پلاگین")

    for name, key in _EXPECTED_PLATFORMS.items():
        if key not in PLUGINS_CONFIG:
            r.add(
                f"integ.config.{name}",
                "warn",
                f"کلید {key} در PLUGINS_CONFIG نیست",
            )
            continue
        cfg = PLUGINS_CONFIG[key]
        if isinstance(cfg, dict):
            r.add(
                f"integ.config.{name}",
                "ok" if "enabled" in cfg else "warn",
                f"enabled={cfg.get('enabled')}",
            )
        else:
            r.add(
                f"integ.config.{name}",
                "info",
                f"نوع={type(cfg).__name__} (dict نیست)",
            )

    env_file = ROOT / ".env"
    if env_file.is_file():
        content = _src(env_file)
        env_keys = set(re.findall(r"^([A-Z_]+)=", content, re.M))
        missing_env = [
            f"{name.upper()}_ENABLED"
            for name in _EXPECTED_PLATFORMS
            if f"{name.upper()}_ENABLED" not in env_keys
        ]
        if missing_env:
            r.add(
                "integ.env",
                "info",
                f"در .env نیست: {missing_env}",
                items=missing_env,
            )
        else:
            r.add("integ.env", "ok", "همهٔ کلیدها در .env هستند")
    else:
        r.add("integ.env", "warn", ".env نیست")


# ═════════════════════════════════════════════════════════
# ۶) تمیزی
# ═════════════════════════════════════════════════════════
def check_cleanliness(r: HealthReport) -> None:
    r.section("۶) تمیزی (unused imports/vars/args، dead code)")

    unused_imports: list[str] = []
    unused_vars: list[str] = []
    unused_args: list[str] = []
    dead_code: list[str] = []

    # ─── جمع‌آوری همهٔ اسم‌های صدا زده‌شده در کل پروژه ───
    all_called: set[str] = set()
    for py in _py_files(skip_tests=False):
        tree = _parse(py)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                f = node.func
                if isinstance(f, ast.Name):
                    all_called.add(f.id)
                elif isinstance(f, ast.Attribute):
                    all_called.add(f.attr)

    for py in _py_files():
        tree = _parse(py)
        if tree is None:
            continue
        rel = _rel(py)
        used = _names_used_in_tree(tree)

        # ─── unused imports ───
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.asname or alias.name.split(".")[0]
                    if name in _IMPORT_SPECIAL:
                        continue
                    if name.startswith("_"):
                        continue
                    if name not in used:
                        unused_imports.append(
                            f"{rel}:{node.lineno} {name}"
                        )
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if mod == "__future__":
                    continue
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    name = alias.asname or alias.name
                    if name in _IMPORT_SPECIAL:
                        continue
                    if name.startswith("_"):
                        continue
                    if name not in used:
                        unused_imports.append(
                            f"{rel}:{node.lineno} {name}"
                        )

        # ─── unused module vars ───
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        name = t.id
                        if name.startswith("_") or name.isupper():
                            continue
                        if name not in used:
                            unused_vars.append(
                                f"{rel}:{node.lineno} {name}"
                            )

        # ─── unused function args ───
        for fn in _funcs(tree):
            if fn.name in _MAGIC_METHODS:
                continue
            args = [a.arg for a in fn.args.args
                    if a.arg not in ("self", "cls")]
            body_src = ast.unparse(fn) if hasattr(ast, "unparse") else ""
            for a in args:
                if a.startswith("_"):
                    continue
                pattern = rf"\b{re.escape(a)}\b"
                count = len(re.findall(pattern, body_src))
                if count <= 1:
                    unused_args.append(f"{rel}:{fn.name}({a})")

        # ─── dead code ───
        for fn in _funcs(tree):
            if fn.name.startswith("_"):
                continue
            if fn.name in _MAGIC_METHODS:
                continue
            if fn.name not in all_called:
                dead_code.append(f"{rel}:{fn.name}")

    r.add(
        "clean.unused_imports",
        "ok" if not unused_imports else "warn",
        f"{len(unused_imports)} مورد",
        items=unused_imports,
    )
    r.add(
        "clean.unused_module_vars",
        "ok" if not unused_vars else "warn",
        f"{len(unused_vars)} مورد",
        items=unused_vars,
    )
    r.add(
        "clean.unused_args",
        "ok" if not unused_args else "warn",
        f"{len(unused_args)} مورد",
        items=unused_args,
    )
    r.add(
        "clean.dead_code_candidates",
        "info" if len(dead_code) < 30 else "warn",
        f"{len(dead_code)} تابع public بدون call",
        items=dead_code,
    )


# ═════════════════════════════════════════════════════════
# ۷) به‌روز بودن
# ═════════════════════════════════════════════════════════
def check_freshness(r: HealthReport) -> None:
    r.section("۷) به‌روز بودن (requirements، کتابخانه‌ها)")

    req = ROOT / "requirements.txt"
    if req.is_file():
        lines = [
            ln.strip() for ln in _src(req).splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
        r.add("fresh.requirements", "ok", f"{len(lines)} پکیج")
        text = _src(req).lower()
        missing = sorted(
            p for p in _CRITICAL_REQ_PKGS
            if p.lower() not in text
            and p.replace("-", "_").lower() not in text
        )
        r.add(
            "fresh.critical_pkgs",
            "ok" if not missing else "warn",
            f"missing={missing}" if missing else "همه موجود",
            items=missing,
        )
    else:
        r.add("fresh.requirements", "fail", "requirements.txt نیست")

    missing_libs: list[str] = []
    for mod, pkg in _KEY_LIBS:
        try:
            m = __import__(mod)
            ver = getattr(m, "__version__", "?")
            r.add(f"fresh.lib.{pkg}", "ok", f"v{ver}")
        except ImportError:
            missing_libs.append(pkg)
            r.add(f"fresh.lib.{pkg}", "warn", "نصب نیست")

    r.add(
        "fresh.libs_summary",
        "ok" if not missing_libs else "warn",
        f"{len(_KEY_LIBS) - len(missing_libs)}/{len(_KEY_LIBS)} نصب"
        + (f" | missing={missing_libs}" if missing_libs else ""),
        items=missing_libs,
    )


# ═════════════════════════════════════════════════════════
# ۸) ایمپورت‌ها
# ═════════════════════════════════════════════════════════
def check_imports(r: HealthReport) -> None:
    r.section("۸) ایمپورت‌ها (ترتیب، سنگین، حلقه‌ای)")

    # ─── ترتیب ───
    bad_order: list[str] = []
    for py in _py_files():
        tree = _parse(py)
        if tree is None:
            continue
        rel = _rel(py)
        seen_local = False
        for node in tree.body[:50]:
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            if isinstance(node, ast.ImportFrom):
                top = (node.module or "").split(".")[0]
                if top == "__future__":
                    continue
                if top in _LOCAL_TOP:
                    seen_local = True
                elif top and top not in _STDLIB and seen_local:
                    bad_order.append(rel)
                    break
            else:
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top in _LOCAL_TOP:
                        seen_local = True
                    elif top not in _STDLIB and seen_local:
                        bad_order.append(rel)
                        break

    r.add(
        "imports.order",
        "ok" if not bad_order else "warn",
        f"{len(set(bad_order))} فایل با ترتیب نادرست",
        items=sorted(set(bad_order)),
    )

    # ─── سنگین ───
    heavy = ("playwright", "telethon", "rubpy", "torch",
         "numpy", "pandas")
    heavy_hits: list[str] = []
    for py in _py_files():
        src = _src(py)
        for h in heavy:
            if re.search(rf"^\s*import\s+{h}\b", src, re.M):
                heavy_hits.append(f"{_rel(py)}: {h}")

    r.add(
        "imports.heavy",
        "info" if len(heavy_hits) < 10 else "warn",
        f"{len(heavy_hits)} ایمپورت سنگین در بالای فایل",
        items=heavy_hits,
    )

    # ─── حلقه‌ای ───
    graph: dict[str, set[str]] = defaultdict(set)
    for py in _py_files(skip_tests=False):
        tree = _parse(py)
        if tree is None:
            continue
        rel = _rel(py).replace("\\", "/").replace("/", ".")
        mod_name = rel[:-3] if rel.endswith(".py") else rel
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                target = node.module or ""
                if target.startswith(("core.", "collectors.",
                                      "handlers.", "analyzers.")):
                    graph[mod_name].add(target)

    cycles: list[str] = []
    for a, targets in graph.items():
        for b in targets:
            if a in graph.get(b, set()):
                cycle = tuple(sorted([a, b]))
                key = f"{cycle[0]} ↔ {cycle[1]}"
                if key not in cycles:
                    cycles.append(key)

    r.add(
        "imports.circular",
        "ok" if not cycles else "warn",
        f"{len(cycles)} حلقه‌ای احتمالی",
        items=cycles,
    )


# ═════════════════════════════════════════════════════════
# ۹) پیچیدگی
# ═════════════════════════════════════════════════════════
def check_complexity(r: HealthReport) -> None:
    r.section("۹) پیچیدگی")

    long_f: list[str] = []
    deep_f: list[str] = []
    many_args_f: list[str] = []

    for py in _py_files():
        tree = _parse(py)
        if tree is None:
            continue
        rel = _rel(py)
        for node in _funcs(tree):
            length = (node.end_lineno or 0) - node.lineno
            if length > 80:
                long_f.append(f"{rel}:{node.name} ({length} خط)")
            depth = _max_depth(node)
            if depth > 5:
                deep_f.append(
                    f"{rel}:{node.name} (depth={depth})"
                )
            n_args = len(node.args.args) + len(node.args.kwonlyargs)
            if n_args > 8:
                many_args_f.append(
                    f"{rel}:{node.name} ({n_args} پارامتر)"
                )

    r.add(
        "complex.long_funcs",
        "ok" if len(long_f) < 5 else "warn",
        f"{len(long_f)} تابع >۸۰ خط",
        items=long_f,
    )
    r.add(
        "complex.deep_nested",
        "ok" if len(deep_f) < 3 else "warn",
        f"{len(deep_f)} تابع با عمق >۵",
        items=deep_f,
    )
    r.add(
        "complex.many_args",
        "ok" if len(many_args_f) < 5 else "warn",
        f"{len(many_args_f)} تابع با >۸ پارامتر",
        items=many_args_f,
    )


# ═════════════════════════════════════════════════════════
# ۱۰) امنیت
# ═════════════════════════════════════════════════════════
_SECRET_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"password\s*=\s*['\"][^'\"]+['\"]", re.I),
     "password="),
    (re.compile(r"api_hash\s*=\s*['\"][^'\"]{8,}['\"]", re.I),
     "api_hash="),
    (re.compile(r"token\s*=\s*['\"][A-Za-z0-9_\-]{20,}['\"]"),
     "token="),
    (re.compile(r"secret\s*=\s*['\"][^'\"]{8,}['\"]", re.I),
     "secret="),
    (re.compile(r"sk-[A-Za-z0-9]{20,}"), "openai-key"),
]

_DANGER_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\beval\s*\("), "eval"),
    (re.compile(r"\bexec\s*\("), "exec"),
    # os.system فقط اگر input کاربر داشته باشد خطرناک است
    (re.compile(
        r"\bos\.system\s*\(\s*(?!['\"](?:chcp|color|pause|cls|clear)"
        r"[^'\"]*['\"])"
    ), "os.system"),
    (re.compile(r"pickle\.loads?\s*\("), "pickle.load"),
    (re.compile(r"subprocess\.\w+\([^)]*shell\s*=\s*True"),
     "shell=True"),
]

_PATH_PATTERNS: list[re.Pattern] = [
    re.compile(r'["\']C:\\\\'),
    re.compile(r'["\']D:\\\\'),
    re.compile(r'["\']F:\\\\'),
    re.compile(r'["\']/home/'),
    re.compile(r'["\']/Users/'),
]


def check_security(r: HealthReport) -> None:
    r.section("۱۰) امنیت")

    hardcoded: list[str] = []
    dangerous: list[str] = []
    secrets: list[str] = []

    for py in _py_files():
        rel = _rel(py)
        for i, line in enumerate(_src(py).splitlines(), 1):
            if any(p.search(line) for p in _PATH_PATTERNS):
                hardcoded.append(f"{rel}:{i}")
            for pat, label in _DANGER_PATTERNS:
                if pat.search(line):
                    dangerous.append(f"{rel}:{i} → {label}")
                    break
            for pat, label in _SECRET_PATTERNS:
                if pat.search(line):
                    secrets.append(f"{rel}:{i} → {label}")
                    break

    r.add(
        "sec.hardcoded_paths",
        "ok" if not hardcoded else "warn",
        f"{len(hardcoded)} مورد",
        items=hardcoded,
    )
    r.add(
        "sec.dangerous_calls",
        "ok" if not dangerous else "warn",
        f"{len(dangerous)} مورد",
        items=dangerous,
    )
    r.add(
        "sec.hardcoded_secrets",
        "ok" if not secrets else "fail",
        f"{len(secrets)} مورد",
        items=secrets,
    )

    gi = ROOT / ".gitignore"
    if gi.is_file():
        ok = ".env" in _src(gi)
        r.add(
            "sec.gitignore_env",
            "ok" if ok else "fail",
            ".env در .gitignore" if ok
            else ".env در .gitignore نیست!",
        )
    else:
        r.add("sec.gitignore", "warn", ".gitignore نیست")


# ═════════════════════════════════════════════════════════
# ۱۱) اسناد
# ═════════════════════════════════════════════════════════
def check_docs(r: HealthReport) -> None:
    r.section("۱۱) اسناد")

    readme_found = False
    for fname in ("README.md", "README.txt", "README.rst", "README"):
        p = ROOT / fname
        if p.is_file():
            r.add("docs.readme", "ok",
                  f"{fname} ({p.stat().st_size}B)")
            readme_found = True
            break
    if not readme_found:
        r.add("docs.readme", "warn", "README پیدا نشد")

    for rel in (
        "config.py", "main.py", "panel.py", "login.py",
        "handlers/storage_handler.py",
        "analyzers/keyword_finder.py",
        "core/database/sqlite.py",
        "core/kernel/event_bus.py",
    ):
        p = ROOT / rel
        if not p.is_file():
            continue
        tree = _parse(p)
        if tree is None:
            continue
        doc = ast.get_docstring(tree)
        r.add(
            f"docs.module.{p.name}",
            "ok" if doc else "warn",
            f"{len(doc)} کاراکتر" if doc else "بدون docstring",
        )


# ═════════════════════════════════════════════════════════
# ۱۲) آمار کلی
# ═════════════════════════════════════════════════════════
def check_stats(r: HealthReport) -> None:
    r.section("۱۲) آمار کلی")

    files = _py_files(skip_tests=False)
    total_lines = 0
    total_funcs = 0
    total_classes = 0
    failed = 0

    for py in files:
        tree = _parse(py)
        if tree is None:
            failed += 1
            continue
        total_lines += len(_src(py).splitlines())
        total_funcs += sum(1 for _ in _funcs(tree))
        total_classes += sum(1 for _ in _classes(tree))

    r.add("stats.files", "info", f"{len(files)} فایل .py")
    r.add("stats.lines", "info", f"{total_lines} خط کد")
    r.add("stats.classes", "info", f"{total_classes} کلاس")
    r.add("stats.funcs", "info", f"{total_funcs} تابع")
    if failed:
        r.add(
            "stats.parse_failures",
            "warn",
            f"{failed} فایل پارس نشد",
        )


# ═════════════════════════════════════════════════════════
# main
# ═════════════════════════════════════════════════════════
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Health Check فوق‌دقیق پروژه",
    )
    parser.add_argument(
        "--json-only", action="store_true",
        help="فقط JSON، بدون چاپ console",
    )
    parser.add_argument(
        "--no-color", action="store_true",
        help="بدون رنگ ANSI",
    )
    args = parser.parse_args()

    if args.no_color or args.json_only:
        _disable_color()

    if not args.json_only:
        print("=" * 70)
        print(f"{C.BOLD}🔬 Health Check فوق‌دقیق{C.END}")
        print("=" * 70)
        print(f"  ROOT: {ROOT}")
        print(
            f"  زمان: "
            f"{datetime.now().isoformat(timespec='seconds')}"
        )

    r = HealthReport(quiet=args.json_only)

    check_project_structure(r)
    check_code_structure(r)
    check_standards(r)
    check_logic(r)
    check_integration(r)
    check_cleanliness(r)
    check_freshness(r)
    check_imports(r)
    check_complexity(r)
    check_security(r)
    check_docs(r)
    check_stats(r)

    ok = r.print_summary()
    r.save()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())