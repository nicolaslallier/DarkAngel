from typing import Any

import uvicorn

from darkangel.api import app


def create_server(
    host: str = "0.0.0.0",
    port: int = 8000,
    **kwargs: Any,
) -> None:
    uvicorn.run(app, host=host, port=port, **kwargs)


def main() -> None:
    create_server()


if __name__ == "__main__":
    main()
