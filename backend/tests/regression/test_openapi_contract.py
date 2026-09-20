"""Regression — the public API shape is pinned.

An unintended change to a path, a status code or a response model shows up here
as a failing diff. An intended one is `make snapshot`, and the snapshot's diff
is then reviewed in the pull request.

Run as a module (`python -m tests.regression.test_openapi_contract`) to rewrite
the snapshot; `make snapshot` does exactly that.
"""

import json
from pathlib import Path

from app.main import app

SNAPSHOT = Path(__file__).with_name("openapi.snapshot.json")


def dump(schema: dict) -> str:
    # Sorted keys: FastAPI's dict ordering is an implementation detail, and
    # churn in it must not fail the build.
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def test_openapi_matches_the_snapshot():
    assert json.loads(SNAPSHOT.read_text()) == app.openapi(), (
        "The OpenAPI schema no longer matches tests/regression/openapi.snapshot.json. "
        "If the change is intended, run `make snapshot` and review the diff in the PR. "
        "Regenerate only after confirming the API change was intentional — an unpinned "
        "FastAPI/Pydantic upgrade can move the schema too."
    )


if __name__ == "__main__":
    SNAPSHOT.write_text(dump(app.openapi()))
    print(f"wrote {SNAPSHOT}")
