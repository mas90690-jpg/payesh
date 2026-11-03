from __future__ import annotations

from core.kernel.logger import log_debug, log_error, log_info, log_warning


def score_message(text: str, keywords: list[str] | None = None) -> float:
    if not text:
        log_warning("score_message: متن خالی است")
        return 0.0
    if not keywords:
        log_debug("score_message: کلیدواژه‌ای نیست، امتیاز پیش‌فرض 0.5")
        return 0.5
    matches = sum(1 for kw in keywords if kw.lower() in text.lower())
    score = min(1.0, matches / max(len(keywords), 1))
    log_info(
        f"score_message: {matches} تطابق از {
            len(keywords)} کلیدواژه، امتیاز={score}")
    return score
