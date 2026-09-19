import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("JEVTWEET_DATA_DIR", "private")))
    account_dir: Path = field(
        default_factory=lambda: (
            Path(
                os.getenv(
                    "JEVTWEET_ACCOUNT_DIR",
                    str(Path(__file__).resolve().parent.parent / "private" / "account"),
                )
            )
            .expanduser()
            .resolve()
        )
    )
    spend_limit_usd: float = field(default_factory=lambda: float(os.getenv("JEVTWEET_SPEND_LIMIT_USD", "0")))
    concurrency: int = field(default_factory=lambda: int(os.getenv("JEVTWEET_CONCURRENCY", "2")))
    requests_per_minute: int = field(
        default_factory=lambda: int(os.getenv("JEVTWEET_REQUESTS_PER_MINUTE", "30"))
    )
    timeout_seconds: float = field(default_factory=lambda: float(os.getenv("JEVTWEET_TIMEOUT_SECONDS", "45")))
    max_attempts: int = field(default_factory=lambda: int(os.getenv("JEVTWEET_MAX_ATTEMPTS", "3")))
    max_rows: int = field(default_factory=lambda: int(os.getenv("JEVTWEET_MAX_ROWS", "1000")))
    model: str = "jev-1.13.0"
    input_price_per_million: float = 0.042
    price_as_of: str = "2026-09-18"
    max_request_tokens: int = field(
        default_factory=lambda: int(os.getenv("JEVTWEET_MAX_REQUEST_TOKENS", "24000"))
    )
    max_state_question_tokens: int = field(
        default_factory=lambda: int(os.getenv("JEVTWEET_MAX_STATE_QUESTION_TOKENS", "16000"))
    )

    def __post_init__(self):
        import math

        if not math.isfinite(self.spend_limit_usd) or self.spend_limit_usd < 0:
            raise ValueError("spend limit must be finite and nonnegative")
        if not 1 <= self.concurrency <= 8 or not 1 <= self.requests_per_minute <= 120:
            raise ValueError("concurrency must be 1–8 and rate 1–120")
        if not math.isfinite(self.timeout_seconds) or not 1 <= self.timeout_seconds <= 120:
            raise ValueError("timeout must be 1–120 seconds")

        if not 1 <= self.max_attempts <= 5 or not 1 <= self.max_rows <= 5000:
            raise ValueError("Attempts must be 1–5 and corpus rows 1–5000")
        if (
            not 1024 <= self.max_request_tokens <= 64000
            or not 1024 <= self.max_state_question_tokens <= 32000
        ):
            raise ValueError("Request limits must respect the pinned model's context bounds")
