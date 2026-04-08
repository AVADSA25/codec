"""Check current Bitcoin price"""
SKILL_NAME = "bitcoin_price"
SKILL_TRIGGERS = ["bitcoin price", "btc price", "check bitcoin", "how much is bitcoin"]
SKILL_DESCRIPTION = "Check current Bitcoin price in USD"

import requests

def run(task, app="", ctx=""):
    try:
        r = requests.get("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd&include_24hr_change=true", timeout=10)
        data = r.json()["bitcoin"]
        price = f"${data['usd']:,.2f}"
        change = data.get("usd_24h_change", 0)
        sign = "+" if change >= 0 else ""
        return f"Bitcoin is currently {price} USD ({sign}{change:.1f}% 24h)"
    except Exception:
        try:
            r = requests.get("https://api.coindesk.com/v1/bpi/currentprice.json", timeout=10)
            price = r.json()["bpi"]["USD"]["rate"]
            return f"Bitcoin is currently ${price} USD"
        except Exception:
            return "Could not fetch Bitcoin price"
