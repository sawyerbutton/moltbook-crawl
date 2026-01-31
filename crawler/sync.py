"""Full and incremental sync orchestration."""

import asyncio
import logging
from datetime import datetime, timezone

from config import Config
from crawler.client import MoltbookClient
from store.db import Database

logger = logging.getLogger(__name__)


async def crawl_submolts(client: MoltbookClient, db: Database):
    """Fetch all submolts with pagination."""
    log_id = db.start_crawl_log("submolts")
    offset = int(db.get_sync_state("submolts_offset", "0"))
    total = 0
    errors = 0

    try:
        while True:
            data = await client.get_submolts(offset=offset)
            submolts = data.get("submolts", [])
            if not submolts:
                break

            with db.transaction() as conn:
                for s in submolts:
                    db.upsert_submolt(s)
                total += len(submolts)

            offset += len(submolts)
            db.set_sync_state("submolts_offset", str(offset))

            if not data.get("has_more", len(submolts) >= 50):
                break

            logger.info("Submolts: fetched %d so far (offset %d)", total, offset)

        db.set_sync_state("submolts_offset", "0")  # Reset for next full crawl
        db.finish_crawl_log(log_id, total, errors)
        logger.info("Submolts crawl complete: %d records", total)
    except Exception as e:
        errors += 1
        db.finish_crawl_log(log_id, total, errors, "error")
        logger.error("Submolts crawl failed: %s", e)
        raise


async def crawl_all_posts(client: MoltbookClient, db: Database):
    """Full crawl of all posts sorted by new, with resume support."""
    log_id = db.start_crawl_log("posts_full")
    offset = int(db.get_sync_state("posts_full_offset", "0"))
    total = 0
    errors = 0

    try:
        while True:
            data = await client.get_posts(sort="new", offset=offset)
            posts = data.get("posts", [])
            if not posts:
                break

            with db.transaction() as conn:
                for p in posts:
                    db.upsert_post(p)
                    # Mark posts with 0 comments as fetched
                    if p.get("comment_count", 0) == 0:
                        db.mark_comments_fetched(p["id"])
                total += len(posts)

            offset += len(posts)
            db.set_sync_state("posts_full_offset", str(offset))

            has_more = data.get("has_more", len(posts) >= 50)
            if not has_more:
                break

            logger.info("Posts: fetched %d so far (offset %d)", total, offset)

        db.set_sync_state("posts_full_offset", "0")
        db.finish_crawl_log(log_id, total, errors)
        logger.info("Posts full crawl complete: %d records", total)
    except Exception as e:
        errors += 1
        db.finish_crawl_log(log_id, total, errors, "error")
        logger.error("Posts full crawl failed: %s", e)
        raise


async def crawl_comments(client: MoltbookClient, db: Database, batch_size: int = 100):
    """Fetch comments for posts that haven't been fetched yet."""
    log_id = db.start_crawl_log("comments")
    total = 0
    errors = 0

    try:
        while True:
            posts = db.get_posts_needing_comments(limit=batch_size)
            if not posts:
                break

            for post in posts:
                post_id = post["id"]
                if post["comment_count"] == 0:
                    db.mark_comments_fetched(post_id)
                    db.connect().commit()
                    continue

                try:
                    data = await client.get_post_comments(post_id)
                    comments = data.get("comments", [])

                    with db.transaction() as conn:
                        for c in comments:
                            db.upsert_comment(c, post_id)
                        db.mark_comments_fetched(post_id)
                        total += len(comments)

                except Exception as e:
                    errors += 1
                    logger.error("Failed to fetch comments for post %s: %s", post_id, e)
                    continue

            logger.info("Comments: fetched %d so far, errors: %d", total, errors)

        db.finish_crawl_log(log_id, total, errors)
        logger.info("Comments crawl complete: %d records, %d errors", total, errors)
    except Exception as e:
        db.finish_crawl_log(log_id, total, errors, "error")
        logger.error("Comments crawl failed: %s", e)
        raise


async def incremental_sync(client: MoltbookClient, db: Database):
    """Fetch new posts since last sync, plus update existing posts."""
    log_id = db.start_crawl_log("incremental")
    last_sync = db.get_sync_state("posts_last_sync", "")
    total = 0
    errors = 0
    found_old = False

    try:
        offset = 0
        while not found_old:
            data = await client.get_posts(sort="new", offset=offset)
            posts = data.get("posts", [])
            if not posts:
                break

            with db.transaction() as conn:
                for p in posts:
                    db.upsert_post(p)
                    if p.get("comment_count", 0) == 0:
                        db.mark_comments_fetched(p["id"])
                    total += len(posts)

                    if last_sync and p.get("created_at", "") < last_sync:
                        found_old = True

            offset += len(posts)

            if not data.get("has_more", len(posts) >= 50):
                break

            # Buffer: fetch one more page after finding old post
            if found_old:
                extra = await client.get_posts(sort="new", offset=offset)
                for p in extra.get("posts", []):
                    db.upsert_post(p)
                    if p.get("comment_count", 0) == 0:
                        db.mark_comments_fetched(p["id"])
                break

        now = datetime.now(timezone.utc).isoformat()
        db.set_sync_state("posts_last_sync", now)

        # Fetch comments for any new posts
        await crawl_comments(client, db)

        db.finish_crawl_log(log_id, total, errors)
        logger.info("Incremental sync complete: %d new/updated posts", total)
    except Exception as e:
        errors += 1
        db.finish_crawl_log(log_id, total, errors, "error")
        logger.error("Incremental sync failed: %s", e)
        raise


async def full_crawl(config: Config):
    """Run a complete crawl: submolts -> posts -> comments."""
    db = Database(config.db_path)
    db.connect()

    try:
        async with MoltbookClient(config) as client:
            db.set_sync_state("crawl_status", "running")

            logger.info("Starting submolts crawl...")
            await crawl_submolts(client, db)

            logger.info("Starting posts crawl...")
            await crawl_all_posts(client, db)

            logger.info("Starting comments crawl...")
            await crawl_comments(client, db)

            logger.info("Updating agent counts...")
            db.update_agent_counts()

            now = datetime.now(timezone.utc).isoformat()
            db.set_sync_state("posts_last_sync", now)
            db.set_sync_state("crawl_status", "idle")

            stats = db.get_stats()
            logger.info(
                "Full crawl complete: %d agents, %d submolts, %d posts, %d comments",
                stats["agents"], stats["submolts"], stats["posts"], stats["comments"],
            )
            logger.info(
                "API requests: %d, errors: %d",
                client.request_count, client.error_count,
            )
    finally:
        db.close()


async def run_incremental(config: Config):
    """Run an incremental sync."""
    db = Database(config.db_path)
    db.connect()

    try:
        async with MoltbookClient(config) as client:
            db.set_sync_state("crawl_status", "syncing")
            await incremental_sync(client, db)
            db.update_agent_counts()
            db.set_sync_state("crawl_status", "idle")
    finally:
        db.close()
