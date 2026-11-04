import asyncio
import sys


async def main():
    print("=" * 60)
    print("Brain check started")
    print("=" * 60)

    try:
        from core.brain.main import get_brain
        brain = get_brain(read_only=True)
        print("Brain connected")
    except Exception as e:
        print("Brain error:", type(e).__name__, e)
        return

    print("--- check core/brain/main.py ---")

    try:
        content = await brain._read_file("core/brain/main.py")
        print(content[:1500])
    except Exception as e:
        print("read error:", e)

    try:
        with open("core/brain/main.py", "rb") as f:
            raw = f.read(3)
        if raw == b"\xef\xbb\xbf":
            print("BOM: YES (must remove)")
        else:
            print("BOM: NO")
    except Exception as e:
        print("BOM check error:", e)

    try:
        import re
        s = open("core/brain/main.py", encoding="utf-8").read()
        m = re.search(r'_TOOL_METHODS[^{]*\{(.*?)\n\}', s, re.S)
        if m:
            keys = re.findall(r'^\s+"[^"]+":', m.group(1), re.M)
            print("tools count:", len(keys))
        else:
            print("_TOOL_METHODS not found")
    except Exception as e:
        print("tools count error:", e)

    try:
        lines = open("core/brain/main.py", encoding="utf-8").read().split("\n")
        first = lines[0]
        print("first line:", repr(first[:50]))
    except Exception as e:
        print("first line error:", e)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print("fatal:", type(e).__name__, e)
        sys.exit(1)