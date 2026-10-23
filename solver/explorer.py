"""Exploration bornée des lignes de jeu du premier tour.

Parcours en profondeur : chaque nœud est une suite de réponses rejouée depuis
un duel neuf. Les positions de commande principale (MSG_SELECT_IDLECMD)
équivalentes sont fusionnées : même plateau, mêmes effets déjà utilisés et même
nombre d'Invocations Normales, quel que soit l'ordre de jeu.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from . import messages as M
from .ocg import (
    LOCATION_GRAVE, LOCATION_HAND, LOCATION_MZONE, LOCATION_REMOVED, LOCATION_SZONE, POS_FACEDOWN, TYPES_EXTRA,
    CardDatabase,
)
from .simulation import Resultat, Simulation, options


@dataclass(frozen=True)
class Limites:
    max_parties: int = 1500
    max_secondes: float = 25.0
    max_actions: int = 20
    max_options_par_choix: int = 6


@dataclass
class Candidat:
    score: float
    reponses: list[bytes]
    cle: tuple
    actions: int


@dataclass
class Exploration:
    candidats: list[Candidat] = field(default_factory=list)
    parties: int = 0
    etats_fusionnes: int = 0
    interrompue: bool = False
    secondes: float = 0.0


def evaluer(plateau: dict, base: CardDatabase, actions: int) -> float:
    """Heuristique d'endboard : présence sur le terrain d'abord, ressources ensuite."""
    score = 0.0
    for code, position in plateau[LOCATION_MZONE]:
        if code:
            type_ = base.type(code)
            score += 10 + (8 if type_ & TYPES_EXTRA else 0)
    for code, position in plateau[LOCATION_SZONE]:
        if code:
            score += 6 if position & POS_FACEDOWN else 4
    score += 3 * len(plateau[LOCATION_HAND])
    score += 0.5 * (len(plateau[LOCATION_GRAVE]) + len(plateau[LOCATION_REMOVED]))
    return round(score - 0.1 * actions, 2)


def explorer(simulation: Simulation, limites: Limites = Limites(), depart: list[bytes] | None = None) -> Exploration:
    """Explore depuis `depart` (début du tour par défaut) dans les limites données."""
    debut = time.monotonic()
    parties_initiales = simulation.parties_jouees
    resultat = Exploration()
    vus: set[tuple] = set()
    pile: list[tuple[list[bytes], int]] = [(list(depart or []), 0)]
    base = simulation.engine.base
    while pile:
        if simulation.parties_jouees - parties_initiales >= limites.max_parties or time.monotonic() - debut > limites.max_secondes:
            resultat.interrompue = True
            break
        reponses, actions = pile.pop()
        partie: Resultat = simulation.jouer(reponses)
        if partie.statut != "decision":
            continue
        decision = partie.decision
        if decision.type == M.MSG_SELECT_IDLECMD:
            cle = partie.cle()
            if cle in vus:
                resultat.etats_fusionnes += 1
                continue
            vus.add(cle)
            resultat.candidats.append(Candidat(evaluer(partie.plateau, base, actions), reponses, cle, actions))
            if actions >= limites.max_actions:
                continue
            actions += 1
        # Ordre inverse : la première option (la plus prometteuse) est explorée en premier.
        for option in reversed(options(decision, limites.max_options_par_choix)):
            pile.append((reponses + [option.reponse], actions))
    resultat.parties = simulation.parties_jouees - parties_initiales
    resultat.secondes = round(time.monotonic() - debut, 2)
    return resultat


def meilleur_prolongement(exploration: Exploration, prefixe: list[bytes]) -> Candidat | None:
    """Meilleur état déjà exploré qui commence par `prefixe`."""
    taille = len(prefixe)
    candidats = [c for c in exploration.candidats if len(c.reponses) >= taille and c.reponses[:taille] == prefixe]
    return max(candidats, key=lambda c: (c.score, -c.actions), default=None)


def meilleures_lignes(exploration: Exploration, nombre: int) -> list[Candidat]:
    """Meilleurs endboards distincts ; les lignes vides (rien joué) sont exclues."""
    choisis: list[Candidat] = []
    plateaux = set()
    for candidat in sorted(exploration.candidats, key=lambda c: (-c.score, c.actions)):
        # cle[0] suit l'ordre des locations : Deck, main, monstres, magies/pièges, …
        terrain = candidat.cle[0][2:4]
        if not candidat.reponses or terrain in plateaux:
            continue
        plateaux.add(terrain)
        choisis.append(candidat)
        if len(choisis) >= nombre:
            break
    return choisis
