"""Tvheadend's HTTP API uses form fields containing JSON, not a JSON body."""

import json
import re
from typing import Any

import httpx

from .config import Settings

JSON = dict[str, Any] | list[Any]


class TvheadendError(RuntimeError):
    """An actionable upstream API or connection error."""


def encode_params(params: dict[str, Any]) -> dict[str, str]:
    result = {}
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, (dict, list)):
            result[key] = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
        elif isinstance(value, bool):
            result[key] = "1" if value else "0"
        else:
            result[key] = str(value)
    return result


class TvheadendClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        auth = None
        if settings.auth == "digest":
            auth = httpx.DigestAuth(settings.username, settings.password)
        elif settings.auth == "basic":
            auth = httpx.BasicAuth(settings.username, settings.password)
        self.http = httpx.AsyncClient(
            base_url=settings.url.rstrip("/") + "/api/",
            auth=auth,
            timeout=settings.timeout,
            verify=settings.verify_ssl,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    async def close(self) -> None:
        await self.http.aclose()

    async def request(
        self, path: str, params: dict[str, Any] | None = None, *, write: bool = False
    ) -> JSON:
        if not re.fullmatch(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*", path):
            raise ValueError("API paths must be relative endpoint names without query strings")
        if write and self.settings.read_only:
            raise TvheadendError("Writes are disabled by TVH_READ_ONLY=true; use dry_run=true.")
        encoded = encode_params(params or {})
        try:
            if write:
                response = await self.http.post(path, data=encoded)
            else:
                response = await self.http.get(path, params=encoded)
        except httpx.TimeoutException:
            suffix = (
                " The write outcome is unknown. Inspect server state before trying again."
                if write
                else ""
            )
            raise TvheadendError(f"Timeout calling {path}.{suffix}") from None
        except httpx.RequestError:
            raise TvheadendError(
                f"Connection failed for {path}. Check TVH_URL, reachability and TLS settings."
            ) from None
        if response.status_code in {401, 403}:
            raise TvheadendError(
                f"HTTP {response.status_code} for {path}. Check TVH_USER/TVH_PASS, TVH_AUTH "
                "and Tvheadend Web interface, Administrator and Video recorder permissions."
            )
        if response.status_code == 404:
            raise TvheadendError(f"Endpoint {path} is unavailable in this Tvheadend build.")
        if not response.is_success:
            suffix = (
                " The write may have reached Tvheadend. Inspect its state before retrying."
                if write and response.status_code >= 500
                else ""
            )
            raise TvheadendError(
                f"Tvheadend returned HTTP {response.status_code} for {path}.{suffix}"
            )
        if not response.content.strip():
            return {}
        try:
            data = response.json()
        except ValueError:
            raise TvheadendError(
                f"Non-JSON response from {path}; check the URL or proxy."
            ) from None
        if not isinstance(data, (dict, list)):
            raise TvheadendError(f"Unexpected response format from {path}.")
        if isinstance(data, dict) and (
            data.get("success") in (False, 0) or data.get("error") or data.get("errors")
        ):
            raise TvheadendError(f"Tvheadend rejected {path}; inspect the Tvheadend log.")
        return data

    async def grid(
        self,
        path: str,
        *,
        start: int = 0,
        limit: int = 100,
        filters: list[dict[str, Any]] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> JSON:
        if start < 0 or not 1 <= limit <= 1000:
            raise ValueError("start must be >= 0 and limit must be between 1 and 1000")
        return await self.request(
            path, {**(extra or {}), "start": start, "limit": limit, "filter": filters}
        )
