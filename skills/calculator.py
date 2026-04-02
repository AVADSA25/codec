"""CODEC Skill: Calculator"""
SKILL_NAME = "calculator"
SKILL_DESCRIPTION = "Quick math calculations"
SKILL_TRIGGERS = ["calculate", "how much is", "what is the sum", "times", "plus", "minus", "divided by", "percent of", "multiply", "subtract"]

def run(task, app="", ctx=""):
    """Evaluate math expressions safely"""
    import re
    expr = task.lower()
    for remove in ["calculate", "how much is", "whats", "please", "can you"]:
        expr = expr.replace(remove, "")
    expr = expr.strip().strip("?").strip()
    expr = expr.replace("times", "*").replace("multiplied by", "*")
    expr = expr.replace("plus", "+").replace("added to", "+")
    expr = expr.replace("minus", "-").replace("subtracted by", "-")
    expr = expr.replace("divided by", "/").replace("over", "/")
    expr = expr.replace("percent of", "*0.01*")
    expr = expr.replace(",", "")
    safe = re.sub(r'[^0-9+\-*/().%\s]', '', expr).strip()
    if not safe:
        return None  # Return None = skill can't handle it, pass to Q-Agent
    try:
        result = eval(safe)
        if isinstance(result, float) and result == int(result):
            result = int(result)
        return f"{safe} = {result}"
    except:
        return None
