# Combo Solver — règles locales

## Périmètre

`combo-solver-service/` est un service FastAPI qui pilote le moteur de règles EDOPro (`ocgcore`)
et les scripts de cartes Project Ignis, tous deux sous **AGPL-3.0** : le service l'est aussi
(`LICENSE`, `NOTICE.md`) et son code doit rester publiable. Le front et le backend ne l'appellent
qu'en HTTP/JSON : ne jamais lier ce moteur ni ces scripts au backend Java ou à l'application.

Trois usages :

- `POST /generer` — graphe TrainMyMat à partir d'un deck et d'une main : rejeu déterministe du duel
  (`solver/simulation.py`) → exploration bornée avec fusion des états équivalents
  (`solver/explorer.py`) → traduction des meilleures lignes en `DeckGraph` (`solver/graphe.py`).
  Chaque choix de carte d'une ligne affichée est développé en arbre (`solver/service.py`) :
  5 candidats au plus, chacun avec sa meilleure suite limitée à 5 actions, greffée sur la même
  action. Une Magie ou un Piège joué depuis la main ou le terrain est une action `activer-carte` ;
  tout autre effet est nommé par le premier résultat de sa résolution, d'après la table du front
  (`mapZoneTransitionToAction`, clés de `STEP_ACTIONS` uniquement : `tuto`, `deck-to-field`,
  `foolish`, `gy-to-field`… ; voir `cle_resultat`) ; sans résultat, `effet-negate` s'il a annulé un
  autre maillon (`MSG_CHAIN_NEGATED`/`MSG_CHAIN_DISABLED`), sinon `effet`. L'effet obtenu reste
  décrit sur le lien de sortie. Une activation en réponse à un maillon reçoit en plus une entrée
  `REQUIREMENT` « En chaîne » depuis la carte de ce maillon, sauf si elle la cible déjà.
- `POST /simulation/etat` — simulation interactive sans état (`solver/simulateur.py`) : la requête
  porte le deck, la main, la graine et les choix déjà faits ; le service rejoue le duel depuis zéro
  et renvoie la décision en cours (options légales libellées, empreinte), le plateau et les
  événements. Le deck est mélangé selon la graine. Le champ optionnel `adversaire` (`deck` ≤ 60,
  main de départ comprise, `extra` ≤ 15, `main` ≤ 6) donne des cartes au joueur 1 : son deck est
  mélangé par un second générateur dérivé de la graine (le mélange du joueur 0 et les graines du
  duel ne changent pas) et ses décisions, marquées `joueur: 1`, sont répondues dans la même liste
  `choix`. Absent, `null` ou sans aucune carte, le joueur 1 passe toujours et les replays existants
  se rejouent à l'identique. Le champ optionnel `tours` (1 à 99, 1 par défaut ; 99 = partie complète) fixe la durée : le
  début du tour `tours + 1` termine la simulation (`raison: fin-de-tour`). Absent ou 1, rien ne
  change (le tour adverse n'est pas joué). Avec 2, le tour 2 est celui du joueur 1 : avec un
  adversaire, il pioche et ses décisions (`joueur: 1`) sont exposées (un adversaire au deck vide
  perd à sa pioche, `raison: victoire`) ; sans adversaire, il ne pioche pas et finit son tour seul.
  En Battle Phase, le joueur exposé reçoit une décision `combat` : `activation` (effet), `attaque` (monstre qui
  peut attaquer, `direct: true` s'il peut viser le joueur), `phase-principale-2` et `fin-de-tour` ; la cible d'une
  attaque se choisit ensuite comme une sélection de cartes. Chaque attaque déclarée est journalisée (`attaque` : `de`,
  `code` de l'attaquant, `vers` ou `direct`). Pour un joueur absent, le combat reste automatique (End Phase).
  Une fenêtre de chaîne (`chaine`) porte `speciaux` : le nombre d'activations déclenchées par ce qui vient de se
  passer (Ash Blossom sur une recherche). À 0, la fenêtre est « libre » (Magie Jeu-Rapide, Effet Rapide sans
  déclencheur) : le front la passe d'office en Draw, Standby et End Phase, comme EDOPro. Le champ n'entre pas dans
  l'empreinte.
- `POST /simulation/graphe` — graphe TrainMyMat tiré des lignes d'un arbre de simulation
  (`solver/graphe_simulation.py`) : même partie que `/simulation/etat` (deck, main, graine,
  `adversaire`, `tours`), `joueur` (0, ou 1 quand l'utilisateur joue en second), `titre` et
  `lignes` (1 à 30, `nom` ≤ 20 caractères, ≤ 1500 `choix` depuis le début de partie), `limites`
  (`maxCartes`, `maxLiens`, bornés comme `/generer`). Chaque ligne est rejouée par le simulateur,
  qui tient un journal au format de `simulation.py` (marqueurs partagés), puis dessinée par
  `ConstructeurGraphe` comme une ligne de `/generer` : les débuts communs partagent les mêmes
  actions, une branche par ligne (≤ 100 étapes, au-delà les étapes sont coupées et la ligne est
  citée dans `stats.etapesTronquees`). Les cartes du joueur suivi sont des nœuds ordinaires, celles
  de l'autre joueur des nœuds `adverse` (instances `i-a-…`), les cartes hors des deux decks des
  nœuds génériques. Une ligne qui ne se rejoue pas jusqu'à son dernier choix renvoie 400.

## Règles de travail

- Ne jamais versionner `vendor/` : moteur, scripts, base de cartes et `strings.conf` sont
  récupérés par `build_core.sh` (local) ou le `Dockerfile`, aux commits épinglés dans
  `build_core.sh`. Changer un commit change le déroulé des replays enregistrés : le faire
  volontairement, mettre à jour `NOTICE.md`, puis revérifier les replays stockés (empreintes).
- Nouvelles cartes : `./mettre_a_jour_cartes.sh` amène CardScripts, BabelCDB et `strings.conf`
  sur les derniers commits de Project Ignis (épingles de `build_core.sh` et `NOTICE.md`),
  reconstruit `vendor/` et relance les tests ; `ocgcore` reste épinglé. Après le déploiement,
  lancer « Vérifier les replays » dans l'admin de l'app (onglet Cartes).
- Le contrat de sortie de `/generer` et de `/simulation/graphe` doit rester valide pour
  `BreakdownService.normaliserGraphe` : références limitées au deck (au deck adverse pour un nœud
  `adverse`), une seule entrée `PRIMARY` par action, identifiants stables et branches ≤ 100 étapes.
  Le test `ComboSolverGrapheContratTest` du backend utilise un graphe réel produit
  par ce service ; le régénérer si le format change. `tests/test_graphe_simulation.py`
  reprend ces règles (`verifier_contrat_backend`).
- Les contrats de `/simulation/etat` et `/simulation/graphe` sont consommés par le backend : toute
  modification d'un champ se répercute dans les DTO du backend et dans le front.
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
