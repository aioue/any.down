#!/usr/bin/env python3
"""
Debug script to troubleshoot Any.do login issues.

This script provides detailed logging to help identify login problems.
"""

import json
import os
from anydo_client import AnyDoClient

def debug_login():
    """Debug login with detailed output."""
    print("=== Any.do Login Debug Tool ===")
    print("This will help diagnose login issues with detailed logging.\n")
    
    # Load config if available
    config = None
    if os.path.exists("config.json"):
        try:
            with open("config.json", 'r') as f:
                config = json.load(f)
                print("✅ Loaded config.json")
        except Exception as e:
            print(f"❌ Error loading config.json: {e}")
    
    # Get credentials
    if config and config.get('email') and config.get('password'):
        email = config.get('email', '')
        password = config.get('password', '')
        print(f"📧 Using email from config: {email}")
    else:
        print("📝 Config not found or incomplete. Please enter credentials:")
        email = input("Email: ")
        password = input("Password: ")
    
    print(f"\n🔍 Debug Info:")
    print(f"- Email: {email}")
    print(f"- Password: {'*' * len(password)} ({len(password)} characters)")
    
    # Create client and attempt login
    print(f"\n🚀 Starting login process...")
    client = AnyDoClient()
    
    print(f"- Base URL: {client.base_url}")
    print(f"- Session: {client.session}")
    
    # Attempt login with debug output
    success = client.login(email, password)
    
    print(f"\n📊 Login Result:")
    print(f"- Success: {success}")
    print(f"- Logged in: {client.logged_in}")
    print(f"- User info: {client.user_info}")
    
    # Show session cookies
    print(f"\n🍪 Session Cookies:")
    for cookie in client.session.cookies:
        print(f"- {cookie.name}: {cookie.value[:20]}...")
    
    if success:
        print(f"\n✅ Login successful! Testing task retrieval...")
        try:
            tasks = client.get_simple_tasks()
            print(f"📋 Found {len(tasks)} tasks")
            if tasks:
                print("First few tasks:")
                for task in tasks[:3]:
                    if task:  # Type guard
                        print(f"  - {task.get('title', 'No title')}")
        except Exception as e:
            print(f"❌ Error getting tasks: {e}")
    else:
        print(f"\n❌ Login failed. Possible issues:")
        print("1. Incorrect email/password")
        print("2. 2FA is enabled (check your email/SMS)")
        print("3. Account locked or requires verification")
        print("4. Any.do changed their API endpoints")
        print("\nTroubleshooting steps:")
        print("- Try logging into Any.do in your browser first")
        print("- Check if you have 2FA enabled in Any.do settings")
        print("- Verify your credentials are correct")

if __name__ == "__main__":
    debug_login() 
