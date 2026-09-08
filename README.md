# Finance Adviser

Tableau de bord financier personnel automatisé, construit autour d'un portefeuille en CHF.

## Objectif

Finance Adviser combine quatre couches :

1. **Portefeuille** — positions, poids, sleeves et profil d'investissement.
2. **Marchés** — performances multi-horizons, volatilité et repères macro via Yahoo Finance.
3. **Moteur de diagnostic** — concentration, diversification effective, exposition thématique et screener qualité/valorisation.
4. **Analyse IA** — synthèse structurée par Google Gemini, strictement ancrée dans les données collectées.

Le site est publié automatiquement sur GitHub Pages.

## Architecture

- `portfolio.json` : source de vérité des positions et du snapshot manuel.
- `investment_policy.json` : doctrine d'investissement et règles méthodologiques.
- `config.json` : modèle IA, univers macro, screener et seuils heuristiques.
- `analyze.py` : collecte, calculs, screener et génération du rapport.
- `data.json` : sortie structurée consommée par le dashboard.
- `index.html` : interface statique du CIO Dashboard.
- `.github/workflows/ai_analyst.yml` : automatisation, publication et health check.

## Statuts du pipeline

- `ok` : données collectées et analyse IA générée.
- `degraded` : le dashboard reste exploitable avec les données quantitatives, mais une partie du pipeline (notamment l'IA) a échoué.

Le workflow publie le dashboard même en mode dégradé puis termine en erreur pour rendre la panne visible dans GitHub Actions.

## Gemini

Le modèle est défini dans `config.json`. La clé API doit exister dans GitHub Actions sous le secret :

`GEMINI_API_KEY`

La clé ne doit jamais être écrite dans le dépôt.

## Philosophie

Le moteur distingue :
- les données observées ;
- les calculs déterministes ;
- les heuristiques de diagnostic ;
- le jugement de l'IA.

Aucune transaction n'est exécutée automatiquement. Les seuils du moteur ne sont pas considérés comme des contraintes personnelles tant qu'ils ne sont pas explicitement définis dans la politique d'investissement.

## Mise à jour du portefeuille

Modifier `portfolio.json` puis pousser sur `main`. Le workflow se relance automatiquement.

Les valeurs de marché du fichier sont considérées comme un snapshot manuel ; les variations de prix sont récupérées séparément par le moteur.

## Limites

Yahoo Finance n'est pas une source institutionnelle garantie et certaines données peuvent être retardées, manquantes ou structurées différemment selon le titre. Le score qualité/valorisation est une heuristique de tri, pas un modèle de valorisation intrinsèque.


## Politique IA zéro coût

Finance Adviser est volontairement configuré en mode `free_only`.

Chaîne actuelle :
1. `gemini-3.7-flash` via le Free Tier Gemini ;
2. `openai/gpt-oss-120b` via le Free Plan Groq, uniquement si un secret `GROQ_API_KEY` est présent.

Le code contient une liste blanche interne. Un modèle ou fournisseur non explicitement autorisé est refusé avant tout appel réseau.

**Important :** cette protection empêche Finance Adviser d'appeler volontairement un modèle non approuvé comme gratuit. Elle ne peut pas vérifier le statut de facturation externe d'un compte Google/Groq. Pour garantir zéro dépense, les projets API correspondants doivent rester sur leurs offres gratuites et ne pas être configurés pour une facturation payante.

Aucune clé API n'est stockée dans le dépôt.


## Screener dynamique et watchlist

Le screener n'est plus une liste de tickers imposés.

Il découvre automatiquement des candidats via les screeners Yahoo Finance/yfinance et un univers européen, puis enrichit les meilleures valeurs avec leurs fondamentaux et leur historique de marché.

Score multi-facteurs :
- qualité : 30 %
- croissance : 20 %
- valorisation : 20 %
- bilan / cash-flow : 15 %
- momentum : 10 %
- risque : 5 %

Le moteur applique aussi :
- comparaison relative au secteur lorsque l'échantillon le permet ;
- taille minimale de capitalisation ;
- exigence de croissance minimale ;
- pénalité lorsque le cours a déjà trop accéléré à 1 ou 3 mois ;
- exclusion de certains faux positifs de type REIT ou cotations exotiques ;
- déduplication des doubles cotations d'une même société.

`watchlist.json` est totalement séparé du screener. Une société peut donc rester suivie parce qu'elle fait partie des convictions personnelles même si elle ne ressort pas comme opportunité du jour.

## Cotations des positions

`quotes.py` produit un snapshot léger des positions personnelles et de la watchlist, sans aucun appel IA.

Le workflow `Market Quotes` le rafraîchit toutes les 30 minutes en semaine entre 06:00 et 22:59 UTC, puis republie le dashboard sans toucher au rapport IA.

Les cours sont indicatifs et peuvent être retardés. Interactive Brokers reste la source de référence pour la valeur officielle du compte.
