"""Deterministic, local task classification.

Keyword/pattern scoring only: no model, no network, and it never alters the prompt. It is a hint for routing,
not an understanding of the task. When the evidence is thin or ambiguous the answer is "unknown", and the
router then uses the configured default model.
"""
import re
from dataclasses import dataclass

from app.schemas.routing import Confidence, TaskType

# (task, rule name, pattern, weight). Rule NAMES are reported as signals; matched text never is.
_RULES: list[tuple[str, str, re.Pattern, int]] = [
    # --- coding
    ("coding", "code_fence", re.compile(r"```"), 3),
    ("coding", "error_trace", re.compile(r"\b\w*(?:Error|Exception):|\bat line \d+|traceback \(most recent|stack ?trace", re.I), 3),
    ("coding", "code_syntax", re.compile(r"\b(?:def|elif|const|printf|println|#include|console\.log|npm (?:install|run)|pip install|git (?:commit|push|rebase|merge))\b|=>|\w\(\)\s*\{"), 2),
    ("coding", "code_words", re.compile(r"\b(?:debug|refactor|compile|compiler|unit tests?|regex|sql quer(?:y|ies)|api endpoint|syntax error|null ?pointer|segfault|runtime error)\b", re.I), 2),
    ("coding", "code_nouns", re.compile(r"\b(?:function|method|class|script|snippet|algorithm|codebase|source code|program|bug)\b", re.I), 1),
    ("coding", "languages", re.compile(r"\b(?:python|javascript|typescript|java|c\+\+|c#|rust|golang|kotlin|swift|sql|html|css|react|django|flask|fastapi|bash|powershell)\b", re.I), 1),
    # --- summarization
    ("summarization", "summarize_verbs", re.compile(r"\b(?:summari[sz]e|summary|tl;?dr|condense|recap|key (?:points|takeaways)|main points|shorten (?:this|the following))\b", re.I), 3),
    ("summarization", "summary_length", re.compile(r"\bin (?:\d+|one|two|three|a few) (?:sentences?|bullets?|bullet points?|lines?|words)\b", re.I), 1),
    # --- extraction
    ("extraction", "extract_verbs", re.compile(r"\b(?:extract|pull out|parse|identify all|list all)\b", re.I), 2),
    ("extraction", "structured_output", re.compile(r"\b(?:as|in|into|to) (?:a )?(?:json|csv|yaml|xml|table|key[- ]value)\b|\bjson (?:format|object|schema|array)\b|\bfields?:", re.I), 2),
    ("extraction", "entity_words", re.compile(r"\b(?:names|dates|emails|prices|addresses|entities|invoice|receipt)\b", re.I), 1),
    # --- reasoning
    ("reasoning", "reasoning_verbs", re.compile(r"\b(?:step[- ]by[- ]step|prove|proof|derive|solve|calculate|compute|puzzle|riddle|theorem|probability|equation|optimi[sz]e)\b", re.I), 2),
    ("reasoning", "analysis_words", re.compile(r"\b(?:trade-?offs?|pros and cons|compare and contrast|analy[sz]e|reasoning|logic|why (?:does|do|is|would|did)|how many)\b", re.I), 2),
    ("reasoning", "math_expression", re.compile(r"\d+\s*[-+*/^=]\s*\d+"), 2),
    # --- conversation
    ("conversation", "greeting", re.compile(r"^\s*(?:hi|hello|hey|good (?:morning|afternoon|evening)|how are you|what'?s up|thanks?|thank you|who are you)\b", re.I), 3),
    ("conversation", "chit_chat", re.compile(r"\b(?:tell me a joke|fun fact|let'?s chat|recommend (?:me )?(?:a )?(?:movie|book|song|show))\b", re.I), 3),
]

_MIN_SCORE = 2  # below this there is not enough evidence
_MIN_MARGIN = 1  # the winner must beat the runner-up by at least this much


@dataclass(frozen=True)
class TaskClassification:
    task_type: TaskType
    confidence: Confidence
    signals: tuple[str, ...]  # rule names that fired, e.g. ("code_fence", "languages")

    @property
    def is_known(self) -> bool:
        return self.task_type != "unknown" and self.confidence != "low"


UNKNOWN = TaskClassification("unknown", "low", ())


def classify(text: str) -> TaskClassification:
    scores: dict[str, int] = {}
    fired: dict[str, list[str]] = {}
    for task, name, pattern, weight in _RULES:
        if pattern.search(text):
            scores[task] = scores.get(task, 0) + weight
            fired.setdefault(task, []).append(name)
    if not scores:
        return UNKNOWN
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    top_task, top_score = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0
    margin = top_score - runner_up
    if top_score < _MIN_SCORE or margin < _MIN_MARGIN:
        return TaskClassification("unknown", "low", tuple(sorted(n for names in fired.values() for n in names)))
    confidence: Confidence = "high" if top_score >= 4 and margin >= 2 else "medium"
    return TaskClassification(top_task, confidence, tuple(fired[top_task]))  # type: ignore[arg-type]
