import json
import yfinance as yf
from google import genai
from datetime import datetime

MACRO_TICKERS = ["^GSPC", "URTH", "GC=F"]

# Le nouveau Screener : Qualité, Moat, Santé, Tech de pointe, Luxe
SCREENER_TICKERS = [
    "TECN.SW", "VACN.SW", "ISRG", "SYK", 
    "CSU.TO", "FTNT", "ASML",
    "RMS.PA", "RACE"
]

def load_portfolio():
    try:
        with open("portfolio.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Erreur portefeuille : {e}")
        return None

def fetch_market_data(tickers):
    data = {}
    for ticker_symbol in tickers:
        try:
            ticker = yf.Ticker(ticker_symbol)
            hist = ticker.history(period="1mo")
            if not hist.empty:
                data[ticker_symbol] = {
                    "variation_30j_pct": round(((hist['Close'].iloc[-1] - hist['Close'].iloc[0]) / hist['Close'].iloc[0]) * 100, 2)
                }
        except:
            pass
    return data

def fetch_financial_news():
    """Récupère les 5 dernières grandes dépêches de Yahoo Finance."""
    try:
        # On utilise l'ETF SPY (S&P 500) comme proxy pour obtenir les news globales du marché
        news_data = yf.Ticker("SPY").news
        headlines = [f"- {item.get('title')} (Source: {item.get('publisher')})" for item in news_data[:5]]
        return "\n".join(headlines) if headlines else "Aucune actualité financière majeure récupérée."
    except Exception as e:
        return f"Erreur lors de la récupération de l'actualité : {e}"

def run_fundamental_screener(tickers):
    """Extrait les ratios fondamentaux stricts pour identifier les anomalies de valorisation."""
    screener_data = {}
    for t in tickers:
        try:
            ticker = yf.Ticker(t)
            info = ticker.info
            if not info:
                continue
            
            screener_data[t] = {
                "nom": info.get("shortName", t),
                "secteur": info.get("sector", "Inconnu"),
                "PER_Forward": info.get("forwardPE", "N/A"),
                "Croissance_Benefices_Est": info.get("earningsGrowth", "N/A"),
                "Marge_Nette": info.get("profitMargins", "N/A"),
                "Rendement_Capitaux_Propres_ROE": info.get("returnOnEquity", "N/A")
            }
        except Exception:
            pass
    return screener_data

def generate_financial_report(portfolio_data, portfolio_market_data, macro_data, screener_data, news_headlines):
    client = genai.Client()
    
    prompt = f"""
    Agissez en tant que stratégiste de marché et gérant de fonds quantitatif de premier plan.
    Vous devez fournir une analyse institutionnelle, impartiale et strictement basée sur les données fournies.
    
    1. DÉPÊCHES DU JOUR :
    {news_headlines}
    
    2. CONTEXTE MACRO (30j) : {json.dumps(macro_data)}
    3. PORTEFEUILLE ACTUEL : {json.dumps(portfolio_data)}
    4. PERF PORTEFEUILLE (30j) : {json.dumps(portfolio_market_data)}
    
    5. SCREENER FONDAMENTAL (VALEURS DE QUALITÉ / SOUS LES RADARS) :
    {json.dumps(screener_data, indent=2)}
    
    Générez un rapport exhaustif respectant rigoureusement ces 5 axes :
    - "actualite_financiere" : Rédigez un "Morning Briefing" digeste analysant les dépêches du jour fournies et leur impact potentiel sur la séance.
    - "analyse_macro_globale" : Synthèse de la dynamique des marchés mondiaux et de l'inflation.
    - "revue_portefeuille" : Critique sévère de l'allocation actuelle, des redondances et de l'exposition au risque.
    - "conseils_ajustement" : Recommandations tactiques chiffrées pour rééquilibrer le portefeuille.
    - "opportunites_marche" : OBLIGATOIRE : Basez-vous UNIQUEMENT sur les données du SCREENER FONDAMENTAL. Identifiez la ou les actions présentant la meilleure équation (Marge Nette forte / ROE élevé / PER justifié). Nommez les entreprises, citez leurs ratios exacts, et expliquez l'avantage concurrentiel fondamental de ces pépites.
    
    Format : Texte suivi exhaustif, argumenté. Utilisez des sauts de ligne (\\n\\n) pour séparer les paragraphes.
    """
    
    try:
        response = client.models.generate_content(
            model='gemini-3.1-flash', 
            contents=prompt,
            config={
                'response_mime_type': 'application/json',
                'response_schema': {
                    "type": "OBJECT",
                    "properties": {
                        "actualite_financiere": {"type": "STRING"},
                        "analyse_macro_globale": {"type": "STRING"},
                        "revue_portefeuille": {"type": "STRING"},
                        "conseils_ajustement": {"type": "STRING"},
                        "opportunites_marche": {"type": "STRING"}
                    },
                    "required": ["actualite_financiere", "analyse_macro_globale", "revue_portefeuille", "conseils_ajustement", "opportunites_marche"]
                }
            }
        )
        return json.loads(response.text)
    except Exception as e:
        print(f"Erreur API Gemini : {e}")
        return {"erreur": "Génération échouée"}

def main():
    portfolio = load_portfolio()
    if not portfolio:
        return

    print("1. Récupération des données du portefeuille...")
    portfolio_tickers = [actif["ticker"] for actif in portfolio.get("actifs_actuels", [])]
    portfolio_market_data = fetch_market_data(portfolio_tickers)
    
    print("2. Récupération des données macroéconomiques...")
    macro_data = fetch_market_data(MACRO_TICKERS)
    
    print("3. Lecture des dernières dépêches financières...")
    news = fetch_financial_news()
    
    print("4. Extraction des fondamentaux boursiers (Screener)...")
    screener_data = run_fundamental_screener(SCREENER_TICKERS)
    
    print("5. Génération de l'analyse institutionnelle par l'IA...")
    report = generate_financial_report(portfolio, portfolio_market_data, macro_data, screener_data, news)
    
    final_output = {
        "date_generation": datetime.utcnow().isoformat() + "Z",
        "rapport": report
    }
    
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(final_output, f, indent=4, ensure_ascii=False)

if __name__ == "__main__":
    main()
