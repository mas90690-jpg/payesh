"""
ابزار بررسی کد برای Brain — pyflakes, flake8, vulture, bandit.

نسخهٔ ۱ (این نسخه):
  - 🎯 ابزار جدید: `audit_code` — بررسی کامل کد با چند ابزار
  - 🎯 ابزار جدید: `audit_file` — بررسی یک فایل خاص
  - 🎯 ابزار جدید: `fix_fstrings` — درست کردن f-string های بی‌placeholder
  - 🎯 سازگار با ساختار Brain (mixin + tools_meta)
  - 🎯 همه چیز env-based برای تنظیم‌پذیری
  - 🎯 خطاها به dict ساختاریافته برمی‌گرده (نه متن خام)

نحوهٔ استفاده:
    from core.brain.tools_audit import audit_code, AUDIT_TOOLS

    result = audit_code("core/ handlers/")
    # result = {
    #   "ok": True,
    #   "summary": {...},
    #   "details": {...},
    #   "raw_output": "..."
    # }
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from core.kernel.logger import (
    log_error,
    log_info,
    log_success,
    log_warning,
)


# ═══════════════════════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════════════════════
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


# ═══════════════════════════════════════════════════════════
# ثابت‌ها (env-based)
# ═══════════════════════════════════════════════════════════
_DEFAULT_PATHS = _env_int  # placeholder — جلوگیری از خطای import
_DEFAULT_PATHS_STR = (
    "core/ handlers/ collectors/ analyzers/ main.py"
)

_AUDIT_TIMEOUT_S = _env_float(
    "BRAIN_AUDIT_TIMEOUT_S", 60.0,
    min_val=5.0, max_val=600.0,
)
_FLAKE8_MAX_LINE = _env_int(
    "BRAIN_AUDIT_MAX_LINE", 120,
    min_val=60, max_val=500,
)
_MAX_OUTPUT_PREVIEW = _env_int(
    "BRAIN_AUDIT_MAX_PREVIEW", 50_000,
    min_val=200, max_val=500_000,
)

# ═══════════════════════════════════════════════════════════
# helper: اجرای ابزار
# ═══════════════════════════════════════════════════════════


def _run_tool(
    tool_name: str,
    args: list[str],
    cwd: str | None = None,
    timeout: float = _AUDIT_TIMEOUT_S,
) -> dict[str, Any]:
    """اجرای یک ابزار خارجی و برگرداندن نتیجه.

    Args:
        tool_name: نام ابزار (pyflakes, flake8, vulture, bandit).
        args: آرگومان‌های ابزار.
        cwd: مسیر اجرا.
        timeout: مهلت اجرا (ثانیه).

    Returns:
        dict با کلیدهای ok، count، output، error.
    """
    try:
        # اگه به‌صورت module نصب شده، با python -m اجرا کن
        cmd = [sys.executable, "-m", tool_name] + args

        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            encoding="utf-8",
            check=False,
            errors="replace",
        )

        output = (r.stdout or "") + (r.stderr or "")

        # ─── شمارش خطوط غیرخالی ───
        lines = [
            ln for ln in output.split("\n")
            if ln.strip() and not ln.strip().startswith("---")
        ]

        return {
            "ok": True,
            "count": len(lines),
            "output": output,  # ← کامل؛ برش در لایهٔ نمایش انجام می‌شود
            "output_preview": output[:_MAX_OUTPUT_PREVIEW],
            "exit_code": r.returncode,
        }
    except subprocess.TimeoutExpired:
        log_warning(
            f"⏰ [AUDIT:{tool_name}] timeout {timeout}s"
        )
        return {
            "ok": False,
            "error": f"timeout after {timeout}s",
            "count": 0,
        }
    except FileNotFoundError:
        log_warning(
            f"⚠️ [AUDIT:{tool_name}] نصب نیست"
        )
        return {
            "ok": False,
            "error": f"{tool_name} نصب نیست",
            "count": 0,
        }
    except Exception as e:
        log_error(
            f"❌ [AUDIT:{tool_name}] {type(e).__name__}: {e}"
        )
        return {
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "count": 0,
        }


# ═══════════════════════════════════════════════════════════
# helper: دسته‌بندی خطاها
# ═══════════════════════════════════════════════════════════
def _categorize_pyflakes(output: str) -> dict[str, list[str]]:
    """دسته‌بندی خطاهای pyflakes."""
    categories: dict[str, list[str]] = {
        "unused_imports": [],
        "unused_variables": [],
        "undefined_names": [],
        "f_string_no_placeholder": [],
        "redefinition": [],
        "other": [],
    }

    for line in output.split("\n"):
        line = line.strip()
        if not line:
            continue

        if "imported but unused" in line:
            categories["unused_imports"].append(line)
        elif "assigned to but never used" in line:
            categories["unused_variables"].append(line)
        elif "undefined name" in line:
            categories["undefined_names"].append(line)
        elif "f-string is missing placeholders" in line:
            categories["f_string_no_placeholder"].append(line)
        elif "redefinition of unused" in line:
            categories["redefinition"].append(line)
        elif line:  # خطای دیگه
            categories["other"].append(line)

    return categories


def _categorize_flake8(output: str) -> dict[str, list[str]]:
    """دسته‌بندی خطاهای flake8."""
    categories: dict[str, list[str]] = {
        "style": [],      # E، W
        "logic": [],      # F (pyflakes)
        "complexity": [],  # C
        "other": [],
    }

    for line in output.split("\n"):
        line = line.strip()
        if not line:
            continue

        # فرمت: file:line:col: CODE message
        match = re.search(r":\s*([EWFC]\d{1,4})\b", line)
        if match:
            code = match.group(1)
            if code.startswith("F"):
                categories["logic"].append(line)
            elif code.startswith(("E", "W")):
                categories["style"].append(line)
            elif code.startswith("C"):
                categories["complexity"].append(line)
            else:
                categories["other"].append(line)
        else:
            categories["other"].append(line)

    return categories


# ═══════════════════════════════════════════════════════════
# ابزار اصلی: audit_code
# ═══════════════════════════════════════════════════════════
def audit_code(
    paths: str = "",
    include_flake8: bool = True,
    include_vulture: bool = False,
    include_bandit: bool = False,
) -> dict[str, Any]:
    """بررسی کامل کد پایتون با ابزارهای متخصص.

    Args:
        paths: مسیرها با فاصله (پیش‌فرض: core/ handlers/ ...).
        include_flake8: بررسی با flake8 (پیش‌فرض True).
        include_vulture: بررسی کد بلااستفاده (پیش‌فرض False).
        include_bandit: بررسی امنیت (پیش‌فرض False).

    Returns:
        dict با ساختار:
        {
            "ok": bool,
            "paths": str,
            "summary": {
                "total_issues": int,
                "by_tool": {...},
            },
            "details": {
                "pyflakes": {
                    "count": int,
                    "categories": {...},
                    "output": str,
                },
                "flake8": {...},
                ...
            },
            "raw_output": str,
        }
    """
    if not paths:
        paths = _DEFAULT_PATHS_STR

    # ═══════════════════════════════════════════
    # پیدا کردن ریشه پروژه
    # ═══════════════════════════════════════════
    try:
        from config import BASE_DIR
        cwd = str(BASE_DIR)
    except Exception:
        cwd = str(Path.cwd())

    # ═══════════════════════════════════════════
    # آماده‌سازی args
    # ═══════════════════════════════════════════
    path_args = paths.split()
    if not path_args:
        path_args = [_DEFAULT_PATHS_STR]

    log_info(
        f"🔍 [AUDIT] شروع | paths={path_args} | "
        f"flake8={include_flake8} | "
        f"vulture={include_vulture} | "
        f"bandit={include_bandit}"
    )

    results: dict[str, Any] = {
        "ok": True,
        "paths": paths,
        "cwd": cwd,
        "details": {},
        "summary": {
            "total_issues": 0,
            "by_tool": {},
            "tools_ok": 0,
            "tools_failed": 0,
        },
        "raw_output": "",
    }

    # ═══════════════════════════════════════════
    # 1. pyflakes — همیشه
    # ═══════════════════════════════════════════
    pyflakes_result = _run_tool("pyflakes", path_args, cwd)
    results["details"]["pyflakes"] = pyflakes_result

    if pyflakes_result.get("ok"):
        categories = _categorize_pyflakes(
            pyflakes_result.get("output", "")
        )
        results["details"]["pyflakes"]["categories"] = categories
        results["summary"]["by_tool"]["pyflakes"] = (
            pyflakes_result.get("count", 0)
        )
        results["summary"]["tools_ok"] += 1
    else:
        results["summary"]["tools_failed"] += 1

    # ═══════════════════════════════════════════
    # 2. flake8 — اختیاری
    # ═══════════════════════════════════════════
    if include_flake8:
        flake8_args = path_args + [
            f"--max-line-length={_FLAKE8_MAX_LINE}",
            # --select حذف شد → setup.cfg اعمال می‌شود
            "--format=%(path)s:%(row)d:%(col)d: %(code)s %(text)s",
        ]
        flake8_result = _run_tool("flake8", flake8_args, cwd)
        results["details"]["flake8"] = flake8_result

        if flake8_result.get("ok"):
            categories = _categorize_flake8(
                flake8_result.get("output", "")
            )
            results["details"]["flake8"]["categories"] = categories
            results["summary"]["by_tool"]["flake8"] = (
                flake8_result.get("count", 0)
            )
            results["summary"]["tools_ok"] += 1
        else:
            results["summary"]["tools_failed"] += 1

    # ═══════════════════════════════════════════
    # 3. vulture — اختیاری
    # ═══════════════════════════════════════════
    if include_vulture:
        vulture_args = path_args + ["--min-confidence=80"]
        vulture_result = _run_tool("vulture", vulture_args, cwd)
        results["details"]["vulture"] = vulture_result

        if vulture_result.get("ok"):
            results["summary"]["by_tool"]["vulture"] = (
                vulture_result.get("count", 0)
            )
            results["summary"]["tools_ok"] += 1
        else:
            results["summary"]["tools_failed"] += 1

    # ═══════════════════════════════════════════
    # 4. bandit — اختیاری
    # ═══════════════════════════════════════════
    if include_bandit:
        bandit_args = ["-r"] + path_args + ["-f", "txt", "-q"]
        bandit_result = _run_tool("bandit", bandit_args, cwd)
        results["details"]["bandit"] = bandit_result

        if bandit_result.get("ok"):
            results["summary"]["by_tool"]["bandit"] = (
                bandit_result.get("count", 0)
            )
            results["summary"]["tools_ok"] += 1
        else:
            results["summary"]["tools_failed"] += 1

    # ═══════════════════════════════════════════
    # خلاصه
    # ═══════════════════════════════════════════
    total = sum(results["summary"]["by_tool"].values())
    results["summary"]["total_issues"] = total

    # خروجی خام (برای نمایش)
    raw_lines = []
    for tool_name, tool_result in results["details"].items():
        if not tool_result.get("ok"):
            raw_lines.append(
                f"❌ {tool_name}: {tool_result.get('error', '?')}"
            )
            continue
        count = tool_result.get("count", 0)
        raw_lines.append(f"📌 {tool_name}: {count} خطا")
        output = tool_result.get("output", "")
        if output:
            for line in output.splitlines():
                if line.strip():
                    raw_lines.append(f"   {line}")

    results["raw_output"] = "\n".join(raw_lines)

    log_success(
        f"✅ [AUDIT] کامل | total={total} | "
        f"tools_ok={results['summary']['tools_ok']} | "
        f"tools_failed={results['summary']['tools_failed']}"
    )

    return results


# ═══════════════════════════════════════════════════════════
# ابزار: audit_file
# ═══════════════════════════════════════════════════════════
def audit_file(
    file_path: str,
    include_flake8: bool = True,
) -> dict[str, Any]:
    """بررسی یک فایل خاص پایتون.

    Args:
        file_path: مسیر فایل (مثلاً: core/brain/main.py).
        include_flake8: بررسی با flake8.

    Returns:
        dict مشابه audit_code.
    """
    if not file_path:
        return {
            "ok": False,
            "error": "مسیر فایل خالی است",
        }

    # چک وجود فایل
    try:
        p = Path(file_path)
        if not p.exists():
            return {
                "ok": False,
                "error": f"فایل وجود ندارد: {file_path}",
            }
        if not p.is_file():
            return {
                "ok": False,
                "error": f"مسیر یک فایل نیست: {file_path}",
            }
        if p.suffix != ".py":
            return {
                "ok": False,
                "error": f"فایل پایتون نیست: {file_path}",
            }
    except Exception as e:
        return {
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
        }

    return audit_code(
        paths=file_path,
        include_flake8=include_flake8,
        include_vulture=False,
        include_bandit=False,
    )


# ═══════════════════════════════════════════════════════════
# ابزار: fix_fstrings
# ═══════════════════════════════════════════════════════════
def fix_fstrings(
    paths: str = "",
    dry_run: bool = True,
) -> dict[str, Any]:
    """درست کردن f-string های بی‌placeholder.

    ⚠️ این ابزار فایل‌ها رو تغییر می‌ده! قبلش بکاپ بگیر.

    Args:
        paths: مسیرها با فاصله.
        dry_run: اگه True باشه، فقط نشون می‌ده (تغییر نمی‌ده).

    Returns:
        dict با لیست فایل‌های تغییر یافته.
    """
    if not paths:
        paths = _DEFAULT_PATHS_STR

    # ─── پیدا کردن ریشه پروژه ───
    try:
        from config import BASE_DIR
        cwd = Path(BASE_DIR)
    except Exception:
        cwd = Path.cwd()

    # ─── پیدا کردن فایل‌ها ───
    files_to_check: list[Path] = []
    for path_str in paths.split():
        p = cwd / path_str
        if p.is_file() and p.suffix == ".py":
            files_to_check.append(p)
        elif p.is_dir():
            files_to_check.extend(p.rglob("*.py"))

    # ─── regex برای f-string های بی‌placeholder ───
    # الگو: f"..." یا f'...' که داخلشون {} نداره
    # ⚠️ ساده و امن — فقط رشته‌های تک‌خطی
    pattern = re.compile(
        r'(?<![a-zA-Z0-9_])f(["\'])(?:(?!\1)[^{}\\]|\\.)*\1'
    )

    results: dict[str, Any] = {
        "ok": True,
        "dry_run": dry_run,
        "files_checked": len(files_to_check),
        "files_fixed": 0,
        "fixes": [],
        "errors": [],
    }

    for fpath in files_to_check:
        try:
            content = fpath.read_text(encoding="utf-8")
            original = content

            # جایگزینی: f"..." → "..."
            new_content = pattern.sub(
                lambda m: f"{m.group(1)}{m.group(2)}",  # فقط quote
                content,
            )

            if new_content == original:
                continue

            # شمارش تغییرات
            count = len(pattern.findall(original))

            if not dry_run:
                # بکاپ
                try:
                    from core.brain.constants import BACKUP_DIR
                    from datetime import datetime as _dt
                    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
                    ts = _dt.now().strftime("%Y%m%d_%H%M%S_%f")
                    backup = BACKUP_DIR / f"{fpath.stem}_{ts}.py"
                    backup.write_text(original, encoding="utf-8")
                except Exception:
                    pass

                # نوشتن
                fpath.write_text(new_content, encoding="utf-8")
                results["files_fixed"] += 1

            results["fixes"].append({
                "file": str(fpath.relative_to(cwd)),
                "count": count,
                "applied": not dry_run,
            })

        except Exception as e:
            results["errors"].append({
                "file": str(fpath),
                "error": f"{type(e).__name__}: {e}",
            })

    if results["fixes"]:
        log_success(
            f"✅ [AUDIT:fix_fstrings] "
            f"files={results['files_fixed']} | "
            f"dry_run={dry_run}"
        )
    else:
        log_info(
            "ℹ️ [AUDIT:fix_fstrings] چیزی برای درست کردن نبود"
        )

    return results


# ═══════════════════════════════════════════════════════════
# helper: گزارش خوانا
# ═══════════════════════════════════════════════════════════
def format_audit_report(result: dict[str, Any]) -> str:
    """تبدیل خروجی audit_code به متن خوانا.

    Args:
        result: خروجی audit_code.

    Returns:
        متن فارسی خوانا.
    """
    if not result.get("ok"):
        return f"❌ خطا: {result.get('error', '?')}"

    summary = result.get("summary", {})
    total = summary.get("total_issues", 0)

    lines = [
        "📋 **گزارش بررسی کد**",
        "━━━━━━━━━━━━━━━",
        f"🔹 مسیرها: {result.get('paths', '?')}",
        f"🔹 کل خطاها: {total}",
        f"🔹 ابزارهای موفق: {summary.get('tools_ok', 0)}",
        f"🔹 ابزارهای ناموفق: {summary.get('tools_failed', 0)}",
        "",
    ]

    by_tool = summary.get("by_tool", {})
    if by_tool:
        lines.append("📊 **تفکیک ابزارها:**")
        for tool_name, count in by_tool.items():
            lines.append(f"   • {tool_name}: {count}")
        lines.append("")

    # ─── جزئیات pyflakes ───
    pyflakes = result.get("details", {}).get("pyflakes", {})
    if pyflakes.get("ok"):
        categories = pyflakes.get("categories", {})
        if any(categories.values()):
            lines.append("🔍 **pyflakes — دسته‌بندی:**")
            cat_names = {
                "unused_imports": "import اضافه",
                "unused_variables": "متغیر استفاده‌نشده",
                "undefined_names": "اسم تعریف‌نشده",
                "f_string_no_placeholder": "f-string بدون placeholder",
                "redefinition": "تعریف دوباره",
                "other": "سایر",
            }
            for cat_key, cat_label in cat_names.items():
                items = categories.get(cat_key, [])
                if items:
                    lines.append(
                        f"   • {cat_label}: {len(items)}"
                    )
                    for item in items[:5]:
                        lines.append(f"      {item}")
                    if len(items) > 5:
                        lines.append(
                            f"      ... و {len(items) - 5} مورد دیگر"
                        )
            lines.append("")

    # ─── جزئیات flake8 ───
    flake8 = result.get("details", {}).get("flake8", {})
    if flake8.get("ok"):
        categories = flake8.get("categories", {})
        if any(categories.values()):
            lines.append("🔍 **flake8 — دسته‌بندی:**")
            flake_names = {
                "style": "style (E/W)",
                "logic": "منطقی (F)",
                "complexity": "پیچیدگی (C)",
                "other": "سایر",
            }
            for cat_key, cat_label in flake_names.items():
                items = categories.get(cat_key, [])
                if items:
                    lines.append(
                        f"   • {cat_label}: {len(items)}"
                    )
            lines.append("")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════
# تعریف ابزارها برای Brain
# ═══════════════════════════════════════════════════════════
AUDIT_TOOLS: list[dict] = [
    {
        "name": "audit_code",
        "description": (
            "بررسی کد پایتون با pyflakes + flake8 (+ vulture + bandit). "
            "پیدا کردن خطاهای import، غلط تایپی، f-string بی‌placeholder، "
            "متغیرهای استفاده‌نشده، اسم‌های تعریف‌نشده و مشکلات منطقی. "
            "خروجی: dict ساختاریافته با دسته‌بندی."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "paths": {
                    "type": "string",
                    "description": (
                        "مسیرها با فاصله "
                        f"(پیش‌فرض: {_DEFAULT_PATHS_STR})"
                    ),
                    "default": _DEFAULT_PATHS_STR,
                },
                "include_flake8": {
                    "type": "boolean",
                    "description": "بررسی با flake8 (پیش‌فرض True)",
                    "default": True,
                },
                "include_vulture": {
                    "type": "boolean",
                    "description": "بررسی کد بلااستفاده (کندتر)",
                    "default": False,
                },
                "include_bandit": {
                    "type": "boolean",
                    "description": "بررسی امنیت (کندتر)",
                    "default": False,
                },
            },
        },
        "function": audit_code,
    },
    {
        "name": "audit_file",
        "description": (
            "بررسی یک فایل پایتون خاص. "
            "خروجی: dict ساختاریافته."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "مسیر فایل (مثلاً: core/brain/main.py)",
                },
                "include_flake8": {
                    "type": "boolean",
                    "default": True,
                },
            },
            "required": ["file_path"],
        },
        "function": audit_file,
    },
    {
        "name": "fix_fstrings",
        "description": (
            "درست کردن f-string های بی‌placeholder "
            "(f\"...\" → \"...\"). "
            "⚠️ فایل‌ها رو تغییر می‌ده — قبلش بکاپ بگیر. "
            "با dry_run=True فقط نشون می‌ده."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "paths": {
                    "type": "string",
                    "default": _DEFAULT_PATHS_STR,
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "اگه True، فقط نشون بده (پیش‌فرض)",
                    "default": True,
                },
            },
        },
        "function": fix_fstrings,
    },
]


# ═══════════════════════════════════════════════════════════
# API عمومی
# ═══════════════════════════════════════════════════════════
def get_tool_count() -> int:
    """تعداد ابزارهای audit."""
    return len(AUDIT_TOOLS)


def get_tool_names() -> list[str]:
    """نام ابزارهای audit."""
    return [t["name"] for t in AUDIT_TOOLS]


def get_stats() -> dict[str, Any]:
    """خلاصهٔ وضعیت — برای debug."""
    return {
        "tools": get_tool_names(),
        "count": get_tool_count(),
        "default_paths": _DEFAULT_PATHS_STR,
        "timeout_s": _AUDIT_TIMEOUT_S,
        "flake8_max_line": _FLAKE8_MAX_LINE,
    }


# ═══════════════════════════════════════════════════════════
# __all__
# ═══════════════════════════════════════════════════════════
__all__ = [
    "audit_code",
    "audit_file",
    "fix_fstrings",
    "format_audit_report",
    "AUDIT_TOOLS",
    "get_tool_count",
    "get_tool_names",
    "get_stats",
]
