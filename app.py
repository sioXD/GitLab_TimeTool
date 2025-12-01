from flask import Flask, render_template, jsonify, request, send_from_directory
from dotenv import load_dotenv
import os
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime, timedelta
from Epic import Epic
from Issue import Issue
from timetracker import accumulateEpicTree
from collections import defaultdict
import requests
import json
from pathlib import Path
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from google import genai

load_dotenv()

app = Flask(__name__)

# Configure logging
if not os.path.exists('logs'):
    os.makedirs('logs')

file_handler = RotatingFileHandler('logs/app.log', maxBytes=10240000, backupCount=10)
file_handler.setFormatter(logging.Formatter(
    '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'
))
file_handler.setLevel(logging.INFO)
app.logger.addHandler(file_handler)
app.logger.setLevel(logging.INFO)
app.logger.info('GitLab Time Tracking Dashboard startup')

# Initialize scheduler for automated reports
scheduler = BackgroundScheduler()
scheduler.start()

# Schedule weekly report generation (every Tuesday at 8:00 AM)
scheduler.add_job(
    func=lambda: generate_weekly_report(),
    trigger=CronTrigger(day_of_week='tue', hour=8, minute=0),
    id='weekly_report',
    name='Generate weekly project status report',
    replace_existing=True
)

# Global variables for data
csv_rows = []
users = []
labels = []
epic_tree = None

def load_data(force_refresh=False, token=None, group_path=None, epic_id=None):
    """
    Load data from GitLab API and build CSV rows structure
    
    Parameters:
    - force_refresh: Force reload from GitLab
    - token: GitLab Personal Access Token (optional, uses ENV if None)
    - group_path: GitLab group full path (optional, uses ENV if None)
    - epic_id: Epic Root IID (optional, uses ENV if None)
    """
    global csv_rows, users, labels, epic_tree
    
    # Always reload if force_refresh is True
    if force_refresh or epic_tree is None:
        app.logger.info(f"Loading data - force_refresh={force_refresh}, epic_tree={'None' if epic_tree is None else 'exists'}")
        print(f"🔄 Fetching fresh data from GitLab...")
        csv_rows = []
        users_set = set()
        labels_set = set()
        
        # Use provided parameters or fall back to environment variables
        GROUP_FULL_PATH = group_path if group_path is not None else os.getenv("GROUP_FULL_PATH")
        EPIC_IID = epic_id if epic_id is not None else os.getenv("EPIC_ROOT_ID")
        TOKEN = token if token is not None else os.getenv("TOKEN")
        
        if not GROUP_FULL_PATH or not EPIC_IID or not TOKEN:
            app.logger.error("Missing required parameters for data loading")
            raise ValueError("Missing required parameters: TOKEN, GROUP_FULL_PATH, and EPIC_ROOT_ID")
        
        # Import users and labels from timetracker module
        import timetracker
        # Clear previous data
        timetracker.users = []
        timetracker.labels = []
        timetracker.csv_rows = []
        
        # Build epic tree with explicit parameters
        epic_tree = accumulateEpicTree(
            group_path=GROUP_FULL_PATH,
            epic_iid=EPIC_IID,
            token=TOKEN
        )
        epic_tree.accumulateTimes()
        
        # Get users and labels from timetracker module
        users = sorted(list(set(timetracker.users)))
        labels = sorted(list(set(timetracker.labels)))
        
        # Build rows
        def build_rows(e):
            parentId = None if (e.parent == None) else e.parent.id
            row = {
                "Typ": e.type,
                "Titel": e.title,
                "IID": e.id,
                "Parent IID": parentId,
                "Zeitaufwand (h)": round(e.hoursSpent, 2),
                "gesch. Zeitaufwand (h)": round(e.hoursEstimate, 2)
            }
            if e.type == "issue":
                # Add user percentages
                user_percentages = e.getUserPercentagesByTime()
                for user in users:
                    row[user] = round(user_percentages.get(user, 0), 4)
                # Add labels
                for label in labels:
                    row[label] = e.hasLabel(label)
                # Add createdAt and state
                row["createdAt"] = getattr(e, 'createdAt', None)
                row["state"] = getattr(e, 'state', 'opened')  # Status hinzufügen
            else:
                # For epics, set user and label columns to None or 0
                for user in users:
                    row[user] = 0
                for label in labels:
                    row[label] = False
                row["createdAt"] = None
                row["state"] = None  # Epics haben keinen Status
            csv_rows.append(row)
            for child in e.children:
                build_rows(child)
        
        build_rows(epic_tree)
        app.logger.info(f"Data loaded successfully: {len(csv_rows)} items, {len(users)} users, {len(labels)} labels")
        print(f"✅ Data loaded successfully: {len(csv_rows)} items, {len(users)} users, {len(labels)} labels")
        
    return csv_rows

def filter_data_by_date(days=None):
    """Filter time data by date range using spentAt from timelogs"""
    if days is None:
        cutoff_date = None
    else:
        cutoff_date = datetime.now(datetime.now().astimezone().tzinfo) - timedelta(days=days)
    
    filtered_rows = []
    
    def build_filtered_rows(e):
        parentId = None if (e.parent == None) else e.parent.id
        
        if e.type == "issue":
            # Filter timelogs by date using 'Datum' field which contains spentAt
            filtered_hours_spent = 0
            filtered_user_times = {}
            
            try:
                for user, time_entries in e.userTimeMap.items():
                    user_total = 0
                    for entry in time_entries:
                        # Parse the date from 'Datum' field
                        date_str = entry['Datum']
                        # Handle both ISO format with Z and without timezone
                        if date_str.endswith('Z'):
                            entry_date = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                        elif '+' in date_str or date_str.count('-') > 2:
                            entry_date = datetime.fromisoformat(date_str)
                        else:
                            # Assume UTC if no timezone info
                            entry_date = datetime.fromisoformat(date_str).replace(tzinfo=datetime.now().astimezone().tzinfo)
                        
                        # Make entry_date timezone-aware if cutoff_date has timezone
                        if cutoff_date is not None:
                            if entry_date.tzinfo is None:
                                entry_date = entry_date.replace(tzinfo=cutoff_date.tzinfo)
                            
                            if entry_date >= cutoff_date:
                                user_total += entry['Zeit(Std)']
                        else:
                            user_total += entry['Zeit(Std)']
                    
                    if user_total > 0:
                        filtered_user_times[user] = user_total
                        filtered_hours_spent += user_total
            except Exception as ex:
                print(f"Error filtering dates for issue {e.title}: {ex}")
                # If filtering fails, include all time
                for user, time_entries in e.userTimeMap.items():
                    user_total = sum(entry['Zeit(Std)'] for entry in time_entries)
                    filtered_user_times[user] = user_total
                    filtered_hours_spent += user_total
            
            # Calculate percentages
            user_percentages = {}
            if filtered_hours_spent > 0:
                for user in users:
                    if user in filtered_user_times:
                        user_percentages[user] = filtered_user_times[user] / filtered_hours_spent
                    else:
                        user_percentages[user] = 0
            else:
                for user in users:
                    user_percentages[user] = 0
            
            row = {
                "Typ": e.type,
                "Titel": e.title,
                "IID": e.id,
                "Parent IID": parentId,
                "Zeitaufwand (h)": round(filtered_hours_spent, 2),
                "gesch. Zeitaufwand (h)": round(e.hoursEstimate, 2),
                "createdAt": getattr(e, 'createdAt', None),
                "state": getattr(e, 'state', 'opened')  # Status hinzufügen
            }
            
            for user in users:
                row[user] = round(user_percentages.get(user, 0), 4)
            for label in labels:
                row[label] = e.hasLabel(label)
                
        else:  # Epic
            row = {
                "Typ": e.type,
                "Titel": e.title,
                "IID": e.id,
                "Parent IID": parentId,
                "Zeitaufwand (h)": 0,
                "gesch. Zeitaufwand (h)": round(e.hoursEstimate, 2),
                "createdAt": None,
                "state": None  # Epics haben keinen Status
            }
            for user in users:
                row[user] = 0
            for label in labels:
                row[label] = False
        
        filtered_rows.append(row)
        
        # Process children first
        for child in e.children:
            build_filtered_rows(child)
        
        # Sum up children's times for epics (from filtered_rows that have been added)
        if e.type == "epic":
            child_rows = [r for r in filtered_rows if r.get("Parent IID") == e.id]
            total_child_time = sum(r["Zeitaufwand (h)"] for r in child_rows)
            row["Zeitaufwand (h)"] = round(total_child_time, 2)
            
            # Also calculate user percentages for epics based on children
            if total_child_time > 0:
                for user in users:
                    user_time_in_children = sum(
                        r["Zeitaufwand (h)"] * r.get(user, 0) 
                        for r in child_rows
                    )
                    row[user] = round(user_time_in_children / total_child_time if total_child_time > 0 else 0, 4)
    
    if epic_tree:
        build_filtered_rows(epic_tree)
    
    return filtered_rows

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/data")
def get_data():
    """API endpoint to get all data with optional date filtering"""
    try:
        app.logger.info(f"API /api/data called - args: {dict(request.args)}")
        days = request.args.get('days', None)
        days = int(days) if days else None
        start_date = request.args.get('start_date', None)
        end_date = request.args.get('end_date', None)
        refresh = request.args.get('refresh', 'false').lower() == 'true'
        mode = request.args.get('mode', 'env')
        
        # Get config based on mode
        if mode == 'local':
            token = request.args.get('token')
            group_full_path = request.args.get('group_path')
            epic_iid = request.args.get('epic_id')
            repository_name = request.args.get('repo_name', '')
            
            if not token or not group_full_path or not epic_iid:
                raise Exception('Missing required parameters for local mode')
            
            # Load data with local parameters
            if refresh:
                load_data(force_refresh=True,
                token=token,
                group_path=group_full_path,
                epic_id=epic_iid)
            elif epic_tree is None:
                # Load data for the first time
                load_data(force_refresh=False,
                token=token,
                group_path=group_full_path,
                epic_id=epic_iid)
           
        else:
            # ENV mode
            group_full_path = os.getenv("GROUP_FULL_PATH", "")
            repository_name = os.getenv("REPOSITORY_NAME", "")
            
            # Only fetch fresh data if explicitly requested via refresh parameter
            if refresh:
                load_data(force_refresh=True)
            elif epic_tree is None:
                # Load data for the first time
                load_data(force_refresh=False)
        
        # Apply date filtering
        if start_date and end_date:
            data = filter_data_by_date_range(start_date, end_date)
        elif days:
            data = filter_data_by_date(days)
        else:
            data = csv_rows
        
        # Calculate statistics
        issues = [d for d in data if d['Typ'] == 'issue']
        total_spent = sum(d['Zeitaufwand (h)'] for d in issues)
        total_estimated = sum(d['gesch. Zeitaufwand (h)'] for d in issues)
        
        user_stats = {}
        for user in users:
            user_total = sum(d['Zeitaufwand (h)'] * d.get(user, 0) for d in issues)
            user_stats[user] = round(user_total, 2)
        
        label_stats = {}
        for label in labels:
            label_issues = [d for d in issues if d.get(label, False)]
            label_stats[label] = {
                'count': len(label_issues),
                'hours': round(sum(d['Zeitaufwand (h)'] for d in label_issues), 2)
            }
        
        # Calculate creation statistics
        target_matrix_labels = ["Entwurf", "Implementation & Test", "Projektmanagement", "Requirements Engineering"]
        
        if start_date and end_date:
            creation_stats = calculate_creation_stats_date_range(issues, start_date, end_date)
            cfd_stats = calculate_cfd_stats_date_range(issues, start_date, end_date)
            label_timeline_stats = calculate_label_timeline_stats_date_range(
                issues, 
                target_matrix_labels,
                start_date, 
                end_date
            )
        else:
            creation_stats = calculate_creation_stats(issues, days)
            cfd_stats = calculate_cfd_stats(issues, days)
            label_timeline_stats = calculate_label_timeline_stats(
                issues, 
                target_matrix_labels,
                days
            )
            
        user_label_matrix = calculate_user_label_matrix(issues, target_matrix_labels, users)
        
        # For local mode, use the provided group_path, otherwise from ENV
        if mode == 'local':
            response_group_path = group_full_path
            response_repo_name = repository_name
        else:
            response_group_path = os.getenv("GROUP_FULL_PATH", "")
            response_repo_name = os.getenv("REPOSITORY_NAME", "")
        
        return jsonify({
            "success": True,
            "data": data,
            "users": users,
            "labels": labels,
            "group_path": response_group_path,
            "repository_name": response_repo_name,
            "stats": {
                "total_spent": round(total_spent, 2),
                "total_estimated": round(total_estimated, 2),
                "user_stats": user_stats,
                "label_stats": label_stats,
                "creation_stats": creation_stats,
                "cfd_stats": cfd_stats,
                "label_timeline_stats": label_timeline_stats,
                "user_label_matrix": user_label_matrix
            }
        })
    except Exception as e:
        import traceback
        app.logger.error(f"Error in /api/data: {str(e)}\n{traceback.format_exc()}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500

def filter_data_by_date_range(start_date_str, end_date_str):
    """Filter time data by specific date range"""
    try:
        start_date = datetime.fromisoformat(start_date_str).replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = datetime.fromisoformat(end_date_str).replace(hour=23, minute=59, second=59, microsecond=999999)
        
        # Make timezone aware
        if start_date.tzinfo is None:
            start_date = start_date.replace(tzinfo=datetime.now().astimezone().tzinfo)
        if end_date.tzinfo is None:
            end_date = end_date.replace(tzinfo=datetime.now().astimezone().tzinfo)
    except Exception as e:
        print(f"Error parsing date range: {e}")
        return csv_rows
    
    filtered_rows = []
    
    def build_filtered_rows(e):
        parentId = None if (e.parent == None) else e.parent.id
        
        if e.type == "issue":
            filtered_hours_spent = 0
            filtered_user_times = {}
            
            try:
                for user, time_entries in e.userTimeMap.items():
                    user_total = 0
                    for entry in time_entries:
                        date_str = entry['Datum']
                        if date_str.endswith('Z'):
                            entry_date = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                        elif '+' in date_str or date_str.count('-') > 2:
                            entry_date = datetime.fromisoformat(date_str)
                        else:
                            entry_date = datetime.fromisoformat(date_str).replace(tzinfo=datetime.now().astimezone().tzinfo)
                        
                        if entry_date.tzinfo is None:
                            entry_date = entry_date.replace(tzinfo=start_date.tzinfo)
                        
                        if start_date <= entry_date <= end_date:
                            user_total += entry['Zeit(Std)']
                    
                    if user_total > 0:
                        filtered_user_times[user] = user_total
                        filtered_hours_spent += user_total
            except Exception as ex:
                print(f"Error filtering date range for issue {e.title}: {ex}")
                for user, time_entries in e.userTimeMap.items():
                    user_total = sum(entry['Zeit(Std)'] for entry in time_entries)
                    filtered_user_times[user] = user_total
                    filtered_hours_spent += user_total
            
            user_percentages = {}
            if filtered_hours_spent > 0:
                for user in users:
                    if user in filtered_user_times:
                        user_percentages[user] = filtered_user_times[user] / filtered_hours_spent
                    else:
                        user_percentages[user] = 0
            else:
                for user in users:
                    user_percentages[user] = 0
            
            row = {
                "Typ": e.type,
                "Titel": e.title,
                "IID": e.id,
                "Parent IID": parentId,
                "Zeitaufwand (h)": round(filtered_hours_spent, 2),
                "gesch. Zeitaufwand (h)": round(e.hoursEstimate, 2),
                "createdAt": getattr(e, 'createdAt', None),
                "state": getattr(e, 'state', 'opened')
            }
            
            for user in users:
                row[user] = round(user_percentages.get(user, 0), 4)
            for label in labels:
                row[label] = e.hasLabel(label)
                
        else:  # Epic
            row = {
                "Typ": e.type,
                "Titel": e.title,
                "IID": e.id,
                "Parent IID": parentId,
                "Zeitaufwand (h)": 0,
                "gesch. Zeitaufwand (h)": round(e.hoursEstimate, 2),
                "createdAt": None,
                "state": None
            }
            for user in users:
                row[user] = 0
            for label in labels:
                row[label] = False
        
        filtered_rows.append(row)
        
        for child in e.children:
            build_filtered_rows(child)
        
        if e.type == "epic":
            child_rows = [r for r in filtered_rows if r.get("Parent IID") == e.id]
            total_child_time = sum(r["Zeitaufwand (h)"] for r in child_rows)
            row["Zeitaufwand (h)"] = round(total_child_time, 2)
            
            if total_child_time > 0:
                for user in users:
                    user_time_in_children = sum(
                        r["Zeitaufwand (h)"] * r.get(user, 0) 
                        for r in child_rows
                    )
                    row[user] = round(user_time_in_children / total_child_time if total_child_time > 0 else 0, 4)
    
    if epic_tree:
        build_filtered_rows(epic_tree)
    
    return filtered_rows

def calculate_creation_stats_date_range(issues, start_date_str, end_date_str):
    """Calculate issue creation statistics for specific date range"""
    start_date = datetime.fromisoformat(start_date_str).replace(tzinfo=datetime.now().astimezone().tzinfo)
    end_date = datetime.fromisoformat(end_date_str).replace(tzinfo=datetime.now().astimezone().tzinfo)
    
    weekly_stats = defaultdict(lambda: defaultdict(int))
    
    for issue in issues:
        created_at = issue.get('createdAt')
        if not created_at:
            continue
        
        try:
            if created_at.endswith('Z'):
                created_date = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            elif '+' in created_at or created_at.count('-') > 2:
                created_date = datetime.fromisoformat(created_at)
            else:
                created_date = datetime.fromisoformat(created_at).replace(tzinfo=datetime.now().astimezone().tzinfo)
            
            if created_date.tzinfo is None:
                created_date = created_date.replace(tzinfo=start_date.tzinfo)
            
            if not (start_date <= created_date <= end_date):
                continue
            
            week_start = created_date - timedelta(days=created_date.weekday())
            week_label = week_start.strftime('%Y-%m-%d')
            
            max_user = None
            max_percentage = 0
            for user in users:
                percentage = issue.get(user, 0)
                if percentage > max_percentage:
                    max_percentage = percentage
                    max_user = user
            
            if max_user:
                weekly_stats[week_label][max_user] += 1
            else:
                weekly_stats[week_label]['Unbekannt'] += 1
                
        except Exception as ex:
            continue
    
    sorted_weeks = sorted(weekly_stats.keys())
    result = {
        'weeks': sorted_weeks,
        'user_data': {}
    }
    
    for user in users + ['Unbekannt']:
        result['user_data'][user] = [weekly_stats[week].get(user, 0) for week in sorted_weeks]
    
    result['user_data'] = {k: v for k, v in result['user_data'].items() if sum(v) > 0}
    
    return result

def calculate_cfd_stats_date_range(issues, start_date_str, end_date_str):
    """Calculate CFD statistics for specific date range"""
    start_date = datetime.fromisoformat(start_date_str).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=datetime.now().astimezone().tzinfo)
    end_date = datetime.fromisoformat(end_date_str).replace(hour=23, minute=59, second=59, microsecond=999999, tzinfo=datetime.now().astimezone().tzinfo)
    
    daily_status = {}
    day = start_date
    
    while day <= end_date:
        day_label = day.strftime('%Y-%m-%d')
        
        todo_count = 0
        in_progress_count = 0
        done_count = 0
        
        for issue in issues:
            created_at = issue.get('createdAt')
            state = issue.get('state', 'opened')
            time_spent = issue.get('Zeitaufwand (h)', 0)
            
            if not created_at:
                continue
            
            try:
                if created_at.endswith('Z'):
                    created_date = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                elif '+' in created_at or created_at.count('-') > 2:
                    created_date = datetime.fromisoformat(created_at)
                else:
                    created_date = datetime.fromisoformat(created_at).replace(tzinfo=datetime.now().astimezone().tzinfo)
                
                if created_date.date() <= day.date():
                    if state == 'closed':
                        done_count += 1
                    elif time_spent > 0:
                        in_progress_count += 1
                    else:
                        todo_count += 1
                    
            except Exception as ex:
                continue
        
        daily_status[day_label] = {
            'todo': todo_count,
            'in_progress': in_progress_count,
            'done': done_count,
            'total': todo_count + in_progress_count + done_count
        }
        
        day += timedelta(days=1)
    
    sorted_dates = sorted(daily_status.keys())
    
    result = {
        'dates': sorted_dates,
        'todo': [daily_status[d]['todo'] for d in sorted_dates],
        'in_progress': [daily_status[d]['in_progress'] for d in sorted_dates],
        'done': [daily_status[d]['done'] for d in sorted_dates],
        'total': [daily_status[d]['total'] for d in sorted_dates]
    }
    
    return result

def calculate_creation_stats(issues, days=None):
    """Calculate issue creation statistics by time period"""
    
    if days is None:
        cutoff_date = None
    else:
        cutoff_date = datetime.now(datetime.now().astimezone().tzinfo) - timedelta(days=days)
    
    # Group issues by week and creator
    weekly_stats = defaultdict(lambda: defaultdict(int))
    
    for issue in issues:
        created_at = issue.get('createdAt')
        if not created_at:
            continue
        
        try:
            # Parse createdAt date
            if created_at.endswith('Z'):
                created_date = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            elif '+' in created_at or created_at.count('-') > 2:
                created_date = datetime.fromisoformat(created_at)
            else:
                created_date = datetime.fromisoformat(created_at).replace(tzinfo=datetime.now().astimezone().tzinfo)
            
            # Apply date filter
            if cutoff_date is not None:
                if created_date.tzinfo is None:
                    created_date = created_date.replace(tzinfo=cutoff_date.tzinfo)
                if created_date < cutoff_date:
                    continue
            
            # Get week start (Monday)
            week_start = created_date - timedelta(days=created_date.weekday())
            week_label = week_start.strftime('%Y-%m-%d')
            
            # Count issues per user per week
            # Note: We don't have creator info in the current data structure
            # We'll use the primary contributor (user with most time) as proxy
            max_user = None
            max_percentage = 0
            for user in users:
                percentage = issue.get(user, 0)
                if percentage > max_percentage:
                    max_percentage = percentage
                    max_user = user
            
            if max_user:
                weekly_stats[week_label][max_user] += 1
            else:
                weekly_stats[week_label]['Unbekannt'] += 1
                
        except Exception as ex:
            print(f"Error parsing createdAt for issue {issue.get('Titel', 'Unknown')}: {ex}")
            continue
    
    # Convert to sorted list format
    sorted_weeks = sorted(weekly_stats.keys())
    result = {
        'weeks': sorted_weeks,
        'user_data': {}
    }
    
    for user in users + ['Unbekannt']:
        result['user_data'][user] = [weekly_stats[week].get(user, 0) for week in sorted_weeks]
    
    # Remove users with no issues created
    result['user_data'] = {k: v for k, v in result['user_data'].items() if sum(v) > 0}
    
    return result

def calculate_cfd_stats(issues, days=None):
    """Calculate Cumulative Flow Diagram data - issues by status over time"""
    
    if days is None:
        # Use all data, find the earliest issue
        cutoff_date = None
        all_dates = []
        for issue in issues:
            created_at = issue.get('createdAt')
            if created_at:
                try:
                    if created_at.endswith('Z'):
                        created_date = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                    elif '+' in created_at or created_at.count('-') > 2:
                        created_date = datetime.fromisoformat(created_at)
                    else:
                        created_date = datetime.fromisoformat(created_at).replace(tzinfo=datetime.now().astimezone().tzinfo)
                    all_dates.append(created_date)
                except:
                    pass
        
        if all_dates:
            cutoff_date = min(all_dates)
        else:
            cutoff_date = datetime.now(datetime.now().astimezone().tzinfo) - timedelta(days=30)
    else:
        cutoff_date = datetime.now(datetime.now().astimezone().tzinfo) - timedelta(days=days)
    
    # Create daily timeline
    current_date = cutoff_date.replace(hour=0, minute=0, second=0, microsecond=0)
    end_date = datetime.now(datetime.now().astimezone().tzinfo).replace(hour=23, minute=59, second=59)
    
    daily_status = {}
    
    # Calculate status counts per day
    day = current_date
    
    while day <= end_date:
        day_label = day.strftime('%Y-%m-%d')
        
        # Count issues by status on this day
        todo_count = 0
        in_progress_count = 0
        done_count = 0
        
        for issue in issues:
            created_at = issue.get('createdAt')
            state = issue.get('state', 'opened')
            time_spent = issue.get('Zeitaufwand (h)', 0)
            
            if not created_at:
                continue
            
            try:
                # Parse created date
                if created_at.endswith('Z'):
                    created_date = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                elif '+' in created_at or created_at.count('-') > 2:
                    created_date = datetime.fromisoformat(created_at)
                else:
                    created_date = datetime.fromisoformat(created_at).replace(tzinfo=datetime.now().astimezone().tzinfo)
                
                # Only count issues that were created before or on this day
                if created_date.date() <= day.date():
                    # Classify issue status
                    if state == 'closed':
                        done_count += 1
                    elif time_spent > 0:
                        # If time was spent, consider it in progress
                        in_progress_count += 1
                    else:
                        # No time spent and not closed = to do
                        todo_count += 1
                    
            except Exception as ex:
                continue
        
        daily_status[day_label] = {
            'todo': todo_count,
            'in_progress': in_progress_count,
            'done': done_count,
            'total': todo_count + in_progress_count + done_count
        }
        
        day += timedelta(days=1)
    
    # Sort by date
    sorted_dates = sorted(daily_status.keys())
    
    result = {
        'dates': sorted_dates,
        'todo': [daily_status[d]['todo'] for d in sorted_dates],
        'in_progress': [daily_status[d]['in_progress'] for d in sorted_dates],
        'done': [daily_status[d]['done'] for d in sorted_dates],
        'total': [daily_status[d]['total'] for d in sorted_dates]
    }
    
    return result

def calculate_label_timeline_stats(issues, target_labels, days=None):
    """Calculate timeline statistics for specific labels"""
    from collections import defaultdict
    
    if days is None:
        # Use all data, find the earliest issue
        cutoff_date = None
        all_dates = []
        for issue in issues:
            created_at = issue.get('createdAt')
            if created_at:
                try:
                    if created_at.endswith('Z'):
                        created_date = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                    elif '+' in created_at or created_at.count('-') > 2:
                        created_date = datetime.fromisoformat(created_at)
                    else:
                        created_date = datetime.fromisoformat(created_at).replace(tzinfo=datetime.now().astimezone().tzinfo)
                    all_dates.append(created_date)
                except:
                    pass
        
        if all_dates:
            cutoff_date = min(all_dates)
        else:
            cutoff_date = datetime.now(datetime.now().astimezone().tzinfo) - timedelta(days=30)
    else:
        cutoff_date = datetime.now(datetime.now().astimezone().tzinfo) - timedelta(days=days)
    
    # Create daily timeline
    current_date = cutoff_date.replace(hour=0, minute=0, second=0, microsecond=0)
    end_date = datetime.now(datetime.now().astimezone().tzinfo).replace(hour=23, minute=59, second=59)
    
    daily_label_hours = defaultdict(lambda: {label: 0 for label in target_labels})
    
    # Iterate through each issue and accumulate time spent per label per day
    for issue in issues:
        created_at = issue.get('createdAt')
        if not created_at:
            continue
        
        try:
            # Parse created date
            if created_at.endswith('Z'):
                created_date = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            elif '+' in created_at or created_at.count('-') > 2:
                created_date = datetime.fromisoformat(created_at)
            else:
                created_date = datetime.fromisoformat(created_at).replace(tzinfo=datetime.now().astimezone().tzinfo)
            
            # Check which labels this issue has
            issue_labels = [label for label in target_labels if issue.get(label, False)]
            
            if not issue_labels:
                continue
            
            # Get total time spent on this issue
            time_spent = issue.get('Zeitaufwand (h)', 0)
            
            if time_spent > 0:
                # Distribute time equally among the issue's matching labels
                time_per_label = time_spent / len(issue_labels)
                
                # Assign this time to the creation date
                day_label = created_date.strftime('%Y-%m-%d')
                
                if created_date >= current_date and created_date <= end_date:
                    for label in issue_labels:
                        daily_label_hours[day_label][label] += time_per_label
                    
        except Exception as ex:
            continue
    
    # Create cumulative timeline
    day = current_date
    cumulative_hours = {label: 0 for label in target_labels}
    sorted_dates = []
    cumulative_data = defaultdict(list)
    
    while day <= end_date:
        day_label = day.strftime('%Y-%m-%d')
        sorted_dates.append(day_label)
        
        # Add today's hours to cumulative
        for label in target_labels:
            cumulative_hours[label] += daily_label_hours[day_label].get(label, 0)
            cumulative_data[label].append(round(cumulative_hours[label], 2))
        
        day += timedelta(days=1)
    
    result = {
        'dates': sorted_dates,
        'labels': target_labels,
        'data': {label: cumulative_data[label] for label in target_labels}
    }
    
    return result

def calculate_label_timeline_stats_date_range(issues, target_labels, start_date_str, end_date_str):
    """Calculate timeline statistics for specific labels in a date range"""
    from collections import defaultdict
    
    start_date = datetime.fromisoformat(start_date_str).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=datetime.now().astimezone().tzinfo)
    end_date = datetime.fromisoformat(end_date_str).replace(hour=23, minute=59, second=59, microsecond=999999, tzinfo=datetime.now().astimezone().tzinfo)
    
    daily_label_hours = defaultdict(lambda: {label: 0 for label in target_labels})
    
    # Iterate through each issue and accumulate time spent per label per day
    for issue in issues:
        created_at = issue.get('createdAt')
        if not created_at:
            continue
        
        try:
            # Parse created date
            if created_at.endswith('Z'):
                created_date = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            elif '+' in created_at or created_at.count('-') > 2:
                created_date = datetime.fromisoformat(created_at)
            else:
                created_date = datetime.fromisoformat(created_at).replace(tzinfo=datetime.now().astimezone().tzinfo)
            
            # Check which labels this issue has
            issue_labels = [label for label in target_labels if issue.get(label, False)]
            
            if not issue_labels:
                continue
            
            # Get total time spent on this issue
            time_spent = issue.get('Zeitaufwand (h)', 0)
            
            if time_spent > 0:
                # Distribute time equally among the issue's matching labels
                time_per_label = time_spent / len(issue_labels)
                
                # Assign this time to the creation date
                day_label = created_date.strftime('%Y-%m-%d')
                
                if created_date >= start_date and created_date <= end_date:
                    for label in issue_labels:
                        daily_label_hours[day_label][label] += time_per_label
                    
        except Exception as ex:
            continue
    
    # Create cumulative timeline
    day = start_date
    cumulative_hours = {label: 0 for label in target_labels}
    sorted_dates = []
    cumulative_data = defaultdict(list)
    
    while day <= end_date:
        day_label = day.strftime('%Y-%m-%d')
        sorted_dates.append(day_label)
        
        # Add today's hours to cumulative
        for label in target_labels:
            cumulative_hours[label] += daily_label_hours[day_label].get(label, 0)
            cumulative_data[label].append(round(cumulative_hours[label], 2))
        
        day += timedelta(days=1)
    
    result = {
        'dates': sorted_dates,
        'labels': target_labels,
        'data': {label: cumulative_data[label] for label in target_labels}
    }
    
    return result

def calculate_user_label_matrix(issues, target_labels, users):
    """Calculate matrix of time spent by user per label"""
    # Initialize matrix
    matrix = {user: {label: 0.0 for label in target_labels} for user in users}
    
    for issue in issues:
        # Find which target labels this issue has
        active_labels = [label for label in target_labels if issue.get(label, False)]
        
        if not active_labels:
            continue
            
        total_time = issue.get('Zeitaufwand (h)', 0)
        if total_time <= 0:
            continue
            
        # Calculate time per label (distribute equally if multiple labels)
        for user in users:
            user_percentage = issue.get(user, 0)
            if user_percentage > 0:
                user_time = total_time * user_percentage
                time_per_label = user_time / len(active_labels)
                
                for label in active_labels:
                    matrix[user][label] += time_per_label
                    
    # Round values
    for user in matrix:
        for label in matrix[user]:
            matrix[user][label] = round(matrix[user][label], 2)
            
    return matrix

def generate_weekly_report():
    """Generate weekly project status report using Google Gemini API"""
    try:
        load_data(force_refresh=True)
        
        # Ensure reports directory exists
        reports_dir = Path("reports")
        reports_dir.mkdir(exist_ok=True)
        
        # Get data from last week
        last_week_data = filter_data_by_date(7)
        issues = [d for d in last_week_data if d['Typ'] == 'issue']
        
        # Calculate statistics
        total_spent = sum(d['Zeitaufwand (h)'] for d in issues)
        total_estimated = sum(d['gesch. Zeitaufwand (h)'] for d in issues)
        
        user_stats = {}
        for user in users:
            user_total = sum(d['Zeitaufwand (h)'] * d.get(user, 0) for d in issues)
            user_stats[user] = round(user_total, 2)
        
        label_stats = {}
        for label in labels:
            label_issues = [d for d in issues if d.get(label, False)]
            if len(label_issues) > 0:
                label_stats[label] = {
                    'count': len(label_issues),
                    'hours': round(sum(d['Zeitaufwand (h)'] for d in label_issues), 2)
                }
    
        # Get top issues
        top_issues = sorted(issues, key=lambda x: x['Zeitaufwand (h)'], reverse=True)[:5]
        
        # Calculate issues opened and closed in the last 7 days
        cutoff_date = datetime.now(datetime.now().astimezone().tzinfo) - timedelta(days=7)
        issues_opened_in_period = 0
        issues_closed_in_period = 0
        
        # Get all issues (not filtered by time spent, but by creation/close date)
        all_data = csv_rows
        all_issues = [d for d in all_data if d['Typ'] == 'issue']
        
        for issue in all_issues:
            # Check if created in period
            created_at = issue.get('createdAt')
            if created_at:
                try:
                    if created_at.endswith('Z'):
                        created_date = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                    elif '+' in created_at or created_at.count('-') > 2:
                        created_date = datetime.fromisoformat(created_at)
                    else:
                        created_date = datetime.fromisoformat(created_at).replace(tzinfo=datetime.now().astimezone().tzinfo)
                    
                    if created_date.tzinfo is None:
                        created_date = created_date.replace(tzinfo=cutoff_date.tzinfo)
                    
                    if created_date >= cutoff_date:
                        issues_opened_in_period += 1
                except:
                    pass
            
            # Check if closed in period (we'd need closedAt field for accurate tracking)
            # For now, we'll count closed issues with time spent in the period as proxy
            if issue.get('state') == 'closed' and issue.get('Zeitaufwand (h)', 0) > 0:
                # Check if any time was logged in the period
                # This is an approximation since we don't have exact close date
                if issue in issues:  # If it appears in filtered data, it had activity
                    issues_closed_in_period += 1
        
        # Calculate user label matrix
        target_matrix_labels = ["Entwurf", "Implementation & Test", "Projektmanagement", "Requirements Engineering"]
        user_label_matrix = calculate_user_label_matrix(issues, target_matrix_labels, users)

        # Prepare data for LLM
        report_data = {
            'week': f"KW {datetime.now().isocalendar()[1]}, {datetime.now().year}",
            'date_range': f"{(datetime.now() - timedelta(days=7)).strftime('%d.%m.%Y')} - {datetime.now().strftime('%d.%m.%Y')}",
            'total_hours': total_spent,
            'total_estimated': total_estimated,
            'progress_percentage': round((total_spent / total_estimated * 100) if total_estimated > 0 else 0, 1),
            'user_stats': user_stats,
            'user_label_matrix': user_label_matrix,
            'top_issues': [
                {
                    'title': issue['Titel'],
                    'iid': issue['IID'],
                    'hours': issue['Zeitaufwand (h)']
                }
                for issue in top_issues
            ],
            'total_issues': len(all_issues),
            'closed_issues': len([i for i in all_issues if i.get('state') == 'closed']),
            'issues_opened_in_period': issues_opened_in_period,
            'issues_closed_in_period': issues_closed_in_period
        }
        
        # Call Google Gemini API
        gemini_api_key = os.getenv("GEMINI_API_KEY")
        if not gemini_api_key:
            raise ValueError("GEMINI_API_KEY not found in environment variables")
        
        # Create Gemini client
        client = genai.Client(api_key=gemini_api_key)
        
        prompt = f"""Erstelle einen professionellen Projektstatusreport in HTML-Format für die letzte Woche.

Projektdaten:
- Berichtszeitraum: {report_data['date_range']} ({report_data['week']})
- Gesamte aufgewendete Zeit: {report_data['total_hours']} Stunden (für diesen Berichtszeitraum)
- Geschätzte Zeit: {report_data['total_estimated']} Stunden (für die gesamte Projekt Laufzeit)
- Anzahl offener Issues: {report_data['total_issues'] - report_data['closed_issues']} (gesamt)
- Anzahl geschlossener Issues: {report_data['closed_issues']} (gesamt)
- Im Berichtszeitraum geöffnete Issues: {report_data['issues_opened_in_period']}
- Im Berichtszeitraum geschlossene Issues: {report_data['issues_closed_in_period']}

Zeitverteilung nach Mitarbeitern:
{json.dumps(report_data['user_stats'], indent=2, ensure_ascii=False)}

Zeitmatrix (Mitarbeiter und Überkategorien):
{json.dumps(report_data['user_label_matrix'], indent=2, ensure_ascii=False)}

Top 5 Issues nach Zeitaufwand:
{json.dumps(report_data['top_issues'], indent=2, ensure_ascii=False)}

Erstelle einen gut strukturierten HTML-Report mit:
1. Überschrift mit Berichtszeitraum
2. Executive Summary (2-3 Sätze)
3. Kennzahlen in einem übersichtlichen Layout (inkl. geöffnete/geschlossene Issues im Zeitraum)
4. Zeitverteilung nach Mitarbeitern (als Tabelle)
5. Zeitmatrix: Mitarbeiter und Überkategorien (als Tabelle)
6. Top 5 Issues
7. Zusammenfassung und Ausblick

Verwende modernes CSS (inline) mit professionellem Design, Farben und guter Lesbarkeit.
Gib NUR den HTML-Code zurück, ohne Markdown-Formatierung."""

        # Generate content with Gemini
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt
        )
        html_report = response.text
        
        # Clean up markdown code blocks if present
        if html_report.startswith('```html'):
            html_report = html_report.replace('```html', '').replace('```', '').strip()
        elif html_report.startswith('```'):
            html_report = html_report.replace('```', '').strip()
        
        # Save report
        timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        filename = f"report_{timestamp}.html"
        filepath = reports_dir / filename
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(html_report)
        
        app.logger.info(f"Weekly report generated successfully: {filename}")
        print(f"✅ Weekly report generated: {filename}")
        return {
            'success': True,
            'filename': filename,
            'filepath': str(filepath),
            'data': report_data
        }
        
    except Exception as e:
        app.logger.error(f"Error generating weekly report: {str(e)}\n{traceback.format_exc()}")
        print(f"❌ Error generating weekly report: {e}")
        import traceback
        traceback.print_exc()
        return {
            'success': False,
            'error': str(e)
        }

@app.route("/api/generate-report", methods=['POST'])
def api_generate_report():
    """API endpoint to manually trigger report generation"""
    app.logger.info("API /api/generate-report called")
    try:
        result = generate_weekly_report()
    except Exception as e:
        import traceback
        app.logger.error(f"Error in /api/generate-report: {str(e)}\n{traceback.format_exc()}")
        result = {
            'success': False,
            'error': str(e)
        }
    
    return jsonify(result)

@app.route("/api/reports")
def list_reports():
    """List all available reports"""
    try:
        app.logger.info("API /api/reports called")
        reports_dir = Path("reports")
        if not reports_dir.exists():
            return jsonify({'success': True, 'reports': []})
        
        reports = []
        for file in sorted(reports_dir.glob("report_*.html"), reverse=True):
            reports.append({
                'filename': file.name,
                'created': datetime.fromtimestamp(file.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
                'size': file.stat().st_size
            })
        
        return jsonify({'success': True, 'reports': reports})
    except Exception as e:
        app.logger.error(f"Error listing reports: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route("/reports/<filename>")
def serve_report(filename):
    """Serve a specific report file"""
    try:
        app.logger.info(f"Serving report: {filename}")
        reports_dir = Path("reports")
        return send_from_directory(reports_dir, filename)
    except Exception as e:
        app.logger.error(f"Error serving report {filename}: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 404

if __name__ == "__main__":
    app.run(debug=True)
