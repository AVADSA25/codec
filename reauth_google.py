#!/usr/bin/env python3
"""Re-authenticate Google OAuth with expanded scopes for CODEC"""
import os, json

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

# Delete old token to force re-auth
if os.path.exists(TOKEN_PATH):
    os.rename(TOKEN_PATH, TOKEN_PATH + ".backup")
    print(f"Backed up old token to {TOKEN_PATH}.backup")

from google_auth_oauthlib.flow import InstalledAppFlow
flow = InstalledAppFlow.from_client_secrets_file(CREDS_PATH, SCOPES)
creds = flow.run_local_server(port=0)

with open(TOKEN_PATH, "w") as f:
    f.write(creds.to_json())

print(f"\n\u2705 New token saved with {len(SCOPES)} scopes!")
print("Skills now available: Gmail, Calendar, Drive, Docs, Sheets, Slides, Tasks")
