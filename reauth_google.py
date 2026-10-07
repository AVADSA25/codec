#!/usr/bin/env python3
"""Re-authenticate Google OAuth with expanded scopes for CODEC"""
import os
import shutil

CREDS_PATH = os.path.expanduser("~/.codec/google_credentials.json")
TOKEN_PATH = os.path.expanduser("~/.codec/google_token.json")

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/presentations",
    "https://www.googleapis.com/auth/tasks"
]

from google_auth_oauthlib.flow import InstalledAppFlow
flow = InstalledAppFlow.from_client_secrets_file(CREDS_PATH, SCOPES)
# Settings > Connectors runs this too (UI P3.9): a sign-in left open stops after 10 minutes.
creds = flow.run_local_server(port=0, timeout_seconds=600)

# Keep the old token until the new one is in hand: it used to be renamed before the
# sign-in, so an abandoned sign-in left Google disconnected. The new token is 0600.
new_path = TOKEN_PATH + ".new"
fd = os.open(new_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    f.write(creds.to_json())
if os.path.exists(TOKEN_PATH):
    shutil.copy2(TOKEN_PATH, TOKEN_PATH + ".backup")
    print(f"Backed up old token to {TOKEN_PATH}.backup")
os.replace(new_path, TOKEN_PATH)

print(f"\n✅ New token saved with {len(SCOPES)} scopes!")
print("Skills now available: Gmail, Calendar, Drive, Docs, Sheets, Slides, Tasks")
