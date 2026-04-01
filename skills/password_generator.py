"""Generate secure random passwords"""
SKILL_NAME = "password_generator"
SKILL_TRIGGERS = ["generate password", "new password", "random password", "secure password"]
SKILL_DESCRIPTION = "Generate secure random passwords"

import string, secrets

def run(task, app="", ctx=""):
    length = 16
    for w in task.split():
        try:
            n = int(w)
            if 4 <= n <= 128:
                length = n
                break
        except:
            pass
    chars = string.ascii_letters + string.digits + "!@#$%&*"
    password = ''.join(secrets.choice(chars) for _ in range(length))
    import subprocess
    subprocess.run(["pbcopy"], input=password.encode(), check=True)
    return f"Password generated ({length} chars) and copied to clipboard."
