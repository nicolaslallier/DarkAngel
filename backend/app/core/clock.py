from datetime import date
from typing import Annotated

from fastapi import Depends


def today() -> date:
    """A dependency so tests can pin the date instead of patching `datetime`."""
    return date.today()


Today = Annotated[date, Depends(today)]
