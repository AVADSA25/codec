"""Google Docs skill for CODEC — search, read, and create Google Docs"""
import os

SKILL_NAME = "google_docs"

SKILL_TRIGGERS = [
    # Create
    "create a google doc", "create google doc", "create a doc",
    "make a google doc", "make a doc", "make a document",
    "new google doc", "new doc", "new document",
    "write a google doc", "write a doc", "write a document",
    "create a google document", "create google document",
    # Read / Search
    "search google docs", "search my docs", "find google doc",
    "open google doc", "read google doc",
    "list my docs", "show my docs", "my google docs",
]
# Words that indicate this is NOT a google docs intent (e.g. mouse control)
_SKIP_IF_CONTAINS = ["click", "press", "tap", "scroll", "move mouse", "cursor"]
SKILL_DESCRIPTION = "Search, read, and create Google Docs"

def _get_creds():
    import sys; sys.path.insert(0, os.path.expanduser("~/codec-repo"))
    from codec_google_auth import get_credentials
    return get_credentials()


def _parse_create_request(task):
    """Extract title and content from natural language."""
    import re
    task.lower()

    # Extract title from "called X", "named X", "titled X"
    title = "CODEC Document"
    title_match = re.search(r'(?:called|named|titled)\s+["\']?([^"\',.]+)["\']?', task, re.I)
    if title_match:
        title = title_match.group(1).strip()
        # Clean trailing filler
        for filler in [" and write", " and put", " and add", " with content",
                       " with the text", " with text", " containing"]:
            idx = title.lower().find(filler)
            if idx > 0:
                title = title[:idx].strip()

    # Extract content from "write X in it", "with content X", "containing X", "put X in it"
    content = ""
    for pattern in [
        r'(?:write|put|add)\s+(.+?)\s+(?:in it|in the doc|inside)',
        r'(?:with (?:the )?(?:content|text|body))\s+(.+?)(?:\.|$)',
        r'(?:containing|that says|saying)\s+(.+?)(?:\.|$)',
        r'(?:write|put|add)\s+(.+?)$',
    ]:
        m = re.search(pattern, task, re.I)
        if m:
            content = m.group(1).strip().rstrip(".,!?")
            break

    return title, content


def _create_doc(task):
    """Create a new Google Doc with optional content."""
    from googleapiclient.discovery import build
    creds = _get_creds()

    title, content = _parse_create_request(task)

    docs_service = build("docs", "v1", credentials=creds)

    # Create the doc
    doc = docs_service.documents().create(body={"title": title}).execute()
    doc_id = doc["documentId"]
    url = f"https://docs.google.com/document/d/{doc_id}/edit"

    # Insert content if provided
    if content:
        requests_body = [
            {"insertText": {"location": {"index": 1}, "text": content}}
        ]
        docs_service.documents().batchUpdate(
            documentId=doc_id,
            body={"requests": requests_body}
        ).execute()

    result = f"Created Google Doc: **{title}**\n{url}"
    if content:
        result += f"\nContent: \"{content[:100]}{'...' if len(content) > 100 else ''}\""
    return result


def run(task, context=None):
    # Skip if the task is clearly a mouse/click action, not a docs query
    task_lower = task.lower()
    if any(skip in task_lower for skip in _SKIP_IF_CONTAINS):
        return None
    try:
        from googleapiclient.discovery import build
        creds = _get_creds()

        # ── Create doc ──
        if any(w in task_lower for w in [
            "create a google doc", "create google doc", "create a doc",
            "make a google doc", "make a doc", "make a document",
            "new google doc", "new doc", "new document",
            "write a google doc", "write a doc", "write a document",
            "create a google document", "create google document",
        ]):
            return _create_doc(task)

        # ── Search / Read ──
        drive = build("drive", "v3", credentials=creds)
        query_terms = task_lower
        for w in ["google docs", "google doc", "my docs", "my documents",
                  "search docs", "find doc", "list docs", "show docs",
                  "open doc", "read doc", "create a doc", "create doc",
                  "hey codec", "codec"]:
            query_terms = query_terms.replace(w, "")
        query_terms = query_terms.strip(" ,.")

        if query_terms and len(query_terms) > 2:
            q = f"mimeType='application/vnd.google-apps.document' and name contains '{query_terms}' and trashed=false"
        else:
            q = "mimeType='application/vnd.google-apps.document' and trashed=false"

        results = drive.files().list(q=q, pageSize=10, orderBy="modifiedTime desc",
            fields="files(id,name,modifiedTime)").execute()
        files = results.get("files", [])

        if not files:
            return "No Google Docs found matching your query."

        if any(w in task_lower for w in ["read", "open", "show", "content"]) and len(files) <= 2:
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
