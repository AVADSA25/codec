"""CODEC Skill: Weather"""
SKILL_NAME = "weather"
SKILL_DESCRIPTION = "Get current weather for a location"
SKILL_TRIGGERS = ["weather", "temperature", "forecast", "how hot", "how cold", "is it raining"]

def run(task, app="", ctx=""):
    """Fetch weather using wttr.in"""
    import requests, re
    # Extract location from task
    words = task.lower()
    for remove in ["what is the", "what's the", "whats the", "how is the", "how's the",
                    "hows the", "can you check the", "check the", "get me the", "give me the",
                    "weather in", "weather for", "weather at", "temperature in", "temperature for",
                    "forecast for", "forecast in", "weather", "forecast", "temperature",
                    "how hot is it in", "how cold is it in", "is it raining in",
                    "how hot", "how cold", "is it raining", "right now", "today", "please"]:
        words = words.replace(remove, "")
    location = words.strip().strip("?.,!").strip()
    if not location:
        location = "Marbella"

    try:
        r = requests.get(f"https://wttr.in/{location}?format=%C+%t+%h+%w", timeout=10)
        r.encoding = "utf-8"
        if r.status_code == 200:
            return f"Weather in {location.title()}: {r.text.strip()}"
    except:
        pass
    return f"Couldn't fetch weather for {location}. Network may be unavailable."
