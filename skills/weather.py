"""CODEC Skill: Weather"""
SKILL_NAME = "weather"
SKILL_DESCRIPTION = "Get current weather for a location"
SKILL_MCP_EXPOSE = True
SKILL_TRIGGERS = ["weather", "temperature", "forecast", "how hot", "how cold", "is it raining"]

def run(task, app="", ctx=""):
    """Fetch weather using wttr.in"""
    import requests, re
    # Extract location from task
    text = task.lower().strip()

    # Strategy 1: explicit "in/for/at <city>" — strongest signal
    loc_match = re.search(r'(?:weather|temperature|forecast|raining|hot|cold)\s+(?:in|for|at|of)\s+([a-zA-Z\s]{2,30})', text)
    if loc_match:
        location = loc_match.group(1).strip().strip("?.,!").strip()
    else:
        location = ""

    # If no explicit city found, default to home — don't try to parse free text
    # Common phrases like "how is the weather today" / "around me" / typos all → Marbella
    _noise = {"weather", "temperature", "forecast", "today", "tonight", "tomorrow",
              "right", "now", "please", "outside", "currently", "around", "me", "here",
              "near", "like", "the", "is", "how", "hwo", "what", "whats", "check",
              "get", "give", "tell", "show", "can", "you", "my", "a", "it", "in",
              "for", "at", "of", "and", "hot", "cold", "raining", "this", "morning",
              "afternoon", "evening", "night", "hows", "s"}
    if location:
        # Verify extracted location isn't all noise words
        loc_words = set(re.findall(r'[a-z]+', location))
        if loc_words and loc_words.issubset(_noise):
            location = ""

    if not location or len(location) < 2:
        location = "Marbella"

    try:
        r = requests.get(f"https://wttr.in/{location}?format=%C+%t+%h+%w", timeout=10)
        r.encoding = "utf-8"
        if r.status_code == 200 and "Unknown location" not in r.text:
            return f"Weather in {location.title()}: {r.text.strip()}"
        # If wttr.in doesn't recognize it, fall back to default
        if location.lower() != "marbella":
            r2 = requests.get("https://wttr.in/Marbella?format=%C+%t+%h+%w", timeout=10)
            r2.encoding = "utf-8"
            if r2.status_code == 200:
                return f"Weather in Marbella: {r2.text.strip()}"
    except:
        pass
    return f"Couldn't fetch weather for {location}. Network may be unavailable."
