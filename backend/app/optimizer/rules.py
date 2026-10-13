"""Deterministic, local prompt-optimization rules.

Nothing here calls a model or the network. Each rule is a small, explainable text transformation.
Protected spans (code, quoted text, URLs, emails, placeholders, long opaque tokens) are masked
before any rule runs, so rules can never alter them.
"""
import re
from dataclasses import dataclass
from typing import Callable

# Private-use characters: never appear in normal prompts, never match \w or \s.
_OPEN, _CLOSE, _MARK = "", "", ""

_PROTECTED = re.compile(
    "|".join(
        [
            r"```.*?(?:```|\Z)",  # fenced code blocks
            r"`[^`\n]+`",  # inline code
            r'"[^"\n]*"',  # double-quoted strings
            r"“[^”\n]*”",  # curly-quoted strings
            r"https?://\S+",  # URLs
            r"\S+@\S+\.\S+",  # emails
            r"\[[A-Z_]{3,}\]",  # redaction placeholders such as [EMAIL_ADDRESS]
            r"\S{20,}",  # long opaque tokens (keys, hashes, paths)
        ]
    ),
    re.DOTALL,
)
_SENTINEL = re.compile(f"{_OPEN}(\\d+){_CLOSE}")


def protected_spans(text: str) -> list[str]:
    return _PROTECTED.findall(text)


def mask(text: str) -> tuple[str, list[str]]:
    spans: list[str] = []

    def hide(m: re.Match) -> str:
        spans.append(m.group())
        return f"{_OPEN}{len(spans) - 1}{_CLOSE}"

    return _PROTECTED.sub(hide, text), spans


def unmask(text: str, spans: list[str]) -> str:
    return _SENTINEL.sub(lambda m: spans[int(m.group(1))], text)


@dataclass(frozen=True)
class Rule:
    name: str
    description: str
    apply: Callable[[str], tuple[str, int]]  # returns (new_text, number_of_edits)
    # Words this rule may delete outright. The preservation check tolerates these (and only these) disappearing.
    removes_words: frozenset[str] = frozenset()


def _sub(pattern: str, repl, text: str, flags: int = re.IGNORECASE) -> tuple[str, int]:
    return re.subn(pattern, repl, text, flags=flags)


# ---- rules -------------------------------------------------------------------------------------

def normalize_whitespace(text: str) -> tuple[str, int]:
    count = 0
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text, n = _sub(r"(?<=\S)[ \t]{2,}", " ", text, 0)  # keeps leading indentation
    count += n
    text, n = _sub(r"[ \t]+$", "", text, re.MULTILINE)
    count += n
    text, n = _sub(r"\n{3,}", "\n\n", text, 0)
    count += n
    return text.strip(), count


def remove_greeting(text: str) -> tuple[str, int]:
    # Requires a comma/exclamation so "Hello world program in C" is left alone.
    return _sub(r"\A\s*(?:hi|hello|hey)(?: there)?\s*[,!]\s*", _MARK, text)


def remove_closing_thanks(text: str) -> tuple[str, int]:
    return _sub(r"[\s,]*\b(?:thanks|thank you)(?: so much| very much)?(?: in advance)?\s*[.!]*\s*\Z", "", text)


def remove_polite_padding(text: str) -> tuple[str, int]:
    total = 0
    for pattern in (
        r"\b(?:could|can|would|will) you (?:please |kindly )",
        r"\bi was wondering if you could (?:please )?",
        r"\bi would like you to ",
        r"\bi want you to ",
        r"\bkindly ",
    ):
        text, n = _sub(pattern, _MARK, text)
        total += n
    text, n = _sub(r"\s*,?\s*\bplease\b(?=\s*[.?!]|\s*\Z)", "", text)  # "..., please." -> "."
    total += n
    text, n = _sub(r"\bplease\b,?\s*", _MARK, text)
    return text, total + n


def remove_filler_words(text: str) -> tuple[str, int]:
    return _sub(r"\b(?:to be honest|basically|honestly)\b,?\s*", _MARK, text)


_VERBOSE = {
    "in order to": "to",
    "due to the fact that": "because",
    "in the event that": "if",
    "for the purpose of": "for",
    "make use of": "use",
    "at this point in time": "now",
    "at the present time": "now",
    "each and every": "every",
    "first and foremost": "first",
}


def shorten_verbose_phrases(text: str) -> tuple[str, int]:
    def swap(m: re.Match) -> str:
        out = _VERBOSE[m.group().lower()]
        return out.capitalize() if m.group()[0].isupper() else out

    return _sub(r"\b(?:" + "|".join(map(re.escape, _VERBOSE)) + r")\b", swap, text)


_KEEP_DOUBLED = {"had", "that", "no"}


def collapse_repeated_words(text: str) -> tuple[str, int]:
    def fix(m: re.Match) -> str:
        return m.group() if m.group(1).lower() in _KEEP_DOUBLED else m.group(1)

    return _sub(r"\b([A-Za-z]+)[ \t]+\1\b", fix, text)


def remove_duplicate_sentences(text: str) -> tuple[str, int]:
    seen: set[str] = set()
    removed = 0
    out_lines = []
    for line in text.split("\n"):
        kept = []
        for sentence in re.split(r"(?<=[.!?])\s+", line):
            key = re.sub(r"[\W_]+", " ", sentence.casefold()).strip()
            if len(key.split()) >= 3 and key in seen:
                removed += 1
                continue
            seen.add(key)
            kept.append(sentence)
        out_lines.append(" ".join(kept))
    return "\n".join(out_lines), removed


def tidy_punctuation(text: str) -> tuple[str, int]:
    text, n = _sub(r"[ \t]+([,.;:!?])", r"\1", text, 0)
    text, m = _sub(r"\n{3,}", "\n\n", text, 0)
    text, k = _sub(r"[ \t]+$", "", text, re.MULTILINE)
    return text.strip(), n + m + k


def _resolve_markers(text: str) -> str:
    """Drops removal markers; capitalises the next letter only where a sentence begins at the marker."""
    text = re.sub(f"{_MARK}+[ \t]*", _MARK, text)
    out: list[str] = []
    i = 0
    while True:
        j = text.find(_MARK, i)
        if j == -1:
            out.append(text[i:])
            break
        out.append(text[i:j])
        built = "".join(out)
        at_start = built.strip() == "" or re.search(r"(?:[.!?]\s*|\n\s*)\Z", built) is not None
        k = j + 1
        if k < len(text) and text[k].isalpha() and text[k].islower() and at_start:
            out.append(text[k].upper())
            k += 1
        i = k
    return "".join(out)


RULES: list[Rule] = [
    Rule("normalize_whitespace", "Collapsed extra spaces and blank lines", normalize_whitespace),
    Rule("remove_greeting", "Removed an opening greeting", remove_greeting, frozenset({"hi", "hello", "hey", "there"})),
    Rule(
        "remove_closing_thanks",
        "Removed a closing thank-you",
        remove_closing_thanks,
        frozenset({"thanks", "thank", "you", "so", "very", "much", "in", "advance"}),
    ),
    Rule(
        "remove_polite_padding",
        "Removed filler politeness (e.g. \"could you please\")",
        remove_polite_padding,
        frozenset({"could", "can", "would", "will", "you", "please", "kindly", "i", "was", "wondering", "if",
                   "like", "to", "want"}),
    ),
    Rule("remove_filler_words", "Removed filler words", remove_filler_words, frozenset({"basically", "honestly", "to", "be", "honest"})),
    Rule(
        "shorten_verbose_phrases",
        "Replaced wordy phrases with shorter equivalents",
        shorten_verbose_phrases,
        frozenset(w for p in _VERBOSE for w in p.split()),
    ),
    Rule("collapse_repeated_words", "Collapsed accidentally repeated words", collapse_repeated_words),
    Rule("remove_duplicate_sentences", "Removed sentences that repeat earlier ones", remove_duplicate_sentences),
    Rule("tidy_punctuation", "Tidied spacing around punctuation", tidy_punctuation),
]

ALLOWED_REMOVED_WORDS: frozenset[str] = frozenset(w for r in RULES for w in r.removes_words)


def apply_rules(text: str, rules: list[Rule] | None = None) -> tuple[str, list[tuple[Rule, int]]]:
    """Runs every rule on the masked text. Returns the optimized text and (rule, edit_count) for rules that fired."""
    masked, spans = mask(text)
    applied: list[tuple[Rule, int]] = []
    for rule in rules if rules is not None else RULES:
        masked, count = rule.apply(masked)
        if count:
            applied.append((rule, count))
    return unmask(_resolve_markers(masked), spans), applied
