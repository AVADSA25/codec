"""CODEC Skill: Weather"""
SKILL_NAME = "weather"
SKILL_DESCRIPTION = "Get current weather for a location"
SKILL_TRIGGERS = ["weather", "temperature", "forecast", "how hot", "how cold", "is it raining"]

def run(task, app="", ctx=""):
    """Fetch weather using wttr.in"""
    import requests, re
    # Extract location from task
    words = task.lower()
    for remove in ["what's the", "whats the", "weather in", "weather for", "temperature in",
                    "how's the weather", "hows the weather", "weather", "forecast for", "forecast"]:
        words = words.replace(remove, "")
    location = words.strip().strip("?").strip()
    if not location:
        location = "Marbella"

    try:
        r = requests.get(f"https://wttr.in/{location}?format=%C+%t+%h+%w", timeout=10)
        if r.status_code == 200:
            return f"Weather in {location.title()}: {r.text.strip()}"
    except:
        pass
    return f"Couldn't fetch weather for {location}. Network may be unavailable."
