name: AI Financial Analyst

STREAMING_CHUNK:Configuration du déclencheur...

on:
schedule:
# 10:00 Heure Suisse (CEST) = 08:00 UTC
- cron: '0 8 * * *'
# 19:45 Heure Suisse (CEST) = 17:45 UTC
- cron: '45 17 * * *'

workflow_dispatch permet de lancer le script manuellement depuis GitHub pour tester

workflow_dispatch:

permissions:
contents: write

STREAMING_CHUNK:Définition des étapes...

jobs:
analyze_and_publish:
runs-on: ubuntu-latest
steps:
- name: Récupérer le code
uses: actions/checkout@v4

  - name: Configurer Python
    uses: actions/setup-python@v5
    with:
      python-version: '3.10'
      
  - name: Installer les dépendances
    run: pip install -r requirements.txt
    
  - name: Exécuter l'analyseur IA
    # Injecte la clé API secrète dans le script
    env:
      GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
    run: python analyze.py
    
  # STREAMING_CHUNK:Sauvegarde automatique...
  - name: Sauvegarder les résultats
    run: |
      git config --global user.name "AI Analyst Bot"
      git config --global user.email "bot@example.com"
      git add data.json
      git commit -m "Mise à jour de l'analyse IA" || echo "Aucun changement à commiter"
      git push
