import requests
from dotenv import load_dotenv
import os
from Epic import Epic
from Issue import Issue

load_dotenv()  # Load environment variables from .env file

# === CONFIGURATION ===
GITLAB_URL = "https://gitlab.com"

# Data structure to store rows for CSV
csv_rows = []
users = []
labels = []


def run_graphql_query(query, variables=None, token=None):
    """Execute GraphQL query with optional token parameter"""
    api_token = token if token is not None else os.getenv("TOKEN")
    
    headers = {
        "Authorization": f"Bearer {api_token}"
    }
    
    graphql_url = f"{GITLAB_URL}/api/graphql"
    
    response = requests.post(
        graphql_url,
        headers=headers,
        json={"query": query, "variables": variables or {}}
    )
    response.raise_for_status()
    data = response.json()
    if 'errors' in data:
        raise Exception(data['errors'])
    return data['data']


def get_epic_and_children(group_path, epic_iid, token=None):
    """Fetch epic and its children from GitLab API"""
    query = """
    query EpicTree($groupPath: ID!, $epicIid: ID!)  {
      group(fullPath: $groupPath) {
        epic(iid: $epicIid) {
          iid
          title
          children{
            nodes{
              iid
            }
          }
          issues {
            nodes {
              iid
              title
              createdAt
              timeEstimate
              totalTimeSpent
              labels{
                nodes{
                  title
                }
              }
              timelogs{
                nodes {
                  timeSpent
                  spentAt
                  user {
                    username
                    name
                  }
                }
              }
            }
          }
        }
      }
    }
    """
    variables = {
        "groupPath": group_path,
        "epicIid": epic_iid
    }
    return run_graphql_query(query, variables, token)['group']['epic']


def accumulateEpicTree(group_path=None, epic_iid=None, parent_iid=None, token=None):
    """
    Build epic tree recursively
    
    Parameters:
    - group_path: GitLab group full path (e.g., 'my-org/my-team')
    - epic_iid: Epic IID (not ID)
    - parent_iid: Parent epic IID (for recursion)
    - token: GitLab Personal Access Token
    
    If parameters are None, they will be read from environment variables.
    """
    # Use environment variables as fallback
    if group_path is None:
        group_path = os.getenv("GROUP_FULL_PATH")
    if epic_iid is None:
        epic_iid = os.getenv("EPIC_ROOT_ID")
    if token is None:
        token = os.getenv("TOKEN")
    
    # Validate required parameters
    if not group_path:
        raise ValueError("GROUP_FULL_PATH is required (either as parameter or environment variable)")
    if not epic_iid:
        raise ValueError("EPIC_ROOT_ID is required (either as parameter or environment variable)")
    if not token:
        raise ValueError("TOKEN is required (either as parameter or environment variable)")
    
    epicData = get_epic_and_children(group_path, epic_iid, token)
    print(f"Processing Epic: {epicData['title']} (IID: {epicData['iid']})")
    epic = Epic(epicData['title'], epicData['iid'])

    for issue in epicData['issues']['nodes']:
        i = Issue(issue['title'], issue['iid'])
        i.hoursEstimate = (issue['timeEstimate'] or 0) / 3600.
        i.hoursSpent = (issue['totalTimeSpent'] or 0) / 3600.
        i.createdAt = issue.get('createdAt')
        
        for log in issue['timelogs']['nodes']:
            # Use name (full name) instead of username
            user_name = log['user']['name'] or log['user']['username']
            i.addTimeSpentByUser(log['timeSpent']/3600, user_name, log['spentAt'])
            if not user_name in users:
                users.append(user_name)
        
        for lab in issue['labels']['nodes']:
            i.addLabel(lab['title'])
            if not lab['title'] in labels:
                labels.append(lab['title'])
        
        epic.addChild(i)

    for child in epicData['children']['nodes']:
        childEpic = accumulateEpicTree(group_path, child['iid'], epicData['iid'], token)
        epic.addChild(childEpic)

    return epic


def build_rows_from_epic(e):
    """Baut aus epic und issues ein homogenes objekt mit einer Spalte pro Attribut, dabei wird 
    die Methode rekursiv auf Kinder des übergebenen Elements angewandt"""
    parentId = None if (e.parent == None) else e.parent.id
    # Hier ein objekt mit allen usern und ihrer investierten Zeit pro issue 
    # und pro existierendes Label einen boolean ob er auf diesem issue klebt
    row = {
        "Typ": e.type,
        "Titel": e.title,
        "IID": e.id,
        "Parent IID": parentId,
        "Zeitaufwand (h)": round(e.hoursSpent, 2),
        "gesch. Zeitaufwand (h)": round(e.hoursEstimate, 2)
    }
    if e.type == "issue":
        row.update(e.getUserPercentagesByTime())
        row.update([(l, e.hasLabel(l)) for l in labels])
        row["createdAt"] = getattr(e, 'createdAt', None)
    else:
        row["createdAt"] = None
    
    csv_rows.append(row)
    for child in e.children:
        build_rows_from_epic(child)


if __name__ == "__main__":
    # When run directly, use environment variables
    GROUP_FULL_PATH = os.getenv("GROUP_FULL_PATH")
    EPIC_IID = os.getenv("EPIC_ROOT_ID")
    TOKEN = os.getenv("TOKEN")
    
    if not GROUP_FULL_PATH or not EPIC_IID or not TOKEN:
        print("❌ Error: Missing required environment variables!")
        print("Please set TOKEN, GROUP_FULL_PATH, and EPIC_ROOT_ID in your .env file")
        exit(1)
    
    print(f"🔄 Fetching data for Epic {EPIC_IID} in {GROUP_FULL_PATH}...")
    epic = accumulateEpicTree(GROUP_FULL_PATH, EPIC_IID, token=TOKEN)
    epic.accumulateTimes()

    build_rows_from_epic(epic)
    print(f"✅ Rows: {len(csv_rows)}")
    print("✅ Daten erfolgreich geladen!")

