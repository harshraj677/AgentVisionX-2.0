"""Custom regex rules that complement Presidio's built-in recognizers.

Each rule is a Presidio PatternRecognizer, so its findings merge with the built-in ones.
All matching happens locally; no text ever leaves the backend during a scan.
"""
import re

from presidio_analyzer import Pattern, PatternRecognizer

# Case-sensitive: token prefixes such as "AKIA" or "ghp_" are exact.
_CASE_SENSITIVE = re.DOTALL | re.MULTILINE
_CASE_INSENSITIVE = re.DOTALL | re.MULTILINE | re.IGNORECASE


def _rule(entity: str, patterns: list[tuple[str, str, float]], flags: int = _CASE_INSENSITIVE) -> PatternRecognizer:
    return PatternRecognizer(
        supported_entity=entity,
        patterns=[Pattern(name, regex, score) for name, regex, score in patterns],
        supported_language="en",
        global_regex_flags=flags,
        name=f"Custom{entity.title().replace('_', '')}Recognizer",
    )


def build_custom_recognizers() -> list[PatternRecognizer]:
    return [
        _rule(
            "API_KEY",
            [
                ("openai_style", r"\bsk-(?:proj-|live-|ant-)?[A-Za-z0-9_\-]{20,}", 0.95),
                ("google", r"\bAIza[0-9A-Za-z_\-]{35}\b", 0.95),
                ("aws_access_key", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b", 0.95),
                ("github", r"\bgh[pousr]_[A-Za-z0-9]{36,}\b", 0.95),
                ("slack", r"\bxox[baprs]-[A-Za-z0-9\-]{10,}", 0.9),
                ("stripe", r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}\b", 0.95),
                ("jwt", r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}", 0.9),
                ("bearer", r"\bBearer\s+[A-Za-z0-9._~+/\-]{20,}=*", 0.85),
            ],
            _CASE_SENSITIVE,
        ),
        _rule(
            "PRIVATE_KEY",
            [("pem", r"-----BEGIN [A-Z ]*PRIVATE KEY-----(?:.*?-----END [A-Z ]*PRIVATE KEY-----|.*$)", 0.99)],
        ),
        _rule(
            "PASSWORD_OR_SECRET",
            [
                (
                    "assignment",
                    r"\b(?:password|passwd|pwd|passphrase|secret|client[_\-\s]?secret|api[_\-\s]?key|access[_\-\s]?token|auth[_\-\s]?token)\b"
                    r"[ \t]*[:=][ \t]*[\"']?(?!\[)[^\s\"']{4,}",
                    0.85,
                )
            ],
        ),
        _rule(
            "EMAIL_ADDRESS",
            [("email", r"(?<![\w.+\-])[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}\b", 0.9)],
        ),
        _rule(
            "PHONE_NUMBER",
            [
                ("international", r"(?<![\w+])\+\d{1,3}[\s.\-]?\(?\d{1,4}\)?(?:[\s.\-]?\d{2,5}){2,4}(?!\d)", 0.7),
                ("us_format", r"(?<!\d)(?:\(\d{3}\)\s?|\d{3}[\s.\-])\d{3}[\s.\-]\d{4}(?!\d)", 0.65),
                ("india_mobile", r"(?<![\d.])[6-9]\d{4}[\s\-]?\d{5}(?![\d.])", 0.6),
            ],
        ),
        _rule(
            "IN_AADHAAR",
            # Only the printed 4-4-4 grouping, to avoid flagging arbitrary 12-digit numbers.
            [("grouped", r"(?<!\d)[2-9]\d{3}[\s\-]\d{4}[\s\-]\d{4}(?!\d)", 0.6)],
        ),
        _rule("IN_PAN", [("pan", r"\b[A-Z]{5}\d{4}[A-Z]\b", 0.6)], _CASE_SENSITIVE),
    ]
