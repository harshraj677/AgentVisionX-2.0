"""Always-on tests for the disposable-test-database guard (no database needed). The real-database tests are only as safe
as this guard, so it must be verified on every machine, not only where a test database exists."""
import pytest

from tests.pg_guard import safe_test_database as guard

LOCAL_OK = "postgresql://u:p@localhost:5432/avx_test"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ("TEST_DATABASE_URL", "DATABASE_URL", "TEST_DATABASE_ALLOW_REMOTE"):
        monkeypatch.delenv(var, raising=False)


def test_without_a_configured_test_database_the_real_database_tests_skip():
    dsn, reason = guard()
    assert dsn is None and "TEST_DATABASE_URL" in reason


def test_blank_value_is_treated_as_unset(monkeypatch):
    monkeypatch.setenv("TEST_DATABASE_URL", "   ")
    assert guard()[0] is None


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://u:p@localhost:5432/avx_test",
        "postgresql://u:p@127.0.0.1:5433/test",
        "postgresql://u:p@[::1]:5432/test_db",
        "postgresql://u:p@LOCALHOST/my-test",
    ],
)
def test_local_databases_named_test_are_accepted(monkeypatch, url):
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    assert guard() == (url, "")


@pytest.mark.parametrize(
    "url, why",
    [
        ("postgresql://u:p@localhost:5432/agentvision", "name"),
        ("postgresql://u:p@localhost:5432/postgres", "name"),
        ("postgresql://u:p@localhost:5432/latest_data", "name"),  # 'test' inside a word is not enough
        ("postgresql://u:p@localhost:5432/contest", "name"),
        ("postgresql://u:p@localhost:5432/", "name"),
        ("postgresql://u:p@db.prod.example.com:5432/avx_test", "remote host"),
        ("postgresql://u:p@10.0.0.5:5432/avx_test", "remote host"),
        ("postgresql://u:p@localhost.evil.example:5432/avx_test", "remote host"),
    ],
)
def test_unsafe_databases_are_refused(monkeypatch, url, why):
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    dsn, reason = guard()
    assert dsn is None and why in reason


def test_the_applications_own_database_is_never_used_as_the_test_database(monkeypatch):
    monkeypatch.setenv("TEST_DATABASE_URL", LOCAL_OK)
    monkeypatch.setenv("DATABASE_URL", LOCAL_OK)
    dsn, reason = guard()
    assert dsn is None and "DATABASE_URL" in reason
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/agentvision")  # a different, real app database
    assert guard() == (LOCAL_OK, "")


def test_remote_hosts_need_an_explicit_opt_in_and_still_need_a_test_name(monkeypatch):
    remote = "postgresql://u:p@db.example.com:5432/avx_test"
    monkeypatch.setenv("TEST_DATABASE_URL", remote)
    assert guard()[0] is None
    monkeypatch.setenv("TEST_DATABASE_ALLOW_REMOTE", "1")
    assert guard() == (remote, "")
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://u:p@db.example.com:5432/production")
    assert guard()[0] is None  # the opt-in never relaxes the name rule
