from flask import Flask, render_template, jsonify, request
from dotenv import load_dotenv
import os
from datetime import datetime, timedelta
from Epic import Epic
from Issue import Issue
from timetracker import accumulateEpicTree

load_dotenv()

app = Flask(__name__)

# Global variables for data
csv_rows = []
users = []
labels = []
epic_tree = None

def load_data():
    """Load data from GitLab API and build CSV rows structure"""
    global csv_rows, users, labels, epic_tree
    csv_rows = []
    users_set = set()
    labels_set = set()
    
    GROUP_FULL_PATH = os.getenv("GROUP_FULL_PATH")
    EPIC_IID = os.getenv("EPIC_ROOT_ID")
    
    # Build epic tree
    epic_tree = accumulateEpicTree(GROUP_FULL_PATH, EPIC_IID)
    epic_tree.accumulateTimes()
    
    # Collect users and labels from all issues
    def collect_users_labels(item):
        if item.type == "issue":
            try:
                for user in item.userTimeMap.keys():
                    users_set.add(user)
            except:
                pass
            try:
                for label in item.labels:
                    labels_set.add(label)
            except:
                pass
        for child in item.children:
            collect_users_labels(child)
    
    collect_users_labels(epic_tree)
    users = sorted(list(users_set))
    labels = sorted(list(labels_set))
    
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
        else:
            # For epics, set user and label columns to None or 0
            for user in users:
                row[user] = 0
            for label in labels:
                row[label] = False
        csv_rows.append(row)
        for child in e.children:
            build_rows(child)
    
    build_rows(epic_tree)
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
                "gesch. Zeitaufwand (h)": round(e.hoursEstimate, 2)
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
                "gesch. Zeitaufwand (h)": round(e.hoursEstimate, 2)
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
        days = request.args.get('days', None)
        days = int(days) if days else None
        
        if epic_tree is None:
            load_data()
        
        if days:
            data = filter_data_by_date(days)
        else:
            data = csv_rows if csv_rows else load_data()
        
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
        
        # Get repository name from GROUP_FULL_PATH (e.g., "group/repo")
        group_full_path = os.getenv("GROUP_FULL_PATH", "")
        repository_name = os.getenv("REPOSITORY_NAME", "")
        
        return jsonify({
            "success": True,
            "data": data,
            "users": users,
            "labels": labels,
            "group_path": group_full_path,
            "repository_name": repository_name,
            "stats": {
                "total_spent": round(total_spent, 2),
                "total_estimated": round(total_estimated, 2),
                "user_stats": user_stats,
                "label_stats": label_stats
            }
        })
    except Exception as e:
        import traceback
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500

if __name__ == "__main__":
    app.run(debug=True)
