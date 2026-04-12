"""CODEC Skill: Translate (any language via Qwen)"""
import subprocess
SKILL_NAME = "translate"
SKILL_DESCRIPTION = "Translate text between any languages"
SKILL_TRIGGERS = ["translate", "in french", "in english", "en francais", "en anglais",
                   "in spanish", "in japanese", "in german", "in italian", "in portuguese",
                   "in chinese", "in arabic", "in russian", "in korean", "in dutch",
                   "to french", "to english", "to spanish", "to japanese", "to german",
                   "to italian", "to portuguese", "to chinese", "to arabic", "to russian",
                   "to korean", "to dutch",
                   "how do you say", "comment dit-on", "what does", "mean in"]

# Languages that use non-Latin scripts — TTS can't speak these
_NON_LATIN = {"japanese", "chinese", "arabic", "russian", "korean", "hindi",
              "thai", "vietnamese", "hebrew", "persian", "ukrainian", "greek"}

def _show_in_terminal(title, body):
    """Open a terminal window showing the translation result."""
    # Use osascript to open Terminal with the result displayed
    script = f'''
tell application "Terminal"
    activate
    do script "clear && echo '\\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━' && echo '  CODEC TRANSLATION' && echo '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━' && echo '' && echo '{body}' && echo '' && echo '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━' && echo '' && echo 'Press any key to close...' && read -n 1"
end tell'''
    subprocess.Popen(["osascript", "-e", script],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def run(task, app="", ctx=""):
    import requests, re
    low = task.lower()
    # Detect target language from "to <language>" or "in <language>"
    lang_match = re.search(r'(?:to|in)\s+(french|english|spanish|japanese|german|italian|portuguese|chinese|arabic|russian|korean|dutch|hindi|turkish|polish|swedish|norwegian|danish|finnish|greek|czech|thai|vietnamese|indonesian|malay|hebrew|persian|ukrainian|romanian|hungarian|catalan)', low)
    if lang_match:
        target = lang_match.group(1).capitalize()
        target_lower = lang_match.group(1)
    elif "in french" in low or "en francais" in low or "to french" in low:
        target = "French"
        target_lower = "french"
    elif "in english" in low or "en anglais" in low or "to english" in low:
        target = "English"
        target_lower = "english"
    else:
        target = "French"  # default
        target_lower = "french"

    is_non_latin = target_lower in _NON_LATIN

    # Extract the text to translate — remove command words
    text = task
    for remove in ["translate", "to " + target_lower, "in " + target_lower,
                    "in french", "in english", "to french", "to english",
                    "en francais", "en anglais", "how do you say", "comment dit-on",
                    "please", "can you", "for me", "hey codec", "codec"]:
        text = re.sub(r'(?i)\b' + re.escape(remove) + r'\b', '', text)
    text = re.sub(r'^\s*[,.\-\s]+|[,.\-\s]+\s*$', '', text).strip().strip('"').strip("'").strip()
    if not text:
        return None

    # For non-Latin scripts: ask for translation + romanization + pronunciation guide
    if is_non_latin:
        prompt = (
            f"Translate to {target}. Reply in EXACTLY this format, nothing else:\n"
            f"Line 1: The translation in {target} script\n"
            f"Line 2: Romanized pronunciation (how to read it in Latin letters)\n"
            f"Line 3: Literal word-by-word meaning\n"
            f"No labels, no explanations. Just the three lines."
        )
    else:
        prompt = f"Translate to {target}. Reply ONLY with the translation in {target}. Nothing else."

    try:
        r = requests.post("http://localhost:8081/v1/chat/completions",
            json={"model": "mlx-community/Qwen3.5-35B-A3B-4bit",
                "messages": [
                    {"role": "system", "content": "You are a translator. You ONLY output what is asked. NEVER explain. NEVER add notes."},
                    {"role": "user", "content": prompt + "\n\nText: " + text}
                ], "max_tokens": 300, "temperature": 0.3,
                "chat_template_kwargs": {"enable_thinking": False}}, timeout=30)
        if r.status_code == 200:
            result = r.json()["choices"][0]["message"].get("content", "").strip()
            result = re.sub(r'<think>[\s\S]*?</think>', '', result).strip()
            if not result:
                return "Translation failed."

            if is_non_latin:
                # Parse the 3-line response
                lines = [l.strip() for l in result.split('\n') if l.strip()]
                native_script = lines[0] if len(lines) > 0 else result
                romanized = lines[1] if len(lines) > 1 else ""
                literal = lines[2] if len(lines) > 2 else ""

                # Show full result in terminal window (with proper characters)
                body_parts = [f"  {text}  →  {target}"]
                body_parts.append(f"")
                body_parts.append(f"  {native_script}")
                if romanized:
                    body_parts.append(f"  Pronunciation: {romanized}")
                if literal:
                    body_parts.append(f"  Literal: {literal}")
                body = "\\n".join(body_parts)
                _show_in_terminal(f"{target} Translation", body)

                # Return speakable version for TTS (romanized pronunciation)
                if romanized:
                    return f"In {target}, {text} is pronounced: {romanized}"
                else:
                    return f"Translation shown in terminal. {target} script cannot be spoken."
            else:
                return f"{text} -> {result}"
    except Exception:
        pass
    return "Translation failed."
