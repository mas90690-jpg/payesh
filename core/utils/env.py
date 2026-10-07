from __future__ import annotations

import os
import sys


_TRUE = frozenset({"true", "1", "yes", "on", "y", "t"})
_FALSE = frozenset({"false", "0", "no", "off", "n", "f"})


def _warn(msg: str) -> None:
    try:
        print(f"[env] {msg}", file=sys.stderr)
    except Exception:
        try:
            os.write(
                2,
                f"[env] {msg}\n".encode("utf-8", errors="replace"),
            )
        except Exception:
            pass


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    v = str(raw).strip().lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    _warn(f"{name}={raw!r} invalid (bool) - using {default}")
    return default


def env_int(
    name: str, default: int,
    min_val: int | None = None,
    max_val: int | None = None,
) -> int:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        v = int(str(raw).strip())
    except (ValueError, TypeError):
        _warn(f"{name}={raw!r} invalid (int) - using {default}")
        return default

    if min_val is not None and v < min_val:
        return min_val
    if max_val is not None and v > max_val:
        return max_val
    return v


def env_float(
    name: str, default: float,
    min_val: float | None = None,
    max_val: float | None = None,
) -> float:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        v = float(str(raw).strip())
    except (ValueError, TypeError):
        _warn(f"{name}={raw!r} invalid (float) - using {default}")
        return default

    if min_val is not None and v < min_val:
        return min_val
    if max_val is not None and v > max_val:
        return max_val
    return v


def env_str(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


# aliases for backward compatibility
_env_bool = env_bool
_env_int = env_int
_env_float = env_float
_env_str = env_str


__all__ = [
    "env_bool", "env_int", "env_float", "env_str",
    "_env_bool", "_env_int", "_env_float", "_env_str",
]