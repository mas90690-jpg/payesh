"""ضد شناسایی — fingerprint + stealth.

این ماژول نشانه‌های headless بودن Chrome رو پاک می‌کنه
و fingerprint واقعی به مرورگر می‌ده.

نسخهٔ ۲.۰:
  - 🐛 FIX: stealth_async اگر خطا داد، crash نمی‌کند (نه فقط ImportError)
  - 🐛 FIX: add_init_script اگر خطا داد، crash نمی‌کند
  - 🎯 fingerprint پویا (hardwareConcurrency، deviceMemory از rng)
  - 🎯 window.chrome کامل‌تر (loadTimes، csi، app)
"""
from __future__ import annotations

import random
from typing import Any

from core.kernel.logger import log_info, log_debug, log_warning

log_info("✅ [anti_detect] بارگذاری شد")


# ─── User-Agent های واقعی ───
_REAL_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
]


async def apply_stealth(
    page: Any, rng: random.Random | None = None
) -> None:
    """اعمال stealth به یک page — پاک‌سازی نشانه‌های headless."""
    rng = rng or random.Random()

    # ─── playwright-stealth (اگر نصب باشه) ───
    try:
        from playwright_stealth import stealth_async
        await stealth_async(page)
    except ImportError as exc:
        log_debug(f'⚠️ {type(exc).__name__}: {exc}')
    except Exception as exc:
        # 🎯 FIX: خطای runtime هم نباید crash کند
        log_warning(
            f'⚠️ stealth_async خطا داد: {type(exc).__name__}: {exc}'
        )

    # ─── مقادیر fingerprint پویا ───
    hw_concurrency = rng.choice([4, 8, 12, 16])
    device_memory = rng.choice([4, 8, 16])

    # ─── init script: پاک‌سازی نشانه‌های headless ───
    init_script = f"""
        // پاک‌کردن webdriver
        Object.defineProperty(navigator, 'webdriver', {{get: () => undefined}});

        // زبان‌ها
        Object.defineProperty(navigator, 'languages', {{
            get: () => ['fa-IR', 'fa', 'en-US', 'en']
        }});

        // plugins (headless = خالی)
        Object.defineProperty(navigator, 'plugins', {{
            get: () => [
                {{name: 'Chrome PDF Plugin'}},
                {{name: 'Chrome PDF Viewer'}},
                {{name: 'Native Client'}},
            ]
        }});

        // hardwareConcurrency
        Object.defineProperty(navigator, 'hardwareConcurrency', {{
            get: () => {hw_concurrency}
        }});

        // deviceMemory
        Object.defineProperty(navigator, 'deviceMemory', {{
            get: () => {device_memory}
        }});

        // chrome runtime (headless = undefined)
        window.chrome = window.chrome || {{
            runtime: {{}},
            loadTimes: function() {{}},
            csi: function() {{}},
            app: {{}}
        }};

        // permissions
        const origQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (params) => (
            params.name === 'notifications'
                ? Promise.resolve({{state: Notification.permission}})
                : origQuery(params)
        );

        // WebGL vendor (headless = Brian Paul)
        const getParameter = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(parameter) {{
            if (parameter === 37445) return 'Intel Inc.';
            if (parameter === 37446) return 'Intel Iris OpenGL Engine';
            return getParameter.call(this, parameter);
        }};
    """

    try:
        await page.add_init_script(init_script)
    except Exception as exc:
        # 🎯 FIX: اگر add_init_script پشتیبانی نشد/خطا داد، ادامه بده
        log_warning(
            f'⚠️ add_init_script: {type(exc).__name__}: {exc}'
        )

    # ─── headers واقعی ───
    try:
        await page.set_extra_http_headers({
            "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1",
        })
    except Exception as exc:
        log_debug(f'⚠️ {type(exc).__name__}: {exc}')

    # ─── viewport تصادفی ───
    try:
        w = rng.randint(1280, 1600)
        h = rng.randint(720, 1000)
        await page.set_viewport_size({"width": w, "height": h})
    except Exception as exc:
        log_debug(f'⚠️ {type(exc).__name__}: {exc}')


def random_ua() -> str:
    """User-Agent واقعی تصادفی."""
    return random.choice(_REAL_UAS)