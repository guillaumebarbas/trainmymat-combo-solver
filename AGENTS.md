# Combo Solver — règles locales

## Périmètre

`combo-solver-service/` est un service FastAPI qui pilote le moteur de règles EDOPro (`ocgcore`)
et les scripts de cartes Project Ignis, tous deux sous **AGPL-3.0** : le service l'est aussi
(`LICENSE`, `NOTICE.md`) et son code doit rester publiable. Le front et le backend ne l'appellent
qu'en HTTP/JSON : ne jamais lier ce moteur ni ces scripts au backend Java ou à l'application.

Deux usages :

- `POST /generer` — graphe TrainMyMat à partir d'un deck et d'une main : rejeu déterministe du duel
  (`solver/simulation.py`) → exploration bornée avec fusion des états équivalents
  (`solver/explorer.py`) → traduction des meilleures lignes en `DeckGraph` (`solver/graphe.py`).
  Chaque choix de carte d'une ligne affichée est développé en arbre (`solver/service.py`) :
  5 candidats au plus, chacun avec sa meilleure suite limitée à 5 actions, greffée sur la même
  action. Les effets sont des actions `activer-carte` ; l'effet obtenu est décrit sur le lien de sortie.
- `POST /simulation/etat` — simulation interactive sans état (`solver/simulateur.py`) : la requête
  porte le deck, la main, la graine et les choix déjà faits ; le service rejoue le duel depuis zéro
  et renvoie la décision en cours (options légales libellées, empreinte), le plateau et les
  événements. Le deck est mélangé selon la graine ; le joueur 1 n'a aucune carte et passe ; le
  tour suivant termine la simulation (`raison: fin-de-tour`).

## Règles de travail

- Ne jamais versionner `vendor/` : moteur, scripts, base de cartes et `strings.conf` sont
  récupérés par `build_core.sh` (local) ou le `Dockerfile`, aux commits épinglés dans
  `build_core.sh`. Changer un commit change le déroulé des replays enregistrés : le faire
  volontairement, mettre à jour `NOTICE.md`, puis revérifier les replays stockés (empreintes).
- Le contrat de sortie de `/generer` doit rester valide pour
  `BreakdownService.normaliserGraphe` : références limitées au deck, une seule
  entrée `PRIMARY` par action, identifiants stables et branches ≤ 100 étapes.
  Le test `ComboSolverGrapheContratTest` du backend utilise un graphe réel produit
  par ce service ; le régénérer si le format change.
- Le contrat de `/simulation/etat` est consommé par le backend : toute modification d'un champ se
  répercute dans les DTO du backend et dans le front.
- Les limites (cartes, liens, parties, secondes, choix, messages) sont bornées côté service ; ne
  pas retirer ces plafonds.
- Le jeton `COMBO_SOLVER_TOKEN` est obligatoire : sans lui, le service répond 503.
- Ne jamais journaliser de jeton ni de donnée utilisateur (deck, main, choix).
- Le dossier est publié tel quel dans le dépôt public `guillaumebarbas/trainmymat-combo-solver` : n'y mettre ni
  secret ni donnée privée. `SOURCE` (`app.py`) et `GET /version` forment l'offre de source AGPL affichée par le
  simulateur ; publier chaque version déployée.

## Publication

Depuis la racine du workspace, après commit :

```bash
git subtree split --prefix=combo-solver-service -b combo-solver-public
git push https://github.com/guillaumebarbas/trainmymat-combo-solver.git combo-solver-public:main
```

## Validation

```bash
./build_core.sh                      # une fois : moteur + données dans vendor/
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q tests
```

Les tests qui ont besoin du moteur sont ignorés s'il n'est pas construit : le
signaler plutôt que de déclarer la génération ou la simulation validée.
