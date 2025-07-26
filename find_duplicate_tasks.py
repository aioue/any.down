#!/usr/bin/env python3
"""
Find duplicate tasks in the latest Any.do tasks JSON export.

This script identifies duplicate tasks based on various criteria like title,
title + category, or other combinations to help identify redundant tasks.
"""

import json
import os
import glob
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Any


def find_latest_json_file(directory: str = "outputs/raw-json") -> str:
    """Find the latest JSON file in the raw-json directory."""
    pattern = os.path.join(directory, "*.json")
    files = glob.glob(pattern)
    
    if not files:
        raise FileNotFoundError(f"No JSON files found in {directory}")
    
    # Sort by filename (which includes timestamp) to get the latest
    latest_file = sorted(files)[-1]
    return latest_file


def load_tasks_from_json(filepath: str) -> List[Dict[str, Any]]:
    """Load tasks from the JSON file."""
    print(f"Loading tasks from: {filepath}")
    
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # Extract tasks from the models.task.items structure
    tasks = data.get('models', {}).get('task', {}).get('items', [])
    print(f"Found {len(tasks)} tasks total")
    
    return tasks


def find_duplicates_by_title(tasks: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Find tasks with identical titles."""
    title_groups = defaultdict(list)
    
    for task in tasks:
        title = task.get('title', '').strip()
        if title:  # Only consider tasks with non-empty titles
            title_groups[title].append(task)
    
    # Only return groups with more than one task
    duplicates = {title: task_list for title, task_list in title_groups.items() if len(task_list) > 1}
    return duplicates


def find_duplicates_by_title_and_category(tasks: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Find tasks with identical titles in the same category."""
    category_title_groups = defaultdict(list)
    
    for task in tasks:
        title = task.get('title', '').strip()
        category_id = task.get('categoryId', '')
        if title:  # Only consider tasks with non-empty titles
            key = f"{category_id}::{title}"
            category_title_groups[key].append(task)
    
    # Only return groups with more than one task
    duplicates = {key: task_list for key, task_list in category_title_groups.items() if len(task_list) > 1}
    return duplicates


def find_near_duplicates(tasks: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Find tasks with very similar titles (case-insensitive, ignoring punctuation)."""
    normalized_groups = defaultdict(list)
    
    for task in tasks:
        title = task.get('title', '').strip()
        if title:
            # Normalize: lowercase, remove punctuation, collapse whitespace
            normalized = ''.join(c.lower() for c in title if c.isalnum() or c.isspace())
            normalized = ' '.join(normalized.split())  # Collapse whitespace
            if normalized:
                normalized_groups[normalized].append(task)
    
    # Only return groups with more than one task
    duplicates = {norm_title: task_list for norm_title, task_list in normalized_groups.items() if len(task_list) > 1}
    return duplicates


def format_task_info(task: Dict[str, Any]) -> str:
    """Format task information for display."""
    task_id = task.get('id', 'N/A')
    title = task.get('title', 'No title')
    status = task.get('status', 'Unknown')
    category_id = task.get('categoryId', 'No category')
    due_date = task.get('dueDate')
    
    due_str = ""
    if due_date:
        try:
            due_dt = datetime.fromtimestamp(due_date / 1000)  # Convert from milliseconds
            due_str = f" | Due: {due_dt.strftime('%Y-%m-%d')}"
        except (ValueError, TypeError):
            due_str = f" | Due: {due_date}"
    
    return f"  • ID: {task_id} | Status: {status} | Category: {category_id}{due_str}"


def print_duplicate_report(duplicates: Dict[str, List[Dict[str, Any]]], title: str):
    """Print a formatted report of duplicates."""
    if not duplicates:
        print(f"\n{title}: No duplicates found ✓")
        return
    
    print(f"\n{title}: Found {len(duplicates)} groups with duplicates")
    print("=" * 60)
    
    for key, task_list in duplicates.items():
        # For category-based keys, extract the title part
        display_key = key.split("::", 1)[-1] if "::" in key else key
        print(f"\n'{display_key}' ({len(task_list)} duplicates):")
        
        for task in task_list:
            print(format_task_info(task))


def analyze_duplicate_patterns(tasks: List[Dict[str, Any]]):
    """Analyze and report on different types of duplicate patterns."""
    print("🔍 Analyzing duplicate task patterns...\n")
    
    # 1. Exact title matches
    exact_duplicates = find_duplicates_by_title(tasks)
    print_duplicate_report(exact_duplicates, "EXACT TITLE DUPLICATES")
    
    # 2. Title + category duplicates (more refined)
    category_duplicates = find_duplicates_by_title_and_category(tasks)
    print_duplicate_report(category_duplicates, "TITLE + CATEGORY DUPLICATES")
    
    # 3. Near duplicates (similar titles)
    near_duplicates = find_near_duplicates(tasks)
    print_duplicate_report(near_duplicates, "SIMILAR TITLE DUPLICATES")
    
    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY:")
    print(f"• Exact title duplicates: {len(exact_duplicates)} groups")
    print(f"• Title + category duplicates: {len(category_duplicates)} groups") 
    print(f"• Similar title duplicates: {len(near_duplicates)} groups")
    
    # Count total duplicate tasks
    total_duplicate_tasks = sum(len(task_list) for task_list in exact_duplicates.values())
    print(f"• Total tasks with exact title duplicates: {total_duplicate_tasks}")


def main():
    """Main function to find and report duplicate tasks."""
    try:
        # Find the latest JSON file
        latest_file = find_latest_json_file()
        
        # Load tasks
        tasks = load_tasks_from_json(latest_file)
        
        if not tasks:
            print("No tasks found in the file.")
            return
        
        # Analyze duplicates
        analyze_duplicate_patterns(tasks)
        
    except FileNotFoundError as e:
        print(f"Error: {e}")
    except json.JSONDecodeError as e:
        print(f"Error parsing JSON: {e}")
    except Exception as e:
        print(f"Unexpected error: {e}")


if __name__ == "__main__":
    main() 
