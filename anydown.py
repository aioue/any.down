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

import getpass
import json
import logging
import os
import sys
import argparse
from anydo_client import AnyDoClient
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Environment variable names
ENV_EMAIL = 'ANYDO_EMAIL'
ENV_PASSWORD = 'ANYDO_PASSWORD'
ENV_SAVE_RAW = 'ANYDO_SAVE_RAW'
ENV_TEXT_WRAP_WIDTH = 'ANYDO_TEXT_WRAP_WIDTH'


def get_credentials_from_env():
    """
    Get credentials from environment variables.

    Returns:
        Tuple of (email, password, save_raw, auto_export, text_wrap_width) or None if not available
    """
    email = os.environ.get(ENV_EMAIL)
    password = os.environ.get(ENV_PASSWORD)

    if email and password:
        logger.info("Using credentials from environment variables")
        print(f"🔐 Using credentials from environment variables ({ENV_EMAIL})")

        # Get optional settings from environment
        save_raw_str = os.environ.get(ENV_SAVE_RAW, 'true').lower()
        save_raw = save_raw_str in ('true', '1', 'yes')

        try:
            text_wrap_width = int(os.environ.get(ENV_TEXT_WRAP_WIDTH, '80'))
        except ValueError:
            text_wrap_width = 80

        auto_export = True
        return email, password, save_raw, auto_export, text_wrap_width

    return None


def load_config():
    """Load configuration from config.json file."""
    config_file = "config.json"
    if os.path.exists(config_file):
        try:
            with open(config_file, 'r') as f:
                config = json.load(f)
                logger.info("Loaded configuration from config.json")
                print("✅ Loaded configuration from config.json")
                return config
        except json.JSONDecodeError:
            logger.error("config.json is not valid JSON")
            print("❌ Error: config.json is not valid JSON")
            return None
        except OSError as e:
            logger.error("Error reading config.json: %s", e)
            print(f"❌ Error reading config.json: {e}")
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
    # First, try environment variables
    env_credentials = get_credentials_from_env()
    if env_credentials:
        return env_credentials

    # Second, try config file
    config = load_config()

    if config:
        email = config.get('email')
        password = config.get('password')
        save_raw = config.get('save_raw_data', True)
        auto_export = config.get('auto_export', True)
        text_wrap_width = config.get('text_wrap_width', 80)

        if email and password:
            print(f"📧 Using email: {email}")
            return email, password, save_raw, auto_export, text_wrap_width
        else:
            logger.warning("config.json missing email or password")
            print("❌ Error: config.json missing email or password")

    # If no config or incomplete config, offer to create one
    if not os.path.exists("config.json"):
        print("📝 No config.json found. Let's create one!")
        print(f"💡 Tip: You can also set {ENV_EMAIL} and {ENV_PASSWORD} environment variables")
        create_config = input("Would you like to create a config.json file? (Y/n): ").lower().strip()
        if create_config not in ['n', 'no']:
            return create_config_file()

    # Fallback to interactive input
    print("📝 Enter your credentials:")
    print(f"💡 Tip: You can also set {ENV_EMAIL} and {ENV_PASSWORD} environment variables")
    email = input("Enter your Any.do email: ")
    password = getpass.getpass("Enter your password: ")
    save_raw = input("Save raw task data to timestamped file? (Y/n): ").lower().strip() not in ['n', 'no']
    auto_export = True
    text_wrap_width = 80

    return email, password, save_raw, auto_export, text_wrap_width


def create_config_file():
    """Create a config.json file interactively."""
    print("\n🔧 Creating config.json file...")

    email = input("Enter your Any.do email: ")
    password = getpass.getpass("Enter your password: ")

    save_raw = input("Save raw task data to timestamped files? (Y/n): ").lower().strip() not in ['n', 'no']
    auto_export = True

    config = {
        "email": email,
        "password": password,
        "save_raw_data": save_raw,
        "auto_export": auto_export,
        "text_wrap_width": 80
    }

    try:
        with open("config.json", 'w') as f:
            json.dump(config, f, indent=2)
        logger.info("config.json created successfully")
        print("✅ config.json created successfully!")
        print("🔒 Note: config.json is in .gitignore for security")
        return email, password, save_raw, auto_export, 80
    except OSError as e:
        logger.error("Error creating config.json: %s", e)
        print(f"❌ Error creating config.json: {e}")
        print("📝 Falling back to interactive mode...")
        return email, password, save_raw, auto_export, 80


def main():
    parser = argparse.ArgumentParser(description='Export tasks from Any.do to JSON and markdown files')

    parser.add_argument('--full-sync', action='store_true',
                       help='Force full sync instead of incremental sync (downloads all tasks)')
    parser.add_argument('--incremental-only', action='store_true',
                       help='Only attempt incremental sync (fail if no last sync timestamp)')
    parser.add_argument('--quiet', '-q', action='store_true',
                       help='Reduce logging output')
    parser.add_argument('--debug', action='store_true',
                       help='Enable debug logging')
    args = parser.parse_args()

    # Configure logging level based on arguments
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    elif args.quiet:
        logging.getLogger().setLevel(logging.WARNING)

    print("=== Any.do Task Fetcher ===")
    print("This script will fetch and display all your Any.do tasks.")
    print("✨ Features: Session persistence, 2FA support, timestamped exports, change detection, incremental sync reduces server load by downloading only changes\n")

    # Get credentials
    email, password, save_raw, auto_export, text_wrap_width = get_credentials()

    # Create client (will automatically try to load existing session)
    client = AnyDoClient(text_wrap_width=text_wrap_width)

    # Login (will skip if valid session exists)
    print("\n🔐 Authenticating...")

    if not client.login(email, password):
        logger.error("Login failed")
        print("❌ Login failed. Please check your credentials and try again.")
        print("💡 If you have 2FA enabled, check your email for the verification code.")
        print("⚠️  If you see 'Email not found in system', you may be rate limited. Wait 5-10 minutes and try again.")
        return

    logger.info("Authentication successful")
    print("✅ Authentication successful!")

    # Fetch tasks using appropriate sync method
    print("\n📋 Fetching tasks...")

    # Choose sync method based on command line arguments
    if args.full_sync:
        logger.info("Forcing full sync")
        print("🔄 Forcing full sync (downloading all tasks)...")
        tasks_data = client.get_tasks_full()
    elif args.incremental_only:
        logger.info("Attempting incremental sync only")
        print("🔄 Attempting incremental sync only...")
        tasks_data = client.get_tasks_incremental()
        if not tasks_data:
            logger.error("Incremental sync failed")
            print("❌ Incremental sync failed. Try running again to use automatic fallback to full sync.")
            return
    else:
        # Smart sync (default behavior)
        tasks_data = client.get_tasks()

    if not tasks_data:
        logger.error("Failed to fetch tasks")
        print("❌ Failed to fetch tasks. Please try again.")
        return

    # Display sync info
    if client.last_sync_timestamp:
        last_sync_time = datetime.fromtimestamp(client.last_sync_timestamp / 1000)
        print(f"💾 Last sync: {last_sync_time.strftime('%Y-%m-%d %H:%M:%S')}")

    # Display tasks summary
    client.print_tasks_summary(tasks_data)

    # Save data if requested
    if save_raw and auto_export:
        print("\n💾 Saving tasks data...")
        saved_file = client.save_tasks_to_file(tasks_data)
        if saved_file:
            logger.info("Tasks saved to %s", saved_file)
            print("✅ Tasks saved successfully")
        else:
            print("ℹ️  No new export created (no changes detected)")
    elif save_raw:
        # Manual save option
        save_now = input("\n💾 Save tasks to timestamped file? (Y/n): ").lower().strip() not in ['n', 'no']
        if save_now:
            saved_file = client.save_tasks_to_file(tasks_data)
            if saved_file:
                logger.info("Tasks saved to %s", saved_file)
                print("✅ Tasks saved successfully")

    # Show session info
    if client.logged_in:
        print("\n🔑 Session saved for future use - no need to re-authenticate")
        print("⚠️  Warning: Deleting session.json to force re-authentication may trigger rate limiting or account restrictions")


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
