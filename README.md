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
