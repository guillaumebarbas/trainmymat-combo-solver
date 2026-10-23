# Notices — service moteur TrainMyMat

Ce service (génération de graphes et simulation interactive de combos) est un logiciel libre
distribué sous **GNU Affero General Public License v3** (voir `LICENSE`). Il charge à l'exécution le
moteur de règles et les scripts de cartes ci-dessous, eux-mêmes sous AGPL : toute personne qui utilise
ce service à travers le réseau, y compris via l'application TrainMyMat, peut en obtenir le code source
complet, modifications comprises, pour la version qu'elle utilise (`GET /version` donne les commits).

L'application et le backend TrainMyMat sont des programmes distincts qui dialoguent avec ce service
uniquement par HTTP/JSON ; ils ne sont pas couverts par cette notice.

Yu-Gi-Oh! est une marque de Konami Digital Entertainment. Ce projet n'est ni affilié à Konami, ni
approuvé par Konami. Les noms, textes et illustrations des cartes appartiennent à leurs détenteurs.

## Composants

| Composant | Rôle | Licence | Version épinglée (`build_core.sh`) |
|---|---|---|---|
| [edo9300/ygopro-core](https://github.com/edo9300/ygopro-core) (ocgcore) | moteur de règles | AGPL-3.0-or-later | `38d04c9feb1a26617407091380634c87262fe3f8` |
| Lua (sous-module d'ocgcore) | interpréteur des scripts | MIT | celui du commit d'ocgcore |
| [ProjectIgnis/CardScripts](https://github.com/ProjectIgnis/CardScripts) | script Lua de chaque carte | AGPL-3.0 | `37f270dc813a12d123707ae255f2bda7922999c4` |
| [ProjectIgnis/BabelCDB](https://github.com/ProjectIgnis/BabelCDB) | caractéristiques et textes des cartes | aucune licence déclarée | `fdf92aea31033cd6c44afa89987c5e00665205e2` |
| [ProjectIgnis/Distribution](https://github.com/ProjectIgnis/Distribution) (`config/strings.conf`) | textes système (invites, zones, Attributs, Types) | voir le dépôt | `54a6e2395c532648ff762540e9615319fac4f51b` |

Aucun de ces composants n'est modifié : ils sont récupérés tels quels aux commits ci-dessus.
