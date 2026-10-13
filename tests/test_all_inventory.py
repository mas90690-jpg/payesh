"""
🔬 تست جامع — هر فایل، هر کلاس، هر تابع
- کل پروژه را اسکن می‌کند
- هر فایل py را import می‌کند
- همه کلاس‌ها و توابع عمومی را لیست می‌کند
- خطای دقیق نشان می‌دهد
"""
from __future__ import annotations

import argparse
import ast
import importlib
import json
import sys
import traceback
from pathlib import Path

TESTS_DIR = Path(__file__).parent.resolve()
ROOT = TESTS_DIR.parent.resolve()
sys.path.insert(0, str(ROOT))


# ══════════════════════════════════════════════════════════
# مسیرهایی که اسکن می‌شوند
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

SKIP_PATTERNS = [
    "__pycache__",
    "__init__.py",
    "test_",
    "_test",
    "conftest",
]


def _should_skip(path: Path) -> bool:
    s = str(path)
    name = path.name
    for pat in SKIP_PATTERNS:
        if pat in s or pat in name:
            return True
    return False


def _file_to_module(py_file: Path) -> str:
    rel = py_file.relative_to(ROOT)
    parts = list(rel.parts)
    if parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _extract_ast(py_file: Path) -> dict:
    """استخراج کلاس‌ها، توابع و importها از یک فایل."""
    result = {
        "classes": [],
        "functions": [],
        "imports": [],
        "syntax_error": None,
    }

    try:
        source = py_file.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except SyntaxError as e:
        result["syntax_error"] = {
            "msg": str(e),
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
            })
        elif isinstance(node, ast.Import):
            for alias in node.names:
                result["imports"].append({
                    "module": alias.name,
                    "lineno": node.lineno,
                })
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                result["imports"].append({
                    "module": node.module,
                    "lineno": node.lineno,
                })

    return result


def _test_import(module_name: str) -> tuple[bool, str]:
    for key in list(sys.modules.keys()):
        if key == module_name or key.startswith(module_name + "."):
            del sys.modules[key]

    try:
        importlib.import_module(module_name)
        return True, ""
    except ImportError as e:
        return False, f"ImportError: {e}"
    except SyntaxError as e:
        return False, (
            f"SyntaxError: {e}\n"
            f"  خط: {e.lineno}\n"
            f"  متن: {e.text}"
        )
    except Exception as e:
        tb = traceback.format_exc()
        lines = tb.splitlines()
        return False, f"{type(e).__name__}: {e}\n" + "\n".join(lines[-12:])


def scan_all() -> dict:
    result = {
        "files": [],
        "total_files": 0,
        "total_ok": 0,
        "total_fail": 0,
        "total_classes": 0,
        "total_functions": 0,
        "total_methods": 0,
        "total_imports": 0,
        "errors": [],
        "syntax_errors": [],
    }

    # ─── جمع‌آوری فایل‌ها ───
    files_to_scan: list[Path] = []

    for d in SCAN_DIRS:
        d_path = ROOT / d
        if d_path.is_dir():
            for py_file in sorted(d_path.rglob("*.py")):
                if _should_skip(py_file):
                    continue
                files_to_scan.append(py_file)

    for f in SCAN_ROOT_FILES:
        f_path = ROOT / f
        if f_path.is_file():
            files_to_scan.append(f_path)

    print("\n" + "=" * 78)
    print(f"  🔬 تست جامع — {len(files_to_scan)} فایل کشف شد")
    print("=" * 78)

    # ─── اسکن هر فایل ───
    for i, py_file in enumerate(files_to_scan, 1):
        rel = py_file.relative_to(ROOT).as_posix()
        result["total_files"] += 1

        print(f"\n[{i}/{len(files_to_scan)}] 📄 {rel}")

        ast_info = _extract_ast(py_file)

        if ast_info["syntax_error"]:
            err = ast_info["syntax_error"]
            print(f"   ❌ SyntaxError در خط {err['lineno']}: {err['msg']}")
            result["syntax_errors"].append({
                "file": rel,
                "msg": err["msg"],
                "lineno": err["lineno"],
            })
            result["total_fail"] += 1
            continue

        module_name = _file_to_module(py_file)
        ok, err = _test_import(module_name)

        # ─── اطلاعات AST ───
        classes = ast_info["classes"]
        functions = ast_info["functions"]
        imports = ast_info["imports"]

        n_classes = len(classes)
        n_functions = len(functions)
        n_methods = sum(len(c["methods"]) for c in classes)
        n_imports = len(imports)

        result["total_classes"] += n_classes
        result["total_functions"] += n_functions
        result["total_methods"] += n_methods
        result["total_imports"] += n_imports

        if ok:
            result["total_ok"] += 1
            print(
                f"   ✅ import موفق | "
                f"{n_classes} کلاس | "
                f"{n_functions} تابع | "
                f"{n_imports} import"
            )
        else:
            result["total_fail"] += 1
            print(f"   ❌ import ناموفق")
            print(f"      {err.splitlines()[0] if err else ''}")
            result["errors"].append({
                "file": rel,
                "module": module_name,
                "error": err,
            })

        # ─── نمایش کلاس‌ها ───
        if classes:
            print(f"   🏛️ کلاس‌ها ({n_classes}):")
            for cls in classes:
                methods_str = ", ".join(cls["methods"][:5])
                if len(cls["methods"]) > 5:
                    methods_str += f" ... (+{len(cls['methods']) - 5})"
                print(
                    f"      • {cls['name']} (خط {cls['lineno']}) "
                    f"— {len(cls['methods'])} متد: {methods_str}"
                )

        # ─── نمایش توابع ───
        if functions:
            print(f"   🔧 توابع ({n_functions}):")
            for fn in functions[:12]:
                print(f"      • {fn['name']} (خط {fn['lineno']})")
            if n_functions > 12:
                print(f"      ... و {n_functions - 12} تابع دیگر")

        result["files"].append({
            "file": rel,
            "module": module_name,
            "import_ok": ok,
            "classes": [c["name"] for c in classes],
            "functions": [f["name"] for f in functions],
            "imports_count": n_imports,
            "error": err if not ok else None,
        })

    # ─── خلاصه ───
    print("\n" + "=" * 78)
    print("  📊 خلاصهٔ نهایی")
    print("=" * 78)
    print(f"\n  📄 کل فایل‌ها:        {result['total_files']}")
    print(f"  ✅ import موفق:       {result['total_ok']}")
    print(f"  ❌ import ناموفق:     {result['total_fail']}")
    print(f"  🏛️ کل کلاس‌ها:        {result['total_classes']}")
    print(f"  ⚙️ کل متدها:          {result['total_methods']}")
    print(f"  🔧 کل توابع:          {result['total_functions']}")
    print(f"  📦 کل importها:       {result['total_imports']}")

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

    print("\n" + "=" * 78)
    if result["total_fail"] == 0:
        print("  ✅✅✅ همه فایل‌ها سالم — هیچ خطا و هیچ syntax error")
    else:
        print(f"  ❌ {result['total_fail']} فایل مشکل دارد")
    print("=" * 78 + "\n")

    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json-out", type=str, default="")
    args = parser.parse_args()

    result = scan_all()

    if args.json_out:
        out = Path(args.json_out)
        if not out.is_absolute():
            out = TESTS_DIR / out
        out.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"  📄 JSON: {out}\n")

    sys.exit(0 if result["total_fail"] == 0 else 1)


if __name__ == "__main__":
    main()