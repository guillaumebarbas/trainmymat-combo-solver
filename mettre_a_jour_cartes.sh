#!/usr/bin/env bash
# Met à jour les données de cartes du moteur vers les derniers commits de Project Ignis : scripts d'effets
# (CardScripts), base des cartes (BabelCDB) et textes (strings.conf de Distribution). Réécrit les épingles de
# build_core.sh et les commits cités par NOTICE.md, reconstruit vendor/ et relance les tests. Le moteur ocgcore reste
# épinglé : le changer demande une revue à part.
#
# Usage : ./mettre_a_jour_cartes.sh [--sans-tests]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
BUILD="$ROOT/build_core.sh"
NOTICE="$ROOT/NOTICE.md"
TESTS=1
[ "${1:-}" = "--sans-tests" ] && TESTS=0
CHANGE=0

# Dernier commit de la branche par défaut d'un dépôt public.
dernier_commit() {
  git ls-remote "$1" HEAD | cut -f1
}

# Valeur actuelle d'une épingle de build_core.sh.
epingle() {
  sed -n "s/^$1=\"\([0-9a-f]*\)\"$/\1/p" "$BUILD"
}

# Applique une expression sed à un fichier, sans `sed -i` (options différentes sous macOS et Linux) ; `cat >` garde
# le mode exécutable du fichier.
remplacer() {
  local expression="$1" fichier="$2" tmp
  tmp="$(mktemp)"
  sed "$expression" "$fichier" > "$tmp"
  cat "$tmp" > "$fichier"
  rm -f "$tmp"
}

# Amène l'épingle `nom` sur le dernier commit de `depot`.
mettre_a_jour() {
  local nom="$1" depot="$2" ancien nouveau
  ancien="$(epingle "$nom")"
  nouveau="$(dernier_commit "$depot")"
  if [ -z "$ancien" ] || [ -z "$nouveau" ]; then
    echo "Épingle $nom ou dernier commit de $depot introuvable." >&2
    exit 1
  fi
  if [ "$ancien" = "$nouveau" ]; then
    echo "$nom : déjà à jour (${ancien:0:10})"
    return
  fi
  remplacer "s/^$nom=\"$ancien\"$/$nom=\"$nouveau\"/" "$BUILD"
  remplacer "s/$ancien/$nouveau/g" "$NOTICE"
  echo "$nom : ${ancien:0:10} → ${nouveau:0:10}"
  CHANGE=1
}

mettre_a_jour SCRIPTS_COMMIT https://github.com/ProjectIgnis/CardScripts.git
mettre_a_jour CDB_COMMIT https://github.com/ProjectIgnis/BabelCDB.git
mettre_a_jour DISTRIBUTION_COMMIT https://github.com/ProjectIgnis/Distribution.git

if [ "$CHANGE" = 0 ]; then
  echo "Rien à mettre à jour."
  exit 0
fi

"$BUILD"

if [ "$TESTS" = 1 ]; then
  if [ -x "$ROOT/.venv/bin/python" ]; then
    "$ROOT/.venv/bin/python" -m pytest -q "$ROOT/tests"
  else
    echo "Pas de .venv : tests non lancés (python -m venv .venv && .venv/bin/pip install -r requirements.txt pytest)." >&2
  fi
fi

cat <<'SUITE'

Données de cartes à jour en local. Pour la prod :
  1. committer build_core.sh et NOTICE.md ;
  2. pousser le dépôt public trainmymat-combo-solver (offre de source AGPL, voir AGENTS.md) ;
  3. redéployer l'application combo-solver dans Coolify (l'image reconstruit vendor/ avec ces épingles) ;
  4. dans l'admin de l'app, onglet Cartes, lancer « Vérifier les replays » : un combo enregistré avec les anciens
     scripts peut ne plus se rejouer à l'identique.
SUITE
