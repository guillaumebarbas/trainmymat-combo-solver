# TrainMyMat — service moteur de combos

> **English summary.** HTTP service behind the Yu-Gi-Oh! combo simulator of [TrainMyMat](https://trainmymat.com).
> It drives the EDOPro rules engine (ocgcore) and the Project Ignis card scripts: from a deck, a starting hand, a
> seed and the choices already made (optionally against an opponent's deck and hand, over several turns), it replays
> the game and returns the legal options, the board and the events. It also turns the lines of a simulation tree into
> a TrainMyMat combo graph.
> Licensed under AGPL-3.0, like the engine and scripts it loads. Code and documentation are in French.

Service FastAPI qui pilote le moteur de règles EDOPro (`ocgcore`) et les scripts de cartes Project Ignis pour
l'application TrainMyMat. Ce dépôt est le miroir public du dossier `combo-solver-service/` du dépôt principal de
TrainMyMat.

| Route | Rôle |
|---|---|
| `POST /simulation/etat` | Simulateur sans état : la requête porte le deck, la main de départ, la graine, les choix déjà faits et, en option, les cartes de l'adversaire (`adversaire` : `deck`, `extra`, `main`), dont les décisions (`joueur: 1`) sont alors exposées elles aussi, et le nombre de tours joués (`tours`, 1 à 99, 1 par défaut ; 99 = partie complète : le début du tour `tours + 1` termine la partie) ; le service rejoue la partie depuis zéro et renvoie la décision en cours (options légales, empreinte), le plateau et les événements. Dans le plateau, les cartes de l'Extra Deck portent leur `niveau`, `rang` ou `lien` (valeur Lien) : le simulateur en déduit les matériels qui manquent pendant une Invocation Synchro, Xyz ou Lien. |
| `POST /simulation/graphe` | Graphe de combos tiré des lignes d'un arbre de simulation : même partie que `/simulation/etat`, plus `lignes` (1 à 30 suites de `choix` nommées), `joueur` (joueur suivi : 1 quand on joue en second), `titre` et `limites` ; une branche par ligne, les débuts communs partagés, les cartes de l'autre joueur en nœuds `adverse`. Réponse `{graphe, stats}` ; 400 si une ligne ne se rejoue pas. |
| `POST /generer` | Graphe de combos à partir d'un deck et d'une main (exploration bornée des lignes de jeu). |
| `POST /cartes/mettre-a-jour` | Amène les données de cartes (CardScripts, BabelCDB, `strings.conf`) sur les derniers commits de Project Ignis : téléchargement à côté, duel d'essai, puis mise en service d'un bloc. `statut` : `a-jour`, `mise-a-jour` (avec `avant`) ou `en-cours` ; `versions` en service après l'appel, `secondes`. 502 si les dépôts sont injoignables ou si le duel d'essai rate : les données en service ne changent pas. |
| `GET /version` | Commits du moteur, des scripts et de la base de cartes **en service**, et de ce service (`service`), avec l'adresse de ce dépôt (`source`). |
| `GET /health` | Sonde de disponibilité. |

Statuts de `POST /simulation/etat` :

- `decision` : une décision attend le joueur ;
- `fin` : la partie est finie, `raison` vaut `fin-de-tour`, `victoire`, `defaite` ou `limite-atteinte` ;
- `invalide` : le moteur a refusé un choix, la réponse reprend à cette décision ;
- `divergent` : l'empreinte d'un choix ne correspond plus à la décision rejouée ;
- `non-supporte` : décision que le service ne sait pas encore exposer.

## Lancer le service

Prérequis : `git`, `curl`, un compilateur C++17 et Python 3.12 ou plus récent.

```bash
./build_core.sh    # moteur, scripts, base de cartes et strings.conf dans vendor/, aux commits épinglés
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
COMBO_SOLVER_TOKEN=change-moi .venv/bin/uvicorn app:app --port 8002
```

Avec Docker :

```bash
docker build -t trainmymat-combo-solver --build-arg COMBO_SOLVER_VERSION="$(git rev-parse HEAD)" .
docker run --rm -p 8002:8002 -e COMBO_SOLVER_TOKEN=change-moi trainmymat-combo-solver
```

Exemple :

```bash
curl -s localhost:8002/simulation/etat -H 'content-type: application/json' -H 'X-Solver-Token: change-moi' \
  -d '{"deck":[40044918,59392529,24094653,50720316,63060238,9411399],"main":[40044918,59392529,24094653],"graine":"42","choix":[]}'
```

| Variable | Rôle |
|---|---|
| `COMBO_SOLVER_TOKEN` | Obligatoire : jeton attendu dans l'en-tête `X-Solver-Token`. Sans lui, le service répond 503. |
| `COMBO_SOLVER_WORKERS` | Nombre de processus uvicorn, un moteur chacun (2 par défaut). |
| `COMBO_SOLVER_VERSION` | Commit déployé, renvoyé par `GET /version` (argument de build Docker) ; à défaut `SOURCE_COMMIT` (injecté au démarrage par Coolify), sinon `dev`. |
| `COMBO_SOLVER_CARTES_AUTO` | `1` (valeur de l'image Docker) : au démarrage, le service met ses données de cartes sur les derniers commits de Project Ignis, en arrière-plan. `0` ou absente : il garde celles de l'image. |
| `COMBO_SOLVER_CARTES_DOSSIER` | Dossier des données téléchargées (`vendor/maj` par défaut). Sur un volume, un redémarrage repart des dernières données au lieu de celles de l'image. |

Le service n'a pas vocation à être exposé sur Internet : seul le backend TrainMyMat l'appelle, avec le jeton.

## Mettre à jour les cartes

Les données de cartes suivent Project Ignis sans reconstruire l'image (`solver/cartes.py`) : au démarrage du
conteneur, puis chaque nuit quand le backend TrainMyMat appelle `POST /cartes/mettre-a-jour` (3 h 30, heure de
Paris). Les nouvelles données sont téléchargées dans un dossier à part (`<scripts>-<cartes>-<chaînes>`), essayées sur
un duel (Invocation Normale et activation de Polymerization), puis mises en service en remplaçant le lien `actuel` ;
chaque worker recharge son moteur à sa requête suivante. Seuls le dossier en service et le précédent sont gardés.
Un combo enregistré avec d'anciens scripts peut ne plus se rejouer à l'identique : le backend revérifie les replays
après chaque changement de versions.

Les données de l'image restent épinglées dans `build_core.sh` (point de départ d'un conteneur neuf, et données des
tests). Pour avancer ces épingles :

```bash
./mettre_a_jour_cartes.sh             # derniers commits de CardScripts, BabelCDB et strings.conf, build, tests
./mettre_a_jour_cartes.sh --sans-tests
```

Le script réécrit les épingles de `build_core.sh` et les commits cités par `NOTICE.md` ; le moteur `ocgcore`
reste épinglé.

## Tests

```bash
.venv/bin/python -m pytest -q tests
```

Les tests qui ont besoin du moteur sont ignorés tant que `./build_core.sh` n'a pas été lancé.

## Licence

GNU Affero General Public License v3 (`LICENSE`). Composants tiers, versions épinglées et avertissement sur les
marques : `NOTICE.md`.

Si vous déployez une version modifiée, faites pointer `SOURCE` (`app.py`) vers votre propre dépôt : c'est l'adresse
que l'application affiche à ses utilisateurs pour obtenir le code source.

Yu-Gi-Oh! est une marque de Konami Digital Entertainment ; ce projet n'est ni affilié à Konami, ni approuvé par
Konami.
