#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Srooyesh / Payesh Plat — Health Check + Forensic Static Analysis v8.0 Professor Edition

هدف:
    ممیزی فوق‌دقیق پروژهٔ Python و گزارش دادن تک‌تک یافته‌ها بدون تجمیع مبهم.

خروجی هر Finding:
    - ID یکتا
    - Rule / نوع مشکل
    - Severity + Confidence
    - ISO/IEC 25010:2023 category
    - CWE-2025 mapping (در صورت وجود)
    - OWASP Top 10:2025 mapping (در صورت وجود)
    - فایل دقیق
    - بازهٔ دقیق خط
    - تابع / کلاس / ماژول
    - متن واقعی خط
    - دلیل
    - اثر
    - راه‌حل پیشنهادی
    - fingerprint برای مقایسهٔ نسخه‌ها

استانداردهای مرجع:
    ISO/IEC 25010:2023 (9 characteristics)
    OWASP Top 10:2025
    CWE Top 25:2025
    SARIF 2.1.0 + Approved Errata 01

اجرا:
    python tests/test_healthcheck_full.py
    python tests/test_healthcheck_full.py --forensic
    python tests/test_healthcheck_full.py --root .
    python tests/test_healthcheck_full.py --json-out report.json
    python tests/test_healthcheck_full.py --sarif report.sarif
    python tests/test_healthcheck_full.py --markdown-out report.md
    python tests/test_healthcheck_full.py --strict
    python tests/test_healthcheck_full.py --min-score 85
    python tests/test_healthcheck_full.py --run-tests
    python tests/test_healthcheck_full.py --run-external

توجه:
    این ابزار static analysis است. Findingهای با confidence=low/medium را باید
    قبل از رفع قطعی بازبینی کرد؛ ابزار عمداً «قطعیت مصنوعی» ایجاد نمی‌کند.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
import textwrap
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional, Sequence
# ---------------------------------------------------------------------------
# Console
# ---------------------------------------------------------------------------

class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    DIM = "\033[2m"


def disable_color() -> None:
    for name in dir(C):
        if name.isupper():
            setattr(C, name, "")


# ---------------------------------------------------------------------------
# Standards metadata
# ---------------------------------------------------------------------------

ISO25010_2023 = {
    "functional_suitability": {
        "label": "Functional suitability",
        "fa": "تناسب عملکردی",
    },
    "performance_efficiency": {
        "label": "Performance efficiency",
        "fa": "کارایی عملکرد",
    },
    "compatibility": {
        "label": "Compatibility",
        "fa": "سازگاری",
    },
    "interaction_capability": {
        "label": "Interaction capability",
        "fa": "قابلیت تعامل",
    },
    "reliability": {
        "label": "Reliability",
        "fa": "قابلیت اطمینان",
    },
    "security": {
        "label": "Security",
        "fa": "امنیت",
    },
    "maintainability": {
        "label": "Maintainability",
        "fa": "قابلیت نگهداری",
    },
    "flexibility": {
        "label": "Flexibility",
        "fa": "انعطاف‌پذیری",
    },
    "safety": {
        "label": "Safety",
        "fa": "ایمنی",
    },
}

OWASP2025 = {
    "A01": "Broken Access Control",
    "A02": "Security Misconfiguration",
    "A03": "Software Supply Chain Failures",
    "A04": "Cryptographic Failures",
    "A05": "Injection",
    "A06": "Insecure Design",
    "A07": "Authentication Failures",
    "A08": "Software or Data Integrity Failures",
    "A09": "Security Logging & Alerting Failures",
    "A10": "Mishandling of Exceptional Conditions",
}

# 2025 Top 25 entries most useful for rules in a Python service.
CWE2025 = {
    "CWE-79": "Improper Neutralization of Input During Web Page Generation (Cross-site Scripting)",
    "CWE-89": "Improper Neutralization of Special Elements used in an SQL Command (SQL Injection)",
    "CWE-352": "Cross-Site Request Forgery (CSRF)",
    "CWE-862": "Missing Authorization",
    "CWE-787": "Out-of-bounds Write",
    "CWE-22": "Improper Limitation of a Pathname to a Restricted Directory (Path Traversal)",
    "CWE-416": "Use After Free",
    "CWE-125": "Out-of-bounds Read",
    "CWE-78": "Improper Neutralization of Special Elements used in an OS Command (OS Command Injection)",
    "CWE-94": "Improper Control of Generation of Code (Code Injection)",
    "CWE-120": "Buffer Copy without Checking Size of Input (Classic Buffer Overflow)",
    "CWE-434": "Unrestricted Upload of File with Dangerous Type",
    "CWE-476": "NULL Pointer Dereference",
    "CWE-121": "Stack-based Buffer Overflow",
    "CWE-502": "Deserialization of Untrusted Data",
    "CWE-122": "Heap-based Buffer Overflow",
    "CWE-863": "Incorrect Authorization",
    "CWE-20": "Improper Input Validation",
    "CWE-284": "Improper Access Control",
    "CWE-200": "Exposure of Sensitive Information to an Unauthorized Actor",
    "CWE-306": "Missing Authentication for Critical Function",
    "CWE-918": "Server-Side Request Forgery (SSRF)",
    "CWE-77": "Improper Neutralization of Special Elements used in a Command (Command Injection)",
    "CWE-639": "Authorization Bypass Through User-Controlled Key",
    "CWE-770": "Allocation of Resources Without Limits or Throttling",
}


SARIF_VERSION = "2.1.0"

CURRENT_REFERENCE_BASELINE = {
    "verified_on": "2026-10-05",
    "python_reference": "3.14.8",
    "owasp": "OWASP Top 10:2025",
    "cwe": "CWE Top 25:2025",
    "iso": "ISO/IEC 25010:2023",
    "sari": "SARIF 2.1.0 + Approved Errata 01",
    "asvs": "OWASP ASVS 5.0.0 (reference; not a completeness claim)",
    "nist": "NIST SP 800-218 SSDF 1.1 final; 1.2 remains draft in the current NIST publication list",
}
LATEST_TOOL_REFERENCE = {
    "python": "3.14.8",
    "ruff": "0.16.10",
    "bandit": "1.9.4",
    "mypy": "2.4.0",
    "note": "Versions verified against public package/release pages on 2026-10-05; the script executes the installed local versions when external mode is enabled.",
}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_IGNORE = {
    ".git", ".hg", ".svn",
    "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".tox",
    ".venv", "venv", "env", "myenv",
    "node_modules", "site-packages",
    "dist", "build",
    "backups", "backup",
    ".idea", ".vscode",
}

DEFAULT_EXPECTED_DIRS = [
    "collectors",
    "handlers",
    "analyzers",
    "core",
    "tests",
]

DEFAULT_EXPECTED_FILES = [
    "config.py",
    "main.py",
    "login.py",
    "panel.py",
    "requirements.txt",
]

SECRET_NAME_RE = re.compile(
    r"(?:api[_-]?key|access[_-]?token|auth[_-]?token|secret|password|passwd|"
    r"private[_-]?key|client[_-]?secret|bot[_-]?token|bearer)",
    re.I,
)
ENV_ACCESS_RE = re.compile(
    r"(os\.environ|os\.getenv|dotenv|load_dotenv|settings\.[A-Z_]+|"
    r"_env_str|_env_secret|_env_int|_env_bool|_env_float|"
    r"getenv|environ\.get|"
    r"CONFIG\.get|CONFIG\[|config\.[A-Z_]+|"
    r"\b[A-Z_]+_CONFIG\.get|"
    r"\.get\(['\"])"
)

ABS_PATH_RE = re.compile(
    r"""(?:
        [A-Za-z]:[\\/][^'"]+
        |
        /(?:etc|var|home|root|opt|tmp|srv|usr|data)/[^'"]+
    )""",
    re.X,
)

WEAK_CRYPTO_RE = re.compile(
    r"\b(?:hashlib\.md5|hashlib\.sha1|MD5|SHA1|DES|3DES|TripleDES|"
    r"Blowfish|ARC4|RC4)\b",
    re.I,
)


# 🔧 NEW: فقط فراخوانی واقعی — نه رشته‌ها و کامنت‌ها
WEAK_CRYPTO_CALL_RE = re.compile(
    r"(?:"
    r"hashlib\.(?:md5|sha1)\s*\("
    r"|"
    r"\b(?:MD5|SHA1|DES|3DES|TripleDES|Blowfish|ARC4|RC4)\s*\("
    r")",
    re.I,
)
DANGEROUS_CALLS = {
    "eval": ("CWE-94", "A05"),
    "exec": ("CWE-94", "A05"),
    "compile": ("CWE-94", "A05"),
    "input": (None, None),  # handled separately as a usability/info rule
}

SHELL_CALL_RE = re.compile(
    r"\b(?:subprocess\.(?:run|Popen|call|check_call|check_output)|os\.(?:system|popen))\b"
)

SQL_METHODS = {
    "execute",
    "executemany",
    "executescript",
    "raw",
    "rawsql",
}

SQL_WORDS_RE = re.compile(
    r"\b(?:SELECT|INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|UNION|EXEC|EXECUTE)\b",
    re.I,
)

USER_INPUT_HINTS = (
    "input", "argv", "request", "query", "param", "params", "form", "user",
    "filename", "path", "url", "host", "target",
)

HTTP_CALLS = {
    "get", "post", "put", "patch", "delete", "head", "options",
    "request", "urlopen",
}

SENSITIVE_LOG_RE = re.compile(
    r"\b(?:password|passwd|token|secret|api[_-]?key|authorization|cookie)\b",
    re.I,
)


# Deep / professor-grade patterns. These are deliberately conservative and emit
# medium/low confidence when static evidence cannot prove exploitability.
NETWORK_MODULES = {"requests", "httpx", "urllib", "urllib3", "aiohttp", "http.client", "socket"}
BLOCKING_IN_ASYNC = {
    "requests.get", "requests.post", "requests.put", "requests.patch", "requests.delete",
    "requests.request", "time.sleep", "subprocess.run", "subprocess.Popen", "os.system",
    "open",
}

INSECURE_FILE_CALLS = {"mktemp", "NamedTemporaryFile"}

SENSITIVE_VALUE_HINTS = (
    "password", "passwd", "token", "secret", "api_key", "apikey",
    "authorization", "cookie", "session", "private_key", "client_secret",
)

WEB_FRAMEWORK_MODULES = {"fastapi", "flask", "django", "starlette", "sanic", "quart"}

PYPROJECT_NAMES = ("pyproject.toml", "requirements.txt", "requirements.in", "constraints.txt", "Pipfile", "Pipfile.lock", "poetry.lock", "uv.lock")


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class Finding:
    finding_id: str
    rule_id: str
    severity: str
    confidence: str
    status: str
    category: str
    iso25010: str
    cwe: Optional[str]
    owasp: Optional[str]
    file: str
    line_start: int
    line_end: int
    symbol: str
    title: str
    message: str
    impact: str
    fix: str
    code: str
    evidence: str
    fingerprint: str
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FileStats:
    path: str
    lines: int = 0
    functions: int = 0
    classes: int = 0
    parse_ok: bool = True
    parse_error: Optional[str] = None
    findings: int = 0
    syntax_errors: int = 0
    max_mccabe: int = 1
    max_cognitive: int = 0
    min_mi: float = 100.0


@dataclass
class ScanConfig:
    root: Path
    include_tests: bool = True
    min_confidence: str = "low"
    show_code: bool = True
    context_lines: int = 2
    max_findings: int = 0
    run_tests: bool = False
    run_external: bool = False
    strict: bool = False
    deep: bool = True
    dependency_audit: bool = False
    runtime_smoke: bool = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CONFIDENCE_ORDER = {"low": 1, "medium": 2, "high": 3}
SEVERITY_ORDER = {"critical": 5, "high": 4, "medium": 3, "low": 2, "info": 1}

PENALTIES = {
    "critical": 1.0,
    "high": 0.55,
    "medium": 0.20,
    "low": 0.05,
    "info": 0.0,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_package_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def safe_rel(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def line_count(src: str) -> int:
    return len(src.splitlines())


def source_line(src: str, line_no: int) -> str:
    lines = src.splitlines()
    if 1 <= line_no <= len(lines):
        return lines[line_no - 1]
    return ""


def source_context(src: str, start: int, end: int, radius: int = 2) -> str:
    lines = src.splitlines()
    a = max(1, start - radius)
    b = min(len(lines), end + radius)
    out = []
    for i in range(a, b + 1):
        marker = ">>" if start <= i <= end else "  "
        out.append(f"{marker} {i:5d} | {lines[i - 1]}")
    return "\n".join(out)


def fingerprint(*parts: Any) -> str:
    raw = "\x1f".join(str(x) for x in parts)
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:20]


def iter_python_files(root: Path, include_tests: bool = True) -> Iterator[Path]:
    if not root.exists():
        return
    for path in sorted(root.rglob("*.py")):
        parts = set(path.parts)
        if parts & DEFAULT_IGNORE:
            continue
        rel = safe_rel(path, root).lower()
        if not include_tests and (
            rel.startswith("tests/") or "/tests/" in rel or rel.startswith("test_")
        ):
            continue
        if path.is_file():
            yield path


def parse_tree(path: Path) -> tuple[Optional[ast.AST], Optional[str]]:
    try:
        src = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            src = path.read_text(encoding="utf-8-sig")
        except Exception as exc:
            return None, f"decode error: {exc}"
    try:
        return ast.parse(src, filename=str(path), type_comments=True), None
    except SyntaxError as exc:
        return None, f"{exc.msg} (line {exc.lineno}, column {exc.offset})"
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def read_source(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8-sig", errors="replace")


def iter_functions(tree: ast.AST) -> Iterator[ast.FunctionDef | ast.AsyncFunctionDef]:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def iter_classes(tree: ast.AST) -> Iterator[ast.ClassDef]:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            yield node


def node_end_lineno(node: ast.AST) -> int:
    return getattr(node, "end_lineno", getattr(node, "lineno", 1))


def qualified_symbol(tree: ast.AST, target_line: int) -> str:
    best: list[tuple[int, str]] = []

    def walk(node: ast.AST, parents: list[str]) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            name = ".".join(parents + [node.name])
            if getattr(node, "lineno", 10**9) <= target_line <= node_end_lineno(node):
                best.append((node_end_lineno(node) - getattr(node, "lineno", 1), name))
            parents = parents + [node.name]
        for child in ast.iter_child_nodes(node):
            walk(child, parents)

    walk(tree, [])
    if not best:
        return "<module>"
    return min(best, key=lambda item: item[0])[1]


def import_map(tree: ast.AST) -> tuple[dict[str, str], set[str]]:
    aliases: dict[str, str] = {}
    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                aliases[local] = alias.name
                imported_names.add(local)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    continue
                local = alias.asname or alias.name
                aliases[local] = f"{node.module or ''}.{alias.name}".strip(".")
                imported_names.add(local)
    return aliases, imported_names


def names_used(tree: ast.AST) -> set[str]:
    return {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }


def assigned_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
    return names


def decorators_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    out: set[str] = set()
    for dec in node.decorator_list:
        for n in ast.walk(dec):
            if isinstance(n, ast.Name):
                out.add(n.id)
    return out


def body_node_count(node: ast.AST) -> int:
    return sum(1 for _ in ast.walk(node))


def cyclomatic(node: ast.AST) -> int:
    cc = 1
    for child in ast.walk(node):
        if isinstance(child, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler,
                              ast.With, ast.AsyncWith)):
            cc += 1
        elif isinstance(child, ast.BoolOp):
            cc += max(0, len(child.values) - 1)
        elif isinstance(child, (ast.Assert, ast.Lambda)):
            cc += 1
        elif isinstance(child, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            cc += 1
            for gen in child.generators:
                cc += len(gen.ifs)
        elif isinstance(child, ast.Match):
            cc += max(0, len(child.cases) - 1)
    return cc


def max_nesting(node: ast.AST) -> int:
    def rec(cur: ast.AST, depth: int) -> int:
        structural = isinstance(cur, (ast.If, ast.For, ast.AsyncFor, ast.While,
                                      ast.With, ast.AsyncWith, ast.Try, ast.ExceptHandler,
                                      ast.Match))
        next_depth = depth + 1 if structural else depth
        best = next_depth
        for child in ast.iter_child_nodes(cur):
            best = max(best, rec(child, next_depth))
        return best
    return rec(node, 0)


def cognitive(node: ast.AST) -> int:
    def rec(cur: ast.AST, depth: int) -> int:
        score = 0
        for child in ast.iter_child_nodes(cur):
            if isinstance(child, ast.If):
                score += 1 + depth
                if child.orelse:
                    score += 1
                score += rec(child, depth + 1)
            elif isinstance(child, (ast.For, ast.AsyncFor, ast.While)):
                score += 1 + depth
                score += rec(child, depth + 1)
            elif isinstance(child, (ast.Try, ast.ExceptHandler)):
                score += 1 + depth
                score += rec(child, depth + 1)
            elif isinstance(child, ast.BoolOp):
                score += max(0, len(child.values) - 1)
                score += rec(child, depth)
            elif isinstance(child, (ast.Break, ast.Continue)):
                score += 1 + depth
                score += rec(child, depth)
            elif isinstance(child, ast.Match):
                score += max(0, len(child.cases) - 1) + depth
                score += rec(child, depth + 1)
            else:
                score += rec(child, depth)
        return score
    return rec(node, 0)


def halstead(tree: ast.AST) -> dict[str, float]:
    operators = Counter()
    operands = Counter()
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp):
            operators[type(node.op).__name__] += 1
        elif isinstance(node, ast.UnaryOp):
            operators[type(node.op).__name__] += 1
        elif isinstance(node, ast.BoolOp):
            operators[type(node.op).__name__] += 1
        elif isinstance(node, ast.Compare):
            for op in node.ops:
                operators[type(op).__name__] += 1
        elif isinstance(node, ast.Call):
            operators["Call"] += 1
        elif isinstance(node, ast.Return):
            operators["Return"] += 1
        elif isinstance(node, ast.Assign):
            operators["Assign"] += 1
        elif isinstance(node, ast.AnnAssign):
            operators["AnnAssign"] += 1
        elif isinstance(node, ast.Name):
            operands[node.id] += 1
        elif isinstance(node, ast.Constant):
            value = repr(node.value)
            if len(value) > 40:
                value = value[:40]
            operands[value] += 1

    n1, n2 = len(operators), len(operands)
    N1, N2 = sum(operators.values()), sum(operands.values())
    vocabulary = n1 + n2
    length = N1 + N2
    volume = length * math.log2(vocabulary) if vocabulary > 0 else 0.0
    difficulty = ((n1 / 2.0) * (N2 / n2)) if n2 else 0.0
    effort = volume * difficulty
    return {
        "n1": float(n1), "n2": float(n2), "N1": float(N1), "N2": float(N2),
        "volume": float(volume), "difficulty": float(difficulty),
        "effort": float(effort),
    }


def maintainability_index(node: ast.AST, loc: int, cc: int) -> float:
    metrics = halstead(node)
    volume = max(1.0, metrics["volume"])
    loc = max(2, loc)
    raw = 171.0 - 5.2 * math.log(volume) - 0.23 * cc - 16.2 * math.log(loc)
    return round(max(0.0, raw * 100.0 / 171.0), 1)


def is_test_file(rel: str) -> bool:
    p = rel.lower()
    return (
        p.startswith("tests/")
        or "/tests/" in p
        or Path(p).name.startswith("test_")
    )


def is_generated_or_migration(rel: str) -> bool:
    p = rel.lower()
    return any(x in p for x in ("/migrations/", "/generated/", "/vendor/"))


# ---------------------------------------------------------------------------
# Finding collector
# ---------------------------------------------------------------------------

class Collector:
    def __init__(self, config: ScanConfig):
        self.config = config
        self.findings: list[Finding] = []
        self._seen: set[tuple[str, str, int, str, str]] = set()
        self.files: dict[str, FileStats] = {}

    def add(
        self,
        *,
        rule_id: str,
        severity: str,
        confidence: str,
        category: str,
        iso25010: str,
        cwe: Optional[str],
        owasp: Optional[str],
        file: str,
        line_start: int,
        line_end: Optional[int] = None,
        symbol: str = "<module>",
        title: str,
        message: str,
        impact: str,
        fix: str,
        code: str = "",
        evidence: str = "",
        extra: Optional[dict[str, Any]] = None,
    ) -> None:
        line_end = line_end or line_start
        if CONFIDENCE_ORDER.get(confidence, 1) < CONFIDENCE_ORDER.get(self.config.min_confidence, 1):
            return
        code_norm = re.sub(r"\s+", " ", code.strip())
        key = (rule_id, file, line_start, symbol, code_norm)
        if key in self._seen:
            return
        self._seen.add(key)

        fid = f"F-{len(self.findings)+1:05d}"
        fp = fingerprint(rule_id, file, line_start, symbol, code_norm)
        finding = Finding(
            finding_id=fid,
            rule_id=rule_id,
            severity=severity,
            confidence=confidence,
            status="open",
            category=category,
            iso25010=iso25010,
            cwe=cwe,
            owasp=owasp,
            file=file,
            line_start=line_start,
            line_end=line_end,
            symbol=symbol,
            title=title,
            message=message,
            impact=impact,
            fix=fix,
            code=code,
            evidence=evidence,
            fingerprint=fp,
            extra=extra or {},
        )
        self.findings.append(finding)

    def sort_findings(self) -> None:
        self.findings.sort(
            key=lambda f: (
                -SEVERITY_ORDER.get(f.severity, 0),
                f.file,
                f.line_start,
                f.rule_id,
            )
        )
        # Re-number after sorting.
        for i, finding in enumerate(self.findings, 1):
            finding.finding_id = f"F-{i:05d}"

    def cap(self) -> None:
        if self.config.max_findings > 0 and len(self.findings) > self.config.max_findings:
            self.findings = self.findings[: self.config.max_findings]

    def summary(self) -> dict[str, Any]:
        severity = Counter(f.severity for f in self.findings)
        rules = Counter(f.rule_id for f in self.findings)
        files = Counter(f.file for f in self.findings)
        categories = Counter(f.iso25010 for f in self.findings)
        cwes = Counter(f.cwe for f in self.findings if f.cwe)
        owasp = Counter(f.owasp for f in self.findings if f.owasp)
        return {
            "findings": len(self.findings),
            "severity": dict(severity),
            "rules": dict(rules),
            "files": dict(files),
            "iso25010": dict(categories),
            "cwe": dict(cwes),
            "owasp2025": dict(owasp),
        }


# ---------------------------------------------------------------------------
# Rules: syntax / project structure
# ---------------------------------------------------------------------------

def rule_project_structure(root: Path, c: Collector) -> None:
    for name in DEFAULT_EXPECTED_DIRS:
        p = root / name
        if not p.exists():
            c.add(
                rule_id="STRUCT-MISSING-DIR",
                severity="medium",
                confidence="high",
                category="functional",
                iso25010="functional_suitability",
                cwe=None,
                owasp=None,
                file="<project>",
                line_start=1,
                symbol="<project>",
                title=f"ساختار مورد انتظار وجود ندارد: {name}/",
                message=f"دایرکتوری مورد انتظار {name}/ در ریشه پروژه پیدا نشد.",
                impact="بخشی از معماری مورد انتظار قابل تأیید نیست یا ساختار پروژه تغییر کرده است.",
                fix=f"در صورت نیاز به این ماژول، ساختار {name}/ را ایجاد و وابستگی‌های مربوط را بررسی کنید.",
                evidence=str(p),
            )
    for name in DEFAULT_EXPECTED_FILES:
        p = root / name
        if not p.exists():
            c.add(
                rule_id="STRUCT-MISSING-FILE",
                severity="low",
                confidence="medium",
                category="functional",
                iso25010="functional_suitability",
                cwe=None,
                owasp=None,
                file="<project>",
                line_start=1,
                symbol="<project>",
                title=f"فایل مورد انتظار پیدا نشد: {name}",
                message=f"فایل {name} در ریشه پروژه موجود نیست.",
                impact="ممکن است بررسی پیکربندی/اجرا ناقص باشد.",
                fix=f"بررسی کنید {name} عمداً حذف یا به مکان دیگری منتقل نشده باشد.",
                evidence=str(p),
            )


def rule_syntax(path: Path, rel: str, src: str, tree: Optional[ast.AST],
                error: Optional[str], c: Collector) -> None:
    if tree is not None:
        return
    line = 1
    m = re.search(r"line\s+(\d+)", error or "")
    if m:
        line = int(m.group(1))
    c.add(
        rule_id="PY-SYNTAX",
        severity="critical",
        confidence="high",
        category="functional",
        iso25010="functional_suitability",
        cwe="CWE-20",
        owasp="A10",
        file=rel,
        line_start=line,
        symbol="<module>",
        title="خطای syntax/parse",
        message=f"فایل Python قابل parse شدن نیست: {error}",
        impact="ماژول ممکن است اصلاً import/execute نشود و کل مسیرهای وابسته از کار بیفتند.",
        fix="Syntax error را دقیقاً در خط مشخص‌شده رفع کنید و سپس فایل را دوباره با AST/compile بررسی کنید.",
        code=source_line(src, line),
        evidence=error or "",
    )


# ---------------------------------------------------------------------------
# Rules: standards / documentation
# ---------------------------------------------------------------------------

def rule_type_hints_docs(tree: ast.AST, rel: str, src: str, c: Collector) -> None:
    for node in iter_functions(tree):
        if node.name.startswith("__") and node.name.endswith("__"):
            continue
        missing_return = node.returns is None
        args = [
            a for a in (
                list(node.args.posonlyargs) +
                list(node.args.args) +
                list(node.args.kwonlyargs)
            )
            if a.arg not in {"sel", "cls"}
        ]
        missing_args = [a.arg for a in args if a.annotation is None]
        if missing_return or missing_args:
            missing = []
            if missing_args:
                missing.append("args=" + ",".join(missing_args))
            if missing_return:
                missing.append("return")
            c.add(
                rule_id="STD-TYPE-HINTS",
                severity="low",
                confidence="high",
                category="maintainability",
                iso25010="maintainability",
                cwe=None,
                owasp=None,
                file=rel,
                line_start=node.lineno,
                line_end=node.lineno,
                symbol=node.name,
                title="type hint ناقص",
                message=f"امضای تابع {node.name} ناقص است: {', '.join(missing)}",
                impact="خوانایی، static checking و نگهداری دشوارتر می‌شود.",
                fix="برای آرگومان‌ها و return annotation دقیق اضافه کنید؛ برای Any غیرضروری استفاده نکنید.",
                code=source_line(src, node.lineno),
                evidence=", ".join(missing),
            )

        if ast.get_docstring(node) is None and not node.name.startswith("_"):
            c.add(
                rule_id="DOC-MISSING-FUNCTION",
                severity="info",
                confidence="high",
                category="interaction_capability",
                iso25010="interaction_capability",
                cwe=None,
                owasp=None,
                file=rel,
                line_start=node.lineno,
                symbol=node.name,
                title="تابع public بدون docstring",
                message=f"تابع public {node.name} مستندات داخلی ندارد.",
                impact="استفاده و نگهداری API داخلی سخت‌تر می‌شود.",
                fix="یک docstring کوتاه و دقیق شامل ورودی، خروجی، side-effect و خطاهای مهم اضافه کنید.",
                code=source_line(src, node.lineno),
            )

    if ast.get_docstring(tree) is None:
        c.add(
            rule_id="DOC-MISSING-MODULE",
            severity="info",
            confidence="high",
            category="interaction_capability",
            iso25010="interaction_capability",
            cwe=None,
            owasp=None,
            file=rel,
            line_start=1,
            symbol="<module>",
            title="module docstring ندارد",
            message="ماژول docstring سطح فایل ندارد.",
            impact="هدف و قرارداد ماژول برای نگهدارنده بعدی شفاف نیست.",
            fix="در بالای فایل یک docstring شامل مسئولیت، وابستگی اصلی و side-effectهای مهم قرار دهید.",
            code=source_line(src, 1),
        )


# ---------------------------------------------------------------------------
# Rules: imports / names / cleanliness
# ---------------------------------------------------------------------------

def rule_imports_and_names(tree: ast.AST, rel: str, src: str, c: Collector) -> None:
    aliases, imported = import_map(tree)
    used = names_used(tree)

    for local, original in aliases.items():
        if local not in used:
            c.add(
                rule_id="CLEAN-UNUSED-IMPORT",
                severity="low",
                confidence="high",
                category="maintainability",
                iso25010="maintainability",
                cwe=None,
                owasp=None,
                file=rel,
                line_start=1,
                symbol="<module>",
                title=f"import استفاده‌نشده: {local}",
                message=f"import مربوط به {original} وارد شده ولی در AST به عنوان Name استفاده‌شده دیده نمی‌شود.",
                impact="کد شلوغ‌تر، startup/import graph سنگین‌تر و نگهداری سخت‌تر می‌شود.",
                fix=f"اگر واقعاً لازم نیست import {original} را حذف کنید؛ اگر dynamic use دارد، false positive را بازبینی کنید.",
                code=source_line(src, _find_import_line(tree, local)),
                evidence=f"{local} <- {original}",
            )

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names):
            c.add(
                rule_id="CLEAN-STAR-IMPORT",
                severity="low",
                confidence="high",
                category="maintainability",
                iso25010="maintainability",
                cwe=None,
                owasp=None,
                file=rel,
                line_start=node.lineno,
                symbol="<module>",
                title="star import",
                message="از import * استفاده شده است.",
                impact="namespace نامشخص می‌شود و تشخیص وابستگی واقعی سخت‌تر می‌گردد.",
                fix="نام‌های موردنیاز را صریح import کنید.",
                code=source_line(src, node.lineno),
            )


def _find_import_line(tree: ast.AST, local: str) -> int:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name.split(".")[0]
                if name == local:
                    return node.lineno
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                name = alias.asname or alias.name
                if name == local:
                    return node.lineno
    return 1


def rule_unused_args(tree: ast.AST, rel: str, src: str, c: Collector) -> None:
    for node in iter_functions(tree):
        used = names_used(node)
        for arg in list(node.args.args) + list(node.args.kwonlyargs) + list(node.args.posonlyargs):
            if arg.arg in {"sel", "cls"} or arg.arg.startswith("_"):
                continue
            if arg.arg not in used:
                c.add(
                    rule_id="CLEAN-UNUSED-ARG",
                    severity="low",
                    confidence="medium",
                    category="maintainability",
                    iso25010="maintainability",
                    cwe=None,
                    owasp=None,
                    file=rel,
                    line_start=arg.lineno,
                    symbol=node.name,
                    title=f"آرگومان استفاده‌نشده: {arg.arg}",
                    message=f"آرگومان {arg.arg} در بدنه تابع {node.name} استفاده نشده است.",
                    impact="API تابع مبهم‌تر و refactor دشوارتر می‌شود؛ گاهی نشانه طراحی قدیمی است.",
                    fix="در صورت عدم نیاز حذف کنید؛ اگر قرارداد خارجی آن را لازم می‌کند، نام را با _ شروع کنید یا علت را مستند کنید.",
                    code=source_line(src, arg.lineno),
                )


# ---------------------------------------------------------------------------
# Rules: logic / exceptions / defaults
# ---------------------------------------------------------------------------

def rule_exceptions_defaults(tree: ast.AST, rel: str, src: str, c: Collector) -> None:
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if node.type is None:
                c.add(
                    rule_id="LOGIC-BARE-EXCEPT",
                    severity="high",
                    confidence="high",
                    category="reliability",
                    iso25010="reliability",
                    cwe="CWE-396",
                    owasp="A10",
                    file=rel,
                    line_start=node.lineno,
                    symbol=qualified_symbol(tree, node.lineno),
                    title="bare except",
                    message="except بدون نوع خطا استفاده شده است.",
                    impact="KeyboardInterrupt/SystemExit و خطاهای غیرمنتظره می‌توانند بلعیده شوند و تشخیص خرابی سخت شود.",
                    fix="نوع exception را محدود کنید؛ در سطح مرزی از Exception استفاده و آن را log/raise کنید.",
                    code=source_context(src, node.lineno, node.lineno, 1),
                )
            elif isinstance(node.type, ast.Name) and node.type.id == "Exception" and not _handler_re_raises(node):
                c.add(
                    rule_id="LOGIC-BROAD-EXCEPT-SWALLOW",
                    severity="medium",
                    confidence="high",
                    category="reliability",
                    iso25010="reliability",
                    cwe="CWE-703",
                    owasp="A10",
                    file=rel,
                    line_start=node.lineno,
                    symbol=qualified_symbol(tree, node.lineno),
                    title="except Exception احتمالاً خطا را می‌بلعد",
                    message="except Exception پیدا شد و بدنه آن نه raise/re-raise روشن دارد و نه الگوی logging تشخیص داده شد.",
                    impact="خطاها ممکن است بی‌صدا ناپدید شوند و reliability و observability کاهش یابد.",
                    fix="exceptionهای مورد انتظار را محدود کنید یا حداقل logging ساختاریافته + raise/return کنترل‌شده داشته باشید.",
                    code=source_context(src, node.lineno, node_end_lineno(node), 1),
                )

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            all_defaults = list(node.args.defaults) + [d for d in node.args.kw_defaults if d is not None]
            for default in all_defaults:
                if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                    c.add(
                        rule_id="LOGIC-MUTABLE-DEFAULT",
                        severity="medium",
                        confidence="high",
                        category="reliability",
                        iso25010="reliability",
                        cwe="CWE-665",
                        owasp="A10",
                        file=rel,
                        line_start=default.lineno,
                        symbol=node.name,
                        title="mutable default argument",
                        message="لیست/دیکشنری/ست mutable به عنوان مقدار پیش‌فرض تابع استفاده شده است.",
                        impact="state بین فراخوانی‌های مختلف می‌تواند ناخواسته به اشتراک گذاشته شود.",
                        fix="از None یا immutable default استفاده کنید و مقدار جدید را داخل تابع بسازید.",
                        code=source_line(src, default.lineno),
                    )


def _handler_re_raises(node: ast.ExceptHandler) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Raise):
            return True
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
            if child.func.attr in {"exception", "error", "warning", "critical", "logger"}:
                return True
    return False


# ---------------------------------------------------------------------------
# Rules: dangerous calls / command injection
# ---------------------------------------------------------------------------

def rule_dangerous_calls(tree: ast.AST, rel: str, src: str, c: Collector) -> None:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            name = node.func.id
            if name in {"eval", "exec", "compile"}:
                c.add(
                    rule_id="SEC-DYNAMIC-CODE",
                    severity="critical",
                    confidence="high",
                    category="security",
                    iso25010="security",
                    cwe="CWE-94",
                    owasp="A05",
                    file=rel,
                    line_start=node.lineno,
                    symbol=qualified_symbol(tree, node.lineno),
                    title=f"اجرای dynamic code با {name}()",
                    message=f"{name}() مستقیماً در کد استفاده شده است.",
                    impact="در صورت کنترل‌شدن ورودی، مهاجم می‌تواند به code execution برسد.",
                    fix="dynamic execution را حذف کنید؛ داده را parse/validate کنید و از APIهای امن به جای eval/exec استفاده کنید.",
                    code=source_line(src, node.lineno),
                )

        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            method = node.func.attr
            if method in {"system", "popen"} and isinstance(node.func.value, ast.Name) and node.func.value.id == "os":
                c.add(
                    rule_id="SEC-OS-COMMAND",
                    severity="high",
                    confidence="high",
                    category="security",
                    iso25010="security",
                    cwe="CWE-78",
                    owasp="A05",
                    file=rel,
                    line_start=node.lineno,
                    symbol=qualified_symbol(tree, node.lineno),
                    title="OS command execution",
                    message=f"os.{method} برای اجرای دستور سیستم استفاده شده است.",
                    impact="ورودی غیرقابل‌اعتماد می‌تواند به OS Command Injection منجر شود.",
                    fix="از APIهای آرگومان‌محور subprocess بدون shell استفاده کنید و ورودی را allowlist/validate کنید.",
                    code=source_line(src, node.lineno),
                )
            if method in {"run", "Popen", "call", "check_call", "check_output"}:
                shell_true = any(
                    isinstance(k, ast.keyword)
                    and k.arg == "shell"
                    and isinstance(k.value, ast.Constant)
                    and k.value.value is True
                    for k in node.args
                )
                if shell_true:
                    c.add(
                        rule_id="SEC-SUBPROCESS-SHELL",
                        severity="high",
                        confidence="high",
                        category="security",
                        iso25010="security",
                        cwe="CWE-78",
                        owasp="A05",
                        file=rel,
                        line_start=node.lineno,
                        symbol=qualified_symbol(tree, node.lineno),
                        title="subprocess با shell=True",
                        message="اجرای subprocess با shell=True شناسایی شد.",
                        impact="ترکیب آن با داده بیرونی ریسک command injection را بالا می‌برد.",
                        fix="shell=True را حذف کنید و command + args را به صورت list/pass-through امن ارسال کنید.",
                        code=source_line(src, node.lineno),
                    )


# ---------------------------------------------------------------------------
# Rules: SQL injection
# ---------------------------------------------------------------------------

def rule_sql_injection(tree: ast.AST, rel: str, src: str, c: Collector) -> None:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        method = node.func.attr
        if method not in SQL_METHODS:
            continue

        receiver = ast.unparse(node.func.value) if hasattr(ast, "unparse") else ""
        receiver_l = receiver.lower()
        receiver_sqlish = any(k in receiver_l for k in ("cursor", "conn", "connection", "db", "session", "engine"))

        if not receiver_sqlish and method != "rawsql":
            continue

        if not node.args:
            continue

        query = node.args[0]
        if isinstance(query, ast.JoinedStr):
            c.add(
                rule_id="SEC-SQL-FSTRING",
                severity="critical",
                confidence="high",
                category="security",
                iso25010="security",
                cwe="CWE-89",
                owasp="A05",
                file=rel,
                line_start=node.lineno,
                symbol=qualified_symbol(tree, node.lineno),
                title="SQL query با f-string",
                message="SQL execution با f-string و interpolation انجام شده است.",
                impact="اگر بخشی از interpolation قابل کنترل باشد، SQL Injection محتمل است.",
                fix="از parameterized query استفاده کنید: execute('... WHERE id = ?', (value,)).",
                code=source_context(src, node.lineno, node_end_lineno(node), 1),
            )
        elif isinstance(query, ast.BinOp) and isinstance(query.op, ast.Add):
            c.add(
                rule_id="SEC-SQL-CONCAT",
                severity="critical",
                confidence="high",
                category="security",
                iso25010="security",
                cwe="CWE-89",
                owasp="A05",
                file=rel,
                line_start=node.lineno,
                symbol=qualified_symbol(tree, node.lineno),
                title="SQL query با string concatenation",
                message="SQL statement با + ساخته شده است.",
                impact="ورودی بیرونی می‌تواند ساختار query را تغییر دهد.",
                fix="parameterized query یا query builder امن استفاده کنید؛ ورودی را داخل SQL string concatenate نکنید.",
                code=source_context(src, node.lineno, node_end_lineno(node), 1),
            )
        elif isinstance(query, ast.Constant) and isinstance(query.value, str):
            if SQL_WORDS_RE.search(query.value):
                placeholder = any(x in query.value for x in ("?", "%s", ":"))
                if not placeholder:
                    c.add(
                        rule_id="SEC-SQL-LITERAL",
                        severity="high",
                        confidence="medium",
                        category="security",
                        iso25010="security",
                        cwe="CWE-89",
                        owasp="A05",
                        file=rel,
                        line_start=node.lineno,
                        symbol=qualified_symbol(tree, node.lineno),
                        title="SQL literal بدون placeholder",
                        message="query ثابت است ولی placeholder پارامتری پیدا نشد.",
                        impact="این مورد به خودی خود injection قطعی نیست، اما در صورت اضافه‌شدن داده dynamic در refactor، ریسک افزایش می‌یابد.",
                        fix="برای queryهای دارای داده بیرونی از parameter binding استفاده کنید.",
                        code=source_line(src, node.lineno),
                    )


# ---------------------------------------------------------------------------
# Rules: path traversal / SSRF / deserialization
# ---------------------------------------------------------------------------

def rule_path_traversal(tree: ast.AST, rel: str, src: str, c: Collector) -> None:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
            for arg in node.args[:1]:
                if isinstance(arg, ast.JoinedStr):
                    names = [
                        v.value.id.lower()
                        for v in arg.values
                        if isinstance(v, ast.FormattedValue) and isinstance(v.value, ast.Name)
                    ]
                    if any(any(h in n for h in USER_INPUT_HINTS) for n in names):
                        c.add(
                            rule_id="SEC-PATH-TRAVERSAL",
                            severity="high",
                            confidence="high",
                            category="security",
                            iso25010="security",
                            cwe="CWE-22",
                            owasp="A05",
                            file=rel,
                            line_start=node.lineno,
                            symbol=qualified_symbol(tree, node.lineno),
                            title="مسیر فایل با input کاربر ساخته می‌شود",
                            message="open() از f-string حاوی variable مشکوک به input استفاده می‌کند.",
                            impact="ورودی مانند ../ می‌تواند دسترسی به فایل خارج از دایرکتوری مجاز ایجاد کند.",
                            fix="مسیر را با pathlib resolve و base-directory check کنترل کنید؛ allowlist نام فایل را ترجیح دهید.",
                            code=source_line(src, node.lineno),
                        )
                elif isinstance(arg, ast.BinOp) and isinstance(arg.op, ast.Add):
                    names = [
                        x.id.lower()
                        for x in ast.walk(arg)
                        if isinstance(x, ast.Name)
                    ]
                    if any(any(h in n for h in USER_INPUT_HINTS) for n in names):
                        c.add(
                            rule_id="SEC-PATH-TRAVERSAL",
                            severity="high",
                            confidence="medium",
                            category="security",
                            iso25010="security",
                            cwe="CWE-22",
                            owasp="A05",
                            file=rel,
                            line_start=node.lineno,
                            symbol=qualified_symbol(tree, node.lineno),
                            title="مسیر فایل با concatenation ورودی ساخته می‌شود",
                            message="open() مسیر را با + و variable مشکوک به input می‌سازد.",
                            impact="داده کنترل‌نشده ممکن است از محدوده فایل مجاز خارج شود.",
                            fix="مسیر را canonicalize و با base path مقایسه کنید؛ از filename allowlist استفاده کنید.",
                            code=source_line(src, node.lineno),
                        )


def rule_ssrf(tree: ast.AST, rel: str, src: str, c: Collector) -> None:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in HTTP_CALLS:
            continue
        if not isinstance(node.func.value, ast.Name):
            continue
        receiver = node.func.value.id.lower()
        if receiver not in {"requests", "httpx", "aiohttp", "urllib", "session", "client"}:
            continue
        if not node.args:
            continue
        url = node.args[0]
        if isinstance(url, (ast.Name, ast.JoinedStr, ast.BinOp, ast.Call)):
            c.add(
                rule_id="SEC-SSRF",
                severity="high",
                confidence="medium",
                category="security",
                iso25010="security",
                cwe="CWE-918",
                owasp="A05",
                file=rel,
                line_start=node.lineno,
                symbol=qualified_symbol(tree, node.lineno),
                title="URL بیرونی به درخواست HTTP داده شده است",
                message="URL request در runtime از expression ساخته/دریافت می‌شود و allowlist/host validation در AST قابل اثبات نیست.",
                impact="در صورت کنترل‌شدن host توسط کاربر، SSRF می‌تواند دسترسی به metadata/internal services ایجاد کند.",
                fix="scheme/host را allowlist کنید، private/link-local IPها را block کنید و redirect policy را کنترل کنید.",
                code=source_line(src, node.lineno),
            )


def rule_insecure_deserialization(tree: ast.AST, rel: str, src: str, c: Collector) -> None:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        call = ""
        if isinstance(node.func, ast.Attribute):
            try:
                call = ast.unparse(node.func)
            except Exception:
                call = ""
        if re.search(r"\b(?:pickle|dill|marshal)\.(?:load|loads)\b", call):
            c.add(
                rule_id="SEC-INSECURE-DESERIALIZATION",
                severity="critical",
                confidence="high",
                category="security",
                iso25010="security",
                cwe="CWE-502",
                owasp="A08",
                file=rel,
                line_start=node.lineno,
                symbol=qualified_symbol(tree, node.lineno),
                title="deserialization ناامن",
                message=f"فراخوانی {call} برای بارگذاری object شناسایی شد.",
                impact="اگر payload غیرقابل‌اعتماد باشد، deserialization می‌تواند به code execution یا compromise منجر شود.",
                fix="برای داده غیرقابل‌اعتماد از JSON/ساختارهای امن استفاده کنید؛ pickle را فقط برای داده کاملاً trusted به‌کار ببرید.",
                code=source_line(src, node.lineno),
            )
        if re.search(r"\byaml\.load\b", call):
            c.add(
                rule_id="SEC-YAML-LOAD",
                severity="high",
                confidence="high",
                category="security",
                iso25010="security",
                cwe="CWE-502",
                owasp="A08",
                file=rel,
                line_start=node.lineno,
                symbol=qualified_symbol(tree, node.lineno),
                title="yaml.load بدون loader امن",
                message="yaml.load() تشخیص داده شد؛ AST امن بودن Loader را تأیید نمی‌کند.",
                impact="YAML ناامن می‌تواند object construction و رفتار ناخواسته ایجاد کند.",
                fix="yaml.safe_load را ترجیح دهید؛ در صورت نیاز، SafeLoader را صریح مشخص کنید.",
                code=source_line(src, node.lineno),
            )


# ---------------------------------------------------------------------------
# Rules: secrets / crypto / logging
# ---------------------------------------------------------------------------

def rule_secrets_crypto_logging(tree: ast.AST, rel: str, src: str, c: Collector) -> None:
    lines = src.splitlines()

    for i, line in enumerate(lines, 1):
        # 🔧 FIX: اگه noqa داره، رد کن
        if "# noqa" in line and "SEC-HARDCODED-SECRET" in line:
            continue
        if SECRET_NAME_RE.search(line) and "=" in line:
            rhs = line.split("=", 1)[1]
            # 🔧 FIX: اگه از env میاد، رد کن
            if ENV_ACCESS_RE.search(rhs):
                continue
            # 🔧 FIX: اگه string کوتاهه یا مسیر فایله، رد کن
            match = re.search(r"""['"]([A-Za-z0-9_\-./+=]{8,})['"]""", rhs)
            if not match:
                continue
            val = match.group(1)
            # اگه شبیه مسیر فایل یا URL یا تنظیمات باشه، رد کن
            if any(x in val.lower() for x in ("/", "\\", "http", ".json", ".py", ".txt", ".db")):
                continue
            # 🔧 FIX: c.add حالا داخل شرط‌هاست — فقط برای secret واقعی
            c.add(
                rule_id="SEC-HARDCODED-SECRET",
                severity="critical",
                confidence="medium",
                category="security",
                iso25010="security",
                cwe="CWE-798",
                owasp="A04",
                file=rel,
                line_start=i,
                symbol=qualified_symbol(tree, i),
                title="احتمال secret hardcoded",
                message="متغیر با نام حساس مقدار string literal نسبتاً طولانی دارد.",
                impact="secret ممکن است وارد git، backup، log و artifactهای build شود.",
                fix="secret را به environment/secret manager منتقل کنید؛ secret فعلی را در صورت افشا rotate کنید.",
                code=line,
                evidence=rhs.strip(),
            )

    for i, line in enumerate(lines, 1):
        # 🔧 FIX: کامنت‌ها رو رد کن
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        # 🔧 FIX: docstringها رو رد کن (تقریب)
        if stripped.startswith('"""') or stripped.startswith("'''"):
            continue
        if stripped.startswith("- ") or stripped.startswith("* "):
            continue
        # 🔧 FIX: noqa و usedforsecurity=False رو رد کن
        if "# noqa" in line and "SEC-WEAK-CRYPTO" in line:
            continue
        if "usedforsecurity=False" in line:
            continue
        # 🔧 FIX: فقط فراخوانی واقعی، نه رشته
        m = WEAK_CRYPTO_CALL_RE.search(line)
        if m:
            c.add(
                rule_id="SEC-WEAK-CRYPTO",
                severity="high",
                confidence="high",
                category="security",
                iso25010="security",
                cwe="CWE-327",
                owasp="A04",
                file=rel,
                line_start=i,
                symbol=qualified_symbol(tree, i),
                title=f"الگوریتم/primitive ضعیف: {m.group(0)}",
                message="استفاده از الگوریتم یا primitive قدیمی/نامناسب برای رمزنگاری یا hashing امنیتی شناسایی شد.",
                impact="ممکن است integrity/confidentiality در برابر حملات امروزی کافی نباشد.",
                fix="برای password از Argon2id/scrypt/bcrypt و برای cryptography از primitiveهای مدرن با تنظیمات مناسب استفاده کنید.",
                code=line,
            )

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in {"debug", "info", "warning", "error", "critical", "exception", "log"}:
            continue
        text = ""
        try:
            text = ast.unparse(node)
        except Exception:
            text = ""
        if SENSITIVE_LOG_RE.search(text):
            c.add(
                rule_id="SEC-SENSITIVE-LOG",
                severity="medium",
                confidence="medium",
                category="security",
                iso25010="security",
                cwe="CWE-200",
                owasp="A09",
                file=rel,
                line_start=node.lineno,
                symbol=qualified_symbol(tree, node.lineno),
                title="احتمال ثبت secret در log",
                message="عبارت logging شامل نام متغیرهای حساس مثل token/password/secret است.",
                impact="credential یا data حساس ممکن است وارد log و سیستم‌های مانیتورینگ شود.",
                fix="secret را redact/mask کنید و logging policy را برای اطلاعات حساس enforce کنید.",
                code=source_line(src, node.lineno),
            )

# ---------------------------------------------------------------------------
# Rules: complexity / maintainability / architecture
# ---------------------------------------------------------------------------

def rule_complexity(tree: ast.AST, rel: str, src: str, c: Collector) -> FileStats:
    fs = FileStats(path=rel, lines=line_count(src))
    funcs = list(iter_functions(tree))
    classes = list(iter_classes(tree))
    fs.functions = len(funcs)
    fs.classes = len(classes)

    for node in funcs:
        loc = max(1, node_end_lineno(node) - node.lineno + 1)
        cc = cyclomatic(node)
        cg = cognitive(node)
        mi = maintainability_index(node, loc, cc)
        fs.max_mccabe = max(fs.max_mccabe, cc)
        fs.max_cognitive = max(fs.max_cognitive, cg)
        fs.min_mi = min(fs.min_mi, mi)

        if loc > 80:
            c.add(
                rule_id="COMPLEX-LONG-FUNCTION",
                severity="medium",
                confidence="high",
                category="maintainability",
                iso25010="maintainability",
                cwe=None,
                owasp="A06",
                file=rel,
                line_start=node.lineno,
                line_end=node_end_lineno(node),
                symbol=node.name,
                title=f"تابع طولانی: {node.name} ({loc} خط)",
                message=f"تابع {node.name} از آستانه 80 خط عبور کرده است.",
                impact="تست، review، تغییر امن و درک جریان کنترل سخت‌تر می‌شود.",
                fix="تابع را بر اساس مسئولیت به helper/serviceهای کوچک‌تر تقسیم کنید؛ side-effectها را مرزی کنید.",
                code=source_context(src, node.lineno, node.lineno, 0),
                evidence=f"loc={loc}",
            )
        if cc > 15:
            c.add(
                rule_id="COMPLEX-MCCABE",
                severity="high" if cc > 25 else "medium",
                confidence="high",
                category="maintainability",
                iso25010="maintainability",
                cwe=None,
                owasp="A06",
                file=rel,
                line_start=node.lineno,
                line_end=node_end_lineno(node),
                symbol=node.name,
                title=f"Cyclomatic Complexity بالا: {cc}",
                message=f"CC تابع {node.name} برابر {cc} است.",
                impact="تعداد مسیرهای تست و احتمال branch defect به‌شدت بالا می‌رود.",
                fix="شرط‌های تو در تو را به strategy/table/guard clause تبدیل و منطق را به توابع کوچک‌تر تفکیک کنید.",
                code=source_line(src, node.lineno),
                evidence=f"cc={cc}",
            )
        if cg > 20:
            c.add(
                rule_id="COMPLEX-COGNITIVE",
                severity="medium",
                confidence="high",
                category="maintainability",
                iso25010="maintainability",
                cwe=None,
                owasp="A06",
                file=rel,
                line_start=node.lineno,
                line_end=node_end_lineno(node),
                symbol=node.name,
                title=f"Cognitive Complexity بالا: {cg}",
                message=f"Cognitive complexity تابع {node.name} برابر {cg} است.",
                impact="درک و نگهداری ذهنی تابع سخت می‌شود و review کیفیت پایین‌تری خواهد داشت.",
                fix="nesting را کم کنید، early return استفاده کنید و branchهای مستقل را استخراج کنید.",
                code=source_line(src, node.lineno),
                evidence=f"cognitive={cg}",
            )
        if mi < 30:
            c.add(
                rule_id="COMPLEX-LOW-MI",
                severity="medium",
                confidence="high",
                category="maintainability",
                iso25010="maintainability",
                cwe=None,
                owasp="A06",
                file=rel,
                line_start=node.lineno,
                line_end=node_end_lineno(node),
                symbol=node.name,
                title=f"Maintainability Index پایین: {mi}",
                message=f"MI تابع {node.name} برابر {mi} است.",
                impact="هزینه نگهداری و احتمال regression بالاتر می‌رود.",
                fix="حجم تابع، branching و coupling را کاهش دهید و naming/docstring را بهبود دهید.",
                code=source_line(src, node.lineno),
                evidence=f"mi={mi}",
            )

        depth = max_nesting(node)
        if depth > 5:
            c.add(
                rule_id="COMPLEX-DEEP-NESTING",
                severity="medium",
                confidence="high",
                category="maintainability",
                iso25010="maintainability",
                cwe=None,
                owasp="A06",
                file=rel,
                line_start=node.lineno,
                line_end=node_end_lineno(node),
                symbol=node.name,
                title=f"تو در تویی عمیق: depth={depth}",
                message=f"عمق ساختاری تابع {node.name} برابر {depth} است.",
                impact="cognitive load بالا می‌رود و branch interactionها سخت‌تر قابل تست می‌شوند.",
                fix="guard clause، استخراج helper و کاهش nesting را اعمال کنید.",
                code=source_line(src, node.lineno),
                evidence=f"depth={depth}",
            )

        arg_count = len(node.args.posonlyargs) + len(node.args.args) + len(node.args.kwonlyargs)
        if arg_count > 8:
            c.add(
                rule_id="COMPLEX-MANY-ARGS",
                severity="low",
                confidence="high",
                category="maintainability",
                iso25010="maintainability",
                cwe=None,
                owasp="A06",
                file=rel,
                line_start=node.lineno,
                symbol=node.name,
                title=f"پارامترهای زیاد: {arg_count}",
                message=f"تابع {node.name} تعداد {arg_count} پارامتر دارد.",
                impact="API ناپایدار و call-siteهای پیچیده می‌شوند.",
                fix="پارامترهای هم‌گروه را در dataclass/config object جمع کنید.",
                code=source_line(src, node.lineno),
                evidence=f"args={arg_count}",
            )

    for node in classes:
        methods = [
            child for child in node.body
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        if len(methods) > 25:
            c.add(
                rule_id="ARCH-GOD-CLASS",
                severity="high",
                confidence="high",
                category="maintainability",
                iso25010="maintainability",
                cwe=None,
                owasp="A06",
                file=rel,
                line_start=node.lineno,
                symbol=node.name,
                title=f"God Class: {node.name} ({len(methods)} method)",
                message=f"کلاس {node.name} بیش از 25 متد دارد.",
                impact="مسئولیت‌های متعدد، coupling و regression risk افزایش می‌یابد.",
                fix="کلاس را بر اساس aggregate responsibility به چند service/adapter/coordinator تقسیم کنید.",
                code=source_line(src, node.lineno),
                evidence=f"methods={len(methods)}",
            )

    return fs


# ---------------------------------------------------------------------------
# Rules: dead code / duplicates / public API
# ---------------------------------------------------------------------------

def rule_dead_code(tree: ast.AST, rel: str, src: str, c: Collector) -> None:
    top_funcs = [
        n for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not n.name.startswith("_")
    ]
    if not top_funcs:
        return

    used_names = names_used(tree)
    decorators = set()
    for n in iter_functions(tree):
        decorators.update(decorators_names(n))

    for node in top_funcs:
        if node.name not in used_names and node.name not in decorators:
            c.add(
                rule_id="CLEAN-DEAD-PUBLIC-FUNCTION",
                severity="low",
                confidence="medium",
                category="maintainability",
                iso25010="maintainability",
                cwe=None,
                owasp="A06",
                file=rel,
                line_start=node.lineno,
                line_end=node_end_lineno(node),
                symbol=node.name,
                title=f"تابع public بدون reference داخلی: {node.name}",
                message=f"تابع سطح ماژول {node.name} در همان AST فراخوانی نشده است.",
                impact="ممکن است dead code، API legacy یا entry point خارجی باشد؛ حذف کورکورانه خطرناک است.",
                fix="ابتدا مصرف‌کنندگان خارجی را بررسی کنید؛ در صورت واقعاً مرده بودن حذف یا deprecate کنید.",
                code=source_line(src, node.lineno),
            )


def rule_duplicate_code(files: dict[str, tuple[str, ast.AST]], c: Collector) -> None:
    # Duplicate exact normalized blocks of >=6 lines among function bodies.
    block_index: dict[str, list[tuple[str, int, str]]] = defaultdict(list)
    for rel, (src, tree) in files.items():
        lines = src.splitlines()
        for node in iter_functions(tree):
            start = node.lineno
            end = node_end_lineno(node)
            if end - start + 1 < 6:
                continue
            body = lines[start - 1:end]
            norm = "\n".join(
                re.sub(r"\s+", " ", x.strip())
                for x in body
                if x.strip()
            )
            if len(norm) < 80:
                continue
            key = hashlib.sha256(norm.encode("utf-8", "replace")).hexdigest()
            block_index[key].append((rel, start, node.name))

    for key, items in block_index.items():
        if len(items) < 2:
            continue
        for rel, line, name in items:
            others = [f"{p}:{ln} {n}" for p, ln, n in items if (p, ln, n) != (rel, line, name)]
            c.add(
                rule_id="CLEAN-DUPLICATE-BLOCK",
                severity="medium",
                confidence="high",
                category="maintainability",
                iso25010="maintainability",
                cwe=None,
                owasp="A06",
                file=rel,
                line_start=line,
                symbol=name,
                title="کد تکراری",
                message="بدنه تابع با حداقل یک تابع دیگر بلوک یکسان نرمال‌شده دارد.",
                impact="رفع bug یا تغییر business rule باید در چند نقطه انجام شود و drift ایجاد می‌کند.",
                fix="منطق مشترک را به یک helper/service استخراج کنید و call-siteها را به آن متصل کنید.",
                code=source_line(files[rel][0], line),
                evidence="; ".join(others[:5]),
            )


# ---------------------------------------------------------------------------
# Rules: hard-coded paths / insecure transport / debug configuration
# ---------------------------------------------------------------------------

def rule_paths_transport_config(tree: ast.AST, rel: str, src: str, c: Collector) -> None:
    for i, line in enumerate(src.splitlines(), 1):
        if ABS_PATH_RE.search(line):
            # Ignore comments that are clearly examples.
            stripped = line.strip()
            if stripped.startswith("#") and ("example" in stripped.lower() or "نمونه" in stripped):
                continue
            c.add(
                rule_id="CLEAN-HARDCODED-PATH",
                severity="low",
                confidence="medium",
                category="flexibility",
                iso25010="flexibility",
                cwe=None,
                owasp="A02",
                file=rel,
                line_start=i,
                symbol=qualified_symbol(tree, i),
                title="absolute/hardcoded path",
                message="مسیر absolute داخل source code دیده شد.",
                impact="جابجایی محیط، deploy روی ماشین دیگر یا containerization شکننده‌تر می‌شود.",
                fix="مسیرها را از config/environment بگیرید و برای portability از pathlib استفاده کنید.",
                code=line,
            )

        if re.search(r"\bhttp://", line, re.I):
            c.add(
                rule_id="SEC-CLEARTEXT-HTTP",
                severity="medium",
                confidence="high",
                category="security",
                iso25010="security",
                cwe="CWE-319",
                owasp="A04",
                file=rel,
                line_start=i,
                symbol=qualified_symbol(tree, i),
                title="HTTP بدون TLS",
                message="URL با http:// پیدا شد.",
                impact="داده در مسیر می‌تواند شنود یا دستکاری شود.",
                fix="در production از HTTPS/TLS استفاده کنید؛ exception فقط برای localhost/test با توضیح صریح.",
                code=line,
            )

        if re.search(r"\b(?:debug\s*=\s*True|DEBUG\s*=\s*True)\b", line):
            c.add(
                rule_id="SEC-DEBUG-ENABLED",
                severity="high",
                confidence="medium",
                category="security",
                iso25010="security",
                cwe="CWE-489",
                owasp="A02",
                file=rel,
                line_start=i,
                symbol=qualified_symbol(tree, i),
                title="debug mode فعال",
                message="الگوی debug=True پیدا شد.",
                impact="traceback و رفتارهای توسعه‌ای می‌تواند اطلاعات حساس یا سطح حمله را افزایش دهد.",
                fix="debug را فقط از environment مخصوص توسعه فعال کنید و در production صریحاً خاموش نگه دارید.",
                code=line,
            )


# ---------------------------------------------------------------------------
# Requirements / dependencies
# ---------------------------------------------------------------------------

def rule_requirements(root: Path, c: Collector) -> dict[str, str]:
    req_paths = [
        root / "requirements.txt",
        root / "requirements-prod.txt",
        root / "requirements-dev.txt",
    ]
    parsed: dict[str, str] = {}
    for req in req_paths:
        if not req.exists():
            continue
        for i, raw in enumerate(req.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith(("-r", "--")):
                continue
            # Basic requirements parser; markers/extras preserved only loosely.
            m = re.match(r"^\s*([A-Za-z0-9_.-]+)\s*([=!<>~].*)?$", line)
            if not m:
                continue
            name = normalize_package_name(m.group(1))
            spec = (m.group(2) or "").strip()
            parsed[name] = spec
            if not spec:
                c.add(
                    rule_id="DEP-UNPINNED",
                    severity="low",
                    confidence="high",
                    category="flexibility",
                    iso25010="flexibility",
                    cwe=None,
                    owasp="A03",
                    file=safe_rel(req, root),
                    line_start=i,
                    symbol="<requirements>",
                    title=f"dependency بدون version pin: {name}",
                    message=f"{name} در requirements بدون محدودیت نسخه ثبت شده است.",
                    impact="build reproducibility و supply-chain control ضعیف‌تر می‌شود.",
                    fix="برای production constraint نسخه (مثلاً == یا بازه دقیق) تعریف کنید و lockfile/upgrade policy داشته باشید.",
                    code=raw,
                )
    return parsed


# ---------------------------------------------------------------------------
# Git / environment
# ---------------------------------------------------------------------------

def rule_repo_hygiene(root: Path, c: Collector) -> None:
    gitignore = root / ".gitignore"
    env = root / ".env"
    if env.exists() and gitignore.exists():
        text = gitignore.read_text(encoding="utf-8", errors="replace")
        if not re.search(r"(^|\n)\s*\.env\s*(\n|$)", text):
            c.add(
                rule_id="SEC-ENV-NOT-IGNORED",
                severity="high",
                confidence="high",
                category="security",
                iso25010="security",
                cwe="CWE-200",
                owasp="A02",
                file=".gitignore",
                line_start=1,
                symbol="<git>",
                title=".env داخل .gitignore نیست",
                message="فایل .env وجود دارد اما rule صریح برای ignore شدن آن پیدا نشد.",
                impact="secretها ممکن است به repository commit شوند.",
                fix=".env و فایل‌های credential محلی را در .gitignore قرار دهید و secretهای قبلی را audit/rotate کنید.",
                code="",
            )

    env_example = root / ".env.example"
    if not env_example.exists() and env.exists():
        c.add(
            rule_id="CONFIG-MISSING-ENV-EXAMPLE",
            severity="low",
            confidence="high",
            category="flexibility",
            iso25010="flexibility",
            cwe=None,
            owasp="A03",
            file="<project>",
            line_start=1,
            symbol="<project>",
            title=".env.example ندارد",
            message="پروژه .env دارد ولی نمونهٔ امن برای کلیدهای موردنیاز پیدا نشد.",
            impact="setup محیط جدید به حدس وابسته می‌شود و onboarding/deploy خطاپذیر می‌گردد.",
            fix=".env.example بدون secret واقعی بسازید و نام تمام کلیدهای لازم را در آن ثبت کنید.",
        )



# ---------------------------------------------------------------------------
# Professor-grade deep passes
# ---------------------------------------------------------------------------

def _const_string(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        parts: list[str] = []
        cur: ast.AST | None = node.func
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
        return ".".join(reversed(parts))
    return "<call>"


def _arg_keyword(node: ast.Call, name: str) -> Optional[ast.AST]:
    for kw in node.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _is_name_like(node: ast.AST, hints: Sequence[str]) -> bool:
    if isinstance(node, ast.Name):
        value = node.id.lower()
        return any(h in value for h in hints)
    if isinstance(node, ast.Attribute):
        return any(h in node.attr.lower() for h in hints)
    return False


def _literal_bool(node: Optional[ast.AST]) -> Optional[bool]:
    if isinstance(node, ast.Constant) and isinstance(node.value, bool):
        return node.value
    return None


def _has_call(node: ast.AST, names: set[str]) -> bool:
    return any(isinstance(n, ast.Call) and _call_name(n) in names for n in ast.walk(node))


def _ancestor_functions(tree: ast.AST) -> dict[int, ast.FunctionDef | ast.AsyncFunctionDef]:
    out: dict[int, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    def visit(node: ast.AST, fn: ast.FunctionDef | ast.AsyncFunctionDef | None = None) -> None:
        current = node if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else fn
        if current is not None:
            out[id(node)] = current
        for child in ast.iter_child_nodes(node):
            visit(child, current)
    visit(tree)
    return out


def _has_exception_log_or_raise(handler: ast.ExceptHandler) -> bool:
    for node in ast.walk(handler):
        if isinstance(node, (ast.Raise, ast.Break, ast.Continue)):
            return True
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if name.endswith(("logger.exception", "logger.error", "logging.exception", "logging.error", "log.exception", "log.error")):
                return True
            if ".add_exception" in name or name.endswith("capture_exception"):
                return True
    return False


def _deep_add(c: Collector, *, rel: str, src: str, node: ast.AST, rule_id: str,
              severity: str, confidence: str, iso: str, cwe: Optional[str],
              owasp: Optional[str], title: str, message: str, impact: str, fix: str,
              extra: Optional[dict[str, Any]] = None) -> None:
    line = getattr(node, "lineno", 1)
    end = node_end_lineno(node)
    c.add(rule_id=rule_id, severity=severity, confidence=confidence,
          category=iso, iso25010=iso, cwe=cwe, owasp=owasp, file=rel,
          line_start=line, line_end=end, symbol=qualified_symbol(ast.parse(src), line) if False else "<see-symbol>",
          title=title, message=message, impact=impact, fix=fix,
          code=source_context(src, line, min(end, line + 2), c.config.context_lines),
          evidence=source_line(src, line), extra=extra)


def rule_professor_security(tree: ast.AST, rel: str, src: str, c: Collector) -> None:
    """Second security pass: high-signal Python web/service patterns."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)

        # Shell injection / unsafe subprocess settings.
        if name in {"subprocess.run", "subprocess.Popen", "subprocess.call", "subprocess.check_call", "subprocess.check_output"}:
            shell = _arg_keyword(node, "shell")
            if _literal_bool(shell) is True:
                confidence = "high" if node.args and not isinstance(node.args[0], (ast.List, ast.Tuple)) else "medium"
                c.add(rule_id="SEC-CMD-SHELL-TRUE", severity="high", confidence=confidence,
                      category="security", iso25010="security", cwe="CWE-78", owasp="A05",
                      file=rel, line_start=node.lineno, line_end=node_end_lineno(node),
                      symbol=qualified_symbol(tree, node.lineno),
                      title="subprocess با shell=True",
                      message="اجرای فرمان با shell=True مرز جداسازی command و data را حذف می‌کند؛ اگر آرگومان‌ها قابل کنترل باشند Command Injection ممکن است.",
                      impact="در سناریوی ورودی غیرقابل اعتماد، مهاجم می‌تواند فرمان دلخواه سیستم‌عامل اجرا کند.",
                      fix="shell=True را حذف کنید و command را به صورت list آرگومان‌های مستقل ارسال کنید؛ در صورت اجبار، ورودی را allowlist کنید و طراحی را بازبینی کنید.",
                      code=source_context(src, node.lineno, node_end_lineno(node), c.config.context_lines))

        # TLS verification disabled.
        verify = _arg_keyword(node, "verify")
        if verify is not None and _literal_bool(verify) is False:
            c.add(rule_id="SEC-TLS-VERIFY-OFF", severity="high", confidence="high",
                  category="security", iso25010="security", cwe="CWE-295", owasp="A04",
                  file=rel, line_start=node.lineno, line_end=node_end_lineno(node),
                  symbol=qualified_symbol(tree, node.lineno), title="TLS certificate verification خاموش شده",
                  message="verify=False بررسی گواهی TLS را غیرفعال می‌کند.",
                  impact="MITM می‌تواند ارتباط رمزگذاری‌شده را رهگیری یا تغییر دهد.",
                  fix="verify=True را حفظ کنید و trust store معتبر یا CA داخلی را به‌درستی تنظیم کنید.",
                  code=source_context(src, node.lineno, node_end_lineno(node), c.config.context_lines))

        # Requests without timeout: reliability/performance issue, not a vulnerability by itself.
        if name in {"requests.get", "requests.post", "requests.put", "requests.patch", "requests.delete", "requests.request", "httpx.get", "httpx.post", "httpx.request"}:
            if _arg_keyword(node, "timeout") is None:
                c.add(rule_id="NET-NO-TIMEOUT", severity="medium", confidence="high",
                      category="performance_efficiency", iso25010="performance_efficiency", cwe="CWE-770", owasp="A10",
                      file=rel, line_start=node.lineno, line_end=node_end_lineno(node),
                      symbol=qualified_symbol(tree, node.lineno), title="تماس شبکه بدون timeout",
                      message=f"فراخوانی {name} بدون timeout مشاهده شد.",
                      impact="قطع یا کندی سرویس مقصد می‌تواند thread/task را برای مدت نامحدود معطل و منابع را مصرف کند.",
                      fix="برای اتصال و خواندن، timeout مشخص و ترجیحاً budget کلی درخواست تعیین کنید.",
                      code=source_context(src, node.lineno, node_end_lineno(node), c.config.context_lines))

        # Archive extraction.
        if name in {"tarfile.TarFile.extractall", "TarFile.extractall", "zipfile.ZipFile.extractall", "ZipFile.extractall"}:
            c.add(rule_id="SEC-ARCHIVE-EXTRACT", severity="high", confidence="high",
                  category="security", iso25010="security", cwe="CWE-22", owasp="A01",
                  file=rel, line_start=node.lineno, line_end=node_end_lineno(node),
                  symbol=qualified_symbol(tree, node.lineno), title="استخراج آرشیو بدون کنترل کافی مسیر",
                  message="extractall بدون مشاهدهٔ کنترل نام فایل‌ها/مسیرهای خروجی تشخیص داده شد.",
                  impact="آرشیو مخرب می‌تواند خارج از directory مقصد بنویسد یا فایل‌های حساس را overwrite کند.",
                  fix="مسیر خروجی و هر member را canonicalize و allowlist کنید؛ برای tarfile از فیلتر امن متناسب با نسخهٔ Python استفاده کنید.",
                  code=source_context(src, node.lineno, node_end_lineno(node), c.config.context_lines))

        # JWT signature verification explicitly disabled.
        if name.endswith("jwt.decode"):
            opts = _arg_keyword(node, "options")
            if isinstance(opts, ast.Dict):
                for k, v in zip(opts.keys, opts.values):
                    if isinstance(k, ast.Constant) and k.value == "verify_signature" and _literal_bool(v) is False:
                        c.add(rule_id="SEC-JWT-VERIFY-OFF", severity="critical", confidence="high",
                              category="security", iso25010="security", cwe="CWE-347", owasp="A07",
                              file=rel, line_start=node.lineno, line_end=node_end_lineno(node),
                              symbol=qualified_symbol(tree, node.lineno), title="اعتبارسنجی امضای JWT غیرفعال شده",
                              message="verify_signature=False در decode شناسایی شد.",
                              impact="توکن جعلی می‌تواند به عنوان توکن معتبر پذیرفته شود و کنترل دسترسی دور زده شود.",
                              fix="اعتبارسنجی امضا، الگوریتم مجاز و claims حیاتی را اجباری کنید؛ الگوریتم از ورودی توکن انتخاب نشود.",
                              code=source_context(src, node.lineno, node_end_lineno(node), c.config.context_lines))

        # CORS wildcard with credentials.
        if name in {"add_middleware", "CORS", "flask_cors.CORS", "fastapi.middleware.cors.CORSMiddleware"} or name.endswith("CORSMiddleware"):
            args_text = ast.get_source_segment(src, node)
            if args_text and "*" in args_text and "allow_credentials=True" in args_text.replace(" ", ""):
                c.add(rule_id="WEB-CORS-WILDCARD-CREDENTIALS", severity="high", confidence="medium",
                      category="security", iso25010="security", cwe="CWE-942", owasp="A05",
                      file=rel, line_start=node.lineno, line_end=node_end_lineno(node),
                      symbol=qualified_symbol(tree, node.lineno), title="CORS با wildcard و credentials",
                      message="تنظیم CORS شامل wildcard و credentials شناسایی شد.",
                      impact="می‌تواند مرز origin را بیش از حد باز کند و سطح حملهٔ مرورگر را افزایش دهد.",
                      fix="Originهای مجاز را allowlist کنید و credentials را فقط برای originهای مورد نیاز فعال کنید.",
                      code=source_context(src, node.lineno, node_end_lineno(node), c.config.context_lines))

    # Async functions containing obviously blocking calls.
    for fn in iter_functions(tree):
        if not isinstance(fn, ast.AsyncFunctionDef):
            continue
        for n in ast.walk(fn):
            if not isinstance(n, ast.Call):
                continue
            call = _call_name(n)
            if call in BLOCKING_IN_ASYNC:
                c.add(rule_id="ASYNC-BLOCKING-CALL", severity="medium", confidence="high",
                      category="performance_efficiency", iso25010="performance_efficiency", cwe="CWE-400", owasp="A10",
                      file=rel, line_start=n.lineno, line_end=node_end_lineno(n),
                      symbol=qualified_symbol(tree, fn.lineno), title="فراخوانی blocking داخل async",
                      message=f"{call} در تابع async مشاهده شد.",
                      impact="می‌تواند event loop را block کند و latency/concurrency را خراب کند.",
                      fix="از API async متناظر یا asyncio.to_thread/run_in_executor برای عملیات blocking استفاده کنید.",
                      code=source_context(src, n.lineno, node_end_lineno(n), c.config.context_lines))

def _walk_excluding_nested_funcs(node: ast.AST):
    """
    پیمایش AST — ولی داخل توابع/لانداهای تودرتو نرو.
    چون return/break/continue داخل یه تابع تودرتو، مال finally نیست.
    """
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        return  # داخل تابع تودرتو نرو
    yield node
    for child in ast.iter_child_nodes(node):
        yield from _walk_excluding_nested_funcs(child)
        
def rule_professor_correctness(tree: ast.AST, rel: str, src: str, c: Collector) -> None:
    """Correctness traps that AST can establish with high signal."""
    for node in ast.walk(tree):
        # return/break/continue in finally is dangerous and easy to miss in review.
        if isinstance(node, ast.Try) and node.finalbody:
            # 🔧 FIX: از walker استفاده کن که داخل توابع تودرتو نره
            for stmt in node.finalbody:
                for n in _walk_excluding_nested_funcs(stmt):
                    if isinstance(n, (ast.Return, ast.Break, ast.Continue)):
                        c.add(rule_id="CORR-CONTROL-FLOW-IN-FINALLY", severity="high", confidence="high",
                              category="reliability", iso25010="reliability", cwe="CWE-584", owasp="A10",
                              file=rel, line_start=n.lineno, line_end=node_end_lineno(n), symbol=qualified_symbol(tree, n.lineno),
                              title="کنترل جریان داخل finally",
                              message="return/break/continue داخل finally می‌تواند exception یا return قبلی را سرکوب کند.",
                              impact="ممکن است خطاهای واقعی ناپدید شوند یا قرارداد تابع به شکل غیرمنتظره تغییر کند.",
                              fix="کنترل جریان را از finally خارج کنید؛ finally را برای cleanup نگه دارید.",
                              code=source_context(src, n.lineno, node_end_lineno(n), c.config.context_lines))
        # Constant conditional expressions.
        if isinstance(node, ast.If) and isinstance(node.test, ast.Constant) and isinstance(node.test.value, bool):
            c.add(rule_id="CORR-CONSTANT-CONDITION", severity="low", confidence="high",
                  category="maintainability", iso25010="maintainability", cwe="CWE-571", owasp="A06",
                  file=rel, line_start=node.lineno, line_end=node_end_lineno(node), symbol=qualified_symbol(tree, node.lineno),
                  title="شرط if ثابت است",
                  message=f"شرط {'True' if node.test.value else 'False'} ثابت است و یکی از شاخه‌ها عملاً unreachable است.",
                  impact="کد اضافی یا dead branch می‌تواند نگهداری و اعتماد به منطق را سخت کند.",
                  fix="شرط را حذف/ساده کنید یا علت عمدی بودن آن را با نام‌گذاری/کامنت روشن ثبت کنید.",
                  code=source_context(src, node.lineno, node_end_lineno(node), c.config.context_lines))
        # Assert used outside test modules for validation.
        if isinstance(node, ast.Assert) and not is_test_file(rel):
            c.add(rule_id="CORR-ASSERT-RUNTIME-CONTROL", severity="medium", confidence="medium",
                  category="reliability", iso25010="reliability", cwe="CWE-617", owasp="A10",
                  file=rel, line_start=node.lineno, line_end=node_end_lineno(node), symbol=qualified_symbol(tree, node.lineno),
                  title="assert برای کنترل منطق runtime",
                  message="assert در کد غیرتستی برای تضمین منطق runtime مشاهده شد.",
                  impact="اجرای Python با -O assertها را حذف می‌کند و validation مورد انتظار از بین می‌رود.",
                  fix="برای validation runtime از if/raise یا validation library استفاده کنید.",
                  code=source_context(src, node.lineno, node_end_lineno(node), c.config.context_lines))
                  
def rule_professor_hygiene(tree: ast.AST, rel: str, src: str, c: Collector) -> None:
    """Style/maintainability details with exact lines."""
    lines = src.splitlines()
    for i, line in enumerate(lines, 1):
        if line.rstrip(" \t") != line:
            c.add(rule_id="STYLE-TRAILING-WHITESPACE", severity="low", confidence="high",
                  category="maintainability", iso25010="maintainability", cwe=None, owasp=None,
                  file=rel, line_start=i, line_end=i, symbol=qualified_symbol(tree, i),
                  title="Trailing whitespace", message="فضای سفید اضافی در انتهای خط وجود دارد.",
                  impact="diff و review را کثیف می‌کند و می‌تواند tooling را مختل کند.",
                  fix="فضای انتهای خط را حذف کنید.", code=source_context(src, i, i, c.config.context_lines))
        if "\t" in line[: len(line) - len(line.lstrip())]:
            c.add(rule_id="STYLE-TAB-INDENT", severity="low", confidence="high",
                  category="maintainability", iso25010="maintainability", cwe=None, owasp=None,
                  file=rel, line_start=i, line_end=i, symbol=qualified_symbol(tree, i),
                  title="Tab در indentation", message="Tab در ابتدای خط کد دیده شد.",
                  impact="ترکیب tab/space می‌تواند indentation را مبهم یا ناسازگار کند.",
                  fix="برای indentation از 4 space استفاده کنید.", code=source_context(src, i, i, c.config.context_lines))
        if len(line) > 120 and not line.lstrip().startswith(("#", "http://", "https://")):
            c.add(rule_id="STYLE-LONG-LINE", severity="low", confidence="high",
                  category="maintainability", iso25010="maintainability", cwe=None, owasp=None,
                  file=rel, line_start=i, line_end=i, symbol=qualified_symbol(tree, i),
                  title="خط بسیار طولانی", message=f"طول خط {len(line)} کاراکتر است و از آستانه 120 گذشته است.",
                  impact="خوانایی، review و refactor سخت‌تر می‌شود.",
                  fix="خط را در مرزهای منطقی بشکنید؛ اگر literal/URL است آن را به شکل مناسب بازنویسی کنید.",
                  code=source_context(src, i, i, c.config.context_lines), extra={"length": len(line), "threshold": 120})

    # star imports are difficult to audit and break namespace provenance.
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names):
            c.add(rule_id="IMPORT-STAR", severity="medium", confidence="high",
                  category="maintainability", iso25010="maintainability", cwe=None, owasp=None,
                  file=rel, line_start=node.lineno, line_end=node_end_lineno(node), symbol=qualified_symbol(tree, node.lineno),
                  title="Wildcard import", message=f"from {node.module or '<relative>'} import * استفاده شده است.",
                  impact="منبع نام‌ها نامشخص می‌شود و احتمال shadowing و import side effect افزایش می‌یابد.",
                  fix="نام‌های مورد نیاز را صریح import کنید و در صورت نیاز __all__ تعریف کنید.",
                  code=source_context(src, node.lineno, node_end_lineno(node), c.config.context_lines))


def rule_professor_supply_chain(root: Path, c: Collector) -> None:
    """Repository/dependency supply-chain checks; no network is used here."""
    for name in PYPROJECT_NAMES:
        path = root / name
        if not path.exists() or not path.is_file():
            continue
        rel = safe_rel(path, root)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            low = line.strip().lower()
            if "trusted-host" in low or low.startswith("--trusted-host"):
                c.add(rule_id="SUPPLY-TRUSTED-HOST", severity="high", confidence="high",
                      category="security", iso25010="security", cwe="CWE-494", owasp="A03",
                      file=rel, line_start=i, line_end=i, symbol="<dependency-config>",
                      title="pip trusted-host configuration", message="trusted-host در dependency configuration مشاهده شد.",
                      impact="TLS host verification می‌تواند تضعیف شود و supply-chain interception ریسک بالاتری پیدا کند.",
                      fix="trusted-host را فقط برای محیط کنترل‌شده و در صورت ضرورت استفاده کنید؛ به HTTPS و certificate validation استاندارد برگردید.",
                      code=source_line(text, i))
            if re.search(r"(?:git\+https?|https?)://", line, re.I) and (name.endswith("requirements.txt") or name.endswith("requirements.in") or name.endswith("constraints.txt")):
                c.add(rule_id="SUPPLY-DIRECT-URL", severity="medium", confidence="high",
                      category="security", iso25010="security", cwe="CWE-494", owasp="A03",
                      file=rel, line_start=i, line_end=i, symbol="<dependency-config>",
                      title="وابستگی مستقیم با URL", message="وابستگی از URL مستقیم/Git URL دریافت می‌شود.",
                      impact="تغییر یا compromise منبع مستقیم می‌تواند artifact ناخواسته وارد build کند.",
                      fix="نسخه و hash/provenance را pin کنید و منبع package را محدود و قابل‌اعتماد نگه دارید.",
                      code=source_line(text, i))

    # Secrets/key files in repository working tree.
    sensitive_suffixes = {".pem", ".key", ".p12", ".pfx", ".jks"}
    for path in root.rglob("*"):
        if not path.is_file() or set(path.parts) & DEFAULT_IGNORE:
            continue
        if path.suffix.lower() in sensitive_suffixes:
            rel = safe_rel(path, root)
            c.add(rule_id="REPO-SENSITIVE-KEY-FILE", severity="high", confidence="medium",
                  category="security", iso25010="security", cwe="CWE-200", owasp="A02",
                  file=rel, line_start=1, line_end=1, symbol="<filesystem>",
                  title="فایل کلید/گواهی حساس در درخت پروژه", message=f"فایل حساس {path.name} در repository tree پیدا شد.",
                  impact="ممکن است artifact حساس در source control یا backup وارد شود.",
                  fix="فایل‌های secret را خارج از repository نگه دارید، .gitignore و secret manager مناسب استفاده کنید.",
                  code="")


def rule_professor_runtime(root: Path, c: Collector) -> None:
    """Safe runtime checks. We intentionally avoid importing project modules."""
    py = shutil.which("python") or sys.executable
    rc, out, err = run_command([py, "-m", "compileall", "-q", str(root)], root, timeout=900)
    if rc != 0:
        c.add(rule_id="RUNTIME-COMPILEALL", severity="high", confidence="high",
              category="reliability", iso25010="reliability", cwe=None, owasp="A10",
              file="<compileall>", line_start=1, line_end=1, symbol="<python -m compileall>",
              title="compileall شکست خورد", message=f"Python compileall با exit code {rc} برگشت.",
              impact="حداقل یک فایل Python در محیط هدف قابل compile شدن نیست.",
              fix="خطای syntax/encoding گزارش‌شده را رفع و compileall را تکرار کنید.", evidence=(out + "\n" + err)[-10000:])


def rule_professor_inventory(root: Path, c: Collector) -> None:
    """Project inventory / testability gaps."""
    py_files = list(iter_python_files(root, include_tests=True))
    test_files = [p for p in py_files if is_test_file(safe_rel(p, root))]
    if not test_files:
        c.add(rule_id="TEST-NO-TEST-FILES", severity="high", confidence="high",
              category="functional_suitability", iso25010="functional_suitability", cwe=None, owasp="A06",
              file="<project>", line_start=1, line_end=1, symbol="<tests>",
              title="هیچ فایل تست Python پیدا نشد", message="در کل درخت پروژه فایل test_*.py یا tests/.* دیده نشد.",
              impact="Regression و correctness قابل اثبات نیستند.", fix="برای ماژول‌های بحرانی تست unit/integration مستقل ایجاد کنید.")
    else:
        # Empty test modules / test files containing no test function/class.
        for path in test_files:
            src = read_source(path)
            try:
                tree = ast.parse(src, filename=str(path))
            except SyntaxError:
                continue
            test_nodes = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name.startswith("test")]
            if not test_nodes:
                rel = safe_rel(path, root)
                c.add(rule_id="TEST-EMPTY-MODULE", severity="low", confidence="high",
                      category="functional_suitability", iso25010="functional_suitability", cwe=None, owasp="A06",
                      file=rel, line_start=1, line_end=1, symbol="<test-module>",
                      title="فایل تست بدون test function/class", message="فایل به عنوان test شناخته شد ولی عنصر تستی ندارد.",
                      impact="ممکن است فایل قدیمی، placeholder یا تستِ ظاهراً موجود ولی عملاً خالی باشد.",
                      fix="فایل را حذف/rename کنید یا تست واقعی به آن اضافه کنید.")


# ---------------------------------------------------------------------------
# Optional test execution / external tooling
# ---------------------------------------------------------------------------

def run_command(cmd: Sequence[str], cwd: Path, timeout: int = 600) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            list(cmd),
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return 127, "", f"command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "", "command timed out"
    except Exception as exc:
        return 125, "", str(exc)


def run_tests(root: Path, c: Collector) -> None:
    pytest = shutil.which("pytest")
    if not pytest:
        c.add(
            rule_id="TEST-PYTEST-MISSING",
            severity="medium",
            confidence="high",
            category="compatibility",
            iso25010="compatibility",
            cwe=None,
            owasp=None,
            file="<environment>",
            line_start=1,
            symbol="<pytest>",
            title="pytest executable پیدا نشد",
            message="--run-tests فعال بود ولی pytest در PATH نیست.",
            impact="نتایج dynamic tests قابل تأیید نیستند.",
            fix="pytest را در محیط پروژه نصب و به PATH/virtualenv اضافه کنید.",
        )
        return

    rc, out, err = run_command([pytest, "-q"], root, timeout=900)
    if rc != 0:
        c.add(
            rule_id="TEST-PYTEST-FAIL",
            severity="high",
            confidence="high",
            category="reliability",
            iso25010="reliability",
            cwe=None,
            owasp="A10",
            file="<pytest>",
            line_start=1,
            symbol="<pytest>",
            title="pytest شکست خورد",
            message=f"pytest با exit code {rc} تمام شد.",
            impact="آزمایش‌های اجرایی پروژه سبز نیستند یا محیط اجرا مشکل دارد.",
            fix="اولین failure را دقیقاً از خروجی pytest رفع کنید و تست‌ها را دوباره اجرا کنید.",
            evidence=(out + "\n" + err)[-6000:],
        )


def _parse_external_json_findings(name: str, stdout: str) -> list[dict[str, Any]]:
    try:
        return json.loads(stdout)
    except Exception:
        return []



def run_dependency_audit(root: Path, c: Collector) -> None:
    """Optional dependency vulnerability audit via pip-audit, one vulnerability per Finding."""
    exe = shutil.which("pip-audit")
    if not exe:
        c.add(rule_id="EXT-PIP-AUDIT-NOT-INSTALLED", severity="info", confidence="high",
              category="security", iso25010="security", cwe=None, owasp="A03",
              file="<environment>", line_start=1, line_end=1, symbol="<pip-audit>",
              title="pip-audit در محیط نصب نیست", message="dependency vulnerability audit اجرا نشد.",
              impact="آسیب‌پذیری‌های شناخته‌شدهٔ dependencyها از این مسیر بررسی نشده‌اند.",
              fix="pip-audit را در محیط CI/virtualenv نصب و در ممیزی امنیتی اجرا کنید.")
        return

    req = root / "requirements.txt"
    cmd = [exe]
    if req.exists():
        cmd += ["-r", str(req)]
    cmd += ["-", "json", "--progress-spinner", "off"]
    rc, out, err = run_command(cmd, root, timeout=1200)
    try:
        payload = json.loads(out) if out.strip() else {}
    except json.JSONDecodeError:
        payload = {}

    deps = payload.get("dependencies", []) if isinstance(payload, dict) else []
    vuln_total = 0
    for dep in deps if isinstance(deps, list) else []:
        vulns = dep.get("vulns", []) if isinstance(dep, dict) else []
        for vuln in vulns if isinstance(vulns, list) else []:
            vuln_total += 1
            vuln_id = str(vuln.get("id") or "PIP-AUDIT")
            package = str(dep.get("name") or "<package>")
            installed = str(dep.get("version") or "?")
            fixes = vuln.get("fix_versions") or []
            aliases = vuln.get("aliases") or []
            c.add(rule_id=f"DEP-VULN-{vuln_id}", severity="high", confidence="high",
                  category="security", iso25010="security", cwe=None, owasp="A03",
                  file="requirements.txt" if req.exists() else "<environment>", line_start=1, line_end=1,
                  symbol=f"<{package}>", title=f"آسیب‌پذیری dependency: {package} {installed}",
                  message=f"pip-audit آسیب‌پذیری {vuln_id} را برای {package} {installed} گزارش کرده است؛ aliases={aliases or '-'}.",
                  impact=str(vuln.get("description") or "کتابخانهٔ وابسته دارای آسیب‌پذیری شناخته‌شده است."),
                  fix=f"به یکی از نسخه‌های رفع‌کننده ارتقا دهید: {fixes or 'نسخهٔ امن را از advisory انتخاب کنید.'}",
                  evidence=json.dumps(vuln, ensure_ascii=False),
                  extra={"package": package, "installed_version": installed, "aliases": aliases, "fix_versions": fixes})

    if rc != 0 and vuln_total == 0:
        c.add(rule_id="EXT-PIP-AUDIT-ERROR", severity="medium", confidence="high",
              category="security", iso25010="security", cwe=None, owasp="A03",
              file="<pip-audit>", line_start=1, line_end=1, symbol="<pip-audit>",
              title="pip-audit با خطا اجرا شد", message=f"pip-audit با exit code {rc} برگشت و finding قابل parsing تولید نشد.",
              impact="نتیجهٔ dependency vulnerability audit قابل اتکا نیست.",
              fix="خطای محیط/requirements را رفع کنید و pip-audit را دوباره اجرا کنید.", evidence=(out + "\n" + err)[-12000:])


def run_external(root: Path, c: Collector) -> None:
    """Run available industry tools without making them hard dependencies."""
    jobs: list[tuple[str, list[str], str]] = [
        ("ruff", ["ruff", "check", ".", "--output-format", "json"], "maintainability"),
        ("bandit", ["bandit", "-r", ".", "-", "json", "-q"], "security"),
        ("pyflakes", ["pyflakes", "."], "maintainability"),
    ]
    for name, cmd, iso in jobs:
        exe = shutil.which(cmd[0])
        if not exe:
            c.add(rule_id=f"EXT-{name.upper()}-NOT-INSTALLED", severity="info", confidence="high",
                  category=iso, iso25010=iso, cwe=None, owasp=None,
                  file="<environment>", line_start=1, line_end=1, symbol=f"<{name}>",
                  title=f"{name} در محیط نصب نیست", message="ابزار خارجی برای scan موجود نیست و اجرا نشد.",
                  impact="پوشش static analysis خارجی این اجرا کاهش یافته است؛ این finding شکست پروژه نیست.",
                  fix=f"برای پوشش کامل‌تر، {name} را در محیط CI/virtualenv نصب کنید.")
            continue
        rc, out, err = run_command([exe, *cmd[1:]], root, timeout=900)
        if rc == 0:
            continue
        # Preserve exact tool output as evidence, but keep each external result auditable.
        parsed = _parse_external_json_findings(name, out) if name in {"ruff", "bandit"} else []
        if name == "bandit" and isinstance(parsed, dict):
            parsed = parsed.get("results", [])
        if isinstance(parsed, list) and parsed:
            for item in parsed:
                if name == "ruff":
                    path = str(item.get("filename", "<ruff>"))
                    loc = item.get("location") or {}
                    line = int((loc.get("row") or 1))
                    rule = item.get("code") or "RUFF"
                    msg = item.get("message") or "ruff finding"
                    c.add(rule_id=f"EXT-RUFF-{rule}", severity="medium", confidence="high",
                          category="maintainability", iso25010="maintainability", cwe=None, owasp=None,
                          file=safe_rel(Path(path), root) if Path(path).is_absolute() else path.replace("\\", "/"),
                          line_start=line, line_end=line, symbol="<ruff>", title=f"Ruff {rule}", message=msg,
                          impact="lint/quality issue گزارش شده توسط Ruff.", fix="مطابق پیام Ruff اصلاح کنید.", code=source_line(read_source(Path(path)) if Path(path).exists() else "", line), evidence=json.dumps(item, ensure_ascii=False))
                else:
                    path = str(item.get("filename", "<bandit>"))
                    line = int(item.get("line_number") or 1)
                    test = item.get("test_id") or "BANDIT"
                    issue = item.get("issue_text") or "bandit finding"
                    sev = str(item.get("issue_severity") or "MEDIUM").lower()
                    sev = sev if sev in {"critical", "high", "medium", "low", "info"} else "medium"
                    c.add(rule_id=f"EXT-BANDIT-{test}", severity=sev, confidence="high",
                          category="security", iso25010="security", cwe=None, owasp="A05",
                          file=safe_rel(Path(path), root) if Path(path).is_absolute() else path.replace("\\", "/"),
                          line_start=line, line_end=int(item.get("line_number") or line), symbol="<bandit>",
                          title=f"Bandit {test}", message=issue,
                          impact="Bandit یک الگوی امنیتی مشکوک گزارش کرده است.", fix=str(item.get("more_info", "راهنمای Bandit را بررسی و کد را اصلاح کنید.")),
                          code=str(item.get("code", "")), evidence=json.dumps(item, ensure_ascii=False),
                          extra={"external": name})
        else:
            c.add(rule_id=f"EXT-{name.upper()}", severity="medium", confidence="high",
                  category=iso, iso25010=iso, cwe=None, owasp="A05" if name == "bandit" else None,
                  file="<external-tool>", line_start=1, line_end=1, symbol=f"<{name}>",
                  title=f"{name} گزارش issue داد", message=f"{name} با exit code {rc} برگشت.",
                  impact="حداقل یک مورد خارج از rules داخلی گزارش شده است.",
                  fix=f"خروجی {name} را بررسی و findingهای واقعی را رفع کنید.", evidence=(out + "\n" + err)[-12000:])


# ---------------------------------------------------------------------------
# Quality scoring
# ---------------------------------------------------------------------------

def quality_score(findings: Sequence[Finding]) -> dict[str, Any]:
    by_iso: dict[str, list[Finding]] = defaultdict(list)
    for f in findings:
        by_iso[f.iso25010].append(f)

    scores = {}
    for key in ISO25010_2023:
        items = by_iso.get(key, [])
        deduction = 0.0
        seen_high = set()
        for f in items:
            # Do not linearly punish hundreds of same-family low issues forever.
            family = f.rule_id
            base = PENALTIES.get(f.severity, 0.0)
            if f.severity in {"high", "critical"}:
                cap_key = (family, f.file)
                if cap_key in seen_high:
                    base *= 0.35
                seen_high.add(cap_key)
            deduction += base
        # Smooth saturation to keep 0..100.
        score = max(0.0, 100.0 * math.exp(-deduction / 8.0))
        scores[key] = round(score, 1)

    # Weighted overall; ISO 25010:2023 has 9 characteristics. Equal weights here
    # avoid implying that this custom score is an official ISO scoring formula.
    overall = round(sum(scores.values()) / max(1, len(scores)), 1)
    if overall >= 95:
        grade = "A+"
    elif overall >= 90:
        grade = "A"
    elif overall >= 80:
        grade = "B"
    elif overall >= 70:
        grade = "C"
    elif overall >= 60:
        grade = "D"
    elif overall >= 40:
        grade = "E"
    else:
        grade = "F"
    return {
        "total": overall,
        "grade": grade,
        "by_category": scores,
    }


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def console_report(c: Collector, config: ScanConfig, started: str, elapsed: float) -> None:
    c.sort_findings()
    c.cap()
    summary = c.summary()
    qs = quality_score(c.findings)

    print("=" * 96)
    print(f"{C.BOLD}🔬 Health Check + Forensic Static Analysis v8.0 Professor Edition{C.RESET}")
    print("=" * 96)
    print(f"ROOT: {config.root}")
    print(f"Started: {started}")
    print(f"Elapsed: {elapsed:.2f}s")
    print("Standards: ISO/IEC 25010:2023 | OWASP Top 10:2025 | CWE Top 25:2025 | SARIF 2.1.0")
    print()

    sev = summary["severity"]
    print(f"{C.BOLD}TOTAL FINDINGS: {len(c.findings)}{C.RESET}")
    print(
        f"  {C.RED}Critical={sev.get('critical',0)}{C.RESET}  "
        f"{C.RED}High={sev.get('high',0)}{C.RESET}  "
        f"{C.YELLOW}Medium={sev.get('medium',0)}{C.RESET}  "
        f"{C.BLUE}Low={sev.get('low',0)}{C.RESET}  "
        f"Info={sev.get('info',0)}"
    )
    print(f"Quality Score (custom diagnostic, not an ISO certification): {qs['total']}/100  Grade={qs['grade']}")
    print()

    print(f"{C.BOLD}📊 ISO/IEC 25010:2023 — 9 characteristics{C.RESET}")
    for key, meta in ISO25010_2023.items():
        score = qs["by_category"].get(key, 100.0)
        print(f"  {meta['fa']:<22} {score:>6.1f}  ({meta['label']})")
    print()

    if not c.findings:
        print(f"{C.GREEN}✅ هیچ findingی پیدا نشد.{C.RESET}")
        return

    print(f"{C.BOLD}🧾 تک‌تک یافته‌ها{C.RESET}")
    print("─" * 96)

    for f in c.findings:
        color = {
            "critical": C.RED,
            "high": C.RED,
            "medium": C.YELLOW,
            "low": C.BLUE,
            "info": C.DIM,
        }.get(f.severity, "")
        print(f"{color}{f.finding_id} | {f.severity.upper()} | confidence={f.confidence}{C.RESET}")
        print(f"  Rule       : {f.rule_id}")
        print(f"  Location   : {f.file}:{f.line_start}-{f.line_end}")
        print(f"  Symbol     : {f.symbol}")
        print(f"  ISO 25010  : {f.iso25010}")
        print(f"  CWE        : {f.cwe or '-'}")
        print(f"  OWASP 2025 : {f.owasp or '-'}")
        print(f"  Problem    : {f.title}")
        print(f"  Why        : {f.message}")
        print(f"  Impact     : {f.impact}")
        print(f"  Fix        : {f.fix}")
        if f.evidence:
            print(f"  Evidence   : {f.evidence[:1500]}")
        if config.show_code and f.code:
            print("  Code:")
            print(textwrap.indent(f.code[:3000], "    "))
        print(f"  Fingerprint: {f.fingerprint}")
        print("─" * 96)


def json_report(c: Collector, config: ScanConfig, started: str, elapsed: float) -> dict[str, Any]:
    c.sort_findings()
    c.cap()
    qs = quality_score(c.findings)
    return {
        "tool": {
            "name": "Srooyesh Health Check Forensic",
            "version": "8.0",
            "started_at": started,
            "elapsed_seconds": round(elapsed, 3),
            "root": str(config.root),
            "standards": {
                "iso": "ISO/IEC 25010:2023",
                "owasp": "OWASP Top 10:2025",
                "cwe": "CWE Top 25:2025",
                "sarif": SARIF_VERSION,
                "baseline": CURRENT_REFERENCE_BASELINE,
                "tool_reference": LATEST_TOOL_REFERENCE,
            },
        },
        "quality": qs,
        "summary": c.summary(),
        "files": {k: asdict(v) for k, v in c.files.items()},
        "findings": [f.to_dict() for f in c.findings],
    }


def sarif_report(c: Collector, config: ScanConfig, started: str) -> dict[str, Any]:
    c.sort_findings()
    results = []
    rules = {}
    for f in c.findings:
        level = "error" if f.severity in {"critical", "high"} else ("warning" if f.severity == "medium" else "note")
        rules.setdefault(
            f.rule_id,
            {
                "id": f.rule_id,
                "name": f.title[:120],
                "shortDescription": {"text": f.title[:256]},
                "helpUri": f"https://cwe.mitre.org/data/definitions/{f.cwe.split('-')[1]}.html" if f.cwe and f.cwe.startswith("CWE-") else None,
            },
        )
        result = {
            "ruleId": f.rule_id,
            "level": level,
            "message": {"text": f"{f.message} | Fix: {f.fix}"},
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": f.file},
                        "region": {
                            "startLine": f.line_start,
                            "endLine": f.line_end,
                        },
                    }
                }
            ],
            "partialFingerprints": {"primaryLocationLineHash": f.fingerprint},
            "properties": {
                "severity": f.severity,
                "confidence": f.confidence,
                "iso25010": f.iso25010,
                "cwe": f.cwe,
                "owasp": f.owasp,
                "symbol": f.symbol,
            },
        }
        results.append(result)

    for r in rules.values():
        if r.get("helpUri") is None:
            r.pop("helpUri", None)

    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": SARIF_VERSION,
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "Srooyesh Health Check Forensic",
                        "version": "8.0",
                        "informationUri": "https://srooyesh.com/",
                        "rules": list(rules.values()),
                    }
                },
                "results": results,
                "properties": {
                    "generatedAt": started,
                    "standards": ["ISO/IEC 25010:2023", "OWASP Top 10:2025", "CWE Top 25:2025"],
                },
            }
        ],
    }


def markdown_report(c: Collector, config: ScanConfig, started: str, elapsed: float) -> str:
    c.sort_findings()
    c.cap()
    qs = quality_score(c.findings)
    sev = c.summary()["severity"]
    out = []
    out.append("# Health Check + Forensic Static Analysis v8.0 Professor Edition")
    out.append("")
    out.append(f"- Root: `{config.root}`")
    out.append(f"- Started: `{started}`")
    out.append(f"- Elapsed: `{elapsed:.2f}s`")
    out.append("- Standards: ISO/IEC 25010:2023 / OWASP Top 10:2025 / CWE Top 25:2025 / SARIF 2.1.0")
    out.append("")
    out.append(f"## Quality Score: {qs['total']}/100 — {qs['grade']}")
    out.append("")
    out.append("| Severity | Count |")
    out.append("|---|---:|")
    for s in ("critical", "high", "medium", "low", "info"):
        out.append(f"| {s} | {sev.get(s,0)} |")
    out.append("")
    out.append("## Findings")
    out.append("")
    if not c.findings:
        out.append("✅ No findings.")
    for f in c.findings:
        out.append(f"### {f.finding_id} — {f.severity.upper()} — {f.title}")
        out.append("")
        out.append(f"- **Rule:** `{f.rule_id}`")
        out.append(f"- **Location:** `{f.file}:{f.line_start}-{f.line_end}`")
        out.append(f"- **Symbol:** `{f.symbol}`")
        out.append(f"- **Confidence:** `{f.confidence}`")
        out.append(f"- **ISO 25010:** `{f.iso25010}`")
        out.append(f"- **CWE:** `{f.cwe or '-'}`")
        out.append(f"- **OWASP 2025:** `{f.owasp or '-'}`")
        out.append(f"- **Why:** {f.message}")
        out.append(f"- **Impact:** {f.impact}")
        out.append(f"- **Fix:** {f.fix}")
        if f.code:
            out.append("")
            out.append("```python")
            out.append(f.code[:5000])
            out.append("```")
        out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Main scan
# ---------------------------------------------------------------------------

def scan(config: ScanConfig) -> tuple[Collector, str, float]:
    started = utc_now()
    start_ts = datetime.now(timezone.utc)

    collector = Collector(config)
    root = config.root.resolve()

    rule_project_structure(root, collector)
    rule_repo_hygiene(root, collector)
    rule_requirements(root, collector)
    if config.deep:
        rule_professor_supply_chain(root, collector)
        rule_professor_inventory(root, collector)

    source_cache: dict[str, tuple[str, ast.AST]] = {}

    files = list(iter_python_files(root, include_tests=config.include_tests))
    for path in files:
        rel = safe_rel(path, root)
        src = read_source(path)
        tree, error = parse_tree(path)
        fs = FileStats(path=rel, lines=line_count(src))
        if tree is None:
            fs.parse_ok = False
            fs.syntax_errors = 1
            fs.parse_error = error
            collector.files[rel] = fs
            rule_syntax(path, rel, src, tree, error, collector)
            continue

        source_cache[rel] = (src, tree)

        aliases, imported = import_map(tree)
        _ = aliases, imported  # used by rule, kept explicit for future extensions

        rule_type_hints_docs(tree, rel, src, collector)
        rule_imports_and_names(tree, rel, src, collector)
        rule_unused_args(tree, rel, src, collector)
        rule_exceptions_defaults(tree, rel, src, collector)
        rule_dangerous_calls(tree, rel, src, collector)
        rule_sql_injection(tree, rel, src, collector)
        rule_path_traversal(tree, rel, src, collector)
        rule_ssrf(tree, rel, src, collector)
        rule_insecure_deserialization(tree, rel, src, collector)
        rule_secrets_crypto_logging(tree, rel, src, collector)
        complexity = rule_complexity(tree, rel, src, collector)
        fs.functions = complexity.functions
        fs.classes = complexity.classes
        fs.max_mccabe = complexity.max_mccabe
        fs.max_cognitive = complexity.max_cognitive
        fs.min_mi = complexity.min_mi
        rule_dead_code(tree, rel, src, collector)
        rule_paths_transport_config(tree, rel, src, collector)
        if config.deep:
            rule_professor_security(tree, rel, src, collector)
            rule_professor_correctness(tree, rel, src, collector)
            rule_professor_hygiene(tree, rel, src, collector)

        collector.files[rel] = fs

    rule_duplicate_code(source_cache, collector)

    # Attach per-file finding counts.
    counts = Counter(f.file for f in collector.findings)
    for rel, fs in collector.files.items():
        fs.findings = counts.get(rel, 0)

    if config.run_tests:
        run_tests(root, collector)
    if config.runtime_smoke:
        rule_professor_runtime(root, collector)
    if config.dependency_audit:
        run_dependency_audit(root, collector)
    if config.run_external:
        run_external(root, collector)

    collector.sort_findings()
    collector.cap()

    elapsed = (datetime.now(timezone.utc) - start_ts).total_seconds()
    return collector, started, elapsed


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    import io as _io
    # UTF-8 safe stdout/stderr (فقط در main، نه در سطح ماژول)
    try:
        if hasattr(sys.stdout, "buffer"):
            sys.stdout = _io.TextIOWrapper(
                sys.stdout.buffer, encoding="utf-8"
            )
        if hasattr(sys.stderr, "buffer"):
            sys.stderr = _io.TextIOWrapper(
                sys.stderr.buffer, encoding="utf-8"
            )
    except Exception:
        pass

    parser = argparse.ArgumentParser(
        description="Health Check + Forensic Static Analysis v8.0 Professor Edition"
    )
    parser.add_argument("--root", type=Path, default=None, help="Project root (default: parent of tests/)")
    parser.add_argument("--forensic", action="store_true", help="all detailed console findings + code")
    parser.add_argument("--no-code", action="store_true", help="do not print code context")
    parser.add_argument("--context", type=int, default=2, help="code context lines")
    parser.add_argument("--json-only", action="store_true")
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--sarif", type=Path, default=None)
    parser.add_argument("--markdown-out", type=Path, default=None)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--min-score", type=float, default=0.0)
    parser.add_argument("--min-confidence", choices=["low", "medium", "high"], default="low")
    parser.add_argument("--max-findings", type=int, default=0, help="0 = unlimited")
    parser.add_argument("--no-tests", action="store_true", help="exclude tests/ from Python scan")
    parser.add_argument("--run-tests", action="store_true", help="actually run pytest -q")
    parser.add_argument("--run-external", action="store_true", help="run ruff/pyflakes/bandit if installed")
    parser.add_argument("--dependency-audit", action="store_true", help="run pip-audit if installed")
    parser.add_argument("--runtime-smoke", action="store_true", help="run safe compileall smoke test")
    parser.add_argument("--quick", action="store_true", help="disable professor-grade deep passes")
    parser.add_argument("--no-color", action="store_true")
    args = parser.parse_args()

    if args.no_color or args.json_only:
        disable_color()

    root = args.root.resolve() if args.root else Path(__file__).resolve().parent.parent
    config = ScanConfig(
        root=root,
        include_tests=not args.no_tests,
        min_confidence=args.min_confidence,
        show_code=(args.forensic or not args.no_code) and not args.json_only,
        context_lines=max(0, args.context),
        max_findings=max(0, args.max_findings),
        run_tests=args.run_tests,
        run_external=args.run_external,
        strict=args.strict,
        deep=not args.quick,
        dependency_audit=args.dependency_audit,
        runtime_smoke=args.runtime_smoke,
    )

    collector, started, elapsed = scan(config)

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(json_report(collector, config, started, elapsed), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    if args.sarif:
        args.sarif.parent.mkdir(parents=True, exist_ok=True)
        args.sarif.write_text(
            json.dumps(sarif_report(collector, config, started), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    if args.markdown_out:
        args.markdown_out.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_out.write_text(markdown_report(collector, config, started, elapsed), encoding="utf-8")

    if args.json_only:
        print(json.dumps(json_report(collector, config, started, elapsed), ensure_ascii=False, indent=2))
    else:
        console_report(collector, config, started, elapsed)

    score = quality_score(collector.findings)["total"]

    exit_code = 0
    if any(f.severity in {"critical", "high"} for f in collector.findings):
        exit_code = 1
    if args.strict and any(f.severity in {"critical", "high", "medium", "low"} for f in collector.findings):
        exit_code = 1
    if args.min_score > 0 and score < args.min_score:
        exit_code = 1

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
