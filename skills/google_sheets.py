"""Google Sheets skill for CODEC — read and search spreadsheets"""
import json, os

SKILL_NAME = "google_sheets"

SKILL_TRIGGERS = ["google sheets", "my spreadsheets", "spreadsheet", "my sheets", "search sheets", "find sheet", "open sheet", "read sheet", "show sheet"]
SKILL_DESCRIPTION = "Search and read Google Sheets from your Google account"

TOKEN_PATH = os.path.expanduser("~/.codec/google_token.json")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly", "https://www.googleapis.com/auth/drive.readonly"]

def _get_creds():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
    return creds

def run(task, context=None):
    try:
        from googleapiclient.discovery import build
        creds = _get_creds()
        task_lower = task.lower()

        drive = build("drive", "v3", credentials=creds)
        query_terms = task_lower
        for w in ["google sheets", "my spreadsheets", "spreadsheet", "my sheets", "search sheets", "find sheet", "open sheet", "read sheet", "show sheet"]:
            query_terms = query_terms.replace(w, "")
        query_terms = query_terms.strip()

        if query_terms and len(query_terms) > 2:
            q = f"mimeType='application/vnd.google-apps.spreadsheet' and name contains '{query_terms}' and trashed=false"
        else:
            q = "mimeType='application/vnd.google-apps.spreadsheet' and trashed=false"

        results = drive.files().list(q=q, pageSize=10, orderBy="modifiedTime desc",
            fields="files(id,name,modifiedTime)").execute()
        files = results.get("files", [])

        if not files:
            return "No Google Sheets found matching your query."

        if any(w in task_lower for w in ["read", "open", "show", "content", "data"]) and len(files) <= 2:
            sheets = build("sheets", "v4", credentials=creds)
            result = sheets.spreadsheets().values().get(
                spreadsheetId=files[0]["id"], range="A1:Z50").execute()
            values = result.get("values", [])
            if not values:
                return f"\U0001f4ca **{files[0]['name']}** \u2014 empty or no data in first sheet."
            lines = [f"\U0001f4ca **{files[0]['name']}** (first 50 rows):\n"]
            for i, row in enumerate(values[:50]):
                if i == 0:
                    lines.append("| " + " | ".join(str(c) for c in row) + " |")
                    lines.append("|" + "---|" * len(row))
                else:
                    lines.append("| " + " | ".join(str(c) for c in row) + " |")
            return "\n".join(lines)

        lines = ["\U0001f4ca **Your Google Sheets:**\n"]
        for f in files:
            mod = f.get("modifiedTime", "")[:10]
            lines.append(f"\u2022 **{f['name']}** \u2014 modified {mod}")
        return "\n".join(lines)
    except Exception as e:
        return f"Google Sheets error: {e}"
