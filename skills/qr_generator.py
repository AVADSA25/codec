"""Generate QR codes from text or URLs"""
SKILL_NAME = "qr_generator"
SKILL_TRIGGERS = ["generate qr", "make qr code", "qr code for", "create qr"]
SKILL_DESCRIPTION = "Generate QR codes from text or URLs"

import subprocess, os, tempfile

def run(task, app="", ctx=""):
    text = task.lower()
    for w in ["generate qr", "make qr code", "qr code for", "create qr", "qr for"]:
        text = text.replace(w, "").strip()
    if not text:
        return "What should I encode in the QR code?"
    try:
        import qrcode
        img = qrcode.make(text)
        path = os.path.join(tempfile.gettempdir(), "codec_qr.png")
        img.save(path)
        subprocess.run(["open", path])
        return f"QR code generated for: {text}"
    except ImportError:
        return "Install qrcode: pip3 install qrcode[pil]"
