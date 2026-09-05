from fastapi import FastAPI

VERSION = "1.0.0"


def health_payload() -> dict[str, str]:
    return {"status": "ok"}


def hello_payload() -> dict[str, str]:
    return {"message": "Hello, World!"}


def create_app() -> FastAPI:
    app = FastAPI(title="DarkAngel API", version=VERSION)

    @app.get("/health")
    def health() -> dict[str, str]:
        return health_payload()

    @app.get("/")
    def hello() -> dict[str, str]:
        return hello_payload()

    return app


app = create_app()

