"""Development-mode document scope.

AgentVision X has no user accounts yet. Each app install generates a random secret "scope key" and sends it in the
X-Document-Scope header. The server stores only SHA-256(key) as the owner of a document and filters EVERY document
read, search, update and delete by it, so a document ID alone never grants access.

This is a bearer capability, NOT authentication: anyone who obtains the key can access that scope, and losing it (e.g.
clearing app data) loses access. Do not treat it as production-grade multi-user isolation.
"""
import hashlib
import re

from fastapi import Request

from app.core.errors import AppError

HEADER = "X-Document-Scope"
_VALID = re.compile(r"^[A-Za-z0-9_-]{32,128}$")


def owner_scope(request: Request) -> str:
    key = request.headers.get(HEADER)
    if not key:
        raise AppError(401, "document_scope_required", f"Document requests need the {HEADER} header (set automatically by the app).")
    if not _VALID.match(key):
        raise AppError(401, "document_scope_invalid", f"The {HEADER} value is malformed.")
    return hashlib.sha256(key.encode("ascii")).hexdigest()
