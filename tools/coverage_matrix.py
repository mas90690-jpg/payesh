"""
🎯 Coverage Matrix v6 — تحلیل صفر تا صد (بدون پلتفرم).

ویژگی‌ها:
  ✅ پوشش کد (line + branch coverage)
  ✅ فایل‌هایی که پوشش ندارن (zero coverage)
  ✅ فایل‌هایی که باید پوشش داشته باشن (priority)
  ✅ تست‌های تکراری (اسم + محتوا)
  ✅ تست‌های قلابی (بدون assert)
  ✅ فایل‌های پراکنده (پیشنهاد ادغام)
  ✅ استاندارد نبودن (نام، طول، docstring)
  ✅ آمار کامل AST
  ✅ خروجی JSON + Markdown آماده برای AI
  ✅ skip پلتفرم‌ها
  ✅ اجرای موازی

اجرا:
    python tests/test.py
    python tests/test.py --jobs 4
    python tests/test.py --top 20
    python tests/test.py --no-coverage
    python tests/test.py --json-out tests/analysis.json
    python tests/test.py --markdown-out tests/analysis.md
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

# ═══════════════════════════════════════════════════════════
# مسیرها
# ═══════════════════════════════════════════════════════════
ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = ROOT / "tests"
UNIT_DIR = TESTS_DIR / "unit"

# ═══════════════════════════════════════════════════════════
# فایل‌هایی که تست نیستن
# ═══════════════════════════════════════════════════════════
SKIP_TEST_FILES = {
    "test.py",
    "_coverage_matrix.py",
    "test_analyzer.py",
    "test_suite_analyzer.py",
    "conftest.py",
    "__init__.py",
}

# ═══════════════════════════════════════════════════════════
# پلتفرم‌ها — همیشه skip می‌شن
# ═══════════════════════════════════════════════════════════
PLATFORM_TEST_FILES = {
    "test_bale_full.py",
    "test_eitaa_full.py",
    "test_rubika_full.py",
    "test_telegram_full.py",
    "test_whatsapp_full.py",
    "test_linkedin_full.py",
    "test_instagram_full.py",
    "test_youtube_full.py",
    "test_facebook_full.py",
    "test_web_full.py",
}

# ═══════════════════════════════════════════════════════════
# الگوهای استاندارد
# ═══════════════════════════════════════════════════════════
TEST_NAME_PATTERN = re.compile(r"^test_[a-z0-9_]+$")
MAX_TEST_LINES = 100
MAX_FILE_LINES = 500

# ═══════════════════════════════════════════════════════════
# ماژول‌های حیاتی (باید پوشش داشته باشن)
# ═══════════════════════════════════════════════════════════
CRITICAL_MODULES = {
    "core.database.sqlite",
    "core.kernel.event_bus",
    "core.kernel.models",
    "core.contracts.base_collector",
    "core.contracts.base_handler",
    "core.contracts.base_analyzer",
    "handlers.storage_handler",
    "handlers.admin_handler",
    "analyzers.keyword_finder",
    "analyzers.lead_scorer",
    "analyzers.priority_finder",
    "analyzers.target_filter",
    "core.services.ai_assistant",
    "core.safe_brain",
    "core.brain.main",
    "core.brain.brain_memory",
    "core.adapters.access_manager",
    "core.adapters.command_parser",
    "core.adapters.translator",
    "core.adapters.messenger_registry",
    "core.adapters.browser_lock",
    "core.adapters.formula_manager",
    "core.adapters.memory_store",
    "core.adapters.web_media",
}


# ═══════════════════════════════════════════════════════════
# پیدا کردن فایل‌های تست
# ═══════════════════════════════════════════════════════════
def find_test_files() -> tuple[list[Path], list[Path]]:
    candidates: list[Path] = []
    for p in TESTS_DIR.glob("test_*.py"):
        candidates.append(p)
    if UNIT_DIR.exists():
        for p in UNIT_DIR.glob("test_*.py"):
            candidates.append(p)

    test_files: list[Path] = []
    skipped: list[Path] = []

    for p in sorted(set(candidates)):
        if p.name in SKIP_TEST_FILES:
            continue
        if p.name in PLATFORM_TEST_FILES:
            skipped.append(p)
        else:
            test_files.append(p)

    return test_files, skipped


# ═══════════════════════════════════════════════════════════
# پیدا کردن ماژول‌های کد
# ═══════════════════════════════════════════════════════════
def find_code_modules() -> dict[str, dict]:
    """همهٔ ماژول‌های .py + اطلاعات (خط، کلاس، تابع)."""
    modules: dict[str, dict] = {}

    for top in ("core", "analyzers", "collectors", "handlers", "data"):
        top_path = ROOT / top
        if not top_path.exists():
            continue
        for py in top_path.rglob("*.py"):
            if "__pycache__" in py.parts:
                continue
            rel = py.relative_to(ROOT)
            mod = str(rel.with_suffix("")).replace("\\", "/").replace("/", ".")
            if mod.endswith(".__init__"):
                mod = mod[:-9]

            try:
                src = py.read_text(encoding="utf-8", errors="replace")
                lines = len(src.splitlines())
                tree = ast.parse(src)
                classes = sum(
                    1 for n in ast.walk(tree) if isinstance(n, ast.ClassDef)
                )
                functions = sum(
                    1 for n in ast.walk(tree)
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                )
            except Exception:
                lines = classes = functions = 0

            modules[mod] = {
                "path": str(rel),
                "lines": lines,
                "classes": classes,
                "functions": functions,
            }

    for f in ("config.py",):
        if (ROOT / f).exists():
            try:
                src = (ROOT / f).read_text(encoding="utf-8", errors="replace")
                lines = len(src.splitlines())
                tree = ast.parse(src)
                classes = sum(
                    1 for n in ast.walk(tree) if isinstance(n, ast.ClassDef)
                )
                functions = sum(
                    1 for n in ast.walk(tree)
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                )
            except Exception:
                lines = classes = functions = 0
            modules["config"] = {
                "path": f,
                "lines": lines,
                "classes": classes,
                "functions": functions,
            }

    return modules


# ═══════════════════════════════════════════════════════════
# استخراج تست‌ها از AST
# ═══════════════════════════════════════════════════════════
def extract_tests(py_file: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "file": py_file.name,
        "path": str(py_file),
        "lines": 0,
        "tests": [],
        "fixtures": [],
        "warnings": [],
        "errors": [],
    }

    try:
        src = py_file.read_text(encoding="utf-8", errors="replace")
        result["lines"] = len(src.splitlines())
        tree = ast.parse(src, filename=str(py_file))
    except SyntaxError as e:
        result["errors"].append(f"SyntaxError: {e.msg} (line {e.lineno})")
        return result
    except Exception as e:
        result["errors"].append(f"{type(e).__name__}: {e}")
        return result

    if result["lines"] > MAX_FILE_LINES:
        result["warnings"].append(f"فایل بزرگ: {result['lines']} خط")
    if not ast.get_docstring(tree):
        result["warnings"].append("بدون docstring سطح فایل")

    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        # ─── fixture ───
        is_fixture = False
        for dec in node.decorator_list:
            if "pytest.fixture" in ast.unparse(dec):
                is_fixture = True
                result["fixtures"].append({
                    "name": node.name,
                    "line": node.lineno,
                    "is_async": isinstance(node, ast.AsyncFunctionDef),
                })
                break

        if is_fixture or not node.name.startswith("test_"):
            continue

        body_src = ast.get_source_segment(src, node) or ""
        body_hash = hashlib.sha256(
            re.sub(r"\s+", " ", body_src).strip().encode()
        ).hexdigest()[:16]

        asserts = sum(1 for n in ast.walk(node) if isinstance(n, ast.Assert))
        lines = (node.end_lineno or node.lineno) - node.lineno + 1
        args = [a.arg for a in node.args.args]
        decorators = [ast.unparse(d) for d in node.decorator_list]

        test_info = {
            "name": node.name,
            "line": node.lineno,
            "lines": lines,
            "is_async": isinstance(node, ast.AsyncFunctionDef),
            "args": args,
            "asserts": asserts,
            "body_hash": body_hash,
            "has_assert": asserts > 0,
            "decorators": decorators,
            "warnings": [],
        }

        if not TEST_NAME_PATTERN.match(node.name):
            test_info["warnings"].append("نام غیراستاندارد")
        if lines > MAX_TEST_LINES:
            test_info["warnings"].append(f"تست طولانی: {lines} خط")
        if asserts == 0:
            test_info["warnings"].append("بدون assert")
        if "pytest.mark.network" in " ".join(decorators):
            test_info["is_network"] = True

        result["tests"].append(test_info)

    return result


# ═══════════════════════════════════════════════════════════
# تحلیل تکراری‌ها
# ═══════════════════════════════════════════════════════════
def find_duplicates(all_tests: dict[str, dict]) -> dict[str, Any]:
    name_map: dict[str, list[str]] = defaultdict(list)
    hash_map: dict[str, list[tuple[str, str]]] = defaultdict(list)

    for fname, info in all_tests.items():
        for t in info.get("tests", []):
            name_map[t["name"]].append(fname)
            hash_map[t["body_hash"]].append((fname, t["name"]))

    return {
        "duplicate_names": {
            n: fs for n, fs in name_map.items() if len(fs) > 1
        },
        "duplicate_content": {
            h: items for h, items in hash_map.items() if len(items) > 1
        },
    }


# ═══════════════════════════════════════════════════════════
# تست‌های قلابی
# ═══════════════════════════════════════════════════════════
def find_fake_tests(all_tests: dict[str, dict]) -> list[dict]:
    fakes = []
    for fname, info in all_tests.items():
        for t in info.get("tests", []):
            if not t["has_assert"]:
                fakes.append({
                    "file": fname,
                    "test": t["name"],
                    "line": t["line"],
                })
    return fakes


# ═══════════════════════════════════════════════════════════
# پراکندگی
# ═══════════════════════════════════════════════════════════
def find_scattered(all_tests: dict[str, dict]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = defaultdict(list)
    for fname in all_tests:
        m = re.match(
            r"test_(\w+?)(?:_additions|_extra|_more)?\.py$", fname
        )
        if m:
            groups[m.group(1)].append(fname)
    return {
        g: fs for g, fs in groups.items() if len(fs) > 1
    }


# ═══════════════════════════════════════════════════════════
# غیراستاندارد
# ═══════════════════════════════════════════════════════════
def find_nonstandard(all_tests: dict[str, dict]) -> list[dict]:
    issues = []
    for fname, info in all_tests.items():
        for w in info.get("warnings", []):
            issues.append({
                "file": fname,
                "type": "file_warning",
                "message": w,
            })
        for t in info.get("tests", []):
            for w in t.get("warnings", []):
                issues.append({
                    "file": fname,
                    "test": t["name"],
                    "line": t["line"],
                    "type": "test_warning",
                    "message": w,
                })
    return issues


# ═══════════════════════════════════════════════════════════
# اجرای coverage
# ═══════════════════════════════════════════════════════════
def run_single_test(
    test_path: Path,
    coverage_targets: list[str],
    timeout: int = 60,
    no_coverage: bool = False,
) -> dict:
    if not test_path.exists():
        return {"status": "missing", "test": str(test_path)}

    cov_json = test_path.parent / f"_cov_{test_path.stem}.json"

    cmd = [
        sys.executable, "-m", "pytest",
        str(test_path),
        "--no-header", "-q",
        f"--timeout={timeout}",
        "-m", "not slow and not network",
    ]

    if not no_coverage:
        for target in coverage_targets:
            cmd.extend(["--cov", target])
        cmd.append("--cov-branch")  # ← branch coverage
        cmd.append(f"--cov-report=json:{cov_json}")

    try:
        r = subprocess.run(
            cmd, cwd=str(ROOT),
            capture_output=True, text=True,
            timeout=timeout * 3,
            encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "test": test_path.name}

    result = {
        "status": "ok" if r.returncode == 0 else "fail",
        "test": test_path.name,
        "passed": 0,
        "failed": 0,
        "deselected": 0,
        "files": {},
        "stdout_tail": r.stdout[-1000:] if r.returncode != 0 else "",
    }

    m = re.search(r"(\d+) passed", r.stdout)
    if m:
        result["passed"] = int(m.group(1))
    m = re.search(r"(\d+) failed", r.stdout)
    if m:
        result["failed"] = int(m.group(1))
    m = re.search(r"(\d+) deselected", r.stdout)
    if m:
        result["deselected"] = int(m.group(1))

    if not no_coverage and cov_json.exists():
        try:
            data = json.loads(cov_json.read_text(encoding="utf-8"))
            for fpath, finfo in data.get("files", {}).items():
                s = finfo.get("summary", {})
                result["files"][fpath.replace("\\", "/")] = {
                    "covered": s.get("covered_lines", 0),
                    "statements": s.get("num_statements", 0),
                    "percent": round(s.get("percent_covered", 0), 1),
                    "missing": finfo.get("missing_lines", []),
                    "branches_covered": s.get("covered_branches", 0),
                    "branches_total": s.get("num_branches", 0),
                }
        except Exception as e:
            result["parse_error"] = str(e)
        finally:
            cov_json.unlink(missing_ok=True)

    return result


# ═══════════════════════════════════════════════════════════
# نقشهٔ تست → ماژول
# ═══════════════════════════════════════════════════════════
MANUAL_MAP = {
    "test_ai_assistant.py": ["core.services.ai_assistant"],
    "test_safe_brain.py": ["core.safe_brain"],
    "test_brain_memory.py": ["core.brain.brain_memory"],
    "test_tools_audit.py": ["core.brain.tools_audit"],
    "test_keyword_finder.py": ["analyzers.keyword_finder"],
    "test_lead_scorer.py": ["analyzers.lead_scorer"],
    "test_priority_finder.py": ["analyzers.priority_finder"],
    "test_target_filter.py": ["analyzers.target_filter"],
    "test_storage.py": ["handlers.storage_handler"],
    "test_db.py": ["core.database.sqlite"],
    "test_collectors_smoke.py": ["collectors"],
    "test_smoke.py": ["core.kernel", "core.contracts"],
    "test_adapters.py": ["core.adapters"],
    "test_contracts.py": ["core.contracts"],
    "test_kernel.py": ["core.kernel"],
    "test_full_project.py": ["config", "handlers", "collectors"],
    "test_score_full.py": ["core", "analyzers", "handlers", "collectors"],
    "test_all_inventory.py": ["core", "analyzers", "collectors", "handlers", "data"],
    "test_healthcheck_full.py": ["core", "analyzers", "collectors", "handlers"],
}


def guess_targets(test_name: str) -> list[str]:
    if test_name in MANUAL_MAP:
        return MANUAL_MAP[test_name]
    stem = test_name.replace("test_", "").replace(".py", "")
    for prefix in ("core", "analyzers", "collectors", "handlers", "data"):
        candidate = ROOT / prefix / f"{stem}.py"
        if candidate.exists():
            return [f"{prefix}.{stem}"]
    return ["core", "analyzers", "handlers", "collectors"]


# ═══════════════════════════════════════════════════════════
# اجرای موازی
# ═══════════════════════════════════════════════════════════
def run_all_tests_parallel(
    test_files: list[Path],
    jobs: int = 4,
    timeout: int = 60,
    no_coverage: bool = False,
) -> dict[str, dict]:
    results: dict[str, dict] = {}

    with ThreadPoolExecutor(max_workers=jobs) as ex:
        futures = {}
        for tf in test_files:
            targets = guess_targets(tf.name)
            future = ex.submit(
                run_single_test, tf, targets, timeout, no_coverage
            )
            futures[future] = tf

        for future in as_completed(futures):
            tf = futures[future]
            try:
                r = future.result()
                results[tf.name] = r
                if r["status"] == "ok":
                    tot_cov = sum(f["covered"] for f in r["files"].values())
                    tot_stmt = sum(f["statements"] for f in r["files"].values())
                    pct = (tot_cov / tot_stmt * 100) if tot_stmt else 0
                    print(
                        f"  ✅ {tf.name:<45s} "
                        f"{r['passed']:>3d}P/{r['failed']:>2d}F  "
                        f"cov={pct:>5.1f}%"
                    )
                else:
                    print(f"  ⚠️  {tf.name:<45s} {r['status']}")
            except Exception as e:
                print(f"  ❌ {tf.name:<45s} {type(e).__name__}: {e}")
                results[tf.name] = {
                    "status": "error", "error": str(e), "test": tf.name,
                }

    return results


# ═══════════════════════════════════════════════════════════
# گزارش نهایی
# ═══════════════════════════════════════════════════════════
def build_full_report(
    results: dict,
    all_modules: dict[str, dict],
    all_tests: dict[str, dict],
    skipped_platforms: list[Path],
) -> dict:
    total_passed = sum(r.get("passed", 0) for r in results.values())
    total_failed = sum(r.get("failed", 0) for r in results.values())
    failed_tests = [
        name for name, r in results.items() if r["status"] == "fail"
    ]

    total_test_funcs = sum(
        len(info.get("tests", [])) for info in all_tests.values()
    )
    total_fixtures = sum(
        len(info.get("fixtures", [])) for info in all_tests.values()
    )
    async_tests = sum(
        1 for info in all_tests.values()
        for t in info.get("tests", [])
        if t["is_async"]
    )

    # ─── پوشش ماژول‌ها ───
    module_coverage: dict[str, dict] = {}
    for test_name, r in results.items():
        for fpath, finfo in r.get("files", {}).items():
            mod = fpath.replace("/", ".").replace(".py", "")
            if mod not in module_coverage:
                module_coverage[mod] = {
                    "covered": 0,
                    "statements": 0,
                    "percent": 0,
                    "tests": [],
                    "missing": set(),
                    "branches_covered": 0,
                    "branches_total": 0,
                }
            module_coverage[mod]["covered"] = max(
                module_coverage[mod]["covered"], finfo["covered"]
            )
            module_coverage[mod]["statements"] = max(
                module_coverage[mod]["statements"], finfo["statements"]
            )
            module_coverage[mod]["branches_covered"] = max(
                module_coverage[mod]["branches_covered"],
                finfo.get("branches_covered", 0)
            )
            module_coverage[mod]["branches_total"] = max(
                module_coverage[mod]["branches_total"],
                finfo.get("branches_total", 0)
            )
            if test_name not in module_coverage[mod]["tests"]:
                module_coverage[mod]["tests"].append(test_name)
            module_coverage[mod]["missing"].update(
                finfo.get("missing", [])
            )

    for mod, info in module_coverage.items():
        if info["statements"]:
            info["percent"] = round(
                info["covered"] / info["statements"] * 100, 1
            )
        info["missing"] = sorted(info["missing"])

    # ─── ماژول‌های صفر٪ ───
    zero_modules = []
    for mod in all_modules:
        if mod not in module_coverage or module_coverage[mod]["percent"] == 0:
            zero_modules.append({
                "module": mod,
                "path": all_modules[mod]["path"],
                "lines": all_modules[mod]["lines"],
                "classes": all_modules[mod]["classes"],
                "functions": all_modules[mod]["functions"],
                "is_critical": mod in CRITICAL_MODULES,
            })

    # ─── ماژول‌هایی که باید پوشش داشته باشن (priority) ───
    needs_coverage = []
    for mod, info in module_coverage.items():
        if mod in CRITICAL_MODULES and info["percent"] < 50:
            needs_coverage.append({
                "module": mod,
                "current_percent": info["percent"],
                "tests": info["tests"],
                "priority": "high" if info["percent"] < 20 else "medium",
            })

    # ─── ماژول‌های حیاتی که کلاً تست ندارن ───
    critical_zero = [
        z for z in zero_modules if z["is_critical"]
    ]

    # ─── تحلیل‌های اضافی ───
    duplicates = find_duplicates(all_tests)
    fakes = find_fake_tests(all_tests)
    scattered = find_scattered(all_tests)
    nonstandard = find_nonstandard(all_tests)

    return {
        "stats": {
            "total_files": len(all_tests),
            "total_test_funcs": total_test_funcs,
            "total_fixtures": total_fixtures,
            "async_tests": async_tests,
            "total_passed": total_passed,
            "total_failed": total_failed,
            "skipped_platforms": len(skipped_platforms),
            "total_modules": len(all_modules),
            "modules_with_coverage": len(module_coverage),
            "modules_zero_coverage": len(zero_modules),
            "critical_modules_zero": len(critical_zero),
        },
        "failed_tests": failed_tests,
        "modules": module_coverage,
        "zero_modules": zero_modules,
        "critical_zero_modules": critical_zero,
        "needs_coverage": needs_coverage,
        "duplicates": duplicates,
        "fakes": fakes,
        "scattered": scattered,
        "nonstandard": nonstandard,
        "tests": results,
        "all_tests": all_tests,
        "all_modules": all_modules,
    }


def print_full_report(report: dict, top_n: int = 0):
    stats = report["stats"]

    print()
    print("=" * 78)
    print("  📊 آمار کلی")
    print("=" * 78)
    print(f"  • فایل‌های تست: {stats['total_files']}")
    print(f"  • کل تست‌ها: {stats['total_test_funcs']}")
    print(f"  • async تست‌ها: {stats['async_tests']}")
    print(f"  • fixtureها: {stats['total_fixtures']}")
    print(f"  • پلتفرم‌های skip: {stats['skipped_platforms']}")
    print(f"  • ✅ پاس: {stats['total_passed']}")
    print(f"  • ❌ fail: {stats['total_failed']}")
    print(f"  • ماژول‌های کد: {stats['total_modules']}")
    print(f"  • ماژول‌های با پوشش: {stats['modules_with_coverage']}")
    print(f"  • 🔴 ماژول‌های صفر٪: {stats['modules_zero_coverage']}")
    print(f"  • 🚨 ماژول‌های حیاتی صفر٪: {stats['critical_modules_zero']}")

    # ─── fail ───
    if report["failed_tests"]:
        print()
        print("=" * 78)
        print("  ❌ تست‌های شکست‌خورده")
        print("=" * 78)
        for t in report["failed_tests"]:
            print(f"     • {t}")

    # ─── ماژول‌های حیاتی صفر ───
    if report["critical_zero_modules"]:
        print()
        print("=" * 78)
        print("  🚨 ماژول‌های حیاتی بدون تست (اولویت بالا)")
        print("=" * 78)
        for z in report["critical_zero_modules"]:
            print(f"  🚨 {z['module']}")
            print(
                f"       مسیر: {z['path']} | "
                f"{z['lines']} خط | "
                f"{z['classes']} کلاس | "
                f"{z['functions']} تابع"
            )

    # ─── بقیه صفرها ───
    non_critical_zero = [
        z for z in report["zero_modules"]
        if not z["is_critical"]
    ]
    if non_critical_zero:
        print()
        print("=" * 78)
        print("  🔴 ماژول‌های بدون تست (اولویت پایین)")
        print("=" * 78)
        for z in non_critical_zero[:30]:
            print(
                f"  🔴 {z['module']} "
                f"({z['lines']} خط، {z['functions']} تابع)"
            )
        if len(non_critical_zero) > 30:
            print(f"  ... و {len(non_critical_zero) - 30} ماژول دیگر")

    # ─── needs coverage ───
    if report["needs_coverage"]:
        print()
        print("=" * 78)
        print("  🟡 ماژول‌های حیاتی با پوشش کم (< 50%)")
        print("=" * 78)
        for n in report["needs_coverage"]:
            print(
                f"  🟡 {n['module']} "
                f"({n['current_percent']}% - {n['priority']})"
            )

    # ─── تکراری اسم ───
    print()
    print("=" * 78)
    print("  🔴 تست‌های با اسم تکراری")
    print("=" * 78)
    dup_names = report["duplicates"]["duplicate_names"]
    if dup_names:
        for name, files in dup_names.items():
            print(f"  ⚠️  {name}")
            for f in files:
                print(f"       • {f}")
    else:
        print("  ✅ هیچ تست با اسم تکراری نیست")

    # ─── تکراری محتوا ───
    print()
    print("=" * 78)
    print("  🔴 تست‌های با محتوای تکراری")
    print("=" * 78)
    dup_content = report["duplicates"]["duplicate_content"]
    if dup_content:
        for h, items in list(dup_content.items())[:10]:
            print(f"  ⚠️  hash={h}")
            for fname, tname in items:
                print(f"       • {fname}::{tname}")
    else:
        print("  ✅ هیچ تست با محتوای تکراری نیست")

    # ─── قلابی ───
    print()
    print("=" * 78)
    print("  🔴 تست‌های بدون assert (قلابی)")
    print("=" * 78)
    if report["fakes"]:
        for f in report["fakes"][:20]:
            print(f"  ⚠️  {f['file']}::{f['test']} (line {f['line']})")
        if len(report["fakes"]) > 20:
            print(f"  ... و {len(report['fakes']) - 20} مورد دیگر")
    else:
        print("  ✅ همه تست‌ها assert دارن")

    # ─── پراکندگی ───
    print()
    print("=" * 78)
    print("  🟡 فایل‌های پراکنده (پیشنهاد ادغام)")
    print("=" * 78)
    if report["scattered"]:
        for group, files in report["scattered"].items():
            print(f"  • گروه «{group}»:")
            for f in files:
                print(f"       • {f}")
    else:
        print("  ✅ هیچ فایل پراکنده‌ای نیست")

    # ─── غیراستاندارد ───
    print()
    print("=" * 78)
    print("  ⚠️  موارد غیراستاندارد")
    print("=" * 78)
    if report["nonstandard"]:
        for item in report["nonstandard"][:30]:
            loc = item["file"]
            if "test" in item:
                loc += f"::{item['test']} (line {item['line']})"
            print(f"  ⚠️  {loc}: {item['message']}")
        if len(report["nonstandard"]) > 30:
            print(
                f"  ... و {len(report['nonstandard']) - 30} مورد دیگر"
            )
    else:
        print("  ✅ همه تست‌ها استاندارد هستن")

    # ─── پوشش ───
    print()
    print("=" * 78)
    print("  📈 پوشش ماژول‌ها (بدترین اول)")
    print("=" * 78)
    print(f"  {'Module':<50s} {'Cover':>6s} {'Stmt':>6s}")
    print(f"  {'-' * 50} {'-' * 6} {'-' * 6}")

    sorted_mods = sorted(
        report["modules"].items(),
        key=lambda kv: kv[1]["percent"],
    )
    if top_n:
        sorted_mods = sorted_mods[:top_n]

    for mod, info in sorted_mods:
        filled = int(info["percent"] / 10)
        bar = "█" * filled + "░" * (10 - filled)
        print(
            f"  {mod:<50s} "
            f"{info['percent']:>5.1f}% "
            f"{info['statements']:>6d}  {bar}"
        )


def save_reports(report: dict, out_json: Path, out_md: Path):
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    stats = report["stats"]
    lines = ["# Coverage Matrix v6 — تحلیل صفر تا صد\n"]

    lines.append("## آمار کلی\n")
    lines.append(f"- فایل‌های تست: **{stats['total_files']}**")
    lines.append(f"- کل تست‌ها: **{stats['total_test_funcs']}**")
    lines.append(f"- async تست‌ها: **{stats['async_tests']}**")
    lines.append(f"- fixtureها: **{stats['total_fixtures']}**")
    lines.append(f"- پلتفرم‌های skip: **{stats['skipped_platforms']}**")
    lines.append(f"- ✅ پاس: **{stats['total_passed']}**")
    lines.append(f"- ❌ fail: **{stats['total_failed']}**")
    lines.append(f"- ماژول‌های کد: **{stats['total_modules']}**")
    lines.append(f"- ماژول‌های با پوشش: **{stats['modules_with_coverage']}**")
    lines.append(f"- 🔴 ماژول‌های صفر٪: **{stats['modules_zero_coverage']}**")
    lines.append(f"- 🚨 ماژول‌های حیاتی صفر٪: **{stats['critical_modules_zero']}**")
    lines.append("")

    if report["failed_tests"]:
        lines.append("## ❌ تست‌های شکست‌خورده\n")
        for t in report["failed_tests"]:
            lines.append(f"- `{t}`")
        lines.append("")

    if report["critical_zero_modules"]:
        lines.append("## 🚨 ماژول‌های حیاتی بدون تست\n")
        lines.append("| Module | Path | Lines | Classes | Functions |")
        lines.append("|---|---|---:|---:|---:|")
        for z in report["critical_zero_modules"]:
            lines.append(
                f"| `{z['module']}` | `{z['path']}` | "
                f"{z['lines']} | {z['classes']} | {z['functions']} |"
            )
        lines.append("")

    non_critical_zero = [
        z for z in report["zero_modules"]
        if not z["is_critical"]
    ]
    if non_critical_zero:
        lines.append("## 🔴 ماژول‌های بدون تست (اولویت پایین)\n")
        lines.append("| Module | Path | Lines | Functions |")
        lines.append("|---|---|---:|---:|")
        for z in non_critical_zero:
            lines.append(
                f"| `{z['module']}` | `{z['path']}` | "
                f"{z['lines']} | {z['functions']} |"
            )
        lines.append("")

    if report["needs_coverage"]:
        lines.append("## 🟡 ماژول‌های حیاتی با پوشش کم\n")
        lines.append("| Module | Coverage | Priority |")
        lines.append("|---|---:|---|")
        for n in report["needs_coverage"]:
            lines.append(
                f"| `{n['module']}` | {n['current_percent']}% | "
                f"{n['priority']} |"
            )
        lines.append("")

    if report["duplicates"]["duplicate_names"]:
        lines.append("## 🔴 تست‌های با اسم تکراری\n")
        for name, files in report["duplicates"]["duplicate_names"].items():
            lines.append(f"### `{name}`")
            for f in files:
                lines.append(f"- {f}")
            lines.append("")

    if report["duplicates"]["duplicate_content"]:
        lines.append("## 🔴 تست‌های با محتوای تکراری\n")
        for h, items in list(report["duplicates"]["duplicate_content"].items())[:10]:
            lines.append(f"### hash `{h}`")
            for fname, tname in items:
                lines.append(f"- `{fname}::{tname}`")
            lines.append("")

    if report["fakes"]:
        lines.append("## 🔴 تست‌های بدون assert\n")
        for f in report["fakes"][:50]:
            lines.append(
                f"- `{f['file']}::{f['test']}` (line {f['line']})"
            )
        lines.append("")

    if report["scattered"]:
        lines.append("## 🟡 فایل‌های پراکنده\n")
        for group, files in report["scattered"].items():
            lines.append(f"### گروه `{group}`")
            for f in files:
                lines.append(f"- {f}")
            lines.append("")

    if report["nonstandard"]:
        lines.append("## ⚠️  موارد غیراستاندارد\n")
        for item in report["nonstandard"][:100]:
            loc = item["file"]
            if "test" in item:
                loc += f"::{item['test']} (line {item['line']})"
            lines.append(f"- {loc}: {item['message']}")
        lines.append("")

    lines.append("## 📈 پوشش ماژول‌ها\n")
    lines.append("| Module | Cover | Statements | Missing |")
    lines.append("|---|---:|---:|---|")
    for mod, info in sorted(
        report["modules"].items(), key=lambda kv: kv[1]["percent"]
    ):
        missing_str = (
            f"lines: {info['missing'][:10]}" if info["missing"] else "—"
        )
        lines.append(
            f"| `{mod}` | {info['percent']:.1f}% | "
            f"{info['statements']} | {missing_str} |"
        )

    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(lines), encoding="utf-8")


# ═══════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(
        description="Coverage Matrix v6 — تحلیل صفر تا صد (بدون پلتفرم)"
    )
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--top", type=int, default=0)
    parser.add_argument("--no-coverage", action="store_true")
    parser.add_argument("--json-out", type=str, default="")
    parser.add_argument("--markdown-out", type=str, default="")
    args = parser.parse_args()

    print("=" * 78)
    print("  🎯 Coverage Matrix v6 — تحلیل صفر تا صد")
    print("=" * 78)

    # ─── پیدا کردن ───
    test_files, skipped_platforms = find_test_files()
    all_modules = find_code_modules()

    print(f"  🧪 {len(test_files)} فایل تست")
    print(f"  ⏭️  {len(skipped_platforms)} پلتفرم skip شد")
    print(f"  📦 {len(all_modules)} ماژول کد")
    print()

    # ─── استخراج AST ───
    print("  🔬 استخراج AST...")
    all_tests: dict[str, dict] = {}
    for tf in test_files:
        info = extract_tests(tf)
        all_tests[tf.name] = info
        if info.get("errors"):
            for err in info["errors"]:
                print(f"  ❌ {tf.name}: {err}")
    print()

    # ─── اجرای موازی ───
    print(f"  ▶ اجرای موازی ({args.jobs} worker)...")
    print()
    results = run_all_tests_parallel(
        test_files,
        jobs=args.jobs,
        timeout=args.timeout,
        no_coverage=args.no_coverage,
    )

    # ─── گزارش ───
    report = build_full_report(
        results, all_modules, all_tests, skipped_platforms
    )
    print_full_report(report, top_n=args.top)

    # ─── ذخیره ───
    if args.json_out:
        out_json = Path(args.json_out)
        if not out_json.is_absolute():
            out_json = ROOT / out_json
    else:
        out_json = TESTS_DIR / "coverage_matrix.json"

    if args.markdown_out:
        out_md = Path(args.markdown_out)
        if not out_md.is_absolute():
            out_md = ROOT / out_md
    else:
        out_md = out_json.with_suffix(".md")

    save_reports(report, out_json, out_md)

    print()
    print(f"  📄 {out_json}")
    print(f"  📄 {out_md}")
    print()


if __name__ == "__main__":
    main()
