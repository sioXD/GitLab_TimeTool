import requests
from dotenv import load_dotenv
import os
from Epic import Epic
from Issue import Issue

load_dotenv()  # Load environment variables from .env file

# === CONFIGURATION ===
GITLAB_URL = "https://gitlab.com"
API_TOKEN = os.getenv("TOKEN")
GROUP_FULL_PATH = os.getenv("GROUP_FULL_PATH")  # Example: "my-org/my-team"
EPIC_IID = os.getenv("EPIC_ROOT_ID")  # Root Epic IID (not ID)

HEADERS = {
    "Authorization": f"Bearer {API_TOKEN}"
}

GRAPHQL_URL = f"{GITLAB_URL}/api/graphql"

# Data structure to store rows for CSV
csv_rows = []
users = []
labels = []


def run_graphql_query(query, variables=None):
    response = requests.post(
        GRAPHQL_URL,
        headers=HEADERS,
        json={"query": query, "variables": variables or {}}
    )
    response.raise_for_status()
    data = response.json()
    if 'errors' in data:
        raise Exception(data['errors'])
    return data['data']


def get_epic_and_children(group_path, epic_iid):
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
    return run_graphql_query(query, variables)['group']['epic']

def accumulateEpicTree(group_path, epic_iid, parent_iid=None):
    epicData = get_epic_and_children(group_path, epic_iid)
    print(epicData)
    epic = Epic(epicData['title'],epicData['iid'])


    for issue in epicData['issues']['nodes']:
        i = Issue(issue['title'],issue['iid'])
        i.hoursEstimate = (issue['timeEstimate'] or 0)/ 3600.
        i.hoursSpent = (issue['totalTimeSpent'] or 0)/ 3600.
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
        #print(f"Issue \"{i.title}\":\n")
        epic.addChild(i)

    for child in epicData['children']['nodes']:
        childEpic = accumulateEpicTree(group_path, child['iid'], epicData['iid'])
        epic.addChild(childEpic)

    return epic


def build_rows_from_epic(e):
    """Baut aus epic und issues ein homogenes objekt mit einer Spalte pro Attribut, dabei wird 
    die Methode rekursiv auf Kinder des übergebenen Elements angewandt"""
    parentId = None if (e.parent == None) else e.parent.id
    #Hier ein objekt mit allen usern und ihrer investierten Zeit pro issue 
    #und pro existierendes Label einen boolean ob er auf diesem issue klebt
    row = {
        "Typ": e.type,
        "Titel": e.title,
        "IID": e.id,
        "Parent IID": parentId,
        "Zeitaufwand (h)": round(e.hoursSpent , 2),
        "gesch. Zeitaufwand (h)": round(e.hoursEstimate , 2)
    }
    if e.type=="issue":
        row.update(e.getUserPercentagesByTime())
        row.update([(l,e.hasLabel(l)) for l in labels])
    csv_rows.append(row)
    for child in e.children:
        build_rows_from_epic(child)


if __name__ == "__main__":
    epic = accumulateEpicTree(GROUP_FULL_PATH, EPIC_IID)
    epic.accumulateTimes()

    build_rows_from_epic(epic)
    print(f"Rows: {len(csv_rows)}")
    print("Daten erfolgreich geladen!")

