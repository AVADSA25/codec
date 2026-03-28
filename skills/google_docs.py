"""Google Docs skill for CODEC — read and search Google Docs"""
import json, os, datetime

SKILL_NAME = "google_docs"

SKILL_TRIGGERS = ["google docs", "my docs", "my documents", "search docs", "find doc", "open doc", "read doc", "list docs", "show docs"]
SKILL_DESCRIPTION = "Search and read Google Docs from your Google account"

TOKEN_PATH = os.path.expanduser("~/.codec/google_token.json")
SCOPES = ["https://www.googleapis.com/auth/documents.readonly", "https://www.googleapis.com/auth/drive.readonly"]

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
        for w in ["google docs", "my docs", "my documents", "search docs", "find doc", "list docs", "show docs", "open doc", "read doc"]:
            query_terms = query_terms.replace(w, "")
        query_terms = query_terms.strip()

        if query_terms and len(query_terms) > 2:
            q = f"mimeType='application/vnd.google-apps.document' and name contains '{query_terms}' and trashed=false"
        else:
            q = "mimeType='application/vnd.google-apps.document' and trashed=false"

        results = drive.files().list(q=q, pageSize=10, orderBy="modifiedTime desc",
            fields="files(id,name,modifiedTime,owners)").execute()
        files = results.get("files", [])

        if not files:
            return "No Google Docs found matching your query."

        if any(w in task_lower for w in ["read", "open", "show", "content"]) and len(files) == 1:
            docs_service = build("docs", "v1", credentials=creds)
            doc = docs_service.documents().get(documentId=files[0]["id"]).execute()
            text_content = ""
            for element in doc.get("body", {}).get("content", []):
                for para in element.get("paragraph", {}).get("elements", []):
                    text_content += para.get("textRun", {}).get("content", "")
            preview = text_content[:3000]
            return f"\U0001f4c4 **{files[0]['name']}**\n\n{preview}{'...(truncated)' if len(text_content) > 3000 else ''}"

        lines = ["\U0001f4c4 **Your Google Docs:**\n"]
        for f in files:
            mod = f.get("modifiedTime", "")[:10]
            lines.append(f"\u2022 **{f['name']}** \u2014 modified {mod}")
        return "\n".join(lines)
    except Exception as e:
        return f"Google Docs error: {e}"
