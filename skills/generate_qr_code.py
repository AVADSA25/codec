"""CODEC Skill: generate_qr_code"""
SKILL_NAME = "generate_qr_code"
SKILL_DESCRIPTION = "Generates a QR code image from provided text data and saves it to a file."
SKILL_TRIGGERS = ["make qr code", "generate qr code", "create qr code", "make a qr code"]

import os
import qrcode

def run(task, app="", ctx=""):
    try:
        # Extract data from task, defaulting to empty string if not provided
        data = task.get("data", "") if isinstance(task, dict) else str(task)
        filename = task.get("filename", "qr.png") if isinstance(task, dict) else "qr.png"
        
        # Ensure filename has .png extension
        if not filename.endswith(".png"):
            filename += ".png"
            
        # Generate QR code
        img = qrcode.make(data)
        img.save(filename)
        
        return f"QR code generated and saved as {filename}"
    except Exception as e:
        return f"Error generating QR code: {str(e)}"