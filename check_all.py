import asyncio
import re


FILES = [
    "core/brain/utils.py",
    "core/brain/learning.py",
    "core/brain/memory.py",
    "core/brain/mixin_info.py",
    "core/brain/mixin_agent.py",
    "core/brain/mixin_reports.py",
    "core/brain/mixin_files.py",
    "core/brain/mixin_platforms.py",
    "core/brain/mixin_system.py",
]


def check_file(path):
    print("=" * 60)
    print("FILE:", path)
    print("=" * 60)
    try:
        with open(path, "rb") as f:
            raw = f.read(3)
        if raw == b"\xef\xbb\xbf":
            print("  BOM: YES (must remove)")
        else:
            print("  BOM: NO")
    except Exception as e:
        print("  BOM check error:", e)
        return
    try:
        s = open(path, encoding="utf-8").read()
        print("  len:", len(s))
        print("  lines:", s.count(chr(10)) + 1)
        first = s.split(chr(10))[0]
        print("  first:", repr(first[:60]))
    except Exception as e:
        print("  read error:", e)
        return
    try:
        import subprocess
        r = subprocess.run(
            ["python", "-m", "py_compile", path],
            capture_output=True, text=True
        )
        if r.returncode == 0:
            print("  syntax: OK")
        else:
            print("  syntax: ERROR")
            print("  ", r.stderr[:300])
    except Exception as e:
        print("  compile error:", e)


for f in FILES:
    check_file(f)