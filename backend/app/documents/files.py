"""Upload validation and safe text extraction (PDF via pypdf, TXT). Everything happens in memory: the bytes are never
written to a path we choose, the client filename is never used as a path, and nothing in a document is executed.
pypdf only parses; it does not run PDF JavaScript or embedded files. No OCR: scanned PDFs have no extractable text.
"""
import io
import logging
import re
import unicodedata
from dataclasses import dataclass

logger = logging.getLogger("agentvision.documents")


class DocumentError(Exception):
    """A user-facing, content-free error (safe to return and log)."""

    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code, self.code, self.message = status_code, code, message


@dataclass(frozen=True)
class Page:
    index: int  # 0-based order within the document
    number: int | None  # 1-based PDF page number; None for TXT
    text: str


_BINARY_SIGNATURES = (b"%PDF", b"PK\x03\x04", b"MZ", b"\x7fELF", b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"\xd0\xcf\x11\xe0", b"\x1f\x8b", b"Rar!", b"7z\xbc\xaf")
_SAFE_NAME = re.compile(r"[^A-Za-z0-9 ._()\-]+")


def sanitize_filename(raw: str | None, file_type: str) -> str:
    """Display name only (never a path): last path component, safe characters, bounded length, correct extension."""
    name = (raw or "").replace("\\", "/").split("/")[-1]
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    name = _SAFE_NAME.sub("_", name).strip(" ._")
    name = re.sub(r"_{2,}", "_", name)
    stem = re.sub(r"\.(pdf|txt)$", "", name, flags=re.IGNORECASE).strip(" ._") or "document"
    return f"{stem[:100]}.{file_type}"


def detect_type(data: bytes, filename: str | None) -> str:
    """Decides pdf/txt from CONTENT, and requires the extension to agree. Anything else is rejected."""
    ext = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
    if ext not in ("pdf", "txt"):
        raise DocumentError(415, "document_unsupported_type", "Only .pdf and .txt files are supported.")
    head = data[:1024]
    if ext == "pdf":
        if b"%PDF-" not in head:
            raise DocumentError(415, "document_unsupported_type", "The file does not look like a PDF.")
        return "pdf"
    if any(data.startswith(sig) for sig in _BINARY_SIGNATURES) or b"\x00" in data[:8192]:
        raise DocumentError(415, "document_unsupported_type", "The file does not look like plain text.")
    return "txt"


def _clean_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return "\n".join(line.strip() for line in text.split("\n")).strip()


def extract_txt(data: bytes) -> list[Page]:
    # UTF-8 only: UTF-16 "decodes" almost any byte sequence, which would make this validation meaningless.
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise DocumentError(422, "document_malformed", "Text files must be UTF-8 encoded.") from None
    controls = sum(1 for c in text[:20000] if ord(c) < 32 and c not in "\n\r\t")
    if controls > max(10, len(text[:20000]) // 50):
        raise DocumentError(415, "document_unsupported_type", "The file does not look like plain text.")
    return [Page(0, None, _clean_text(text))]


def extract_pdf(data: bytes, max_pages: int) -> list[Page]:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            raise DocumentError(422, "document_encrypted", "Password-protected PDFs are not supported.")
        count = len(reader.pages)
    except DocumentError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError, AttributeError, RecursionError, OSError) as exc:
        logger.info("pdf open failed: %s", type(exc).__name__)
        raise DocumentError(422, "document_malformed", "The PDF could not be read (it may be damaged).") from None
    if count == 0:
        raise DocumentError(422, "document_malformed", "The PDF has no pages.")
    if count > max_pages:
        raise DocumentError(413, "document_too_many_pages", f"PDFs are limited to {max_pages} pages.")
    pages = []
    for i in range(count):
        try:
            text = reader.pages[i].extract_text() or ""
        except Exception as exc:  # one bad page should not reveal details or kill the rest
            logger.info("pdf page extraction failed: %s", type(exc).__name__)
            text = ""
        pages.append(Page(i, i + 1, _clean_text(text)))
    return pages


def extract(data: bytes, file_type: str, *, max_pages: int, max_chars: int, min_chars: int) -> list[Page]:
    pages = extract_pdf(data, max_pages) if file_type == "pdf" else extract_txt(data)
    total = sum(len(p.text) for p in pages)
    if total > max_chars:
        raise DocumentError(413, "document_text_too_long", f"The document has more than {max_chars} characters of text.")
    if total < min_chars:
        hint = " It may be a scanned PDF; OCR is not supported." if file_type == "pdf" else ""
        raise DocumentError(422, "document_no_text", f"No usable text was found in the document.{hint}")
    return [p for p in pages if p.text]
