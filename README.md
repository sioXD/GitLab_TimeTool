# GitLab Time Tracking Dashboard

Ein interaktives Dashboard zur Visualisierung und Analyse von Zeiterfassungsdaten aus GitLab Epics und Issues.

## Installation

### Voraussetzungen

- Python 3.8 oder höher
- GitLab Account mit API-Zugriff
- Personal Access Token mit `api` Scope

## ⚙️ Konfiguration

### Schritt 1: `.env` Datei erstellen

Erstellen Sie eine `.env` Datei im Projektverzeichnis:

```env
TOKEN=ihr_gitlab_personal_access_token
GROUP_FULL_PATH=ihre-gruppe/ihre-untergruppe
EPIC_ROOT_ID=123
REPOSITORY_NAME=ihr-projekt
```

### Schritt 2: GitLab Personal Access Token erstellen

1. Gehen Sie zu **GitLab.com** → **Benutzereinstellungen** → **Access Tokens**
2. Klicken Sie auf **"Add new token"**
3. Geben Sie einen Namen ein (z.B. "Time Tracking Dashboard")
4. Wählen Sie die **Scopes**:
   - ✅ `api` (erforderlich für GraphQL-Zugriff)
   - ✅ `read_api` (optional, aber empfohlen)
5. Setzen Sie ein Ablaufdatum (optional)
6. Klicken Sie auf **"Create personal access token"**
7. **Kopieren Sie den Token** und fügen Sie ihn in die `.env` Datei ein

⚠️ **Wichtig**: Der Token wird nur einmal angezeigt!

### Schritt 3: Gruppe und Epic konfigurieren

- **`GROUP_FULL_PATH`**: Der vollständige Pfad Ihrer GitLab-Gruppe
  - Beispiel: `my-organization/my-team`
  - Finden Sie diesen unter: GitLab → Ihre Gruppe → Einstellungen → Allgemein
  
- **`EPIC_ROOT_ID`**: Die IID (nicht ID!) des Root-Epics
  - Beispiel: `42`
  - Finden Sie diese in der Epic-URL: `https://gitlab.com/groups/my-group/-/epics/42`
  - Die Zahl nach `/epics/` ist die IID

### Projektstruktur

```bash
gitlab_timeapp/
├── app.py                 # Flask-Anwendung und API-Endpoints
├── timetracker.py         # GitLab API-Integration und Datenverarbeitung
├── Epic.py               # Epic-Datenmodell
├── Issue.py              # Issue-Datenmodell mit Timelog-Funktionen
├── Workitem.py           # Basis-Klasse für Epic und Issue
├── templates/
│   └── index.html        # Dashboard-Frontend mit Charts
├── requirements.txt      # Python-Abhängigkeiten
├── .env                  # Konfigurationsdatei (nicht im Git!)
└── README.md            # Diese Datei
```

## Was noch impementiert wird

- % ist nur bei allen Daten (sonst erstellte issues in der zeit period)
- mehr graphen
- css bei tree ansicht soll schöner werden
