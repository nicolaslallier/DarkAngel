from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, overridable via environment variables or a .env file."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="DARKANGEL_", extra="ignore")

    app_name: str = "DarkAngel API"
    version: str = "0.1.0"
    debug: bool = False
    # Origins allowed to call the API (the Vite dev server by default).
    cors_origins: list[str] = ["http://localhost:5173"]

    # Keycloak realm `ea`. Tokens must carry this exact `iss` (Keycloak pins it to
    # the public URL via KC_HOSTNAME) and the audience the darkangel-spa client's
    # mapper stamps on them.
    auth_issuer: str = "https://keycloak.famillelallier.net/realms/ea"
    auth_audience: str = "darkangel-api"
    # Where the signing keys are fetched; defaults to the issuer's certs endpoint.
    # The stack points it at http://keycloak:8080 on infra-net, which avoids
    # trusting the Infra CA for the public hostname from inside the container.
    auth_jwks_url: str | None = None

    # Home files, in the Infra MinIO: plain HTTP `minio:9000` on infra-net. The
    # bucket and its scoped user are made by `make minio` (scripts/provision-minio.sh).
    s3_endpoint: str = "minio:9000"
    s3_secure: bool = False
    s3_bucket: str = "darkangel-files"
    s3_access_key: str = "darkangel-api"
    s3_secret_key: str = ""

    # Infra PostgreSQL: the metadata index and the source of truth for what
    # files exist. `make postgres` creates the database and its scoped role;
    # the password arrives as a Portainer stack variable, never in git.
    database_url: str = "postgresql+psycopg://darkangel:@postgres:5432/darkangel"

    # Upload guards. Both are refused with 413: one file over the first, or a
    # file that would push the owner's total over the second.
    max_upload_bytes: int = 100 * 1024 * 1024
    user_quota_bytes: int = 5 * 1024 * 1024 * 1024
    # Refused at upload time. Defence in depth only -- the boundary that
    # actually holds is inline_content_types below, which decides what a
    # browser is ever allowed to render inside our own origin.
    denied_extensions: list[str] = [".html", ".htm", ".xhtml", ".svg", ".js", ".mjs"]
    # The only types ever served with `Content-Disposition: inline`. Anything
    # else downloads as an attachment whatever it claims to be, so an uploaded
    # document cannot run script against the SPA's origin.
    inline_content_types: list[str] = [
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
        "application/pdf",
        "text/plain",
    ]


@lru_cache
def get_settings() -> Settings:
    return Settings()
