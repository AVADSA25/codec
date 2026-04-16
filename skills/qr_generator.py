"""Generate QR codes from text or URLs"""
SKILL_NAME = "qr_generator"
SKILL_TRIGGERS = ["generate qr", "make qr code", "qr code for", "create qr"]
SKILL_DESCRIPTION = "Generate QR codes from text or URLs"
SKILL_MCP_EXPOSE = True

import subprocess, os, tempfile, re

def run(task, app="", ctx=""):
    text = task

    # Try to extract a URL first (most common QR use case)
    url_match = re.search(r'(https?://\S+)', text)
    if url_match:
        content = url_match.group(1).rstrip(".,!?)")
    else:
        # Extract content after "for" or "of" or "with"
        for_match = re.search(r'(?:code\s+)?(?:for|of|with)\s+(.+?)$', text, re.I)
        if for_match:
            content = for_match.group(1).strip().rstrip(".,!?")
        else:
            # Fallback: strip trigger phrases
            content = text.lower()
            for w in ["generate a qr code", "generate qr code", "generate qr",
                       "make a qr code", "make qr code", "create a qr code",
                       "create qr code", "create qr", "qr code for", "qr for",
                       "hey codec", "codec"]:
                content = content.replace(w, "").strip()
            content = content.strip(" ,.")

    if not content:
        return "What should I encode in the QR code?"

    # If it looks like a domain without protocol, add https://
    if re.match(r'^[a-zA-Z0-9][\w.-]+\.[a-zA-Z]{2,}(/\S*)?$', content):
        content = "https://" + content

    try:
        import qrcode
        img = qrcode.make(content)
        path = os.path.join(tempfile.gettempdir(), "codec_qr.png")
        img.save(path)
        subprocess.run(["open", path])
        return f"QR code generated for: {content}"
    except ImportError:
        return "Install qrcode: pip3 install qrcode[pil]"
