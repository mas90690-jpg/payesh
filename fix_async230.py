"""fix_async230.py — رفع ASYNC230 با asyncio.to_thread در 22 خط."""
import re
from pathlib import Path

# الگوهای جایگزینی
FIXES = [
    # ─── dump_dom (گروه ۱) ───
    ("collectors/bale.py",
     '            with open(path, "w", encoding="utf-8") as f:\n                f.write(html)',
     '            def _write():\n                with open(path, "w", encoding="utf-8") as f:\n                    f.write(html)\n            await asyncio.to_thread(_write)'),
    ("collectors/eitaa.py",
     '            with open(path, "w", encoding="utf-8") as f:\n                f.write(html)',
     '            def _write():\n                with open(path, "w", encoding="utf-8") as f:\n                    f.write(html)\n            await asyncio.to_thread(_write)'),
    ("collectors/facebook.py",
     '            with open(path, "w", encoding="utf-8") as f:\n                f.write(html)',
     '            def _write():\n                with open(path, "w", encoding="utf-8") as f:\n                    f.write(html)\n            await asyncio.to_thread(_write)'),
    ("collectors/instagram.py",
     '            with open(path, "w", encoding="utf-8") as f:\n                f.write(html)',
     '            def _write():\n                with open(path, "w", encoding="utf-8") as f:\n                    f.write(html)\n            await asyncio.to_thread(_write)'),
    ("collectors/telegram.py",
     '            with open(path, "w", encoding="utf-8") as f:\n                f.write(html)',
     '            def _write():\n                with open(path, "w", encoding="utf-8") as f:\n                    f.write(html)\n            await asyncio.to_thread(_write)'),
    ("collectors/whatsapp.py",
     '        with open(path, "w", encoding="utf-8") as f:\n            f.write(html)',
     '        def _write():\n            with open(path, "w", encoding="utf-8") as f:\n                f.write(html)\n        await asyncio.to_thread(_write)'),
    ("collectors/youtube.py",
     '            with open(path, "w", encoding="utf-8") as f:\n                f.write(html)',
     '            def _write():\n                with open(path, "w", encoding="utf-8") as f:\n                    f.write(html)\n            await asyncio.to_thread(_write)'),
    ("collectors/linkedin.py",
     '        with open(path, "w", encoding="utf-8") as f:\n            f.write(html)',
     '        def _write():\n            with open(path, "w", encoding="utf-8") as f:\n                f.write(html)\n        await asyncio.to_thread(_write)'),
    # ─── linkedin load JSON ───
    ("collectors/linkedin.py",
     '            with open(self._state_path, encoding="utf-8") as f:\n                state = json.load(f)',
     '            def _load():\n                with open(self._state_path, encoding="utf-8") as f:\n                    return json.load(f)\n            state = await asyncio.to_thread(_load)'),
    # ─── web_media ───
    ("core/adapters/web_media.py",
     '        with open(path, "wb") as f:\n            f.write(raw)\n            try:\n                f.flush()\n                os.fsync(f.fileno())\n            except (AttributeError, OSError):\n                pass',
     '        def _write():\n            with open(path, "wb") as f:\n                f.write(raw)\n                try:\n                    f.flush()\n                    os.fsync(f.fileno())\n                except (AttributeError, OSError):\n                    pass\n        await asyncio.to_thread(_write)'),
    # ─── mixin_files.py ───
    ("core/brain/mixin_files.py",
     '            with open(p, "rb") as f:\n                for _ in f:\n                    line_count += 1',
     '            def _count():\n                n = 0\n                with open(p, "rb") as f:\n                    for _ in f:\n                        n += 1\n                return n\n            line_count = await asyncio.to_thread(_count)'),
    # ─── mixin_reports.py (خواندن .env) ───
    ("core/brain/mixin_reports.py",
     '                with open(env_path, encoding="utf-8") as f:\n                    lines = f.readlines()',
     '                def _read():\n                    with open(env_path, encoding="utf-8") as f:\n                        return f.readlines()\n                lines = await asyncio.to_thread(_read)'),
    # ─── mixin_reports.py (نوشتن .env) ───
    ("core/brain/mixin_reports.py",
     '            with open(tmp_path, "w", encoding="utf-8") as f:\n                f.writelines(lines)\n                try:\n                    f.flush()\n                    os.fsync(f.fileno())\n                except (AttributeError, OSError):\n                    pass',
     '            def _write_env():\n                with open(tmp_path, "w", encoding="utf-8") as f:\n                    f.writelines(lines)\n                    try:\n                        f.flush()\n                        os.fsync(f.fileno())\n                    except (AttributeError, OSError):\n                        pass\n            await asyncio.to_thread(_write_env)'),
    # ─── admin_actions (نوشتن PID) ───
    ("handlers/admin_actions.py",
     '                with open(self.pid_file, "w") as f:\n                    f.write(str(proc.pid))',
     '                def _write_pid():\n                    with open(self.pid_file, "w") as f:\n                        f.write(str(proc.pid))\n                await asyncio.to_thread(_write_pid)'),
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