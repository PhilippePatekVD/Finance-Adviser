import yfinance as yf
from google import genai
import json
import datetime
import os

# Vous pouvez modifier ces symboles par ceux de vos ETF ou actions
ASSETS = {
    "VWCE.DE": "Vanguard FTSE All-World",
    "IGLN.L": "iShares Physical Gold"
}

market_data = {}
for ticker, name in ASSETS.items():
    try:
        stock = yf.Ticker(ticker)
        # On récupère l'historique sur 1 mois
        hist = stock.history(period="1mo")
        if not hist.empty:
            current_price = hist['Close'].iloc[-1]
            past_price = hist['Close'].iloc[0]
            perf_30d = ((current_price - past_price) / past_price) * 100
            
            market_data[name] = {
                "Prix Actuel": round(current_price, 2),
                "Performance 30J (%)": round(perf_30d, 2)
            }
    except Exception as e:
        print(f"Erreur lors de la récupération de {ticker}: {e}")

context = f"""
Tu es un analyste financier de haut niveau travaillant pour un Family Office suisse. 
Voici l'état actuel des marchés pour mon portefeuille de suivi (données en pourcentages) :
{json.dumps(market_data, indent=2)}

Rédige une note de synthèse boursière courte. 
Structure ta réponse en HTML clair (utilise uniquement des balises <h3>, <p>, <ul>, <li>) avec :
1. <h3>Tendance Actuelle</h3> : Une brève analyse de la tendance.
2. <h3>Risques Macro-économiques</h3> : Un risque potentiel à surveiller aujourd'hui.
3. <h3>Recommandation Stratégique</h3> : Une orientation de haut niveau.

Ne donne pas de conseils financiers personnels, reste sur une analyse macro impartiale et au ton professionnel.
"""

try:
    api_key = os.environ.get("GEMINI_API_KEY")
    client = genai.Client(api_key=api_key)

    response = client.models.generate_content(
        model='gemini-2.5-pro',
        contents=context,
    )
    ai_text = response.text
except Exception as e:
    ai_text = f"<p>Erreur lors de l'analyse IA : {e}</p>"

output = {
    "last_update": datetime.datetime.now(datetime.timezone.utc).strftime("%d/%m/%Y %H:%M UTC"),
    "market_data": market_data,
    "ai_analysis": ai_text
}

with open("data.json", "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False, indent=4)

print("Analyse terminée et sauvegardée avec succès dans data.json.")
