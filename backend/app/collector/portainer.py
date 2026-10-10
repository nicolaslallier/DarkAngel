from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.core.config import PortainerInstance


def slug(name: str) -> str:
    """The bucket prefix scripts/portainer-backup.sh uses for an instance."""
    return name.lower().replace("_", "-")


@dataclass(frozen=True)
class Reading:
    reachable: bool
    version: str | None = None
    environments: int | None = None
    stacks: int | None = None
    error: str | None = None


def _get(client: httpx.Client, path: str):
    response = client.get(path)
    response.raise_for_status()
    return response.json()


def _count(client: httpx.Client, path: str) -> int:
    body = _get(client, path)
    if not isinstance(body, list):
        raise ValueError(f"{path} did not return a list")
    return len(body)


def check(instance: PortainerInstance, transport: httpx.BaseTransport | None = None) -> Reading:
    """Read-only look at one Portainer. Anything wrong with the instance or its
    reply becomes `reachable=False` plus a short message; it never raises."""
    try:
        with httpx.Client(
            base_url=instance.url.rstrip("/"),
            headers={"X-API-KEY": instance.api_key},
            timeout=10.0,
            verify=not instance.insecure,
            transport=transport,
        ) as client:
            version = str(_get(client, "/api/system/status")["Version"])
            environments = _count(client, "/api/endpoints")
            stacks = _count(client, "/api/stacks")
    except (httpx.HTTPError, httpx.InvalidURL, ValueError, KeyError, TypeError) as e:
        # httpx messages carry the URL, never request headers, so the key stays out.
        return Reading(False, error=f"{type(e).__name__}: {e}"[:200])
    return Reading(True, version, environments, stacks)
