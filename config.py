from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    base_url: str = "https://www.moltbook.com/api/v1"
    db_path: Path = Path("moltbook.db")
    page_limit: int = 50

    # Rate limiting
    bucket_capacity: int = 100
    bucket_refill_rate: float = 1.667  # tokens per second

    # Concurrency
    max_concurrent: int = 5

    # Retry
    max_retries: int = 3
    retry_base_delay: float = 2.0
    default_429_wait: float = 60.0

    # Incremental sync
    sync_interval_minutes: int = 15

    # User agent
    user_agent: str = "MoltbookCrawler/1.0"
