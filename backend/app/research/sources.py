"""Turns raw search results into a small, safe, bounded evidence set.

Validation here is structural (URL shape, size, duplicates, instruction-like text). It does NOT verify that a page
is accurate or that its content still matches what Tavily returned.
"""
import ipaddress
import re
import unicodedata
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.research.tavily import SearchResult

MAX_URL_CHARS = 2048
MAX_TITLE_CHARS = 200
DISPLAY_SNIPPET_CHARS = 300

# Heuristic: lines that read like instructions to an AI. They are removed from evidence and the source is flagged.
_INJECTION = re.compile(
    r"(?i)\b(?:"
    r"(?:ignore|disregard|forget|override)\s+(?:all\s+|any\s+|the\s+|your\s+)?(?:previous|prior|above|earlier|system|existing)?\s*(?:instructions?|rules|prompts?|polic(?:y|ies))"
    r"|system\s+prompt|you\s+are\s+now|developer\s+mode|jailbreak"
    r"|(?:reveal|print|show|output|send|share)\s+(?:me\s+)?(?:your|the)\s+(?:api[\s_-]?keys?|system\s+prompt|password|secrets?|credentials?|instructions)"
    r"|(?:disable|turn\s+off|bypass)\s+(?:the\s+)?(?:privacy|safety|scanner|filter|redaction)"
    r"|(?:search|browse)\s+(?:again|the\s+web\s+for|for\s+more)|call\s+(?:the\s+)?tool|execute\s+(?:this|the\s+following)"
    r")"
)
# Our own evidence delimiters must not be forgeable from inside source text.
_DELIMITER = re.compile(r"<<<|>>>")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_TRACKING = ("utm_", "fbclid", "gclid", "mc_cid", "mc_eid")


@dataclass
class Source:
    id: int
    title: str
    url: str
    domain: str
    evidence: str  # bounded text given to the model (untrusted)
    snippet: str  # shorter text shown in the app
    published_date: str | None
    retrieved_at: str
    flags: list[str] = field(default_factory=list)


@dataclass
class SelectionReport:
    sources: list[Source]
    returned: int
    rejected_invalid_url: int = 0
    rejected_duplicate: int = 0
    rejected_empty: int = 0
    truncated: int = 0


def _is_private_host(host: str) -> bool:
    if host in ("localhost",) or host.endswith(".localhost") or host.endswith(".local") or host.endswith(".internal"):
        return True
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified


def validate_url(url: str) -> str | None:
    """Returns a clean URL, or None if it is malformed or unsafe to show as a link."""
    url = url.strip()
    if not url or len(url) > MAX_URL_CHARS or any(c.isspace() for c in url) or _CONTROL.search(url):
        return None
    try:
        parts = urlsplit(url)
        port = parts.port  # raises on garbage ports
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None
    if parts.username or parts.password:  # credentials embedded in a link
        return None
    if _is_private_host(parts.hostname.lower()):
        return None
    if port is not None and not (0 < port < 65536):
        return None
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, ""))


def dedupe_key(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().removeprefix("www.")
    if parts.port and not ((parts.scheme == "http" and parts.port == 80) or (parts.scheme == "https" and parts.port == 443)):
        host = f"{host}:{parts.port}"
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not k.lower().startswith(_TRACKING)))
    return f"{host}{parts.path.rstrip('/') or '/'}?{query}"


def _clean(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = _CONTROL.sub(" ", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def neutralize_untrusted(text: str) -> tuple[str, bool]:
    """Removes instruction-like lines and our delimiters from source text. Returns (text, was_flagged)."""
    flagged = False
    kept = []
    for line in text.splitlines() or [text]:
        if _INJECTION.search(line):
            flagged = True
            kept.append("[instruction-like text removed]")
        else:
            kept.append(line)
    out = _DELIMITER.sub("", "\n".join(kept))
    return out, flagged


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    cut = text[:limit]
    space = cut.rfind(" ")
    return (cut[:space] if space > limit * 0.6 else cut).rstrip() + " …", True


def select_sources(
    results: list[SearchResult], *, retrieved_at: str, max_sources: int, per_source_chars: int, total_chars: int
) -> SelectionReport:
    report = SelectionReport(sources=[], returned=len(results))
    seen: set[str] = set()
    budget = total_chars
    for r in results:
        if len(report.sources) >= max_sources or budget <= 0:
            break
        url = validate_url(r.url)
        if url is None:
            report.rejected_invalid_url += 1
            continue
        key = dedupe_key(url)
        if key in seen:
            report.rejected_duplicate += 1
            continue
        content = _clean(r.content)
        if not content:
            report.rejected_empty += 1
            continue
        seen.add(key)

        neutral, flagged = neutralize_untrusted(content)
        evidence, cut1 = _truncate(neutral, min(per_source_chars, budget))
        budget -= len(evidence)
        snippet, _ = _truncate(neutral, DISPLAY_SNIPPET_CHARS)
        report.truncated += int(cut1)
        domain = (urlsplit(url).hostname or "").removeprefix("www.")
        title = _DELIMITER.sub("", _clean(r.title))[:MAX_TITLE_CHARS] or domain
        flags = ["possible_prompt_injection"] if flagged else []
        if _INJECTION.search(title):
            title, flags = domain, sorted(set(flags) | {"possible_prompt_injection"})
        report.sources.append(
            Source(
                id=len(report.sources) + 1, title=title, url=url, domain=domain, evidence=evidence, snippet=snippet,
                published_date=(r.published_date or "")[:40] or None, retrieved_at=retrieved_at, flags=flags,
            )
        )
    return report
