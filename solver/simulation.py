"""Rejeu déterministe d'une suite de réponses et énumération des choix.

Le joueur 0 est le joueur qui commence et dont on explore les combos. Le
joueur 1 n'a aucune carte et passe toujours. Les décisions sans enjeu pour le
graphe (zone, position, tri, annonces…) reçoivent une réponse automatique.
"""

from __future__ import annotations

import itertools
import struct
from collections import Counter
from dataclasses import dataclass, field

from . import messages as M
from .ocg import (
    DUEL_STATUS_END, LOCATION_DECK, LOCATION_EXTRA, LOCATION_GRAVE, LOCATION_HAND, LOCATION_MZONE,
    LOCATION_REMOVED, LOCATION_SZONE, POS_FACEDOWN_DEFENSE, POS_FACEUP_ATTACK, POS_FACEUP_DEFENSE, Engine,
)

MAX_MESSAGES_PAR_PARTIE = 20_000
CODE_ANNONCE_PAR_DEFAUT = 89631139  # Blue-Eyes White Dragon : annonce neutre.


@dataclass(frozen=True)
class Partie:
    deck: tuple[int, ...]
    extra: tuple[int, ...]
    main: tuple[int, ...]


@dataclass(frozen=True)
class Option:
    reponse: bytes
    type: str
    code: int = 0
    location: int = 0
    description: int = 0


@dataclass
class Resultat:
    statut: str  # "decision", "fin" ou "invalide"
    decision: M.Message | None = None
    evenements: list[tuple[int, M.Message]] = field(default_factory=list)
    activations: Counter = field(default_factory=Counter)
    invocations_normales: int = 0
    plateau: dict[int, list[tuple[int, int]]] | None = None

    def cle(self) -> tuple:
        """Identifie un état pour fusionner les ordres de jeu équivalents."""
        assert self.plateau is not None
        zones = tuple(
            tuple(sorted(self.plateau[loc])) if loc not in (LOCATION_MZONE, LOCATION_SZONE)
            else tuple(sorted(c for c in self.plateau[loc] if c[0]))
            for loc in sorted(self.plateau)
        )
        return zones, tuple(sorted(self.activations.items())), self.invocations_normales


class Simulation:
    LOCATIONS_PLATEAU = (LOCATION_HAND, LOCATION_MZONE, LOCATION_SZONE, LOCATION_GRAVE, LOCATION_REMOVED, LOCATION_EXTRA, LOCATION_DECK)

    def __init__(self, engine: Engine, partie: Partie, seed: int = 1):
        self.engine, self.partie, self.seed = engine, partie, seed
        self.parties_jouees = 0

    def jouer(self, reponses: list[bytes], journal: bool = False) -> Resultat:
        self.parties_jouees += 1
        duel = self.engine.creer_duel(self.seed)
        try:
            return self._jouer(duel, reponses, journal)
        finally:
            duel.fermer()

    def _jouer(self, duel, reponses: list[bytes], journal: bool) -> Resultat:
        for code in self.partie.deck:
            duel.ajouter_carte(code, 0, LOCATION_DECK)
        for code in self.partie.extra:
            duel.ajouter_carte(code, 0, LOCATION_EXTRA)
        for code in self.partie.main:
            duel.ajouter_carte(code, 0, LOCATION_HAND, position=POS_FACEDOWN_DEFENSE)
        duel.demarrer()

        resultat = Resultat("fin")
        utilisees, total = 0, 0
        while True:
            statut, bruts = duel.avancer()
            decision = None
            for brut in bruts:
                total += 1
                message = M.decoder(brut)
                if message.type == M.MSG_RETRY:
                    resultat.statut = "invalide"
                    return resultat
                if message.type == M.MSG_CHAINING and message.cartes[0].loc.controleur == 0:
                    carte = message.cartes[0]
                    resultat.activations[(carte.code, carte.description)] += 1
                if message.type == M.MSG_SUMMONING:
                    resultat.invocations_normales += 1
                if journal and message.type not in M.DECISIONS:
                    resultat.evenements.append((utilisees, message))
                if message.type in M.DECISIONS:
                    decision = message
            if statut == DUEL_STATUS_END or decision is None or total > MAX_MESSAGES_PAR_PARTIE:
                return resultat

            auto = reponse_automatique(decision)
            if auto is not None:
                duel.repondre(auto)
                continue
            if decision.type == M.MSG_SELECT_IDLECMD and decision.joueur != 0:
                return resultat  # le tour est passé : fin de la ligne
            if utilisees < len(reponses):
                if journal:
                    resultat.evenements.append((utilisees, _marqueur(decision, reponses[utilisees])))
                duel.repondre(reponses[utilisees])
                utilisees += 1
                continue

            resultat.statut = "decision"
            resultat.decision = decision
            if decision.type == M.MSG_SELECT_IDLECMD:
                resultat.plateau = {loc: duel.cartes(0, loc) for loc in self.LOCATIONS_PLATEAU}
            return resultat


SELECTIONS_CARTES = (M.MSG_SELECT_CARD, M.MSG_SELECT_TRIBUTE, M.MSG_SELECT_SUM, M.MSG_SELECT_UNSELECT_CARD)


def _marqueur(decision: M.Message, reponse: bytes) -> M.Message:
    """Trace dans le journal le choix fait : commande principale ou sélection de cartes.

    Pour une sélection, `cartes` liste tous les candidats proposés à ce moment et
    `extra["choisis"]` les indices retenus.
    """
    marqueur = M.Message(-decision.type, decision.joueur)
    if decision.type == M.MSG_SELECT_IDLECMD:
        (valeur,) = struct.unpack("<i", reponse)
        type_, index = valeur & 0xFFFF, valeur >> 16
        groupes = ("summon", "spsummon", "repos", "mset", "sset", "activate")
        if type_ < len(groupes):
            marqueur.cartes = [decision.extra[groupes[type_]][index]]
        marqueur.extra["commande"] = type_
    elif decision.type in SELECTIONS_CARTES:
        marqueur.cartes = list(decision.cartes) + list(decision.extra.get("deselection", []))
        marqueur.extra["choisis"] = _indices_choisis(decision.type, reponse)
    return marqueur


def _indices_choisis(type_: int, reponse: bytes) -> list[int]:
    """Relit les indices d'une réponse produite par `rep_cartes` ou `rep_unselect`."""
    if type_ == M.MSG_SELECT_UNSELECT_CARD:
        return [struct.unpack_from("<i", reponse, 4)[0]] if len(reponse) >= 8 else []
    if len(reponse) < 8:
        return []
    (nombre,) = struct.unpack_from("<I", reponse, 4)
    return list(struct.unpack_from(f"<{nombre}I", reponse, 8))


def reponse_automatique(d: M.Message) -> bytes | None:
    """Réponse pour une décision qui ne crée pas de branche, sinon None."""
    if d.joueur != 0:
        return _reponse_adversaire(d)
    t = d.type
    if t == M.MSG_SELECT_CHAIN and not d.cartes:
        return M.rep_i32(-1)
    if t == M.MSG_SELECT_BATTLECMD:
        return M.rep_idle(3, 0)
    if t in (M.MSG_SELECT_PLACE, M.MSG_SELECT_DISFIELD):
        return _reponse_zone(d)
    if t == M.MSG_SELECT_POSITION:
        positions = d.extra["positions"]
        for pos in (POS_FACEUP_ATTACK, POS_FACEUP_DEFENSE, POS_FACEDOWN_DEFENSE):
            if positions & pos:
                return M.rep_i32(pos)
        return M.rep_i32(positions & -positions)
    if t in (M.MSG_SORT_CARD, M.MSG_SORT_CHAIN):
        return struct.pack("<b", -1)
    if t == M.MSG_SELECT_COUNTER:
        restant, valeurs = d.extra["nombre"], []
        for carte in d.cartes:
            pris = min(restant, carte.param)
            valeurs.append(pris)
            restant -= pris
        return b"".join(struct.pack("<h", v) for v in valeurs)
    if t in (M.MSG_ANNOUNCE_RACE, M.MSG_ANNOUNCE_ATTRIB):
        disponibles, bits = d.extra["disponibles"], []
        for i in range(64):
            if disponibles >> i & 1 and len(bits) < d.extra["nombre"]:
                bits.append(1 << i)
        valeur = sum(bits)
        return struct.pack("<Q", valeur) if t == M.MSG_ANNOUNCE_RACE else struct.pack("<I", valeur)
    if t == M.MSG_ANNOUNCE_CARD:
        return M.rep_i32(CODE_ANNONCE_PAR_DEFAUT)
    if t == M.MSG_ANNOUNCE_NUMBER:
        return M.rep_i32(0)
    if t == M.MSG_ROCK_PAPER_SCISSORS:
        return M.rep_i32(1)
    return None


def _reponse_adversaire(d: M.Message) -> bytes | None:
    t = d.type
    if t == M.MSG_SELECT_CHAIN:
        return M.rep_i32(0 if d.extra.get("force") and d.cartes else -1)
    if t in (M.MSG_SELECT_EFFECTYN, M.MSG_SELECT_YESNO, M.MSG_SELECT_OPTION, M.MSG_ANNOUNCE_NUMBER):
        return M.rep_i32(0)
    if t in (M.MSG_SELECT_CARD, M.MSG_SELECT_TRIBUTE, M.MSG_SELECT_SUM):
        return M.rep_cartes(list(range(d.extra.get("min", 1) or 1)))
    if t == M.MSG_SELECT_UNSELECT_CARD:
        return M.rep_unselect(None if d.extra["finissable"] or d.extra["annulable"] else 0)
    if t == M.MSG_SELECT_IDLECMD:
        return None
    return reponse_automatique(M.Message(t, 0, d.cartes, d.extra))


def _reponse_zone(d: M.Message) -> bytes:
    drapeau, choix = d.extra["drapeau"], []
    for _ in range(d.extra["nombre"]):
        for bit in list(range(7)) + list(range(8, 16)) + list(range(16, 23)) + list(range(24, 32)):
            if not drapeau >> bit & 1:
                cote, local = divmod(bit, 16)
                location = LOCATION_MZONE if local < 8 else LOCATION_SZONE
                choix.append(struct.pack("<BBB", d.joueur ^ cote, location, local % 8))
                drapeau |= 1 << bit
                break
    return b"".join(choix)


# --- Énumération des branches ------------------------------------------------------------

PRIORITE_IDLE = {"activate": 0, "spsummon": 1, "summon": 2, "sset": 3, "mset": 4}
TYPES_IDLE = {"summon": M.IDLE_SUMMON, "spsummon": M.IDLE_SPSUMMON, "mset": M.IDLE_MSET, "sset": M.IDLE_SSET, "activate": M.IDLE_ACTIVATE}


def options(d: M.Message, max_cartes: int = 6) -> list[Option]:
    """Choix explorés pour une décision du joueur 0, dédoublonnés et bornés."""
    t = d.type
    if t == M.MSG_SELECT_IDLECMD:
        resultat, vus = [], set()
        for groupe in sorted(TYPES_IDLE, key=PRIORITE_IDLE.get):
            for index, carte in enumerate(d.extra[groupe]):
                cle = (groupe, carte.code, carte.loc.location, carte.description)
                if cle in vus:
                    continue
                vus.add(cle)
                resultat.append(Option(M.rep_idle(TYPES_IDLE[groupe], index), groupe, carte.code, carte.loc.location, carte.description))
        return resultat
    if t == M.MSG_SELECT_CHAIN:
        resultat = _par_carte(d.cartes, lambda i: M.rep_i32(i), "chaine", max_cartes)
        if not d.extra["force"]:
            resultat.append(Option(M.rep_i32(-1), "passer"))
        return resultat
    if t in (M.MSG_SELECT_EFFECTYN, M.MSG_SELECT_YESNO):
        return [Option(M.rep_i32(1), "oui"), Option(M.rep_i32(0), "non")]
    if t == M.MSG_SELECT_OPTION:
        return [Option(M.rep_i32(i), "option", description=o) for i, o in enumerate(d.extra["options"][:4])]
    if t in (M.MSG_SELECT_CARD, M.MSG_SELECT_TRIBUTE):
        return _selection(d, max_cartes)
    if t == M.MSG_SELECT_UNSELECT_CARD:
        resultat = _par_carte(d.cartes, lambda i: M.rep_unselect(i), "selection", max_cartes)
        if d.extra["finissable"] or (d.extra["annulable"] and not d.cartes):
            resultat.append(Option(M.rep_unselect(None), "terminer"))
        return resultat
    if t == M.MSG_SELECT_SUM:
        return _somme(d)
    return []


def _par_carte(cartes, reponse, type_: str, limite: int) -> list[Option]:
    resultat, vus = [], set()
    for index, carte in enumerate(cartes):
        cle = (carte.code, carte.loc.location, carte.loc.controleur, carte.description)
        if cle in vus:
            continue
        vus.add(cle)
        resultat.append(Option(reponse(index), type_, carte.code, carte.loc.location, carte.description))
        if len(resultat) >= limite:
            break
    return resultat


def _selection(d: M.Message, limite: int) -> list[Option]:
    minimum = max(d.extra["min"], 1)
    if minimum == 1:
        return _par_carte(d.cartes, lambda i: M.rep_cartes([i]), "carte", limite)
    resultat, vus = [], set()
    for combinaison in itertools.combinations(range(len(d.cartes)), minimum):
        cle = tuple(sorted((d.cartes[i].code, d.cartes[i].loc.location) for i in combinaison))
        if cle in vus:
            continue
        vus.add(cle)
        resultat.append(Option(M.rep_cartes(list(combinaison)), "cartes", d.cartes[combinaison[0]].code))
        if len(resultat) >= min(limite, 4):
            break
    return resultat


def _somme(d: M.Message) -> list[Option]:
    """Combinaisons dont la somme (niveaux, rangs…) atteint exactement la cible."""
    cible = d.extra["cible"] - sum(c.param & 0xFFFF for c in d.extra["obligatoires"])
    resultat, vus = [], set()
    for taille in range(1, min(len(d.cartes), 5) + 1):
        for combinaison in itertools.combinations(range(len(d.cartes)), taille):
            valeurs = [d.cartes[i].param for i in combinaison]
            if not any(sum(v) == cible for v in itertools.product(*[{p & 0xFFFF, p >> 16 or p & 0xFFFF} for p in valeurs])):
                continue
            cle = tuple(sorted(d.cartes[i].code for i in combinaison))
            if cle in vus:
                continue
            vus.add(cle)
            resultat.append(Option(M.rep_cartes(list(combinaison)), "somme", d.cartes[combinaison[0]].code))
            if len(resultat) >= 3:
                return resultat
    return resultat
