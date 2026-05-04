"""Google Sheets skill for CODEC — read and search spreadsheets"""
import os

SKILL_NAME = "google_sheets"

SKILL_TRIGGERS = [
    "google sheet", "google sheets", "create a sheet", "create sheet",
    "make a sheet", "make a spreadsheet", "create a spreadsheet", "create spreadsheet",
    "new spreadsheet", "new sheet",
    "my spreadsheets", "spreadsheet", "my sheets",
    "search sheets", "find sheet", "open sheet", "read sheet", "show sheet",
]
SKILL_DESCRIPTION = "Search, read, and create Google Sheets"
SKILL_MCP_EXPOSE = True

def _get_creds():
    import sys; sys.path.insert(0, os.path.expanduser("~/codec-repo"))
    from codec_google_auth import get_credentials
    return get_credentials()

def _parse_create_request(task):
    """Extract title, columns, and sample rows from a create request using simple parsing."""
    import re
    task_l = task.lower()
    # Extract title if quoted
    title_match = re.search(r'(?:called|named|titled)\s+["\']([^"\']+)["\']', task, re.I)
    title = title_match.group(1) if title_match else "CODEC Sheet"

    # Extract columns: "3 columns: Date, Task, Status" or "columns Date, Task, Status"
    cols = []
    col_match = re.search(r'columns?[:\s]+([A-Za-z][A-Za-z, ]+)', task, re.I)
    if col_match:
        cols = [c.strip() for c in col_match.group(1).split(',') if c.strip()]
    if not cols:
        # Try to extract from "with X columns"
        num_match = re.search(r'(\d+)\s+columns?', task_l)
        n = int(num_match.group(1)) if num_match else 3
        cols = [f"Column {i+1}" for i in range(n)]

    # Check for sample rows
    rows_match = re.search(r'(\d+)\s+sample\s+rows?', task_l)
    num_rows = int(rows_match.group(1)) if rows_match else 0

    return title, cols, num_rows


def _create_sheet(task):
    """Create a new Google Sheet with optional columns and sample data."""
    from googleapiclient.discovery import build
    creds = _get_creds()
    sheets_service = build("sheets", "v4", credentials=creds)

    title, cols, num_sample = _parse_create_request(task)

    body = {"properties": {"title": title}}
    sheet = sheets_service.spreadsheets().create(body=body).execute()
    sheet_id = sheet["spreadsheetId"]
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"

    # Write header row
    values = [cols]
    # Add sample rows if requested
    for i in range(num_sample):
        row = []
        for c in cols:
            cl = c.lower()
            if "date" in cl:
                from datetime import datetime, timedelta
                row.append((datetime.now() + timedelta(days=i)).strftime("%Y-%m-%d"))
            elif "status" in cl:
                row.append("Pending" if i % 2 == 0 else "Done")
            elif "task" in cl or "name" in cl or "item" in cl:
                row.append(f"Sample item {i+1}")
            else:
                row.append(f"Value {i+1}")
        values.append(row)

    if values:
        sheets_service.spreadsheets().values().update(
            spreadsheetId=sheet_id, range="A1",
            valueInputOption="RAW", body={"values": values}
        ).execute()

    result = f"Created Google Sheet: **{title}**\n{url}"
    if cols:
        result += f"\nColumns: {', '.join(cols)}"
    if num_sample:
        result += f"\n{num_sample} sample rows added"
    return result


def run(task, context=None):
    try:
        from googleapiclient.discovery import build
        creds = _get_creds()
        task_lower = task.lower()

        # ── Create sheet ──
        if any(w in task_lower for w in ["create a sheet", "create sheet", "create a spreadsheet",
                "create spreadsheet", "make a sheet", "make a spreadsheet", "new sheet", "new spreadsheet",
                "create a google sheet", "create google sheet"]):
            return _create_sheet(task)

        drive = build("drive", "v3", credentials=creds)
        query_terms = task_lower
        for w in ["google sheets", "google sheet", "my spreadsheets", "spreadsheet", "my sheets",
                  "search sheets", "find sheet", "open sheet", "read sheet", "show sheet",
                  "create a sheet", "create sheet", "make a sheet", "new sheet"]:
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
