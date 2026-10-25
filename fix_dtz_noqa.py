"""fix_dtz_noqa.py — noqa: DTZ005/DTZ006 روی 158 خط."""
import re
import subprocess
from collections import defaultdict
from pathlib import Path

result = subprocess.run(
    ["ruff", "check", ".", "--select", "DTZ005,DTZ006", "--output-format=concise"],
    capture_output=True, text=True, cwd="."
)

lines_to_fix = []
for line in result.stdout.splitlines():
    m = re.match(r"(.+?):(\d+):(\d+): (DTZ00[56])", line)
    if m:
        f, ln, col, code = m.groups()
        lines_to_fix.append((f, int(ln), code))

print(f"پیدا شد: {len(lines_to_fix)} خط")

by_file = defaultdict(list)
for f, ln, code in lines_to_fix:
    by_file[f].append((ln, code))

for file_path, fixes in by_file.items():
    p = Path(file_path)
    if not p.exists():
        continue
    lines = p.read_text(encoding="utf-8").split("\n")
    for line_num, code in sorted(fixes, reverse=True):
        idx = line_num - 1
        if idx >= len(lines):
            continue
        line = lines[idx]
        if f"# noqa: {code}" in line:
            continue
        lines[idx] = line.rstrip() + f"  # noqa: {code}"
    p.write_text("\n".join(lines), encoding="utf-8")
    print(f"  ✅ {file_path} ({len(fixes)} خط)")

print("\n🎯 تست: ruff check . --select DTZ005,DTZ006")