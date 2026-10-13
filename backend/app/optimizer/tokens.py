"""Local token counting. Every number produced here is an ESTIMATE and is labelled as such.

tiktoken implements OpenAI's tokenizers. Gemini (and Claude) tokenize differently, so counts here are a
consistent yardstick for comparing two prompts, not the number a provider will bill. Real, provider-reported
usage only exists after a request is sent and is shown separately (metadata.usage).
"""
import logging
import math
from typing import Protocol

logger = logging.getLogger("agentvision.optimizer")


class TokenCounter(Protocol):
    method: str
    note: str

    def count(self, text: str) -> int: ...


class TiktokenCounter:
    method = "tiktoken:o200k_base"
    note = "Estimate using OpenAI's o200k_base tokenizer. Gemini's tokenizer differs, so real counts will vary."

    def __init__(self) -> None:
        import tiktoken

        # Fetches the public vocabulary file on first use, then caches it. No prompt text is sent.
        self._encoding = tiktoken.get_encoding("o200k_base")

    def count(self, text: str) -> int:
        return len(self._encoding.encode(text, disallowed_special=()))


class HeuristicCounter:
    method = "heuristic:chars/4"
    note = "Rough estimate (about 4 characters per token) because a real tokenizer was unavailable."

    def count(self, text: str) -> int:
        return math.ceil(len(text) / 4)


def build_counter() -> TokenCounter:
    try:
        return TiktokenCounter()
    except Exception as exc:  # missing package, offline first run, corrupt cache...
        logger.warning("tiktoken unavailable (%s); using heuristic token estimates", type(exc).__name__)
        return HeuristicCounter()
