#!/usr/bin/env bash
# Récupère et compile le moteur de règles EDOPro (ocgcore, AGPL-3.0) ainsi que
# ses données (CardScripts + BabelCDB + strings.conf). Rien n'est versionné :
# tout vit dans vendor/ (ignoré par Git) et est reconstruit par ce script ou par
# le Dockerfile.
#
# Les versions sont épinglées : un combo enregistré (graine + choix) ne se
# rejoue à l'identique qu'avec le même moteur et les mêmes scripts. Changer un
# commit ici impose de revérifier les replays stockés.
set -euo pipefail

OCGCORE_COMMIT="38d04c9feb1a26617407091380634c87262fe3f8"
SCRIPTS_COMMIT="37f270dc813a12d123707ae255f2bda7922999c4"
CDB_COMMIT="fdf92aea31033cd6c44afa89987c5e00665205e2"
DISTRIBUTION_COMMIT="54a6e2395c532648ff762540e9615319fac4f51b"

ROOT="$(cd "$(dirname "$0")" && pwd)"
VENDOR="$ROOT/vendor"
mkdir -p "$VENDOR"
cd "$VENDOR"

# Amène $dir sur $commit (clone superficiel, réutilise un clone existant).
recuperer() {
  local url="$1" dir="$2" commit="$3"
  if [ -d "$dir/.git" ] && [ "$(git -C "$dir" rev-parse HEAD)" = "$commit" ]; then
    return
  fi
  if [ ! -d "$dir/.git" ]; then
    git init -q "$dir"
    git -C "$dir" remote add origin "$url"
  fi
  git -C "$dir" fetch -q --depth 1 origin "$commit"
  git -C "$dir" checkout -q --detach FETCH_HEAD
}

recuperer https://github.com/edo9300/ygopro-core.git ocgcore "$OCGCORE_COMMIT"
git -C ocgcore submodule update -q --init --depth 1
recuperer https://github.com/ProjectIgnis/CardScripts.git scripts "$SCRIPTS_COMMIT"
recuperer https://github.com/ProjectIgnis/BabelCDB.git cdb "$CDB_COMMIT"

if [ ! -f strings.conf ] || [ "$(cat .distribution-commit 2>/dev/null)" != "$DISTRIBUTION_COMMIT" ]; then
  curl -fsSL "https://raw.githubusercontent.com/ProjectIgnis/Distribution/$DISTRIBUTION_COMMIT/config/strings.conf" -o strings.conf
  echo "$DISTRIBUTION_COMMIT" > .distribution-commit
fi

case "$(uname -s)" in
  Darwin) LIB="libocgcore.dylib"; SHARED="-dynamiclib" ;;
  *) LIB="libocgcore.so"; SHARED="-shared" ;;
esac

if [ ! -f "$VENDOR/$LIB" ] || [ "$(cat .ocgcore-commit 2>/dev/null)" != "$OCGCORE_COMMIT" ]; then
  BUILD="$VENDOR/build"
  rm -rf "$BUILD"
  mkdir -p "$BUILD"
  CXX="${CXX:-c++}"
  LUA_SRC=$(ls ocgcore/lua/src/*.c | grep -vE '/(lbitlib|lcorolib|ldblib|linit|loadlib|loslib|ltests|lua|luac|lutf8lib|onelua)\.c$')
  OBJS=()
  for f in $LUA_SRC; do
    o="$BUILD/lua_$(basename "$f" .c).o"
    "$CXX" -x c++ -std=c++17 -O2 -fPIC -include ocgcore/lua/luaconf-customize.h -c "$f" -o "$o"
    OBJS+=("$o")
  done
  for f in ocgcore/*.cpp ocgcore/RNG/*.cpp; do
    [ -f "$f" ] || continue
    o="$BUILD/core_$(basename "$f" .cpp).o"
    "$CXX" -std=c++17 -O2 -fPIC -fno-rtti -DOCGCORE_EXPORT_FUNCTIONS -Iocgcore/lua/src -c "$f" -o "$o"
    OBJS+=("$o")
  done
  "$CXX" $SHARED -o "$VENDOR/$LIB" "${OBJS[@]}"
  echo "$OCGCORE_COMMIT" > .ocgcore-commit
fi

# Lu par le service (GET /version) et stocké avec chaque replay.
cat > versions.json <<EOF
{"ocgcore": "$OCGCORE_COMMIT", "scripts": "$SCRIPTS_COMMIT", "cartes": "$CDB_COMMIT", "chaines": "$DISTRIBUTION_COMMIT"}
EOF

echo "ocgcore prêt : $VENDOR/$LIB"
