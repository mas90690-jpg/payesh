# tests/test_facebook_selector_check.py
"""
تست سریع — کدام سلکتور متن پست فیسبوک را می‌گیرد؟

هدف: فقط یک چیز — کدام سلکتور در DOM واقعی متن می‌دهد؟

اجرا:
  python tests/test_facebook_selector_check.py
  python tests/test_facebook_selector_check.py --headless
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

from tests.helpers import (
    TestReport, banner, check_env, section, C,
)


async def _check_selector(page, sel: str) -> dict:
    """یک سلکتور را در همهٔ article ها چک می‌کند."""
    return await page.evaluate(f"""
        () => {{
            const articles = document.querySelectorAll('div[role="article"]');
            const results = [];
            for (let i = 0; i < Math.min(articles.length, 5); i++) {{
                const a = articles[i];
                try {{
                    const els = a.querySelectorAll('{sel}');
                    let longest = 0;
                    let longest_text = "";
                    for (const el of els) {{
                        const t = (el.innerText || '').trim();
                        if (t.length > longest) {{
                            longest = t.length;
                            longest_text = t.slice(0, 200);
                        }}
                    }}
                    results.push({{
                        idx: i + 1,
                        count: els.length,
                        longest,
                        text: longest_text,
                    }});
                }} catch (e) {{
                    results.push({{
                        idx: i + 1,
                        count: -1,
                        longest: 0,
                        text: 'ERROR: ' + e.message,
                    }});
                }}
            }}
            return results;
        }}
    """)


async def main(headless: bool = False):
    banner("چک سلکتور متن پست فیسبوک")
    report = TestReport("facebook_selector")

    if not check_env("facebook").get("enabled", False):
        print(f"{C.YELLOW}فیسبوک غیرفعال{C.END}")
        return False

    # ─── ساخت collector ───
    from collectors.facebook import FacebookCollector

    collector = FacebookCollector(
        headless=headless,
        poll_seconds=999,
        max_posts=10,
        read_feed=True,
        read_groups=False,
        scroll_rounds=3,
    )

    t0 = time.monotonic()
    ok = await collector.connect()
    elapsed = (time.monotonic() - t0) * 1000
    report.add("connect", "ok" if ok else "fail",
               f"{elapsed:.0f}ms", elapsed)
    if not ok:
        await collector.disconnect()
        return False

    page = collector._page

    # ─── goto فید ───
    section("رفتن به فید")
    try:
        await page.goto(
            "https://www.facebook.com/?filter=all&sk=h_chr",
            wait_until="domcontentloaded",
            timeout=60000,
        )
        await asyncio.sleep(8)
    except Exception as e:
        print(f"❌ goto: {e}")
        await collector.disconnect()
        return False

    # ─── صبر تا رندر ───
    section("صبر تا رندر پست‌های واقعی")
    for i in range(30):
        state = await page.evaluate("""
            () => {
                const articles = document.querySelectorAll(
                    'div[role="article"]'
                );
                let loading = 0, real = 0;
                for (const a of articles) {
                    if (a.querySelector('[aria-label="Loading..."]')) {
                        loading++;
                    } else if ((a.innerText || '').trim().length >= 30) {
                        real++;
                    }
                }
                return { total: articles.length, loading, real };
            }
        """)
        total = state.get("total", 0)
        real = state.get("real", 0)
        loading = state.get("loading", 0)
        print(f"   [{i*2}s] total={total} | real={real} | loading={loading}")
        if real >= 1 and loading == 0:
            break
        await asyncio.sleep(2)

    # ─── چک همه سلکتورها ───
    section("چک سلکتورها")

    selectors = [
        # 🎯 جدید (از DOM واقعی)
        'div.xdj266r.x14z9mp.xat24cr.x1lziwak.x1vvkbs.x126k92a > div[dir="auto"]',
        'div[dir="auto"][style*="text-align"]',
        # کلاسیک
        'div[data-ad-preview="message"]',
        'div[data-ad-comet-preview="message"]',
        'div[data-ad-rendering-role="story_message"]',
        # عمومی
        'div[dir="auto"]',
        'span[dir="auto"]',
        'p',
    ]

    best_selector = None
    best_len = 0

    for sel in selectors:
        results = await _check_selector(page, sel)

        print(f"\n  📋 `{sel}`")
        total_hits = 0
        max_len = 0
        for r in results:
            count = r.get("count", 0)
            longest = r.get("longest", 0)
            text = r.get("text", "")
            total_hits += count if count > 0 else 0
            max_len = max(max_len, longest)

            marker = "✅" if longest >= 30 else (
                "⚠️" if longest > 0 else "❌"
            )
            print(
                f"     {marker} [article {r['idx']}] "
                f"count={count} | longest={longest}"
            )
            if longest >= 30:
                print(f"        '{text[:100]}'")

        report.add(
            f"selector.{sel[:40]}",
            "ok" if max_len >= 30 else "warn",
            f"hits={total_hits} | max={max_len}"
        )

        if max_len > best_len:
            best_len = max_len
            best_selector = sel

    # ─── نتیجه ───
    section("🏆 نتیجه")
    print(f"\n  بهترین سلکتور:")
    print(f"     `{best_selector}`")
    print(f"     طولانی‌ترین متن: {best_len} کاراکتر")

    if best_len >= 30:
        print(f"\n  {C.OK if hasattr(C, 'OK') else ''}✅ این سلکتور را در "
              f"collectors/facebook.py استفاده کن{C.END if hasattr(C, 'END') else ''}")
    else:
        print(f"\n  ⚠️ هیچ سلکتوری متن کافی نداد")
        print(f"     ممکن است فید خالی باشد یا فیسبوک رندر نکند")

    await collector.disconnect()
    ok = report.print_summary()
    report.save()
    return ok


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()

    ok = asyncio.run(main(headless=args.headless))
    sys.exit(0 if ok else 1)