from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import Depends


def today() -> date:
    """A dependency so tests can pin the date instead of patching `datetime`."""
    return date.today()


def now() -> datetime:
    """Same idea for the instant: ages in hours need more than the date."""
    return datetime.now(UTC)


Today = Annotated[date, Depends(today)]
Now = Annotated[datetime, Depends(now)]
