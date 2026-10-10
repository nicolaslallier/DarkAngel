from functools import lru_cache

from pydantic import BaseModel, ConfigDict, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class PortainerInstance(BaseModel):
    """One Portainer the collector polls. `name` becomes the bucket prefix the
    backup script writes to (lower-cased, `_` to `-`)."""

    # SecretStr masks repr/dump; hide_input_in_errors keeps the raw dict (with the
    # key) out of a ValidationError message.
    model_config = ConfigDict(hide_input_in_errors=True)

    name: str
    url: str
    api_key: SecretStr
    insecure: bool = False  # self-signed certificate


class Settings(BaseSettings):
    """Application settings, overridable via environment variables or a .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="DARKANGEL_",
        extra="ignore",
        hide_input_in_errors=True,  # a bad portainer_instances must not echo its keys
    )

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

    # Home files, in the Infra SeaweedFS: plain HTTP `s3:8333` on infra-net. The
    # bucket and its scoped identity are made by Infra's `make s3-provision`.
    s3_endpoint: str = "s3:8333"
    s3_secure: bool = False
    s3_bucket: str = "darkangel-files"
    s3_access_key: str = "darkangel"
    s3_secret_key: str = ""

    # Infra PostgreSQL: the metadata index and the source of truth for what
    # files exist. `make postgres` creates the database and its scoped role;
    # the password arrives as a Portainer stack variable, never in git.
    database_url: str = "postgresql+psycopg://darkangel:@postgres:5432/darkangel"

    # Post-upload AI summary by an Ollama server (`/api/generate`). Empty URL =
    # no summaries. The stack points it at the Ollama on the Mac at 192.168.2.35.
    ollama_url: str = ""
    ollama_model: str = "gemma4:26b-a4b-it-qat"

    # The collector (python -m app.collector). It is the only process that gets
    # Portainer API keys -- Docker-root tokens -- so darkangel-api leaves
    # `portainer_instances` empty. A JSON list in the environment.
    portainer_instances: list[PortainerInstance] = []
    # The archives scripts/portainer-backup.sh writes, read with a read-only
    # identity (Infra: `make s3-provision`, see docs/dashboard.md).
    backup_s3_endpoint: str = "s3:8333"
    backup_s3_secure: bool = False
    backup_s3_bucket: str = "portainer-backups"
    backup_s3_access_key: str = "portainer-backups-ro"
    backup_s3_secret_key: SecretStr = SecretStr("")
    # Older than this = the dashboard marks the backup stale. Read by the API.
    backup_max_age_hours: int = 48
    collector_interval_seconds: int = 300

    # The Prometheus the metrics panels read (node-exporter + cAdvisor). No auth.
    prometheus_url: str = "http://prometheus:9090"
    prometheus_timeout_seconds: float = 5

    # Upload guards. Both are refused with 413: one file over the first, or a
    # file that would push the owner's total over the second.
    max_upload_bytes: int = 5 * 1024 * 1024 * 1024
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
