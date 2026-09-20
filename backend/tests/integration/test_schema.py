"""Integration — the schema, against a real PostgreSQL.

Everything here needs SQL that SQLite cannot run (uuid, text[], jsonb, partial
indexes with NULLS NOT DISTINCT), which is why none of it is a unit test.
"""

from sqlalchemy import text


def test_the_database_is_reachable(db):
    assert db.scalar(text("SELECT 1")) == 1


def test_it_is_postgres_15_or_newer(db):
    # `UNIQUE NULLS NOT DISTINCT` in migration 0001 needs 15+.
    major = db.scalar(text("SHOW server_version_num"))
    assert int(major) >= 150000
