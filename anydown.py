#!/usr/bin/env python3
"""
Simple script to fetch and display your Any.do tasks.

Usage:
    python anydown.py

You can either:
1. Create a config.json file with your credentials (copy from config.json.example)
2. Enter your credentials interactively when prompted

Features:
- Session persistence: Saves login session to avoid re-authentication
- 2FA support: Interactive prompts for two-factor authentication
- Timestamped exports: Saves tasks to outputs/YYYY-MM-DD_HHMM-SS_anydo-tasks.json
- Markdown generation: Creates markdown files from JSON when meaningful changes are detected
- Change detection: Only creates new files when tasks have changed
"""

import getpass
import json
import os
import sys
import argparse
from anydo_client import AnyDoClient
from datetime import datetime


def load_config():
    """Load configuration from config.json file."""
    config_file = "config.json"
    if os.path.exists(config_file):
        try:
            with open(config_file, 'r') as f:
                config = json.load(f)
                print("✅ Loaded configuration from config.json")
                return config
        except json.JSONDecodeError:
            print("❌ Error: config.json is not valid JSON")
            return None
        except Exception as e:
            print(f"❌ Error reading config.json: {e}")
            return None
    return None


def get_credentials():
    """Get credentials from config file or interactive input."""
    config = load_config()
    
    if config:
        email = config.get('email')
        password = config.get('password')
        save_raw = config.get('save_raw_data', True)  # Default to True now
        auto_export = config.get('auto_export', True)  # New option
        text_wrap_width = config.get('text_wrap_width', 80)  # Default to 80 characters
        
        if email and password:
            print(f"📧 Using email: {email}")
            return email, password, save_raw, auto_export, text_wrap_width
        else:
            print("❌ Error: config.json missing email or password")
    
    # If no config or incomplete config, offer to create one
    if not os.path.exists("config.json"):
        print("📝 No config.json found. Let's create one!")
        create_config = input("Would you like to create a config.json file? (Y/n): ").lower().strip()
        if create_config not in ['n', 'no']:
            return create_config_file()
    
    # Fallback to interactive input
    print("📝 Enter your credentials:")
    email = input("Enter your Any.do email: ")
    password = getpass.getpass("Enter your password: ")
    save_raw = input("Save raw task data to timestamped file? (Y/n): ").lower().strip() not in ['n', 'no']
    auto_export = True  # Always enable auto-export
    text_wrap_width = 80  # Default for interactive mode
    
    return email, password, save_raw, auto_export, text_wrap_width


def create_config_file():
    """Create a config.json file interactively."""
    print("\n🔧 Creating config.json file...")
    
    email = input("Enter your Any.do email: ")
    password = getpass.getpass("Enter your password: ")
    
    save_raw = input("Save raw task data to timestamped files? (Y/n): ").lower().strip() not in ['n', 'no']
    auto_export = True  # Always enable auto-export
    
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
        print("✅ config.json created successfully!")
        print("🔒 Note: config.json is in .gitignore for security")
        return email, password, save_raw, auto_export, 80
    except Exception as e:
        print(f"❌ Error creating config.json: {e}")
        print("📝 Falling back to interactive mode...")
        return email, password, save_raw, auto_export, 80


def main():
    parser = argparse.ArgumentParser(description='Export tasks from Any.do to JSON and markdown files')
    parser.add_argument('--force', action='store_true', 
                       help='Force export even if no changes detected')
    parser.add_argument('--full-sync', action='store_true',
                       help='Force full sync instead of incremental sync (downloads all tasks)')
    parser.add_argument('--incremental-only', action='store_true',
                       help='Only attempt incremental sync (fail if no last sync timestamp)')
    parser.add_argument('--disable-optimizations', action='store_true',
                       help='Disable network optimizations (connection pooling, caching, etc.)')
    parser.add_argument('--show-stats', action='store_true',
                       help='Show network optimization statistics')
    args = parser.parse_args()
    
    print("=== Any.do Task Fetcher ===")
    print("This script will fetch and display all your Any.do tasks.")
    
    # Show optimization status
    if args.disable_optimizations:
        print("⚠️  Network optimizations disabled")
    else:
        print("🚀 Network optimizations enabled:")
        print("   • Connection pooling and keep-alive")
        print("   • Request retry with exponential backoff")
        print("   • Conditional requests with ETags")
        print("   • Response caching for static data")
        print("   • Optimized polling with backoff")
    
    print("✨ Features: Session persistence, 2FA support, timestamped exports, change detection, incremental sync reduces server load by downloading only changes\n")
    
    # Get credentials
    email, password, save_raw, auto_export, text_wrap_width = get_credentials()
    
    # Create client (will automatically try to load existing session)
    client = AnyDoClient(text_wrap_width=text_wrap_width)
    
    # Disable optimizations if requested
    if args.disable_optimizations:
        client._disable_optimizations()
    
    # Login (will skip if valid session exists)
    print("\n🔐 Authenticating...")
    
    if not client.login(email, password):
        print("❌ Login failed. Please check your credentials and try again.")
        print("💡 If you have 2FA enabled, check your email for the verification code.")
        print("⚠️  If you see 'Email not found in system', you may be rate limited. Wait 5-10 minutes and try again.")
        return
    
    print("✅ Authentication successful!")
    
    # Show optimization stats if requested
    if args.show_stats:
        client._show_optimization_stats()
    
    # Fetch tasks using appropriate sync method
    print("\n📋 Fetching tasks...")
    
    # Choose sync method based on command line arguments
    if args.full_sync:
        print("🔄 Forcing full sync (downloading all tasks)...")
        tasks_data = client.get_tasks_full()
    elif args.incremental_only:
        print("🔄 Attempting incremental sync only...")
        tasks_data = client.get_tasks_incremental()
        if not tasks_data:
            print("❌ Incremental sync failed. Run without --incremental-only to allow fallback to full sync.")
            return
    else:
        # Smart sync (default behavior)
        tasks_data = client.get_tasks()
    
    if not tasks_data:
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
        saved_file = client.save_tasks_to_file(tasks_data, force=args.force)
        if saved_file:
            print(f"✅ Tasks saved successfully")
        else:
            print("ℹ️  No new export created (no changes detected)")
    elif save_raw:
        # Manual save option
        save_now = input("\n💾 Save tasks to timestamped file? (Y/n): ").lower().strip() not in ['n', 'no']
        if save_now:
            saved_file = client.save_tasks_to_file(tasks_data, force=args.force)
            if saved_file:
                print(f"✅ Tasks saved successfully")
    
    # Show final optimization stats if requested
    if args.show_stats:
        print("\n📊 Final optimization statistics:")
        client._show_optimization_stats()
    
    # Show session info
    if client.logged_in:
        print(f"\n🔑 Session saved for future use - no need to re-authenticate")
        print("💡 To force re-authentication, delete the session.json file")
        print("💡 To force full sync next time, use --full-sync")
        print("💡 To only use incremental sync, use --incremental-only")
        if not args.disable_optimizations:
            print("💡 To disable optimizations, use --disable-optimizations")
            print("💡 To see optimization statistics, use --show-stats")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 Goodbye!")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        sys.exit(1) 
