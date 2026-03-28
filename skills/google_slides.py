"""Google Slides skill for CODEC — list and read presentations"""
import json, os

SKILL_TRIGGERS = ["google slides", "my slides", "presentations", "my presentations", "find presentation", "search slides", "open slides", "read slides"]
SKILL_DESCRIPTION = "Search and read Google Slides presentations"

TOKEN_PATH = os.path.expanduser("~/.codec/google_token.json")
SCOPES = ["https://www.googleapis.com/auth/presentations.readonly", "https://www.googleapis.com/auth/drive.readonly"]

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
        for w in ["google slides", "my slides", "presentations", "my presentations", "find presentation", "search slides", "open slides", "read slides"]:
            query_terms = query_terms.replace(w, "")
        query_terms = query_terms.strip()

        if query_terms and len(query_terms) > 2:
            q = f"mimeType='application/vnd.google-apps.presentation' and name contains '{query_terms}' and trashed=false"
        else:
            q = "mimeType='application/vnd.google-apps.presentation' and trashed=false"

        results = drive.files().list(q=q, pageSize=10, orderBy="modifiedTime desc",
            fields="files(id,name,modifiedTime)").execute()
        files = results.get("files", [])

        if not files:
            return "No Google Slides presentations found."

        if any(w in task_lower for w in ["read", "open", "show", "content"]) and len(files) <= 2:
            slides = build("slides", "v1", credentials=creds)
            pres = slides.presentations().get(presentationId=files[0]["id"]).execute()
            slide_list = pres.get("slides", [])
            lines = [f"\U0001f39e\ufe0f **{files[0]['name']}** \u2014 {len(slide_list)} slides:\n"]
            for i, slide in enumerate(slide_list):
                title = ""
                for el in slide.get("pageElements", []):
                    shape = el.get("shape", {})
                    if shape.get("placeholder", {}).get("type") in ["TITLE", "CENTERED_TITLE"]:
                        for tr in shape.get("text", {}).get("textElements", []):
                            title += tr.get("textRun", {}).get("content", "")
                lines.append(f"  Slide {i+1}: {title.strip() or '(no title)'}")
            return "\n".join(lines)

        lines = ["\U0001f39e\ufe0f **Your Google Slides:**\n"]
        for f in files:
            mod = f.get("modifiedTime", "")[:10]
            lines.append(f"\u2022 **{f['name']}** \u2014 modified {mod}")
        return "\n".join(lines)
    except Exception as e:
        return f"Google Slides error: {e}"
