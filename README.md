# Portfolio Intelligence

Anciennement **Finance Adviser**.

Le projet a été volontairement simplifié pour privilégier la confiance dans les données plutôt que la quantité de commentaires.

## Ce que fait le site

- valorise les positions configurées avec les dernières cotations Yahoo Finance disponibles ;
- recalcule les poids actuels du portefeuille ;
- estime le P&L journalier à partir des cotations ;
- reconstruit l’évolution des **positions actuelles à quantités constantes** ;
- compare cette reconstruction au FTSE All-World via `FWRA.SW` ;
- calcule concentration, HHI, nombre de positions effectives, volatilité et drawdown ;
- calcule les corrélations entre positions ;
- suit une watchlist avec 1 mois / 3 mois / 1 an / distance au plus haut 52 semaines ;
- produit seulement quelques constats déterministes et vérifiables.

## Ce que le site ne fait plus

- aucune analyse IA automatique ;
- aucun appel Gemini ou Groq ;
- aucun score opaque de type « 82/100 » ;
- aucun screener présenté comme une recommandation ;
- aucune actualité financière générale ;
- aucun résumé macro généré ;
- aucun commit automatique de données de marché dans le dépôt.

## Sources

### Positions

Les quantités sont actuellement lues depuis `portfolio.json`.

Le fichier contient encore un snapshot manuel provenant d’Interactive Brokers. La date du snapshot est affichée directement sur le site pour éviter toute confusion.

### Cotations

Les cours et historiques proviennent de Yahoo Finance via `yfinance`.

Ces données sont indicatives et peuvent être retardées ou ponctuellement indisponibles.

### Performance

La performance affichée est une **reconstruction des positions actuelles à quantités constantes**.

Elle ne correspond pas à la performance réelle du compte IBKR, car le projet ne connaît pas encore tout l’historique des achats, ventes, apports et retraits.

## Workflow

Un seul workflow existe désormais :

`.github/workflows/portfolio_intelligence.yml`

Il s’exécute :

- manuellement ;
- lors d’une modification des fichiers principaux ;
- une fois par heure les jours ouvrés entre 06:15 et 22:15 UTC.

Une indisponibilité ponctuelle d’une donnée Yahoo est enregistrée comme avertissement dans le dashboard. Elle ne transforme plus automatiquement le run en échec.

## Structure

```text
Finance-Adviser/
├── index.html
├── portfolio_intelligence.py
├── portfolio.json
├── watchlist.json
├── requirements.txt
└── .github/
    └── workflows/
        └── portfolio_intelligence.yml
```

## Étape suivante possible : IBKR

L’amélioration la plus importante restante serait de remplacer le snapshot manuel par une source IBKR automatisée, par exemple via Flex Web Service.

Tant que cette connexion n’est pas configurée, le site affiche explicitement la date du snapshot de positions et ne prétend pas être une copie temps réel du compte Interactive Brokers.

## Confidentialité

Le dépôt est actuellement public. Les fichiers `portfolio.json` et `watchlist.json` sont donc visibles publiquement.


## Catalyst Radar — Robotique & Physical AI

Le projet contient désormais un moteur de recherche de catalyseurs :

- `catalyst_radar.py`
- `research_config.json`
- `radar.html`

Le Radar collecte deux familles de preuves gratuites :

1. **GDELT DOC 2.0** pour la couverture journalistique mondiale ;
2. **SEC EDGAR** pour les filings récents des sociétés américaines / émetteurs couverts.

Il surveille notamment :

- humanoïdes ;
- robotique industrielle et cobots ;
- logistique / AMR ;
- motion control, servomoteurs, réducteurs et actuateurs ;
- machine vision et capteurs ;
- Physical AI / embodied AI.

Les catalyseurs recherchés sont explicites : commandes/backlog, capacité/capex, partenariats, lancements, guidance, adoption client, contrats publics/défense et exposition robotique.

### Priorité de recherche

Le Radar ne calcule pas de score d'investissement. Il classe uniquement les éléments en :

- **Élevée**
- **Moyenne**
- **Veille**

La priorité dépend de critères visibles : source primaire, récence, type de catalyseur et corroboration par des sources indépendantes.

### Gemini

Le code sait utiliser **Gemini 3.8 Flash** comme couche de synthèse des preuves déjà collectées.

Cette couche est **désactivée par défaut** dans GitHub Actions avec :

`ENABLE_GEMINI_FREE=0`

Elle n'est pas nécessaire au fonctionnement du Radar.

Cette précaution est volontaire : un abonnement grand public Google AI Pro/Gemini et la facturation de la Gemini API sont distincts. Avant d'activer Gemini dans le workflow, il faut vérifier que la clé utilisée appartient bien à un projet API free-tier si l'objectif reste zéro coût API.

`Gemini 3.1 Pro Preview` et l'agent API Deep Research ne sont pas activés automatiquement.

### Fréquence

Le workflow unique met à jour Portfolio Intelligence et Catalyst Radar trois fois par jour, plus à la demande.
