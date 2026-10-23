# TrainMyMat — service moteur de combos

> **English summary.** HTTP service behind the Yu-Gi-Oh! combo simulator of [TrainMyMat](https://trainmymat.com).
> It drives the EDOPro rules engine (ocgcore) and the Project Ignis card scripts: from a deck, a starting hand, a
> seed and the choices already made, it replays the game and returns the legal options, the board and the events.
> Licensed under AGPL-3.0, like the engine and scripts it loads. Code and documentation are in French.

Service FastAPI qui pilote le moteur de règles EDOPro (`ocgcore`) et les scripts de cartes Project Ignis pour
l'application TrainMyMat. Ce dépôt est le miroir public du dossier `combo-solver-service/` du dépôt principal de
TrainMyMat.

| Route | Rôle |
|---|---|
| `POST /simulation/etat` | Simulateur sans état : la requête porte le deck, la main de départ, la graine et les choix déjà faits ; le service rejoue la partie depuis zéro et renvoie la décision en cours (options légales, empreinte), le plateau et les événements. |
| `POST /generer` | Graphe de combos à partir d'un deck et d'une main (exploration bornée des lignes de jeu). |
| `GET /version` | Commits du moteur, des scripts, de la base de cartes et de ce service (`service`), avec l'adresse de ce dépôt (`source`). |
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
| `COMBO_SOLVER_VERSION` | Commit déployé, renvoyé par `GET /version` (argument de build Docker). |

Le service n'a pas vocation à être exposé sur Internet : seul le backend TrainMyMat l'appelle, avec le jeton.

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
