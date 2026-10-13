import re
from dataclasses import dataclass, field
from typing import Protocol

from app.optimizer.rules import ALLOWED_REMOVED_WORDS, RULES, Rule, apply_rules, protected_spans

_WORD = re.compile(r"[^\W_]+(?:'[^\W_]+)?", re.UNICODE)
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


@dataclass
class Change:
    rule: str
    description: str
    count: int


@dataclass
class Check:
    name: str
    passed: bool


@dataclass
class OptimizationResult:
    text: str
    changes: list[Change] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)
    verified: bool = True
    note: str = ""

    @property
    def changed(self) -> bool:
        return bool(self.changes)


class PromptOptimizer(Protocol):
    """Extension point. A future model-based optimizer must implement this too, and may only ever be
    given text that has already passed the privacy policy (see PrivacyService), never a raw prompt."""

    def optimize(self, text: str) -> OptimizationResult: ...


def _words(text: str) -> set[str]:
    return {w.casefold() for w in _WORD.findall(text)}


def check_preservation(original: str, optimized: str) -> list[Check]:
    """Heuristic safety net, not a proof of equivalence: nothing meaningful may disappear."""
    lost_words = _words(original) - _words(optimized) - ALLOWED_REMOVED_WORDS
    return [
        Check("not_empty", bool(optimized.strip())),
        Check("protected_spans_kept", set(protected_spans(original)) == set(protected_spans(optimized))),
        Check("numbers_kept", set(_NUMBER.findall(original)) <= set(_NUMBER.findall(optimized))),
        Check("no_content_words_lost", not lost_words),
    ]


class RuleBasedOptimizer:
    """Deterministic, local, free. Same input always gives the same output. Falls back to the
    original text if the preservation check fails."""

    def __init__(self, rules: list[Rule] | None = None):
        self._rules = rules if rules is not None else RULES

    def optimize(self, text: str) -> OptimizationResult:
        optimized, applied = apply_rules(text, self._rules)
        checks = check_preservation(text, optimized)
        if not all(c.passed for c in checks):
            return OptimizationResult(
                text=text,
                checks=checks,
                verified=False,
                note="An automatic check found the rewrite might change meaning, so the original was kept.",
            )
        if optimized == text:
            return OptimizationResult(text=text, checks=checks, note="No safe improvements found.")
        changes = [Change(rule.name, rule.description, count) for rule, count in applied]
        return OptimizationResult(text=optimized, changes=changes, checks=checks)
