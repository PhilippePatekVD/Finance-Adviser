import json
import yfinance as yf
from google import genai
from datetime import datetime

# Indices de référence pour l'analyse macro (S&P 500, MSCI World, Or)
MACRO_TICKERS = ["^GSPC", "URTH", "GC=F"]

def load_portfolio():
    """Charge le portefeuille depuis le fichier JSON."""
    try:
        with open("portfolio.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Erreur lors de la lecture du portefeuille : {e}")
        return None

def fetch_market_data(tickers):
    """Récupère les cours de clôture et la variation à 30 jours."""
    data = {}
    for ticker_symbol in tickers:
        try:
            ticker = yf.Ticker(ticker_symbol)
            hist = ticker.history(period="1mo")
            if hist.empty:
                continue
            current_price = hist['Close'].iloc[-1]
            price_30d_ago = hist['Close'].iloc[0]
            variation = ((current_price - price_30d_ago) / price_30d_ago) * 100
            data[ticker_symbol] = {
                "prix_cloture": round(current_price, 2),
                "variation_30j_pct": round(variation, 2)
            }
        except Exception:
            pass
    return data

def generate_financial_report(portfolio_data, portfolio_market_data, macro_data):
    """Interroge Gemini 2.5 Pro pour une analyse financière experte."""
    client = genai.Client() # Utilise GEMINI_API_KEY automatiquement
    
    prompt = f"""
    Agissez en tant qu'analyste financier institutionnel de premier plan, impartial et rigoureux.
    
    Voici le contexte macro-économique actuel basé sur les indices de référence sur 30 jours :
    {json.dumps(macro_data, indent=2)}
    
    Voici le portefeuille actuel de l'investisseur et son profil :
    {json.dumps(portfolio_data, indent=2)}
    
    Voici les performances récentes des actifs de ce portefeuille :
    {json.dumps(portfolio_market_data, indent=2)}
    
    Générez un rapport structuré au format JSON strict avec les clés suivantes :
    - "analyse_macro_globale" : Une synthèse claire de la dynamique actuelle des marchés mondiaux.
    - "revue_portefeuille" : Une critique impartiale de l'allocation actuelle (les faiblesses, les risques de concentration, l'adéquation avec les conditions de marché).
    - "conseils_ajustement" : Des recommandations précises et argumentées par des faits économiques pour rééquilibrer le portefeuille.
    - "opportunites_marche" : Identifiez 2 ou 3 secteurs, classes d'actifs ou zones géographiques actuellement sous-évalués ou présentant de "bonnes affaires" potentielles, en expliquant la thèse d'investissement fondamentale.
    """
    
    try:
        response = client.models.generate_content(
            model='gemini-2.5-pro',
            contents=prompt,
            config={'response_mime_type': 'application/json'}
        )
        return json.loads(response.text)
    except Exception as e:
        print(f"Erreur API : {e}")
        return {"erreur": "Génération échouée"}

def main():
    portfolio = load_portfolio()
    if not portfolio:
        return

    # Extraire les tickers du portefeuille
    portfolio_tickers = [actif["ticker"] for actif in portfolio.get("actifs_actuels", [])]
    
    # Récupérer les données
    portfolio_market_data = fetch_market_data(portfolio_tickers)
    macro_data = fetch_market_data(MACRO_TICKERS)
    
    # Générer le rapport via IA
    report = generate_financial_report(portfolio, portfolio_market_data, macro_data)
    
    # Sauvegarde
    final_output = {
        "date_generation": datetime.utcnow().isoformat() + "Z",
        "rapport": report
    }
    
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(final_output, f, indent=4, ensure_ascii=False)

if __name__ == "__main__":
    main()
