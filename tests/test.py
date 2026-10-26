"""
🎯 Coverage Matrix v3 — سریع، دقیق، خودکار.

ویژگی‌ها:
  ✅ پیدا کردن خودکار همهٔ فایل‌های تست (نه لیست دستی)
  ✅ پیدا کردن خودکار همهٔ ماژول‌های کد (نه لیست دستی)
  ✅ اجرای موازی با pytest-xdist (سریع‌تر)
  ✅ گزارش دقیق با missing lines
  ✅ شناسایی ماژول‌های صفر٪ (بدون تست)
  ✅ شناسایی فایل‌های تست شکست‌خورده
  ✅ ذخیره در JSON + Markdown

اجرا:
    python tests/_coverage_matrix.py
    python tests/_coverage_matrix.py --fast       (فقط تست‌های سریع)
    python tests/_coverage_matrix.py --jobs 4     (پارالل ۴ هسته)
    python tests/_coverage_matrix.py --top 10     (نمایش ۱۰ ماژول بدتر)
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = ROOT / "tests"
UNIT_DIR = TESTS_DIR / "unit"

# ═══════════════════════════════════════════════════════════
# پیدا کردن خودکار فایل‌های تست
# ═══════════════════════════════════════════════════════════
def find_test_files() -> list[Path]:
    """همهٔ فایل‌های تست را پیدا کن."""
    test_files = []
    
    # tests/*.py
    for p in TESTS_DIR.glob("test_*.py"):
        test_files.append(p)
    
    # tests/unit/*.py
    if UNIT_DIR.exists():
        for p in UNIT_DIR.glob("test_*.py"):
            test_files.append(p)
    
    return sorted(test_files)


# ═══════════════════════════════════════════════════════════
# پیدا کردن خودکار ماژول‌های کد
# ═══════════════════════════════════════════════════════════
def find_code_modules() -> list[str]:
    """همهٔ ماژول‌های .py در core/, analyzers/, collectors/, handlers/."""
    modules = []
    for top in ("core", "analyzers", "collectors", "handlers", "data"):
        top_path = ROOT / top
        if not top_path.exists():
            continue
        for py in top_path.rglob("*.py"):
            # نام ماژول
            rel = py.relative_to(ROOT)
            mod = str(rel.with_suffix("")).replace("\\", "/").replace("/", ".")
            if mod.endswith(".__init__"):
                mod = mod[:-9]  # core/__init__.py → core
            modules.append(mod)
    
    # فایل‌های ریشه
    for f in ("config.py",):
        if (ROOT / f).exists():
            modules.append(f[:-3])
    
    return sorted(set(modules))


# ═══════════════════════════════════════════════════════════
# اجرای یک فایل تست با coverage
# ═══════════════════════════════════════════════════════════
def run_single_test(
    test_path: Path,
    coverage_targets: list[str],
    timeout: int = 60,
) -> dict:
    """اجرای یک تست با coverage فقط روی ماژول‌های مربوطه."""
    if not test_path.exists():
        return {"status": "missing", "test": str(test_path)}
    
    cov_json = test_path.parent / f"_cov_{test_path.stem}.json"
    
    cmd = [
        sys.executable, "-m", "pytest",
        str(test_path),
        "-m", "not network",
        "--no-header", "-q",
        f"--timeout={timeout}",
        *[arg for t in coverage_targets for arg in ("--cov", t)],
        f"--cov-report=json:{cov_json}",
    ]
    
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
        "files": {},
        "stdout_tail": r.stdout[-500:] if r.returncode != 0 else "",
    }
    
    m = re.search(r"(\d+) passed", r.stdout)
    if m:
        result["passed"] = int(m.group(1))
    m = re.search(r"(\d+) failed", r.stdout)
    if m:
        result["failed"] = int(m.group(1))
    
    if cov_json.exists():
        try:
            data = json.loads(cov_json.read_text(encoding="utf-8"))
            for fpath, finfo in data.get("files", {}).items():
                s = finfo.get("summary", {})
                result["files"][fpath.replace("\\", "/")] = {
                    "covered": s.get("covered_lines", 0),
                    "statements": s.get("num_statements", 0),
                    "percent": round(s.get("percent_covered", 0), 1),
                    "missing": finfo.get("missing_lines", []),
                }
        except Exception as e:
            result["parse_error"] = str(e)
        finally:
            cov_json.unlink(missing_ok=True)
    
    return result


# ═══════════════════════════════════════════════════════════
# ساخت نقشهٔ تست → ماژول (خودکار + override)
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
    "test_storage_additions.py": ["handlers.storage_handler"],
    "test_db.py": ["core.database.sqlite"],
    "test_db_additions.py": ["core.database.sqlite"],
    "test_collectors_smoke.py": ["collectors"],
    "test_smoke.py": ["core.kernel", "core.contracts"],
    "test_adapters.py": ["core.adapters"],
    "test_contracts.py": ["core.contracts"],
    "test_kernel.py": ["core.kernel"],
}


def guess_targets(test_name: str) -> list[str]:
    """حدس زدن ماژول هدف از نام تست."""
    # override دستی
    if test_name in MANUAL_MAP:
        return MANUAL_MAP[test_name]
    
    # از اسم test_<module>.py → <module>
    stem = test_name.replace("test_", "").replace(".py", "")
    
    # اگر فایل با این اسم وجود داشت
    for prefix in ("core", "analyzers", "collectors", "handlers", "data"):
        candidate = ROOT / prefix / f"{stem}.py"
        if candidate.exists():
            return [f"{prefix}.{stem}"]
    
    # fallback: همهٔ core
    return ["core", "analyzers", "handlers"]


# ═══════════════════════════════════════════════════════════
# اجرای موازی همهٔ تست‌ها
# ═══════════════════════════════════════════════════════════
def run_all_tests_parallel(
    test_files: list[Path],
    jobs: int = 4,
    timeout: int = 60,
) -> dict[str, dict]:
    """اجرای همهٔ تست‌ها به صورت موازی."""
    results = {}
    
    with ThreadPoolExecutor(max_workers=jobs) as ex:
        futures = {}
        for tf in test_files:
            targets = guess_targets(tf.name)
            future = ex.submit(run_single_test, tf, targets, timeout)
            futures[future] = tf
        
        for future in as_completed(futures):
            tf = futures[future]
            try:
                results[tf.name] = future.result()
                r = results[tf.name]
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
                results[tf.name] = {"status": "error", "error": str(e)}
    
    return results


# ═══════════════════════════════════════════════════════════
# گزارش نهایی
# ═══════════════════════════════════════════════════════════
def build_report(results: dict, all_modules: list[str]) -> dict:
    """گزارش کامل."""
    # آمار تست‌ها
    total_passed = sum(r.get("passed", 0) for r in results.values())
    total_failed = sum(r.get("failed", 0) for r in results.values())
    failed_tests = [name for name, r in results.items() if r["status"] == "fail"]
    
    # پوشش هر ماژول
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
                }
            module_coverage[mod]["covered"] = max(
                module_coverage[mod]["covered"], finfo["covered"]
            )
            module_coverage[mod]["statements"] = max(
                module_coverage[mod]["statements"], finfo["statements"]
            )
            module_coverage[mod]["tests"].append(test_name)
            module_coverage[mod]["missing"].update(finfo.get("missing", []))
    
    # محاسبه درصد نهایی
    for mod, info in module_coverage.items():
        if info["statements"]:
            info["percent"] = round(
                info["covered"] / info["statements"] * 100, 1
            )
        info["missing"] = sorted(info["missing"])
    
    # ماژول‌های صفر٪
    zero_modules = [
        m for m in all_modules
        if m not in module_coverage or module_coverage[m]["percent"] == 0
    ]
    
    return {
        "total_passed": total_passed,
        "total_failed": total_failed,
        "failed_tests": failed_tests,
        "modules": module_coverage,
        "zero_modules": zero_modules,
        "tests": results,
    }


def print_report(report: dict, top_n: int = 0):
    """چاپ گزارش."""
    print()
    print("=" * 78)
    print("  📊 خلاصه")
    print("=" * 78)
    print(f"  ✅ پاس: {report['total_passed']}")
    print(f"  ❌ fail: {report['total_failed']}")
    if report["failed_tests"]:
        print(f"  ⚠️  تست‌های شکست‌خورده:")
        for t in report["failed_tests"]:
            print(f"     • {t}")
    
    print()
    print("=" * 78)
    print("  📈 پوشش ماژول‌ها (بدترین اول)")
    print("=" * 78)
    print(f"  {'Module':<50s} {'Cover':>6s} {'Stmt':>6s}")
    print(f"  {'-'*50} {'-'*6} {'-'*6}")
    
    sorted_mods = sorted(
        report["modules"].items(),
        key=lambda kv: kv[1]["percent"],
    )
    if top_n:
        sorted_mods = sorted_mods[:top_n]
    
    for mod, info in sorted_mods:
        bar = "█" * int(info["percent"] / 10) + "░" * (10 - int(info["percent"] / 10))
        print(
            f"  {mod:<50s} "
            f"{info['percent']:>5.1f}% "
            f"{info['statements']:>6d}  {bar}"
        )
    
    print()
    print("=" * 78)
    print("  🔴 ماژول‌های صفر٪ (بدون تست)")
    print("=" * 78)
    if report["zero_modules"]:
        for mod in report["zero_modules"]:
            print(f"  🔴 {mod}")
    else:
        print("  ✅ هیچ ماژول صفر٪ نیست")


# ═══════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--top", type=int, default=0)
    args = parser.parse_args()
    
    print("=" * 78)
    print("  🎯 Coverage Matrix v3 — خودکار و دقیق")
    print("=" * 78)
    
    # ─── پیدا کردن خودکار ───
    test_files = find_test_files()
    all_modules = find_code_modules()
    
    print(f"  🧪 {len(test_files)} فایل تست")
    print(f"  📦 {len(all_modules)} ماژول کد")
    print()
    
    # ─── اجرای موازی ───
    print(f"  ▶ اجرای موازی ({args.jobs} worker)...")
    print()
    results = run_all_tests_parallel(
        test_files, jobs=args.jobs, timeout=args.timeout,
    )
    
    # ─── گزارش ───
    report = build_report(results, all_modules)
    print_report(report, top_n=args.top)
    
    # ─── ذخیره ───
    out_json = TESTS_DIR / "coverage_matrix.json"
    out_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    
    # Markdown
    lines = ["# Coverage Matrix v3\n"]
    lines.append(f"- ✅ Passed: **{report['total_passed']}**")
    lines.append(f"- ❌ Failed: **{report['total_failed']}**")
    lines.append("")
    lines.append("## Modules\n")
    lines.append("| Module | Cover | Statements |")
    lines.append("|---|---:|---:|")
    for mod, info in sorted(
        report["modules"].items(), key=lambda kv: kv[1]["percent"]
    ):
        lines.append(
            f"| `{mod}` | {info['percent']:.1f}% | {info['statements']} |"
        )
    
    if report["zero_modules"]:
        lines.append("\n## Zero coverage\n")
        for mod in report["zero_modules"]:
            lines.append(f"- 🔴 `{mod}`")
    
    out_md = TESTS_DIR / "coverage_matrix.md"
    out_md.write_text("\n".join(lines), encoding="utf-8")
    
    print()
    print(f"  📄 {out_json.name}")
    print(f"  📄 {out_md.name}")


if __name__ == "__main__":
    main()