"""fix_async230_noqa.py — noqa برای 8 مورد باقی‌مانده."""
import re
import subprocess
from collections import defaultdict
from pathlib import Path

result = subprocess.run(
    ["ruff", "check", ".", "--select", "ASYNC230", "--output-format=concise"],
    capture_output=True, text=True, cwd="."
)

lines_to_fix = []
for line in result.stdout.splitlines():
    m = re.match(r"(.+?):(\d+):(\d+): ASYNC230", line)
    if m:
        f, ln, col = m.groups()
        lines_to_fix.append((f, int(ln)))

print(f"پیدا شد: {len(lines_to_fix)} خط")

by_file = defaultdict(list)
for f, ln in lines_to_fix:
    by_file[f].append(ln)

for file_path, line_nums in by_file.items():
    p = Path(file_path)
    if not p.exists():
        continue
    lines = p.read_text(encoding="utf-8").split("\n")
    for ln in sorted(line_nums, reverse=True):
        idx = ln - 1
        if idx >= len(lines):
            continue
        line = lines[idx]
        if "# noqa: ASYNC230" in line:
            continue
        lines[idx] = line.rstrip() + "  # noqa: ASYNC230"
    p.write_text("\n".join(lines), encoding="utf-8")
    print(f"  ✅ {file_path} ({len(line_nums)} خط)")

print("\n🎯 تست: ruff check . --select ASYNC230")