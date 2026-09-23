# analyzers/target_filter.py
"""
فیلترِ «گروه‌های هدف» — تمرکزِ عقاب فقط روی گروه‌هایی که خودت انتخاب کرده‌ای.

مشکل: بعضی پلتفرم‌ها گروه‌های تبلیغاتی را بدونِ اجازه عضوت می‌کنند یا نشانت می‌دهند.
این فیلتر باعث میشود ربات فقط گروههایی را چک کند که تو در `data/sources/target_groups.txt`
نوشته‌ای (نام یا یوزرنیمِ گروه — تطبیقِ بخشی و بی‌توجه به بزرگ/کوچک).

قالبِ فایل (حرفه‌ای و ساده — هر دو حالت):
  نهاده اصفهان            ← برای همهٔ پلتفرم‌ها
  eitaa: بازار کود        ← فقط ایتا (وقتی نامِ گروه در یک پلتفرم فرق دارد)
  bale: @keshavarz_group  ← فقط بله

منطق (بدونِ سورپرایز):
- فایل خالی → همه‌جا همه‌چیز چک می‌شود.
- یک پلتفرم که هیچ قانونی (نه سراسری، نه مخصوصِ خودش) ندارد → برای آن پلتفرم فیلتر خاموش
  است (همه‌چیزش چک می‌شود). یعنی اگر فقط برای ایتا هدف نوشتی، بله دست‌نخورده می‌ماند.
hot-reload: تغییرِ فایل بدونِ ری‌استارت اعمال می‌شود.
"""
import os
import time

_RELOAD_SECONDS = 5.0
_PLATFORMS = ("eitaa", "bale", "rubika", "telegram", "whatsapp", "linkedin", "web")


class TargetGroups:
    def __init__(self, path: str = None):
        from data import paths as data_paths
        self.path = path or str(data_paths.TARGET_GROUPS)
        self._all: list[str] = []  # اعمال به همهٔ پلتفرم‌ها
        self._by_platform: dict[str, list[str]] = {}  # اعمال فقط به یک پلتفرم
        self._mtime = None
        self._last_check = 0.0
        self._load()

    def _load(self):
        all_t: list[str] = []
        by: dict[str, list[str]] = {}
        try:
            self._mtime = os.path.getmtime(self.path)
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    s = line.strip()
                    if not s or s.startswith("#"):
                        continue
                    plat = None
                    if ":" in s:
                        head, rest = s.split(":", 1)
                        if head.strip().lower() in _PLATFORMS:
                            plat, s = head.strip().lower(), rest.strip()
                    s = s.lstrip("@").lower()  # @ حذف تا با یوزرنیم تطبیق بخورد
                    if not s:
                        continue
                    if plat:
                        by.setdefault(plat, []).append(s)
                    else:
                        all_t.append(s)
        except OSError:
            self._mtime = None
        self._all, self._by_platform = all_t, by

    def _maybe_reload(self):
        now = time.monotonic()
        if now - self._last_check < _RELOAD_SECONDS:
            return
        self._last_check = now
        try:
            m = os.path.getmtime(self.path)
        except OSError:
            m = None
        if m != self._mtime:
            self._load()

    def _targets_for(self, platform: str | None) -> list[str]:
        return self._all + (self._by_platform.get(platform, []) if platform else [])

    def enabled(self, platform: str | None = None) -> bool:
        """آیا برای این پلتفرم هدفی تعریف شده؟ (بدونِ پلتفرم = آیا اصلاً هدفی هست؟)"""
        self._maybe_reload()
        if platform:
            return bool(self._targets_for(platform))
        return bool(self._all or self._by_platform)

    def is_target(
        self,
        chat_name: str | None,
        chat_username: str | None = None,
        platform: str | None = None,
    ) -> bool:
        """آیا این گروه هدف است؟ اگر برای این پلتفرم قانونی نباشد → True (فیلتر خاموش)."""
        self._maybe_reload()
        targets = self._targets_for(platform)
        if not targets:
            return True
        hay = ((chat_name or "") + " " + (chat_username or "")).lower()
        return any(t in hay for t in targets)


_shared: TargetGroups | None = None


def get_target_groups() -> TargetGroups:
    global _shared
    if _shared is None:
        _shared = TargetGroups()
    return _shared
