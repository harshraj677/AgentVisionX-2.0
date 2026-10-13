import hashlib
import logging
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass

from app.privacy.recognizers import build_custom_recognizers

logger = logging.getLogger("agentvision.privacy")

# Presidio logs the text it analyses (e.g. "Context list is: <your prompt words>") at DEBUG level.
# Pin its loggers to WARNING so a DEBUG LOG_LEVEL can never write prompts to the logs.
for _name in ("presidio-analyzer", "presidio_analyzer"):
    logging.getLogger(_name).setLevel(logging.WARNING)

# Entities we scan for. Deliberately excludes noisy ones (DATE_TIME, ORGANIZATION, URL, LOCATION).
ENTITIES = [
    "PERSON", "EMAIL_ADDRESS", "PHONE_NUMBER", "CREDIT_CARD", "US_SSN", "US_PASSPORT",
    "US_DRIVER_LICENSE", "US_BANK_NUMBER", "US_ITIN", "IBAN_CODE", "IP_ADDRESS", "CRYPTO",
    "MEDICAL_LICENSE",
    # custom rules (app/privacy/recognizers.py)
    "API_KEY", "PRIVATE_KEY", "PASSWORD_OR_SECRET", "IN_AADHAAR", "IN_PAN",
]

# Our own redaction placeholders, e.g. "[EMAIL_ADDRESS]". The NER model mistakes them for names,
# so they are blanked out (same length, offsets unchanged) before analysis. Only exact category
# names are blanked, so a secret wrapped in brackets can't hide this way.
_PLACEHOLDER = re.compile(r"\[(?:" + "|".join(ENTITIES) + r")\]")

SPACY_MODEL = "en_core_web_sm"
_CACHE_SIZE = 256


@dataclass(frozen=True)
class RawFinding:
    category: str
    start: int  # Python str index (Unicode code points)
    end: int
    score: float


class PrivacyDetector:
    """Wraps Microsoft Presidio. Loads lazily (the spaCy model takes a few seconds)."""

    def __init__(self, min_score: float = 0.4):
        self._min_score = min_score
        self._analyzer = None
        self._lock = threading.Lock()
        # Keyed by a hash so the cache never holds prompt text.
        self._cache: OrderedDict[str, tuple[RawFinding, ...]] = OrderedDict()

    @property
    def ready(self) -> bool:
        return self._analyzer is not None

    def warm_up(self) -> None:
        with self._lock:
            self._ensure_loaded()

    def _ensure_loaded(self) -> None:
        if self._analyzer is not None:
            return
        from presidio_analyzer import AnalyzerEngine, RecognizerRegistry
        from presidio_analyzer.nlp_engine import NlpEngineProvider

        nlp = NlpEngineProvider(
            nlp_configuration={
                "nlp_engine_name": "spacy",
                "models": [{"lang_code": "en", "model_name": SPACY_MODEL}],
            }
        ).create_engine()
        registry = RecognizerRegistry(supported_languages=["en"])
        registry.load_predefined_recognizers(languages=["en"], nlp_engine=nlp)
        for recognizer in build_custom_recognizers():
            registry.add_recognizer(recognizer)
        self._analyzer = AnalyzerEngine(nlp_engine=nlp, registry=registry, supported_languages=["en"])
        logger.info("Presidio analyzer loaded")

    def detect(self, text: str) -> list[RawFinding]:
        key = hashlib.sha256(text.encode("utf-8")).hexdigest()
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return list(cached)
            self._ensure_loaded()
            masked = _PLACEHOLDER.sub(lambda m: " " * len(m.group()), text)
            results = self._analyzer.analyze(  # type: ignore[union-attr]
                text=masked, language="en", entities=ENTITIES, score_threshold=self._min_score
            )
            findings = tuple(
                RawFinding(r.entity_type, r.start, r.end, float(r.score)) for r in results if r.end > r.start
            )
            self._cache[key] = findings
            if len(self._cache) > _CACHE_SIZE:
                self._cache.popitem(last=False)
            return list(findings)
