#!/usr/bin/env python3
"""
Simple script to fetch and display your Any.do tasks.

Usage:
    python anydown.py

You can provide credentials via:
1. Environment variables: ANYDO_EMAIL and ANYDO_PASSWORD
2. A config.json file with your credentials (copy from config.json.example)
3. Interactive prompts when running the script

Features:
- Session persistence: Saves login session to avoid re-authentication
- 2FA support: Interactive prompts for two-factor authentication
- Timestamped exports: Saves tasks to outputs/YYYY-MM-DD_HHMM-SS_anydo-tasks.json
- Markdown generation: Creates markdown files from JSON when meaningful changes are detected
- Change detection: Only creates new files when tasks have changed
"""

import argparse
import getpass
import json
import logging
import os
import random
import sys
import time
from datetime import datetime

from anydown.client import AnyDoClient, send_ntfy

ENV_EMAIL = "ANYDO_EMAIL"
ENV_PASSWORD = "ANYDO_PASSWORD"
ENV_SAVE_RAW = "ANYDO_SAVE_RAW"
ENV_TEXT_WRAP_WIDTH = "ANYDO_TEXT_WRAP_WIDTH"

logger = logging.getLogger(__name__)


class EmojiFormatter(logging.Formatter):
    """Logging formatter that prepends emoji prefixes by level."""

    LEVEL_EMOJI = {
        logging.DEBUG: "🔍",
        logging.INFO: "✅",
        logging.WARNING: "⚠️ ",
        logging.ERROR: "❌",
        logging.CRITICAL: "🔥",
    }

    def format(self, record: logging.LogRecord) -> str:
        emoji = self.LEVEL_EMOJI.get(record.levelno, "")
        message = super().format(record)
        return f"{emoji} {message}"


def setup_logging(*, debug: bool = False, quiet: bool = False) -> None:
    """Configure root logging with emoji-formatted console output."""
    if debug:
        level = logging.DEBUG
    elif quiet:
        level = logging.WARNING
    else:
        level = logging.INFO

    handler = logging.StreamHandler()
    handler.setFormatter(EmojiFormatter("%(message)s"))
    logging.root.handlers = [handler]
    logging.root.setLevel(level)


def get_credentials_from_env():
    """Get credentials from environment variables."""
    email = os.environ.get(ENV_EMAIL)
    password = os.environ.get(ENV_PASSWORD)

    if email and password:
        logger.info("Using credentials from environment variables (%s)", ENV_EMAIL)

        save_raw_str = os.environ.get(ENV_SAVE_RAW, "true").lower()
        save_raw = save_raw_str in ("true", "1", "yes")

        try:
            text_wrap_width = int(os.environ.get(ENV_TEXT_WRAP_WIDTH, "80"))
        except ValueError:
            text_wrap_width = 80

        auto_export = True
        return email, password, save_raw, auto_export, text_wrap_width, False, {}

    return None


def load_config():
    """Load configuration from config.json file."""
    config_file = "config.json"
    if os.path.exists(config_file):
        try:
            with open(config_file) as f:
                config = json.load(f)
                logger.info("Loaded configuration from config.json")
                return config
        except json.JSONDecodeError:
            logger.error("config.json is not valid JSON")
            return None
        except OSError as e:
            logger.error("Error reading config.json: %s", e)
            return None
    return None


def get_credentials():
    """
    Get credentials from environment, config file, or interactive input.

    Priority:
    1. Environment variables (ANYDO_EMAIL, ANYDO_PASSWORD)
    2. config.json file
    3. Interactive prompts
    """
    env_credentials = get_credentials_from_env()
    if env_credentials:
        return env_credentials

    config = load_config()

    if config:
        email = config.get("email")
        password = config.get("password")
        save_raw = config.get("save_raw_data", True)
        auto_export = config.get("auto_export", True)
        text_wrap_width = config.get("text_wrap_width", 80)
        rotate_client_id = config.get("rotate_client_id", False)
        ntfy_config = config.get("ntfy", {})

        if email and password:
            logger.info("Using email: %s", email)
            return email, password, save_raw, auto_export, text_wrap_width, rotate_client_id, ntfy_config
        logger.warning("config.json missing email or password")

    if not os.path.exists("config.json"):
        print("📝 No config.json found. Let's create one!")
        print(f"💡 Tip: You can also set {ENV_EMAIL} and {ENV_PASSWORD} environment variables")
        create_config = input("Would you like to create a config.json file? (Y/n): ").lower().strip()
        if create_config not in ["n", "no"]:
            return create_config_file()

    print("📝 Enter your credentials:")
    print(f"💡 Tip: You can also set {ENV_EMAIL} and {ENV_PASSWORD} environment variables")
    email = input("Enter your Any.do email: ")
    password = getpass.getpass("Enter your password: ")
    save_raw = input("Save raw task data to timestamped file? (Y/n): ").lower().strip() not in ["n", "no"]
    auto_export = True
    text_wrap_width = 80
    rotate_client_id = False

    return email, password, save_raw, auto_export, text_wrap_width, rotate_client_id, {}


def create_config_file():
    """Create a config.json file interactively."""
    print("\n🔧 Creating config.json file...")

    email = input("Enter your Any.do email: ")
    password = getpass.getpass("Enter your password: ")

    save_raw = input("Save raw task data to timestamped files? (Y/n): ").lower().strip() not in ["n", "no"]
    auto_export = True

    config = {
        "email": email,
        "password": password,
        "save_raw_data": save_raw,
        "auto_export": auto_export,
        "text_wrap_width": 80,
    }

    try:
        with open("config.json", "w") as f:
            json.dump(config, f, indent=2)
        logger.info("config.json created successfully")
        print("🔒 Note: config.json is in .gitignore for security")
        return email, password, save_raw, auto_export, 80, False, {}
    except OSError as e:
        logger.error("Error creating config.json: %s", e)
        print("📝 Falling back to interactive mode...")
        return email, password, save_raw, auto_export, 80, False, {}


def run_sync(client: AnyDoClient, args: argparse.Namespace, save_raw: bool, auto_export: bool) -> bool:
    """
    Perform one sync-and-export cycle.

    Returns True if the cycle completed without a fatal error, False otherwise.
    """
    logger.info("Fetching tasks...")

    if args.full_sync:
        logger.info("Forcing full sync (downloading all tasks)...")
        tasks_data = client.get_tasks_full()
    elif args.incremental_only:
        logger.info("Attempting incremental sync only...")
        tasks_data = client.get_tasks_incremental()
        if not tasks_data:
            logger.error("Incremental sync failed. Try running again to use automatic fallback to full sync.")
            return False
    else:
        tasks_data = client.get_tasks()

    if not tasks_data:
        logger.error("Failed to fetch tasks. Please try again.")
        return False

    if client.last_sync_timestamp:
        last_sync_time = datetime.fromtimestamp(client.last_sync_timestamp / 1000)
        logger.info("Last sync: %s", last_sync_time.strftime("%Y-%m-%d %H:%M:%S"))

    client.print_tasks_summary(tasks_data)

    if save_raw and auto_export:
        logger.info("Saving tasks data...")
        saved_file = client.save_tasks_to_file(tasks_data)
        if saved_file:
            logger.info("Tasks saved to %s", saved_file)
        else:
            logger.info("No new export created (no changes detected)")
    elif save_raw:
        save_now = input("\n💾 Save tasks to timestamped file? (Y/n): ").lower().strip() not in ["n", "no"]
        if save_now:
            saved_file = client.save_tasks_to_file(tasks_data)
            if saved_file:
                logger.info("Tasks saved to %s", saved_file)

    return True


def main():
    parser = argparse.ArgumentParser(description="Export tasks from Any.do to JSON and markdown files")

    parser.add_argument(
        "--full-sync", action="store_true", help="Force full sync instead of incremental sync (downloads all tasks)"
    )
    parser.add_argument(
        "--incremental-only", action="store_true", help="Only attempt incremental sync (fail if no last sync timestamp)"
    )
    parser.add_argument("--quiet", "-q", action="store_true", help="Reduce logging output")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Run continuously, syncing on a recurring schedule (see --watch-interval and --watch-jitter)",
    )
    parser.add_argument(
        "--watch-interval",
        type=int,
        default=90,
        metavar="MINUTES",
        help="Base interval between syncs in watch mode (default: 90 minutes)",
    )
    parser.add_argument(
        "--watch-jitter",
        type=int,
        default=10,
        metavar="MINUTES",
        help="Random ± jitter added to each interval in watch mode (default: ±10 minutes)",
    )
    args = parser.parse_args()

    setup_logging(debug=args.debug, quiet=args.quiet)

    print("=== Any.do Task Fetcher ===")
    print("Exports your Any.do tasks to JSON and markdown files.\n")

    email, password, save_raw, auto_export, text_wrap_width, rotate_client_id, ntfy_config = get_credentials()

    session_file = os.environ.get("ANYDO_SESSION_FILE", "session.json")
    client = AnyDoClient(session_file=session_file, text_wrap_width=text_wrap_width, rotate_client_id=rotate_client_id)

    logger.info("Authenticating...")

    if not client.login(email, password):
        logger.error("Login failed. Please check your credentials and try again.")
        print("💡 If you have 2FA enabled, check your email for the verification code.")
        print("💡 If you see 'Email not found in system', you may be rate limited. Wait 5-10 minutes and try again.")
        return

    logger.info("Authentication successful")

    if args.watch:
        logger.info(
            "Watch mode enabled — syncing every %d ± %d minutes. Press Ctrl+C to stop.",
            args.watch_interval,
            args.watch_jitter,
        )
        consecutive_errors = 0
        while True:
            if run_sync(client, args, save_raw, auto_export):
                consecutive_errors = 0
            else:
                consecutive_errors += 1
                if consecutive_errors >= 3:
                    error_msg = "Three consecutive sync failures — exiting watch mode."
                    logger.error(error_msg)
                    send_ntfy(
                        ntfy_config,
                        title="Any.down Watch Mode Failed",
                        message=error_msg,
                        priority=4,
                        tags=["warning", "red_circle"],
                    )
                    return

            jitter = random.randint(-args.watch_jitter, args.watch_jitter)
            sleep_minutes = args.watch_interval + jitter
            sleep_seconds = sleep_minutes * 60
            next_run = datetime.fromtimestamp(time.time() + sleep_seconds)
            logger.info("Next sync at %s (%d min)", next_run.strftime("%H:%M:%S"), sleep_minutes)
            time.sleep(sleep_seconds)
    else:
        run_sync(client, args, save_raw, auto_export)

    if client.logged_in:
        logger.info("Session saved for future use - no need to re-authenticate")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 Goodbye!")
        sys.exit(0)
    except Exception as e:
        logger.exception("Unexpected error: %s", e)
        print(f"\n❌ Unexpected error: {e}")
        sys.exit(1)
