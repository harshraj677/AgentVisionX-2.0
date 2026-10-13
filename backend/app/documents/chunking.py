"""Deterministic, page-aware chunking. A chunk never spans two pages (so every chunk has one page reference), prefers
sentence and paragraph boundaries, and carries a bounded overlap of whole sentences into the next chunk."""
import re
from dataclasses import dataclass

from app.documents.files import Page


@dataclass(frozen=True)
class Chunk:
    index: int  # order within the document
    page_number: int | None
    text: str


def _units(text: str, size: int) -> list[tuple[str, bool]]:
    """(unit, starts_paragraph). Units are sentences, or word-split pieces of sentences longer than `size`."""
    out: list[tuple[str, bool]] = []
    for para in re.split(r"\n\s*\n", text):
        para = re.sub(r"\s+", " ", para).strip()
        if not para:
            continue
        first = True
        for sentence in re.split(r"(?<=[.!?])\s+", para):
            while len(sentence) > size:
                cut = sentence.rfind(" ", 0, size)
                cut = cut if cut > size // 2 else size
                out.append((sentence[:cut].strip(), first))
                first = False
                sentence = sentence[cut:].strip()
            if sentence:
                out.append((sentence, first))
                first = False
    return out


def _join(units: list[tuple[str, bool]]) -> str:
    text = ""
    for i, (u, new_para) in enumerate(units):
        text += ("" if i == 0 else ("\n\n" if new_para else " ")) + u
    return text


def chunk_pages(pages: list[Page], size: int, overlap: int) -> list[Chunk]:
    chunks: list[Chunk] = []
    for page in pages:
        current: list[tuple[str, bool]] = []
        length = 0
        fresh = 0  # units added since the last emitted chunk (overlap units don't count)
        for unit in _units(page.text, size):
            extra = len(unit[0]) + (1 if current else 0)
            if current and length + extra > size:
                if fresh:
                    chunks.append(Chunk(len(chunks), page.number, _join(current)))
                # carry trailing whole sentences (<= overlap chars) into the next chunk
                carry: list[tuple[str, bool]] = []
                carried = 0
                for u in reversed(current):
                    if carried + len(u[0]) + 1 > overlap:
                        break
                    carry.insert(0, u)
                    carried += len(u[0]) + 1
                if carried + len(unit[0]) + 1 > size:
                    carry, carried = [], 0
                current, length, fresh = carry, carried, 0
                extra = len(unit[0]) + (1 if current else 0)
            current.append(unit)
            length += extra
            fresh += 1
        if current and fresh:
            chunks.append(Chunk(len(chunks), page.number, _join(current)))
    return chunks
