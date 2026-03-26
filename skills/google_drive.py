"""Google Drive — Search and list files"""
SKILL_NAME = "google_drive"
SKILL_TRIGGERS = ["search drive", "find file", "find document", "my files", "drive files", "search for file", "find in drive", "google drive", "recent files", "recent documents"]
SKILL_DESCRIPTION = "Search and list files in your Google Drive"

import json, os

TOKEN_PATH = os.path.expanduser("~/.codec/google_token.json")

def _get_service():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    creds = Credentials.from_authorized_user_file(TOKEN_PATH)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_PATH, 'w') as f: f.write(creds.to_json())
    return build('drive', 'v3', credentials=creds)

def run(task, app="", ctx=""):
    try:
        service = _get_service()
        low = task.lower()

        # Extract search term
        search_term = ""
        for prefix in ["search drive for", "find file", "find document", "search for file", "find in drive", "search drive"]:
            if prefix in low:
                search_term = low.split(prefix)[-1].strip()
                break

        if search_term:
            query = f"name contains '{search_term}' and trashed = false"
        elif "recent" in low:
            query = "trashed = false"
        else:
            query = "trashed = false"

        results = service.files().list(
            q=query,
            pageSize=10,
            fields="files(id, name, mimeType, modifiedTime, size)",
            orderBy="modifiedTime desc"
        ).execute()

        files = results.get('files', [])
        if not files:
            return f"No files found{' for: ' + search_term if search_term else ''}."

        lines = [f"Found {len(files)} files{' for: ' + search_term if search_term else ' (recent)'}:"]
        for f in files:
            name = f.get('name', 'Unknown')
            mime = f.get('mimeType', '')
            modified = f.get('modifiedTime', '')[:10]
            icon = "📄"
            if 'folder' in mime: icon = "📁"
            elif 'spreadsheet' in mime or 'excel' in mime: icon = "📊"
            elif 'presentation' in mime: icon = "📊"
            elif 'image' in mime: icon = "🖼"
            elif 'pdf' in mime: icon = "📕"
            lines.append(f"  {icon} {name} ({modified})")
        return "\n".join(lines)

    except Exception as e:
        return f"Drive error: {str(e)}"
