"""Environment-based connection settings; credentials never appear in repr()."""

import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name, str(default)).lower().strip()
    if value not in {"true", "false", "1", "0", "yes", "no"}:
        raise ValueError(f"{name} must be true or false")
    return value in {"true", "1", "yes"}


@dataclass(frozen=True)
class Settings:
    url: str
    username: str = field(default="", repr=False)
    password: str = field(default="", repr=False)
    auth: str = "digest"
    verify_ssl: bool = True
    timeout: float = 30
    timezone: str = "Europe/Berlin"
    read_only: bool = False
    state_dir: Path = field(default_factory=lambda: Path.home() / ".local/state/tvheadend-mcp")

    def __post_init__(self) -> None:
        parsed = urlsplit(self.url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path.rstrip("/").endswith("/api")
        ):
            raise ValueError("TVH_URL must be an HTTP(S) base URL without credentials or /api")
        # Also validate malformed ports before creating the HTTP client.
        _ = parsed.port
        if self.auth not in {"digest", "basic", "none"}:
            raise ValueError("TVH_AUTH must be digest, basic or none")
        if not self.state_dir.is_absolute():
            raise ValueError("TVH_STATE_DIR must be an absolute path")
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError("TVH_TIMEOUT must be a positive finite number")
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError:
            raise ValueError("TVH_TIMEZONE must be an IANA timezone") from None

    @classmethod
    def from_env(cls) -> "Settings":
        url = os.getenv("TVH_URL", "").strip().rstrip("/")
        if not url:
            raise ValueError(
                "TVH_URL is missing. Copy .env.example to .env and configure Tvheadend."
            )
        return cls(
            url=url,
            username=os.getenv("TVH_USER", ""),
            password=os.getenv("TVH_PASS", ""),
            auth=os.getenv("TVH_AUTH", "digest").lower().strip(),
            verify_ssl=env_bool("TVH_VERIFY_SSL", True),
            timeout=float(os.getenv("TVH_TIMEOUT", "30")),
            timezone=os.getenv("TVH_TIMEZONE", "Europe/Berlin"),
            read_only=env_bool("TVH_READ_ONLY", False),
            state_dir=Path(
                os.getenv("TVH_STATE_DIR", str(Path.home() / ".local/state/tvheadend-mcp"))
            ).expanduser(),
        )
