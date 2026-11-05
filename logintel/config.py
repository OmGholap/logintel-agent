import os
from dataclasses import dataclass, field
from typing import Optional

from dotenv import find_dotenv, load_dotenv


@dataclass(frozen=True)
class Config:
    """Settings for one Agent. Nothing here is read at import time."""
    loki_url: str = "http://localhost:3100"
    loki_user: Optional[str] = None
    loki_password: Optional[str] = field(default=None, repr=False)
    openai_api_key: Optional[str] = field(default=None, repr=False)
    model: str = "gpt-5-mini"
    lookback_days: int = 365           # how far back to read logs from Loki
    snapshot_ttl_seconds: float = 60   # re-read Loki when the last read is older than this

    @classmethod
    def from_env(cls) -> "Config":
        """Read LOKI_URL, LOKI_USER, LOKI_PASSWORD, OPENAI_API_KEY, OPENAI_CHAT_MODEL,
        LOGINTEL_LOOKBACK_DAYS and LOGINTEL_SNAPSHOT_TTL. A .env file in the working
        directory is loaded first; real environment variables win over it."""
        load_dotenv(find_dotenv(usecwd=True))
        env = os.environ.get
        defaults = cls()
        return cls(
            loki_url=env("LOKI_URL", defaults.loki_url),
            loki_user=env("LOKI_USER"),
            loki_password=env("LOKI_PASSWORD"),
            openai_api_key=env("OPENAI_API_KEY"),
            model=env("OPENAI_CHAT_MODEL", defaults.model),
            lookback_days=int(env("LOGINTEL_LOOKBACK_DAYS", defaults.lookback_days)),
            snapshot_ttl_seconds=float(env("LOGINTEL_SNAPSHOT_TTL", defaults.snapshot_ttl_seconds)),
        )
