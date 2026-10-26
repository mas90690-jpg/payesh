# tests/test_all_inventory.py
"""
🔬 تست جامع — هر فایل، هر کلاس، هر تابع.

هدف:
    - اسکن کل پروژه (core, analyzers, collectors, handlers, data + فایل‌های ریشه)
    - استخراج AST هر فایل (کلاس‌ها، توابع، importها)
    - import هر ماژول و گزارش دقیق خطا
    - تشخیص circular imports
    - گزارش نهایی

اجرا:
    python tests/test_all_inventory.py
    python tests/test_all_inventory.py --quiet
    python tests/test_all_inventory.py --strict
    python tests/test_all_inventory.py --json-out inventory.json

اجرا با pytest:
    pytest tests/test_all_inventory.py -v
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import importlib
import json
import logging
import os
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

# ══════════════════════════════════════════════════════════
# مسیرها
# ══════════════════════════════════════════════════════════
TESTS_DIR = Path(__file__).parent.resolve()
ROOT = TESTS_DIR.parent.resolve()

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ══════════════════════════════════════════════════════════
# تنظیمات
# ══════════════════════════════════════════════════════════
SCAN_DIRS = [
    "core",
    "analyzers",
    "collectors",
    "handlers",
    "data",
]

SCAN_ROOT_FILES = [
    "config.py",
    "main.py",
    "login.py",
    "panel.py",
]

# پوشه‌هایی که هرگز اسکن نمی‌شوند
SKIP_DIR_NAMES = frozenset({
    "__pycache__",
    ".git",
    ".hg",
    ".svn",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "env",
    "myenv",
    "node_modules",
    "site-packages",
    "dist",
    "build",
    "backups",
    "backup",
    ".idea",
    ".vscode",
})

# فایل‌هایی که هرگز اسکن نمی‌شوند
SKIP_FILE_NAMES = frozenset({
    "__init__.py",
    "conftest.py",
})

# فایل‌های تست skip می‌شوند (self-skip، نه تست‌های دیگر)
SKIP_FILE_PREFIXES = ("test_",)
SKIP_FILE_SUFFIXES = ("_test.py",)


# ══════════════════════════════════════════════════════════
# آمار
# ══════════════════════════════════════════════════════════
@dataclass
class InventoryStats:
    total_files: int = 0
    total_ok: int = 0
    total_fail: int = 0
    total_classes: int = 0
    total_functions: int = 0
    total_methods: int = 0
    total_imports: int = 0
    elapsed_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_files": self.total_files,
            "total_ok": self.total_ok,
            "total_fail": self.total_fail,
            "total_classes": self.total_classes,
            "total_functions": self.total_functions,
            "total_methods": self.total_methods,
            "total_imports": self.total_imports,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
        }


# ══════════════════════════════════════════════════════════
# helpers — suppress logging
# ══════════════════════════════════════════════════════════
@contextlib.contextmanager
def _suppress_logging():
    """خفه کردن لاگ‌ها و printها."""
    old_level = logging.root.manager.disable
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    logging.disable(logging.CRITICAL)
    try:
        with open(os.devnull, "w", encoding="utf-8") as devnull:
            sys.stdout = devnull
            sys.stderr = devnull
            yield
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr
        logging.disable(old_level)


# ══════════════════════════════════════════════════════════
# helpers — skip
# ══════════════════════════════════════════════════════════
def _should_skip(path: Path) -> bool:
    """آیا این فایل باید skip شود؟"""
    for part in path.parts:
        if part in SKIP_DIR_NAMES:
            return True

    name = path.name
    if name in SKIP_FILE_NAMES:
        return True
    if name.startswith(SKIP_FILE_PREFIXES):
        return True
    if name.endswith(SKIP_FILE_SUFFIXES):
        return True

    return False


# ══════════════════════════════════════════════════════════
# helpers — module name
# ══════════════════════════════════════════════════════════
def _file_to_module(py_file: Path) -> str:
    """مسیر فایل → نام ماژول پایتون."""
    try:
        rel = py_file.relative_to(ROOT)
    except ValueError:
        return py_file.stem

    parts = list(rel.parts)
    if parts and parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


# ══════════════════════════════════════════════════════════
# helpers — AST extraction
# ══════════════════════════════════════════════════════════
def _extract_ast(py_file: Path) -> dict[str, Any]:
    """استخراج کلاس‌ها، توابع و importها از یک فایل."""
    result: dict[str, Any] = {
        "classes": [],
        "functions": [],
        "imports": [],
        "syntax_error": None,
        "size_bytes": 0,
        "lines": 0,
    }

    try:
        source = py_file.read_text(encoding="utf-8", errors="replace")
        result["size_bytes"] = py_file.stat().st_size
        result["lines"] = len(source.splitlines())
        tree = ast.parse(source, filename=str(py_file))
    except SyntaxError as e:
        result["syntax_error"] = {
            "msg": e.msg or str(e),
            "lineno": e.lineno,
            "text": e.text,
        }
        return result
    except Exception as e:
        result["syntax_error"] = {
            "msg": f"{type(e).__name__}: {e}",
            "lineno": None,
            "text": None,
        }
        return result

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            methods = [
                n.name for n in node.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            result["classes"].append({
                "name": node.name,
                "lineno": node.lineno,
                "methods": methods,
            })
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            result["functions"].append({
                "name": node.name,
                "lineno": node.lineno,
                "is_async": isinstance(node, ast.AsyncFunctionDef),
            })
        elif isinstance(node, ast.Import):
            for alias in node.names:
                result["imports"].append({
                    "module": alias.name,
                    "lineno": node.lineno,
                    "type": "import",
                })
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                result["imports"].append({
                    "module": node.module,
                    "lineno": node.lineno,
                    "type": "from",
                })

    return result


# ══════════════════════════════════════════════════════════
# helpers — import test
# ══════════════════════════════════════════════════════════
def _test_import(module_name: str) -> tuple[bool, str]:
    """تست import یک ماژول."""
    for key in list(sys.modules.keys()):
        if key == module_name or key.startswith(module_name + "."):
            del sys.modules[key]

    try:
        importlib.import_module(module_name)
        return True, ""
    except ImportError as e:
        tb = traceback.format_exc()
        return False, f"ImportError: {e}\n{tb}"
    except SyntaxError as e:
        return False, (
            f"SyntaxError: {e.msg}\n"
            f"  فایل: {e.filename}\n"
            f"  خط: {e.lineno}\n"
            f"  متن: {e.text}"
        )
    except Exception as e:
        tb = traceback.format_exc()
        lines = tb.splitlines()
        return False, f"{type(e).__name__}: {e}\n" + "\n".join(lines[-15:])


# ══════════════════════════════════════════════════════════
# helpers — circular import detection
# ══════════════════════════════════════════════════════════
def _detect_circular_imports(
    file_ast_map: dict[str, dict[str, Any]],
) -> list[tuple[str, str]]:
    """تشخیص import حلقه‌ای بین ماژول‌های پروژه."""
    graph: dict[str, set[str]] = {}

    for mod, info in file_ast_map.items():
        deps: set[str] = set()
        for imp in info.get("imports", []):
            target = imp["module"]
            if target in file_ast_map:
                deps.add(target)
            parts = target.split(".")
            for i in range(1, len(parts)):
                parent = ".".join(parts[:i])
                if parent in file_ast_map:
                    deps.add(parent)
        graph[mod] = deps

    cycles: list[tuple[str, str]] = []
    visited: set[str] = set()
    path: list[str] = []

    def dfs(node: str) -> None:
        if node in path:
            idx = path.index(node)
            cycle = path[idx:] + [node]
            for i in range(len(cycle) - 1):
                pair = (cycle[i], cycle[i + 1])
                if pair not in cycles:
                    cycles.append(pair)
            return
        if node in visited:
            return
        visited.add(node)
        path.append(node)
        for dep in graph.get(node, set()):
            dfs(dep)
        path.pop()

    for mod in graph:
        dfs(mod)

    return cycles


# ══════════════════════════════════════════════════════════
# جمع‌آوری فایل‌ها
# ══════════════════════════════════════════════════════════
def _collect_files(verbose: bool = True) -> list[Path]:
    """جمع‌آوری همهٔ فایل‌های پایتون قابل اسکن."""
    files_to_scan: list[Path] = []
    seen: set[Path] = set()

    for d in SCAN_DIRS:
        d_path = ROOT / d
        if not d_path.is_dir():
            if verbose:
                print(f"  ⚠️ پوشه نیست: {d}")
            continue
        for py_file in sorted(d_path.rglob("*.py")):
            if py_file in seen:
                continue
            if _should_skip(py_file):
                continue
            seen.add(py_file)
            files_to_scan.append(py_file)

    for f in SCAN_ROOT_FILES:
        f_path = ROOT / f
        if not f_path.is_file():
            if verbose:
                print(f"  ⚠️ فایل نیست: {f}")
            continue
        if f_path in seen:
            continue
        if _should_skip(f_path):
            continue
        seen.add(f_path)
        files_to_scan.append(f_path)

    return files_to_scan


# ══════════════════════════════════════════════════════════
# اسکن اصلی
# ══════════════════════════════════════════════════════════
def scan_all(verbose: bool = True, quiet: bool = False) -> dict[str, Any]:
    """اسکن کامل پروژه — wrapper برای quiet."""
    if quiet:
        with _suppress_logging():
            return _scan_all_inner(verbose=False, quiet=True)
    return _scan_all_inner(verbose=verbose, quiet=quiet)


def _scan_all_inner(verbose: bool = True, quiet: bool = False) -> dict[str, Any]:
    """اسکن داخلی."""
    t_start = time.monotonic()

    result: dict[str, Any] = {
        "files": [],
        "stats": InventoryStats(),
        "errors": [],
        "syntax_errors": [],
        "circular_imports": [],
        "meta": {
            "tool": "test_all_inventory",
            "version": "2.0",
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "root": str(ROOT),
        },
    }

    stats: InventoryStats = result["stats"]

    files_to_scan = _collect_files(verbose=not quiet)

    if not quiet:
        print()
        print("=" * 78)
        print(f"  🔬 تست جامع — {len(files_to_scan)} فایل کشف شد")
        print("=" * 78)

    file_ast_map: dict[str, dict[str, Any]] = {}

    for i, py_file in enumerate(files_to_scan, 1):
        rel = py_file.relative_to(ROOT).as_posix()
        stats.total_files += 1

        if not quiet:
            print(f"\n[{i}/{len(files_to_scan)}] 📄 {rel}")

        ast_info = _extract_ast(py_file)

        if ast_info["syntax_error"]:
            err = ast_info["syntax_error"]
            if not quiet:
                print(f"   ❌ SyntaxError در خط {err['lineno']}: {err['msg']}")
            result["syntax_errors"].append({
                "file": rel,
                "msg": err["msg"],
                "lineno": err["lineno"],
            })
            stats.total_fail += 1
            continue

        module_name = _file_to_module(py_file)
        ok, err = _test_import(module_name)

        classes = ast_info["classes"]
        functions = ast_info["functions"]
        imports = ast_info["imports"]

        n_classes = len(classes)
        n_functions = len(functions)
        n_methods = sum(len(c["methods"]) for c in classes)
        n_imports = len(imports)

        stats.total_classes += n_classes
        stats.total_functions += n_functions
        stats.total_methods += n_methods
        stats.total_imports += n_imports

        if ok:
            stats.total_ok += 1
            if not quiet:
                print(
                    f"   ✅ import موفق | "
                    f"{n_classes} کلاس | "
                    f"{n_functions} تابع | "
                    f"{n_imports} import"
                )
        else:
            stats.total_fail += 1
            if not quiet:
                print("   ❌ import ناموفق")
                first_line = err.splitlines()[0] if err else ""
                print(f"      {first_line}")
            result["errors"].append({
                "file": rel,
                "module": module_name,
                "error": err,
            })

        if classes and not quiet:
            print(f"   🏛️ کلاس‌ها ({n_classes}):")
            for cls in classes:
                methods_str = ", ".join(cls["methods"][:5])
                if len(cls["methods"]) > 5:
                    methods_str += f" ... (+{len(cls['methods']) - 5})"
                print(
                    f"      • {cls['name']} (خط {cls['lineno']}) "
                    f"— {len(cls['methods'])} متد: {methods_str}"
                )

        if functions and not quiet:
            print(f"   🔧 توابع ({n_functions}):")
            for fn in functions[:12]:
                marker = "async " if fn.get("is_async") else ""
                print(f"      • {marker}{fn['name']} (خط {fn['lineno']})")
            if n_functions > 12:
                print(f"      ... و {n_functions - 12} تابع دیگر")

        file_ast_map[module_name] = ast_info

        result["files"].append({
            "file": rel,
            "module": module_name,
            "import_ok": ok,
            "size_bytes": ast_info["size_bytes"],
            "lines": ast_info["lines"],
            "classes": [c["name"] for c in classes],
            "functions": [f["name"] for f in functions],
            "imports_count": n_imports,
            "error": err if not ok else None,
        })

    if file_ast_map:
        circular = _detect_circular_imports(file_ast_map)
        result["circular_imports"] = [
            {"from": a, "to": b} for a, b in circular
        ]

    stats.elapsed_seconds = time.monotonic() - t_start

    if not quiet:
        print("\n" + "=" * 78)
        print("  📊 خلاصهٔ نهایی")
        print("=" * 78)
        print(f"\n  📄 کل فایل‌ها:        {stats.total_files}")
        print(f"  ✅ import موفق:       {stats.total_ok}")
        print(f"  ❌ import ناموفق:     {stats.total_fail}")
        print(f"  🏛️  کل کلاس‌ها:       {stats.total_classes}")
        print(f"  ⚙️  کل متدها:         {stats.total_methods}")
        print(f"  🔧 کل توابع:          {stats.total_functions}")
        print(f"  📦 کل importها:       {stats.total_imports}")
        print(f"  ⏱️  زمان:             {stats.elapsed_seconds:.2f}s")

        if result["syntax_errors"]:
            print(f"\n  ❌ خطاهای syntax ({len(result['syntax_errors'])}):")
            for e in result["syntax_errors"]:
                print(f"     • {e['file']}:{e['lineno']} — {e['msg']}")

        if result["errors"]:
            print(f"\n  ❌ خطاهای import ({len(result['errors'])}):")
            for e in result["errors"]:
                print(f"\n     📄 {e['file']}")
                print(f"        ماژول: {e['module']}")
                for line in e["error"].splitlines()[:10]:
                    print(f"        {line}")

        if result["circular_imports"]:
            print(
                f"\n  🔄 circular imports "
                f"({len(result['circular_imports'])}):"
            )
            for c in result["circular_imports"][:10]:
                print(f"     • {c['from']} → {c['to']}")

        print("\n" + "=" * 78)
        if stats.total_fail == 0:
            print("  ✅✅✅ همه فایل‌ها سالم — هیچ خطا و هیچ syntax error")
        else:
            print(f"  ❌ {stats.total_fail} فایل مشکل دارد")
        print("=" * 78 + "\n")

    return result


# ══════════════════════════════════════════════════════════
# pytest interface
# ══════════════════════════════════════════════════════════
def test_all_files_importable() -> None:
    """pytest: همهٔ فایل‌های پروژه باید import شوند."""
    result = scan_all(verbose=False, quiet=True)

    errors = result["errors"]
    syntax_errors = result["syntax_errors"]

    assert not syntax_errors, (
        f"{len(syntax_errors)} فایل syntax error دارد:\n"
        + "\n".join(
            f"  • {e['file']}:{e['lineno']} — {e['msg']}"
            for e in syntax_errors
        )
    )

    assert not errors, (
        f"{len(errors)} فایل import نمی‌شود:\n"
        + "\n".join(
            f"  • {e['file']} → {e['error'].splitlines()[0]}"
            for e in errors[:10]
        )
    )


def test_no_circular_imports() -> None:
    """pytest: هیچ import حلقه‌ای در core/ نباشد."""
    result = scan_all(verbose=False, quiet=True)
    circular = result["circular_imports"]

    core_cycles = [
        c for c in circular
        if c["from"].startswith("core") and c["to"].startswith("core")
    ]

    assert not core_cycles, (
        f"{len(core_cycles)} circular import در core/:\n"
        + "\n".join(f"  • {c['from']} → {c['to']}" for c in core_cycles[:10])
    )


# ══════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════
def main() -> int:
    parser = argparse.ArgumentParser(
        description="🔬 تست جامع — هر فایل، هر کلاس، هر تابع",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
مثال‌ها:
    python tests/test_all_inventory.py
    python tests/test_all_inventory.py --quiet
    python tests/test_all_inventory.py --strict
    python tests/test_all_inventory.py --json-out inventory.json
        """,
    )
    parser.add_argument(
        "--json-out", type=str, default="",
        help="مسیر فایل JSON خروجی",
    )
    parser.add_argument(
        "--quiet", "-q", action="store_true",
        help="خروجی مختصر (فقط خطاها)",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="هر خطا → exit code 1 (برای CI)",
    )
    args = parser.parse_args()

    result = scan_all(verbose=not args.quiet, quiet=args.quiet)
    stats: InventoryStats = result["stats"]

    if args.json_out:
        out = Path(args.json_out)
        if not out.is_absolute():
            out = TESTS_DIR / out

        output = dict(result)
        output["stats"] = stats.to_dict()
        output["meta"]["finished_at"] = datetime.now().isoformat(
            timespec="seconds"
        )

        out.write_text(
            json.dumps(output, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"  📄 JSON: {out}\n")

    has_error = stats.total_fail > 0 or len(result["syntax_errors"]) > 0

    if args.strict:
        return 1 if has_error else 0
    return 0 if stats.total_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())