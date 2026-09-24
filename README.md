# Portfolio Intelligence · Research OS

Portfolio Intelligence est un outil personnel de recherche et de lecture de portefeuille.

Il ne cherche plus à remplacer Yahoo Finance. Il ajoute quatre couches complémentaires :

1. **Portfolio** — ce que le portefeuille contient et comment il se comporte.
2. **Radar** — ce qui émerge dans la robotique et le Physical AI.
3. **Research** — mémoire, preuves, changements, hypothèses et audit des signaux.
4. **Company Lab** — dossier fondamental, ratios, historique, actualités et simulateur.

## Portfolio

- valorisation des positions configurées ;
- poids actuels ;
- performance reconstruite vs FTSE All-World ;
- volatilité, drawdown, concentration, HHI ;
- corrélations ;
- watchlist factuelle ;
- sources et fraîcheur visibles.

La performance reconstruite n'est pas présentée comme la performance réelle IBKR tant que l'historique complet des transactions n'est pas disponible.

## Company Lab

`company_lab.py` construit un dossier pour chaque ticker de `watchlist.json` :

- profil de cotation, secteur et industrie ;
- évolution du cours et rendement total sur 1 jour, 1 semaine, 1, 3 et 6 mois, 1, 2 et 5 ans ;
- comparaison cinq ans avec un indice local ;
- bêta hebdomadaire deux ans, volatilité et drawdowns ;
- résultats annuels, bilan, flux de trésorerie et allocation du capital ;
- P/E recalculé, P/E anticipé, PEG, P/S, P/B, EV/CA, EV/EBITDA, earnings yield et FCF yield ;
- croissance, marges, ROE, ROA, ROIC approximatif, conversion du cash, dette et liquidité ;
- actualités récentes reliées au ticker et derniers formulaires SEC ;
- simulateur historique et liste de suivi locale ;
- guide de plus de 40 indicateurs avec définitions et ordres de grandeur.

Les ratios incohérents ne sont pas maquillés. Le P/E fournisseur reste traçable, mais le P/E calculé est déclaré non interprétable lorsque le BPA LTM est nul ou négatif.

### Ajouter un ticker

La page Company permet de consigner une demande au format `[Company Lab] ADD TICKER`. Après vérification du symbole et de la place de cotation, le ticker est ajouté à `watchlist.json`, puis le workflow principal calcule et publie le dossier. Vous pouvez aussi simplement me donner le ticker dans la conversation.

### Données et coût

- **Yahoo Finance, endpoints publics** — cours, historiques, comptes standardisés et actualités, pour recherche personnelle ;
- **SEC EDGAR** — dépôts réglementaires primaires, sans clé ;
- **GitHub Actions et GitHub Pages** — calcul et hébergement du site public.

Le projet ne requiert aucune API payante, aucune carte bancaire et aucune clé susceptible de générer une facture. Le cache `.company_state` conserve le dernier dossier valide lorsqu'une source publique limite temporairement les requêtes.

## Catalyst Radar

Le Radar surveille :

- humanoïdes ;
- robotique industrielle et cobots ;
- logistique / AMR ;
- motion control / actuateurs / réducteurs ;
- vision et capteurs ;
- Physical AI / embodied AI.

### Sources actives

- **SEC EDGAR** — filings primaires ;
- **GDELT DOC 2.0** — actualité mondiale ;
- **Google News RSS** — fallback média ;
- **Yahoo Finance / yfinance** — données de marché.

### Signaux alternatifs

Le Radar recherche également des proxies explicites pour :

- recrutements robotique ;
- brevets ;
- contrats publics / subventions / appels d'offres ;
- nouvelles usines / capacité / mass production.

Ces éléments restent marqués comme **proxy**, jamais comme preuve primaire.

## Research OS

`research_os.py` transforme les signaux du Radar en couche de recherche persistante.

### Change Detection

Chaque signal est comparé à la mémoire précédente et classé en :

- **new**
- **confirmation**
- **acceleration**
- **materialisation**
- **invalidation**
- **existing**

### Evidence Packs

Chaque signal prioritaire peut produire un dossier contenant :

- preuve principale ;
- corroborations ;
- type de changement ;
- matérialité financière connue / inconnue ;
- questions ouvertes ;
- conditions d'invalidation.

### Research Memory

La mémoire machine est conservée entre les runs via **GitHub Actions cache** dans `.research_state`.

Elle n'est pas commitée dans le dépôt.

Le système mémorise :

- première apparition d'un signal ;
- dernière apparition ;
- fréquence ;
- catégories observées ;
- historique des runs ;
- évolution des thèmes.

### Company Dossiers

Chaque société couverte dispose d'un dossier généré avec :

- exposition thématique ;
- signaux ;
- preuves primaires ;
- hypothèse de travail ;
- anti-thèse ;
- questions ouvertes ;
- prochain élément à surveiller.

Les **notes personnelles saisies dans Research OS restent dans le localStorage du navigateur**.

### Knowledge Graph

Le graphe relie :

`société → sous-thème → composant technologique`

Exemples de composants :

- actuateurs ;
- precision reducers ;
- servo motors ;
- force/torque sensing ;
- machine vision ;
- edge compute ;
- simulation ;
- robot learning.

Le graphe décrit une exposition thématique. Il ne prétend pas prouver une relation fournisseur-client sans source.

### Theme Emergence & Saturation

Pour chaque sous-thème, le moteur suit :

- nombre de signaux ;
- signaux des 7 derniers jours ;
- largeur des sociétés concernées ;
- diversité des sources ;
- variation par rapport au run précédent ;
- saturation média ;
- émergence / accélération / refroidissement.

### Unknown Candidates

Les titres hors univers sont analysés pour faire ressortir des entités récurrentes.

Ces résultats sont marqués **candidate** : ils doivent être vérifiés avant d'être considérés comme sociétés cotées ou opportunités.

### Signal Audit

Avec le temps, un signal est réévalué :

- confirmé par d'autres signaux ;
- encore en attente ;
- non confirmé après une période suffisante.

Cela permet de mesurer quels types de signaux ont réellement été informatifs.

## Lien avec le portefeuille

Research OS relie le Radar :

- aux positions directes ;
- à la watchlist.

Les expositions indirectes via ETF ne sont pas inventées : elles restent non disponibles tant qu'une source de composition fiable n'est pas connectée.

## Gemini

Le projet sait utiliser `gemini-3.8-flash` pour synthétiser des preuves déjà collectées.

L'IA n'est jamais utilisée pour inventer des catalyseurs.

Pipeline :

`collecte → filtrage → preuves → mémoire → IA optionnelle`

et non :

`IA → recherche libre → conclusion`

### Garde-fou coût

Dans GitHub Actions :

`ENABLE_GEMINI_FREE=0`

par défaut.

L'application fonctionne entièrement sans Gemini.

Gemini ne doit être activé que lorsque la clé API utilisée est confirmée sur un projet dont l'usage reste dans le **free tier**. Un abonnement Google AI Pro / Gemini grand public n'est pas traité comme une garantie de gratuité de la Gemini Developer API.

## Workflow

Le workflow `portfolio_intelligence.yml` s'exécute :

- trois fois par jour ;
- manuellement ;
- lors de modifications des fichiers principaux.

Ordre :

1. validation du code ;
2. restauration de la mémoire Research OS ;
3. build Portfolio ;
4. build Company Lab ;
5. build Catalyst Radar ;
6. build Research OS ;
7. validation des quatre sorties ;
8. déploiement GitHub Pages ;
9. sauvegarde des mémoires machine.

## Pages

- `index.html` — Portfolio
- `company.html` — Company Lab
- `radar.html` — Catalyst Radar
- `research.html` — Research OS

## Fichiers principaux

```text
Finance-Adviser/
├── portfolio_intelligence.py
├── company_lab.py
├── catalyst_radar.py
├── research_os.py
├── research_config.json
├── portfolio.json
├── watchlist.json
├── index.html
├── company.html
├── radar.html
├── research.html
└── .github/workflows/portfolio_intelligence.yml
```

## Principe

Une information importante doit toujours permettre de répondre à :

> **Qu'est-ce qui a changé ? Pourquoi cela pourrait-il compter ? Quelle est la preuve ? Qu'est-ce qui invaliderait l'hypothèse ?**

Aucun score d'achat, objectif de cours ou recommandation automatique n'est généré.


## Robustesse des sources — v3

La collecte a été retravaillée pour éviter qu'une API publique instable ne rende le projet inutilisable.

### GDELT

Le Radar n'effectue plus une requête par sous-thème. Il utilise désormais **deux requêtes agrégées** :

- robotique cœur : humanoïdes, industrie, cobots, logistique ;
- technologies habilitantes : actuateurs, réducteurs, servos, vision, capteurs et Physical AI.

Les articles sont ensuite reclassés localement dans les six sous-thèmes.

En cas de 429, timeout ou erreur serveur :

1. réessai court ;
2. cache GDELT persistant ;
3. Google News RSS ;
4. cache Google RSS.

### SEC EDGAR

Le Radar ne télécharge plus `company_tickers.json` pendant les GitHub Actions.

Les sociétés SEC suivies disposent de leur **CIK embarqué dans `research_config.json`**. Le moteur appelle donc directement les submissions de la société.

Chaque société possède un cache dédié. Une entrée SEC fraîche est réutilisée pendant environ 20 heures.

Pour respecter les services publics et raccourcir les runs :

- au maximum **6 sociétés SEC** sont rafraîchies par run ;
- les autres utilisent leur cache ;
- les trois runs quotidiens font naturellement tourner l'univers ;
- après plusieurs échecs consécutifs, un **circuit breaker** arrête les appels SEC du run.

### Last-good result

`.research_state/last_good_radar.json` mémorise la dernière collecte suffisamment riche.

Si toutes les sources externes se dégradent simultanément, le site conserve la dernière sortie robuste au lieu de remplacer le Radar par un écran presque vide. L'interface indique que cette sortie vient du cache.

### Provider health

`radar.json` expose désormais un objet `provider_status` structuré.

Les pages Radar et Research montrent donc l'état réel de :

- GDELT ;
- SEC EDGAR ;
- Google News RSS.

Un fournisseur peut être `active`, `degraded`, `fallback`, `cache` ou `unavailable`.

La présence d'un fallback n'est plus assimilée à un échec du projet.
