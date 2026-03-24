"""CODEC Skill: Calculator"""
SKILL_NAME = "calculator"
SKILL_DESCRIPTION = "Quick math calculations"
SKILL_TRIGGERS = ["calculate", "what is", "how much is", "times", "plus", "minus", "divided by", "percent of"]

def run(task, app="", ctx=""):
    """Evaluate math expressions safely"""
    import re
    # Extract the math part
    expr = task.lower()
    for remove in ["calculate", "what is", "what's", "how much is", "whats", "please", "can you"]:
        expr = expr.replace(remove, "")
    expr = expr.strip().strip("?").strip()

    # Convert words to operators
    expr = expr.replace("times", "*").replace("multiplied by", "*")
    expr = expr.replace("plus", "+").replace("added to", "+")
    expr = expr.replace("minus", "-").replace("subtracted by", "-")
    expr = expr.replace("divided by", "/").replace("over", "/")
    expr = expr.replace("percent of", "*0.01*")
    expr = expr.replace(",", "")

    # Only allow safe characters
    safe = re.sub(r'[^0-9+\-*/().%\s]', '', expr).strip()
    if not safe:
        return "I couldn't parse that math expression."

    try:
        result = eval(safe)
        if isinstance(result, float) and result == int(result):
            result = int(result)
        return f"{safe} = {result}"
    except:
        return f"Couldn't calculate: {safe}"
