"""CODEC Skill: Weather Information Fetcher"""
SKILL_NAME = "weather_info_fetcher"
SKILL_DESCRIPTION = "Fetches and summarizes current weather data for a specified location."
SKILL_TRIGGERS = ["what's the weather like", "check weather for", "tell me the weather", "how is the weather"]

import os, requests, json

def run(task, app="", ctx=""):
    try:
        # Extract location from task if not provided in context
        location = task.get("location") if isinstance(task, dict) else task.split("for ")[-1] if "for " in task else "London"
        
        # Simulate API call or use real API if key is available
        # In a real scenario, replace with actual API endpoint and key
        api_key = os.getenv("WEATHER_API_KEY", "demo_key")
        base_url = "https://api.openweathermap.org/data/2.5/weather"
        
        params = {
            "q": location,
            "appid": api_key,
            "units": "metric"
        }
        
        # Note: This is a placeholder for actual API logic. 
        # If running without a real key, return a simulated response for demonstration.
        if api_key == "demo_key":
            return f"Simulated weather for {location}: Sunny, 22°C, with a slight breeze."
            
        response = requests.get(base_url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        city = data.get("name", "Unknown")
        temp = data.get("main", {}).get("temp", "N/A")
        description = data.get("weather", [{}])[0].get("description", "unknown")
        humidity = data.get("main", {}).get("humidity", "N/A")
        
        return f"Weather in {city}: {description.capitalize()}, {temp}°C, Humidity {humidity}%."
        
    except Exception as e:
        return f"Could not retrieve weather information: {str(e)}"