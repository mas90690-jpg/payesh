# tests/helpers.py
"""ابزارهای مشترک تست‌های پلتفرم."""
import os
import sys
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
load_dotenv(os.path.join(ROOT, ".env"))


# ═══════════════════════════════════════════════════════════════════════════
# رنگ‌های ANSI
# ═══════════════════════════════════════════════════════════════════════════
class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    MAGENTA = "\033[95m"

    OK = "\033[92m✅\033[0m"
    FAIL = "\033[91m❌\033[0m"
    WARN = "\033[93m⚠️\033[0m"
    INFO = "\033[96mℹ️\033[0m"

    END = "\033[0m"


# ═══════════════════════════════════════════════════════════════════════════
# نقشهٔ نام واقعی پوشه‌های session
# ═══════════════════════════════════════════════════════════════════════════
_SESSION_DIR_MAP = {
    "bale": "bale_web",
    "eitaa": "eitaa_web",
    "telegram": "telegram_web",
    "youtube": "youtube_web",
    "instagram": "instagram",
    "linkedin": "linkedin_v2",
    "whatsapp": "whatsapp",
    "facebook": "facebook_stealth",
    "rubika": "rubika_session",
}


# ═══════════════════════════════════════════════════════════════════════════
# TestReport
# ═══════════════════════════════════════════════════════════════════════════
class TestReport:
    """گزارش تست با شمارش ok/fail/warn/skip."""

    def __init__(self, platform: str):
        self.platform = platform
        self.results: list[tuple[str, str, str]] = []
        self.timings: list[tuple[str, float]] = []

    def add(
        self,
        name: str,
        status: str,
        detail: str = "",
        elapsed_ms: float = 0.0,
    ) -> None:
        self.results.append((name, status, detail))
        if elapsed_ms:
            self.timings.append((name, elapsed_ms))

    def summary(self) -> dict[str, int]:
        counts = {"ok": 0, "fail": 0, "warn": 0, "skip": 0, "info": 0}
        for _, status, _ in self.results:
            if status in counts:
                counts[status] += 1
        return counts

    def print_summary(self) -> bool:
        s = self.summary()
        print()
        print("═" * 72)
        print(f"{C.BOLD}📊 خلاصهٔ تست {self.platform.upper()}{C.RESET}")
        print("═" * 72)
        for name, status, detail in self.results:
            icon = {
                "ok": "✅",
                "fail": "❌",
                "warn": "⚠️ ",
                "skip": "⏭️ ",
                "info": "ℹ️ ",
            }.get(status, "•")
            line = f"  {icon} {name}"
            if detail:
                line += f" | {detail}"
            print(line)

        print()
        print(
            f"  ✅ موفق: {s['ok']} | ❌ ناموفق: {s['fail']} | "
            f"⚠️ هشدار: {s['warn']} | ⏭️ رد: {s['skip']}"
        )
        print("═" * 72)

        return s["fail"] == 0

    def save(self, path: str | None = None) -> None:
        """ذخیره در JSON — پیش‌فرض: storage/reports/test_<platform>.json"""
        import json
        from datetime import datetime

        if path is None:
            reports_dir = os.path.join(ROOT, "storage", "reports")
            os.makedirs(reports_dir, exist_ok=True)
            path = os.path.join(
                reports_dir, f"test_{self.platform}.json"
            )

        data = {
            "platform": self.platform,
            "timestamp": datetime.now().isoformat(),
            "summary": self.summary(),
            "results": [
                {"name": n, "status": s, "detail": d}
                for n, s, d in self.results
            ],
            "timings": [
                {"name": n, "elapsed_ms": ms}
                for n, ms in self.timings
            ],
        }

        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"  ⚠️ ذخیرهٔ گزارش شکست خورد: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# banner / section
# ═══════════════════════════════════════════════════════════════════════════
def banner(title: str) -> None:
    print()
    print("╔" + "═" * 68 + "╗")
    print(f"║ {C.BOLD}{title[:66]:<66}{C.RESET} ║")
    print("╚" + "═" * 68 + "╝")
    print()


def section(title: str) -> None:
    print(f"\n{C.CYAN}{'─' * 72}{C.RESET}")
    print(f"{C.BOLD}{title}{C.RESET}")
    print(f"{C.CYAN}{'─' * 72}{C.RESET}")


# ═══════════════════════════════════════════════════════════════════════════
# env / session
# ═══════════════════════════════════════════════════════════════════════════
def check_env(platform: str) -> dict:
    """اطلاعات env و مسیر session برای یک پلتفرم.

    🎯 session_dir_map: نام واقعی پوشهٔ session در storage/sessions/
    (bale → bale_web، eitaa → eitaa_web، ...)
    """
    var = f"{platform.upper()}_ENABLED"
    dir_name = _SESSION_DIR_MAP.get(platform, platform)
    return {
        "enabled": os.environ.get(var, "false").lower() == "true",
        "env_var": var,
        "phone": os.environ.get(f"{platform.upper()}_PHONE", ""),
        "email": os.environ.get(f"{platform.upper()}_EMAIL", ""),
        "session_path": os.path.join(
            ROOT, "storage", "sessions", dir_name
        ),
    }


def check_session_exists(path: str) -> bool:
    """چک می‌کند آیا session واقعاً ذخیره شده.

    پشتیبانی از:
      - فایل منفرد (rubika: rubika.rp)
      - پوشه با محتوا (bale_web/Default/, ...)
      - فایل .rp / .json کنار پوشه
    """
    if not path:
        return False

    # اگه خود مسیر فایل بود
    if os.path.isfile(path):
        return True

    # اگه فولدر بود
    if os.path.isdir(path):
        try:
            # پوشه‌های پروفایل Chromium: Default/ و Local State
            entries = os.listdir(path)
            if "Default" in entries or "Local State" in entries:
                return True
            # هر فایل/پوشه‌ای غیر از . و ..
            if any(not e.startswith(".") for e in entries):
                return True
        except OSError:
            return False

    # اگه فایل .rp کنارش بود (هم rubika.rp هم rubika_session.rp)
    for suffix in (".rp", "_session.rp", ".json", "_session.json"):
        if os.path.isfile(path + suffix):
            return True

    return False


async def safe_disconnect(collector, report: TestReport) -> None:
    try:
        await collector.disconnect()
        report.add("disconnect", "ok", "قطع شد")
    except Exception as e:
        report.add("disconnect", "warn", str(e))