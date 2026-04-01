"""Check current Bitcoin price"""
SKILL_NAME = "bitcoin_price"
SKILL_TRIGGERS = ["bitcoin price", "btc price", "check bitcoin", "how much is bitcoin"]
SKILL_DESCRIPTION = "Check current Bitcoin price in USD"

import requests

def run(task, app="", ctx=""):
    try:
        r = requests.get("https://api.coindesk.com/v1/bpi/currentprice.json", timeout=10)
        price = r.json()["bpi"]["USD"]["rate"]
        return f"Bitcoin is currently ${price} USD"
    except:
        return "Could not fetch Bitcoin price"
