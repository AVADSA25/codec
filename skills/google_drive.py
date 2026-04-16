"""Google Drive — Search and list files"""
SKILL_NAME = "google_drive"
SKILL_TRIGGERS = [
    "search my drive", "search drive", "search in drive", "find in drive", "find on drive",
    "find file", "find document", "find in my drive",
    "my files", "drive files", "my documents",
    "search for file", "search for document",
    "google drive", "my drive",
    "recent files", "recent documents",
    "look in drive", "check drive", "check my drive",
    "in my drive", "on my drive", "from my drive", "from drive",
]
SKILL_DESCRIPTION = "Search and list files in your Google Drive"
SKILL_MCP_EXPOSE = True

import os

def _get_service():
    import sys; sys.path.insert(0, os.path.expanduser("~/codec-repo"))
    from codec_google_auth import build_service
    return build_service("drive", "v3")

def run(task, app="", ctx=""):
    try:
        service = _get_service()
        low = task.lower()

        # Extract search term — longest prefix first for greedy matching
        search_term = ""
        for prefix in [
            "search my drive for", "search drive for", "search in drive for",
            "find in my drive", "find in drive", "find on drive",
            "find file", "find document", "find on my drive",
            "search for file", "search for document",
            "check my drive for", "check drive for",
            "look in drive for", "look in my drive for",
            "from my drive", "from drive",
            "search my drive", "search drive",
            "in my drive", "on my drive", "my drive",
        ]:
            if prefix in low:
                search_term = low.split(prefix)[-1].strip()
                # Clean trailing noise
                for noise in [" what is the", " what's the", " and tell me", " please", " for me",
                              " can you", " could you", " do you"]:
                    idx = search_term.find(noise)
                    if idx > 0:
                        search_term = search_term[:idx].strip()
                # Remove filler words from search query
                filler = {"the", "a", "an", "my", "our", "latest", "most", "recent",
                          "last", "newest", "oldest", "all", "any", "some", "this", "that"}
                words = [w for w in search_term.split() if w not in filler]
                search_term = " ".join(words).strip(".,!?")
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
