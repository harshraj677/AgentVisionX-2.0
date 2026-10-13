"""Safety guard for the opt-in real-PostgreSQL tests. These tests insert and delete rows, so they must only ever run
against a database that is clearly disposable. Returns (dsn, "") when safe, or (None, reason) when the tests must skip."""
import os
import re
from urllib.parse import urlsplit

_TEST_NAME = re.compile(r"(^|[^a-z])test([^a-z]|$)")  # avx_test, test_db, my-test; NOT latest_data / contest
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def safe_test_database() -> tuple[str | None, str]:
    dsn = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not dsn:
        return None, "set TEST_DATABASE_URL to a disposable PostgreSQL+pgvector database to run"
    parts = urlsplit(dsn)
    name = parts.path.lstrip("/").lower()
    if not _TEST_NAME.search(name):
        return None, "refusing: the database name must contain 'test' as a separate word (e.g. avx_test)"
    if (parts.hostname or "").lower() not in _LOCAL_HOSTS and os.environ.get("TEST_DATABASE_ALLOW_REMOTE") != "1":
        return None, "refusing: remote host (set TEST_DATABASE_ALLOW_REMOTE=1 only for a disposable remote test database)"
    if dsn == os.environ.get("DATABASE_URL", "").strip():
        return None, "refusing: TEST_DATABASE_URL equals DATABASE_URL (the app's own database)"
    return dsn, ""
