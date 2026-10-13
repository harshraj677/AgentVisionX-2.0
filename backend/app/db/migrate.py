"""Minimal migration runner: applies app/db/migrations/*.sql in order, once each, inside transactions.

Usage (from backend/, with DATABASE_URL set in .env or the environment):
    python -m app.db.migrate            # apply pending migrations
    python -m app.db.migrate --status   # list applied / pending
"""
import argparse
import sys
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def render(sql: str, dimensions: int) -> str:
    if not isinstance(dimensions, int) or not 1 <= dimensions <= 16000:
        raise ValueError("invalid embedding dimensions")
    return sql.replace("{EMBEDDING_DIMENSIONS}", str(dimensions))


def pending(conn) -> list[Path]:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
    done = {row[0] for row in conn.execute("SELECT name FROM schema_migrations").fetchall()}
    return [p for p in sorted(MIGRATIONS_DIR.glob("*.sql")) if p.name not in done]


def migrate(dsn: str, dimensions: int, status_only: bool = False) -> list[str]:
    import psycopg

    applied: list[str] = []
    with psycopg.connect(dsn, autocommit=True) as conn:
        todo = pending(conn)
        if status_only:
            return [p.name for p in todo]
        for path in todo:
            with conn.transaction():
                conn.execute(render(path.read_text(encoding="utf-8"), dimensions))
                conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,))
            applied.append(path.name)
    return applied


def main() -> int:
    from app.core.config import Settings

    parser = argparse.ArgumentParser(description="Apply AgentVision X database migrations.")
    parser.add_argument("--status", action="store_true", help="only list pending migrations")
    args = parser.parse_args()
    settings = Settings()
    if settings.database_url is None or not settings.database_url.get_secret_value():
        print("DATABASE_URL is not set (backend/.env).", file=sys.stderr)
        return 2
    names = migrate(settings.database_url.get_secret_value(), settings.embedding_dimensions, args.status)
    if args.status:
        print("pending:", ", ".join(names) or "none")
    else:
        print("applied:", ", ".join(names) or "nothing (up to date)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
