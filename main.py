"""CLI entry point: crawl / sync / serve / daemon."""

import argparse
import asyncio
import logging
import subprocess
import sys
from pathlib import Path

from config import Config


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def cmd_crawl(args):
    from crawler.sync import full_crawl
    config = Config()
    asyncio.run(full_crawl(config))


def cmd_sync(args):
    from crawler.sync import run_incremental
    config = Config()
    asyncio.run(run_incremental(config))


def cmd_serve(args):
    app_path = Path(__file__).parent / "viewer" / "app.py"
    subprocess.run([sys.executable, "-m", "streamlit", "run", str(app_path)], check=True)


def cmd_daemon(args):
    from crawler.scheduler import run_daemon
    config = Config()
    asyncio.run(run_daemon(config))


def main():
    setup_logging()

    parser = argparse.ArgumentParser(description="Moltbook Community Observatory")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("crawl", help="Full crawl of all submolts, posts, and comments")
    sub.add_parser("sync", help="Incremental sync of new posts and comments")
    sub.add_parser("serve", help="Start Streamlit viewer")
    sub.add_parser("daemon", help="Run crawler with periodic incremental sync")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        "crawl": cmd_crawl,
        "sync": cmd_sync,
        "serve": cmd_serve,
        "daemon": cmd_daemon,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
