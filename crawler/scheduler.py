"""Periodic incremental sync scheduler."""

import asyncio
import logging

from config import Config
from crawler.sync import run_incremental

logger = logging.getLogger(__name__)


async def run_daemon(config: Config):
    """Run incremental sync on a loop."""
    interval = config.sync_interval_minutes * 60
    logger.info("Daemon started, syncing every %d minutes", config.sync_interval_minutes)

    while True:
        try:
            logger.info("Starting incremental sync...")
            await run_incremental(config)
            logger.info("Incremental sync complete, sleeping %d minutes", config.sync_interval_minutes)
        except Exception as e:
            logger.error("Sync error: %s", e)

        await asyncio.sleep(interval)
