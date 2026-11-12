"""Données de cartes du moteur tenues à jour depuis Project Ignis, sans reconstruire l'image.

L'image embarque les données épinglées par `build_core.sh` (`vendor/`). Une mise à jour compare leurs commits aux
derniers de CardScripts, BabelCDB et Distribution (`strings.conf`) ; s'ils ont bougé, elle télécharge les nouvelles
données dans un dossier à part, les essaie sur un duel, puis les met en service d'un bloc. ocgcore reste celui de
l'image.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import subprocess
import urllib.request
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from solver.ocg import VENDOR, CardDatabase, Engine, ScriptLibrary, SystemStrings, chemins_cdb
from solver.simulateur import PartieSimulee, simuler

CLES_DONNEES = ("scripts", "cartes", "chaines")
ACTUEL = "actuel"  # lien vers le dossier des données en service
# Dossier d'un jeu de données : début des trois commits, `.tmp` pendant le téléchargement.
NOM_DONNEES = re.compile(r"[0-9a-f]{12}-[0-9a-f]{12}-[0-9a-f]{12}(\.tmp)?")


class MiseAJourImpossible(RuntimeError):
    """Téléchargement ou duel d'essai raté : les données en service restent celles d'avant."""


@dataclass(frozen=True)
class Donnees:
    """Un jeu de données du moteur : `scripts/`, `cdb/`, `strings.conf` et `versions.json` dans un même dossier."""

    dossier: Path

    def versions(self) -> dict[str, str]:
        fichier = self.dossier / "versions.json"
        return json.loads(fichier.read_text()) if fichier.is_file() else {}

    def moteur(self) -> Engine:
        return Engine(CardDatabase(chemins_cdb(self.dossier / "cdb")), ScriptLibrary(self.dossier / "scripts"),
                      SystemStrings(self.dossier / "strings.conf"))


# Duel d'essai : cartes présentes depuis des années, dont les actions demandent la base (Invocation Normale), les
# scripts communs et celui de la carte (activation de Polymerization).
STRATOS, LIQUID, POLYMERIZATION, SHADOW_MIST, BLAZEMAN, MALICIOUS, SUNRISE = (
    40044918, 59392529, 24094653, 50720316, 63060238, 9411399, 22908820)
PARTIE_ESSAI = PartieSimulee(deck=(STRATOS, LIQUID, POLYMERIZATION, SHADOW_MIST, BLAZEMAN, MALICIOUS),
                             extra=(SUNRISE,), main=(STRATOS, LIQUID, POLYMERIZATION), graine=1)
ACTIONS_ESSAI = {("invocation-normale", STRATOS), ("activation", POLYMERIZATION)}


def essayer_un_duel(nouvelles: Donnees, actives: Donnees):
    """Lève une erreur si les nouvelles données ne permettent pas de jouer le premier coup du duel d'essai."""
    etat = simuler(nouvelles.moteur(), PARTIE_ESSAI, [])
    options = {(option["action"], option["code"]) for option in (etat.get("decision") or {}).get("options", [])}
    if etat.get("statut") != "decision" or not ACTIONS_ESSAI <= options:
        raise RuntimeError(f"Duel d'essai raté (statut {etat.get('statut')}, erreur {etat.get('erreur')}) : "
                           "Invocation Normale ou activation de Polymerization absente.")


DEPOTS = {
    "scripts": "https://github.com/ProjectIgnis/CardScripts.git",
    "cartes": "https://github.com/ProjectIgnis/BabelCDB.git",
    "chaines": "https://github.com/ProjectIgnis/Distribution.git",
}
URL_CHAINES = "https://raw.githubusercontent.com/ProjectIgnis/Distribution/{commit}/config/strings.conf"
DELAI_GIT = 300  # secondes par commande : CardScripts pèse une vingtaine de Mo compressé
ENV_GIT = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}


def derniers_commits_project_ignis() -> dict[str, str]:
    """Dernier commit de la branche par défaut de chaque dépôt, comme `mettre_a_jour_cartes.sh`."""
    commits = {}
    for cle, depot in DEPOTS.items():
        sortie = _git(["ls-remote", depot, "HEAD"], timeout=60).split()
        if not sortie or not re.fullmatch(r"[0-9a-f]{40}", sortie[0]):
            raise RuntimeError(f"Dernier commit de {depot} illisible.")
        commits[cle] = sortie[0]
    return commits


def telecharger_project_ignis(commits: dict[str, str], dossier: Path):
    """CardScripts et BabelCDB aux commits donnés (clones superficiels, sans `.git`), puis `strings.conf`."""
    _recuperer(DEPOTS["scripts"], commits["scripts"], dossier / "scripts")
    _recuperer(DEPOTS["cartes"], commits["cartes"], dossier / "cdb")
    with urllib.request.urlopen(URL_CHAINES.format(commit=commits["chaines"]), timeout=60) as reponse:
        (dossier / "strings.conf").write_bytes(reponse.read())


def _recuperer(depot: str, commit: str, dossier: Path):
    """Même récupération que `recuperer` dans build_core.sh ; l'historique n'est pas gardé."""
    dossier.mkdir()
    for arguments in (["init", "-q"], ["remote", "add", "origin", depot],
                      ["fetch", "-q", "--depth", "1", "origin", commit], ["checkout", "-q", "--detach", "FETCH_HEAD"]):
        _git(["-C", str(dossier), *arguments], timeout=DELAI_GIT)
    shutil.rmtree(dossier / ".git")


def _git(arguments: list[str], timeout: int) -> str:
    try:
        return subprocess.run(["git", *arguments], capture_output=True, text=True, check=True, timeout=timeout,
                              env=ENV_GIT).stdout
    except subprocess.CalledProcessError as erreur:
        raise RuntimeError(f"git {arguments[0]} a échoué : {erreur.stderr.strip()[-200:]}") from erreur


def donnees_du_service() -> DonneesCartes:
    """Données de l'image (`vendor/`), mises à jour dans COMBO_SOLVER_CARTES_DOSSIER (par défaut `vendor/maj`)."""
    racine = Path(os.environ.get("COMBO_SOLVER_CARTES_DOSSIER") or VENDOR / "maj")
    return DonneesCartes(VENDOR, racine, derniers_commits=derniers_commits_project_ignis,
                         telecharger=telecharger_project_ignis, essayer=essayer_un_duel)


class DonneesCartes:
    """Données de l'image, puis les mises à jour téléchargées dans `racine`."""

    def __init__(self, image: Path, racine: Path, *, derniers_commits: Callable[[], dict[str, str]],
                 telecharger: Callable[[dict[str, str], Path], None],
                 essayer: Callable[[Donnees, Donnees], None]):
        self.image = Donnees(image)
        self.racine = racine
        self._derniers_commits = derniers_commits
        self._telecharger = telecharger
        self._essayer = essayer

    def actives(self) -> Donnees:
        """Données en service : la dernière mise à jour réussie, sinon celles de l'image."""
        lien = self.racine / ACTUEL
        if lien.is_symlink():
            dossier = lien.resolve()
            if (dossier / "versions.json").is_file():
                return Donnees(dossier)
        return self.image

    def versions(self, donnees: Donnees | None = None) -> dict[str, str]:
        """Commits du moteur (image) et des données en service."""
        return {**self.image.versions(), **(donnees or self.actives()).versions()}

    def mettre_a_jour(self, verrou_moteur: AbstractContextManager | None = None) -> dict:
        """Amène les données sur les derniers commits de Project Ignis.

        `statut` : `a-jour`, `mise-a-jour` (avec `avant`) ou `en-cours` quand un autre worker s'en occupe déjà.
        Lève `MiseAJourImpossible` si les dépôts sont injoignables ou si les nouvelles données échouent au duel d'essai.
        `verrou_moteur` est pris pendant le duel d'essai seulement : les parties continuent pendant le téléchargement.
        """
        self.racine.mkdir(parents=True, exist_ok=True)
        with open(self.racine / ".verrou", "w") as verrou:
            try:
                fcntl.flock(verrou, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {"statut": "en-cours"}
            return self._mettre_a_jour(verrou_moteur or nullcontext())

    def _mettre_a_jour(self, verrou_moteur: AbstractContextManager) -> dict:
        actives = self.actives()
        avant = self.versions(actives)
        try:
            derniers = self._derniers_commits()
        except Exception as erreur:
            raise MiseAJourImpossible(f"Derniers commits de Project Ignis illisibles ({erreur})"[:300]) from erreur
        if all(avant.get(cle) == commit for cle, commit in derniers.items()):
            return {"statut": "a-jour"}
        nom = "-".join(derniers[cle][:12] for cle in CLES_DONNEES)
        temporaire = self.racine / f"{nom}.tmp"
        shutil.rmtree(temporaire, ignore_errors=True)
        try:
            temporaire.mkdir(parents=True)
            self._telecharger(derniers, temporaire)
            (temporaire / "versions.json").write_text(json.dumps({cle: derniers[cle] for cle in CLES_DONNEES}))
            with verrou_moteur:
                self._essayer(Donnees(temporaire), actives)
        except Exception as erreur:
            shutil.rmtree(temporaire, ignore_errors=True)
            raise MiseAJourImpossible(f"{type(erreur).__name__} : {erreur}"[:300]) from erreur
        shutil.rmtree(self.racine / nom, ignore_errors=True)
        temporaire.rename(self.racine / nom)
        self._mettre_en_service(nom)
        self._nettoyer(garder={nom, actives.dossier.name})
        return {"statut": "mise-a-jour", "avant": avant}

    def _nettoyer(self, garder: set[str]):
        """Supprime les jeux de données plus anciens que le précédent, et les restes d'un téléchargement coupé."""
        for chemin in self.racine.iterdir():
            if NOM_DONNEES.fullmatch(chemin.name) and chemin.name not in garder and not chemin.is_symlink():
                shutil.rmtree(chemin, ignore_errors=True)

    def _mettre_en_service(self, nom: str):
        """Remplace le lien `actuel` d'un bloc : chaque worker voit l'ancien dossier ou le nouveau, jamais un mélange."""
        provisoire = self.racine / f"{ACTUEL}.tmp"
        provisoire.unlink(missing_ok=True)
        provisoire.symlink_to(nom)
        provisoire.replace(self.racine / ACTUEL)
