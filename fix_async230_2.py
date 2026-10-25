"""fix_async230_2.py — رفع 8 مورد باقی‌مانده ASYNC230."""
from pathlib import Path

FIXES = [
    # ─── mixin_files.py خط 619 (خواندن — همان الگوی 567) ───
    ("core/brain/mixin_files.py",
     '            with open(p, "rb") as f:\n                for _ in f:\n                    line_count += 1',
     '            def _count():\n                n = 0\n                with open(p, "rb") as f:\n                    for _ in f:\n                        n += 1\n                return n\n            line_count = await asyncio.to_thread(_count)'),
    # ─── mixin_files.py خط 1204 (نوشتن append) ───
    ("core/brain/mixin_files.py",
     '            with open(str(p), "a", encoding="utf-8") as f:\n                f.write(new_block)\n                try:\n                    f.flush()\n                    os.fsync(f.fileno())\n                except (AttributeError, OSError):\n                    pass',
     '            def _append():\n                with open(str(p), "a", encoding="utf-8") as f:\n                    f.write(new_block)\n                    try:\n                        f.flush()\n                        os.fsync(f.fileno())\n                    except (AttributeError, OSError):\n                        pass\n            await asyncio.to_thread(_append)'),
    # ─── mixin_reports.py خط 132 (خواندن .env) ───
    ("core/brain/mixin_reports.py",
     '                with open(env_path, encoding="utf-8") as f:\n                    lines = f.readlines()',
     '                def _read():\n                    with open(env_path, encoding="utf-8") as f:\n                        return f.readlines()\n                lines = await asyncio.to_thread(_read)'),
    # ─── mixin_reports.py خط 147 (نوشتن .env) ───
    ("core/brain/mixin_reports.py",
     '            with open(tmp_path, "w", encoding="utf-8") as f:\n                f.writelines(lines)\n                try:\n                    f.flush()\n                    os.fsync(f.fileno())\n                except (AttributeError, OSError):\n                    pass',
     '            def _write_env():\n                with open(tmp_path, "w", encoding="utf-8") as f:\n                    f.writelines(lines)\n                    try:\n                        f.flush()\n                        os.fsync(f.fileno())\n                    except (AttributeError, OSError):\n                        pass\n            await asyncio.to_thread(_write_env)'),
    # ─── mixin_info.py خط 235 (خواندن خط به خط) ───
    # (اینو فعلاً skip می‌کنیم چون complex هست و باید کل بلوک 235-245 برداشته بشه)
]


def main():
    results = []
    for path, old, new in FIXES:
        p = Path(path)
        if not p.exists():
            results.append((path, "❌ فایل نیست"))
            continue
        text = p.read_text(encoding="utf-8")
        cnt = text.count(old)
        if cnt == 0:
            results.append((path, "❌ متن پیدا نشد"))
            continue
        if cnt > 1:
            results.append((path, f"⚠️ {cnt} بار تکرار — skip"))
            continue
        text = text.replace(old, new, 1)
        p.write_text(text, encoding="utf-8")
        results.append((path, "✅"))

    print("\n📊 نتایج:")
    for path, result in results:
        print(f"  {result}  {path}")

    ok = sum(1 for _, r in results if r == "✅")
    print(f"\n✅ {ok}/{len(results)} اعمال شد")


if __name__ == "__main__":
    main()