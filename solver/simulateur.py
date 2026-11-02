"""Simulation interactive : chaque décision des joueurs est exposée avec ses options légales.

Le service ne garde aucune partie : chaque appel rejoue le duel depuis un état neuf à partir de la
graine et des choix déjà faits (≈ 10 ms pour un combo complet, mesuré sur le moteur natif). Sans
adversaire, le joueur 1 n'a aucune carte et passe toujours. Avec un adversaire (deck, Extra Deck, main
de départ), ses décisions sont exposées comme celles du joueur 0 et répondues dans la même liste de choix.
La partie dure `tours` tours (1 par défaut) : le début du tour suivant la termine.
"""

from __future__ import annotations

import hashlib
import json
import struct
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace

from . import messages as M
from .ocg import (
    DUEL_MODE_MR5, DUEL_STATUS_END, LOCATION_DECK, LOCATION_EXTRA, LOCATION_GRAVE, LOCATION_HAND, LOCATION_MZONE,
    LOCATION_OVERLAY, LOCATION_REMOVED, LOCATION_SZONE, POS_FACEDOWN_ATTACK, POS_FACEDOWN_DEFENSE,
    POS_FACEUP_ATTACK, POS_FACEUP_DEFENSE, QUERY_ATTACK,
    QUERY_CODE, QUERY_COUNTERS, QUERY_DEFENSE, QUERY_LEVEL, QUERY_LINK, QUERY_OVERLAY_CARD, QUERY_OWNER,
    QUERY_POSITION, QUERY_RANK, Duel, Engine,
)
from .simulation import Simulation, marqueur, reponse_automatique

MASQUE_64 = (1 << 64) - 1
# Partie complète : le duel se termine avant (victoire, défaite, plafond de choix ou de messages). Le backend porte la
# même borne et réserve la partie complète à une offre ; sans elle, il demande 2 tours (le sien et celui de l'adversaire).
MAX_TOURS = 99
# Le deck adverse est mélangé par un second générateur (graine ^ cette constante arbitraire, décimales de √2) :
# le mélange du joueur 0 et les graines du duel restent ceux de la même graine sans adversaire.
GRAINE_ADVERSAIRE = 0x6A09E667F3BCC908
LIMITE_MESSAGES = 100_000  # garde-fou contre une boucle du moteur ou des réponses automatiques
FLAGS_CARTES = (QUERY_CODE | QUERY_POSITION | QUERY_LEVEL | QUERY_RANK | QUERY_ATTACK | QUERY_DEFENSE
                | QUERY_OVERLAY_CARD | QUERY_COUNTERS | QUERY_OWNER | QUERY_LINK)
PILES = (("main", LOCATION_HAND), ("cimetiere", LOCATION_GRAVE), ("bannis", LOCATION_REMOVED), ("extra", LOCATION_EXTRA))
INVOCATIONS = {M.MSG_SUMMONING: "invocation-normale", M.MSG_SPSUMMONING: "invocation-speciale",
               M.MSG_FLIPSUMMONING: "invocation-flip"}
# Raisons d'un déplacement (REASON_* d'ocgapi_constants.h) utiles à l'affichage.
RAISONS = (
    (0x1, "destruction"), (0x2, "sacrifice"), (0x8, "materiau"), (0x10, "invocation"), (0x20, "combat"),
    (0x40, "effet"), (0x80, "cout"), (0x400, "regle"), (0x800, "invocation-speciale"), (0x4000, "defausse"),
    (0x20000, "retour"), (0x40000, "fusion"), (0x80000, "synchro"), (0x100000, "rituel"), (0x200000, "xyz"),
    (0x2000000, "pioche"), (0x10000000, "lien"),
)

ZONES = {
    LOCATION_DECK: "deck", LOCATION_HAND: "main", LOCATION_MZONE: "monstre", LOCATION_SZONE: "magie-piege",
    LOCATION_GRAVE: "cimetiere", LOCATION_REMOVED: "bannis", LOCATION_EXTRA: "extra", LOCATION_OVERLAY: "materiau",
}

# Groupes de MSG_SELECT_IDLECMD : (clé du décodeur, action exposée, type de réponse).
COMMANDES = (
    ("summon", "invocation-normale", M.IDLE_SUMMON),
    ("spsummon", "invocation-speciale", M.IDLE_SPSUMMON),
    ("repos", "changer-position", M.IDLE_REPOS),
    ("mset", "poser-monstre", M.IDLE_MSET),
    ("sset", "poser-magie-piege", M.IDLE_SSET),
    ("activate", "activation", M.IDLE_ACTIVATE),
)


class PartieInvalide(ValueError):
    """Partie impossible à mettre en place (main absente du deck, etc.)."""


@dataclass(frozen=True)
class Adversaire:
    """Cartes du joueur 1 : Main Deck (main de départ comprise), Extra Deck et main de départ."""
    deck: tuple[int, ...] = ()
    extra: tuple[int, ...] = ()
    main: tuple[int, ...] = ()


@dataclass(frozen=True)
class PartieSimulee:
    deck: tuple[int, ...]
    extra: tuple[int, ...]
    main: tuple[int, ...]
    graine: int
    adversaire: Adversaire | None = None
    tours: int = 1  # tours joués : le début du tour `tours + 1` termine la simulation

    @property
    def avec_adversaire(self) -> bool:
        """Un adversaire sans aucune carte équivaut à son absence : le joueur 1 passe toujours."""
        adversaire = self.adversaire
        return adversaire is not None and bool(adversaire.deck or adversaire.extra or adversaire.main)


@dataclass(frozen=True)
class Choix:
    indices: tuple[int, ...] = ()
    valeur: int | None = None  # valeur libre : code de la carte déclarée
    empreinte: str | None = None  # empreinte de la décision au moment de l'enregistrement


@dataclass(frozen=True)
class _Option:
    action: str
    reponse: bytes = b""
    code: int | None = None
    loc: M.Loc | None = None
    description: int = 0
    details: dict | None = None
    systeme: int = 0  # texte système d'EDOPro servant de libellé (Attribut, Type…)


@dataclass(frozen=True)
class _Decision:
    type: str
    options: list[_Option]
    min: int = 1
    max: int = 1
    carte: M.CarteChoix | None = None
    description: int = 0
    encodeur: Callable[[list[int]], bytes] | None = None
    infos: dict | None = None  # précisions propres au type de décision (cible d'une somme…)

    def reponse(self, choix: Choix) -> bytes:
        if self.type in VALEUR_LIBRE:
            return M.rep_i32(choix.valeur)
        if self.encodeur:
            return self.encodeur(list(choix.indices))
        return b"".join(self.options[i].reponse for i in choix.indices)

    def empreinte(self) -> str:
        """Identifie la décision : un replay enregistré qui ne retrouve pas la même empreinte a divergé."""
        options = [(o.action, o.code, o.loc and (o.loc.controleur, o.loc.location, o.loc.sequence), o.description,
                    sorted((o.details or {}).items())) for o in self.options]
        contenu = json.dumps([self.type, self.min, self.max, options])
        return hashlib.sha1(contenu.encode()).hexdigest()[:16]

    def erreur(self, choix: Choix) -> str | None:
        """Motif de refus d'un choix, avant tout envoi au moteur."""
        if self.type in VALEUR_LIBRE:
            return None if choix.valeur and choix.valeur > 0 else "Une valeur (code de carte) est attendue."
        indices = choix.indices
        if any(not 0 <= i < len(self.options) for i in indices):
            return f"Option inconnue parmi {list(indices)} ({len(self.options)} options)."
        if len(set(indices)) != len(indices):
            return "Une option est choisie plusieurs fois."
        minimum, maximum = (self.min, self.max) if self.type in MULTIPLES else (1, 1)
        if not minimum <= len(indices) <= maximum:
            return f"{len(indices)} option(s) choisie(s), attendu entre {minimum} et {maximum}."
        return None


# Décisions où plusieurs options sont choisies d'un coup, entre `min` et `max`.
MULTIPLES = {"cartes", "zone", "annonce-attribut", "annonce-type", "ordre", "somme"}
# Décisions sans liste d'options : le choix porte une valeur, validée par le moteur.
VALEUR_LIBRE = {"annonce-carte"}


class _SplitMix64:
    """Générateur déterministe : même graine, même ordre de deck et même duel."""

    def __init__(self, graine: int):
        self.etat = graine & MASQUE_64

    def suivant(self) -> int:
        self.etat = (self.etat + 0x9E3779B97F4A7C15) & MASQUE_64
        z = self.etat
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & MASQUE_64
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & MASQUE_64
        return z ^ (z >> 31)


def valider(partie: PartieSimulee):
    if not 1 <= partie.tours <= MAX_TOURS:
        raise PartieInvalide(f"Le nombre de tours doit être compris entre 1 et {MAX_TOURS}.")
    _verifier_main(partie.main, partie.deck, "Cartes de la main absentes du deck")
    if partie.adversaire:
        _verifier_main(partie.adversaire.main, partie.adversaire.deck,
                       "Cartes de la main adverse absentes du deck adverse")


def _verifier_main(main: tuple[int, ...], deck: tuple[int, ...], motif: str):
    manquantes = Counter(main) - Counter(deck)
    if manquantes:
        raise PartieInvalide(f"{motif} : {sorted(manquantes)}.")


def preparer(partie: PartieSimulee) -> tuple[list[int], tuple[int, int, int, int]]:
    """Deck restant mélangé selon la graine (main retirée) et graines du duel."""
    valider(partie)
    alea = _SplitMix64(partie.graine)
    deck = _melanger(_sans_la_main(partie.deck, partie.main), alea)
    return deck, (alea.suivant(), alea.suivant(), alea.suivant(), alea.suivant())


def preparer_adversaire(partie: PartieSimulee) -> list[int]:
    """Deck restant de l'adversaire (main retirée), mélangé par son propre générateur dérivé de la graine."""
    adversaire = partie.adversaire
    alea = _SplitMix64(partie.graine ^ GRAINE_ADVERSAIRE)
    return _melanger(_sans_la_main(adversaire.deck, adversaire.main), alea)


def _sans_la_main(deck: tuple[int, ...], main: tuple[int, ...]) -> list[int]:
    """Deck privé d'un exemplaire de chaque carte de la main de départ."""
    a_retirer = Counter(main)
    restant = []
    for code in deck:
        if a_retirer[code]:
            a_retirer[code] -= 1
            continue
        restant.append(code)
    return restant


def _melanger(cartes: list[int], alea: _SplitMix64) -> list[int]:
    """Mélange de Fisher-Yates : même générateur, même ordre."""
    for i in range(len(cartes) - 1, 0, -1):
        j = alea.suivant() % (i + 1)
        cartes[i], cartes[j] = cartes[j], cartes[i]
    return cartes


def simuler(engine: Engine, partie: PartieSimulee, choix: list[Choix], evenements_depuis: int = 0) -> dict:
    """Rejoue `choix` et renvoie la décision suivante, le plateau et les événements.

    `choix` répond dans l'ordre aux décisions exposées, celles du joueur 0 comme celles de l'adversaire.
    `evenements_depuis` ne garde que les événements survenus après ce nombre de choix (journal incrémental).
    """
    with _duel(engine, partie) as duel:
        resultat = _Rejeu(engine, duel, choix, partie.avec_adversaire, partie.tours).jouer()
    resultat["evenements"] = [e for e in resultat["evenements"] if e["apresChoix"] >= evenements_depuis]
    return resultat


@dataclass
class Journal:
    """Ligne rejouée pour en tirer un graphe, au format de `simulation.py` attendu par `graphe.extraire_actions`."""
    resultat: dict  # comme `simuler` : statut, choixAppliques, erreur, raison…
    evenements: list[tuple[int, M.Message]]  # (choix appliqués, message du moteur ou marqueur d'un choix)
    reponses: list[bytes]  # réponse envoyée au moteur pour chaque choix appliqué
    plateau: dict[int, list[tuple[int, int]]]  # (code, position) des cartes du joueur suivi, par zone, en fin de ligne


def journaliser(engine: Engine, partie: PartieSimulee, choix: list[Choix], joueur: int = 0) -> Journal:
    """Rejoue `choix` comme `simuler` en gardant les messages du moteur et le marqueur de chaque choix."""
    with _duel(engine, partie) as duel:
        rejeu = _Rejeu(engine, duel, choix, partie.avec_adversaire, partie.tours, journal=True)
        resultat = rejeu.jouer()
        plateau_final = {location: duel.cartes(joueur, location) for location in Simulation.LOCATIONS_PLATEAU}
    return Journal(resultat, rejeu.journal, rejeu.reponses, plateau_final)


@contextmanager
def _duel(engine: Engine, partie: PartieSimulee) -> Iterator[Duel]:
    """Duel prêt à jouer : decks mélangés selon la graine, cartes en place ; fermé à la sortie."""
    deck, graines = preparer(partie)
    # Sans adversaire, le joueur 1 n'a pas de deck : s'il piochait à son tour, il perdrait aussitôt (deck vide).
    pioches = (1, 1) if partie.avec_adversaire or partie.tours == 1 else (1, 0)
    duel = engine.creer_duel(graines=graines, flags=DUEL_MODE_MR5, pioches=pioches)
    try:
        _installer(duel, 0, deck, partie.extra, partie.main)
        if partie.avec_adversaire:
            adversaire = partie.adversaire
            _installer(duel, 1, preparer_adversaire(partie), adversaire.extra, adversaire.main)
        duel.demarrer()
        yield duel
    finally:
        duel.fermer()


def _installer(duel, joueur: int, deck: list[int], extra: tuple[int, ...], main: tuple[int, ...]):
    """Place les cartes d'un joueur avant le début du duel : deck dans l'ordre donné, Extra Deck, puis main."""
    for location, cartes in ((LOCATION_DECK, deck), (LOCATION_EXTRA, extra), (LOCATION_HAND, main)):
        for code in cartes:
            duel.ajouter_carte(code, joueur, location)


class _Rejeu:
    def __init__(self, engine: Engine, duel, choix: list[Choix], adversaire: bool = False, tours: int = 1,
                 journal: bool = False):
        self.engine, self.duel, self.choix = engine, duel, choix
        self.joueurs = (0, 1) if adversaire else (0,)  # joueurs dont les décisions sont exposées
        self.appliques = 0
        self.evenements: list[dict] = []
        self.indice: dict[int, int] = {}  # dernier HINT_SELECTMSG de chaque joueur : texte de sa prochaine sélection
        self.tours = 0
        self.dernier_tour = tours  # le tour suivant termine la simulation
        self.phase: str | None = None
        # Monstres qui pouvaient attaquer à la dernière commande de Battle Phase (contrôleur, zone) → code : l'attaque
        # déclarée ne donne que l'emplacement de l'attaquant.
        self.attaquants: dict[tuple[int, int], int] = {}
        # Réponses envoyées et, sur demande, journal au format de `simulation.py` (graphe d'une ligne).
        self.reponses: list[bytes] = []
        self.journal: list[tuple[int, M.Message]] | None = [] if journal else None

    def jouer(self) -> dict:
        messages = 0
        while True:
            statut, bruts = self.duel.avancer()
            messages += len(bruts)
            if messages > LIMITE_MESSAGES:
                return self._resultat("fin", raison="limite-atteinte")
            decision, refus = None, False
            for brut in bruts:
                message = M.decoder(brut)
                if message.type == M.MSG_RETRY:
                    refus = True
                    continue
                if message.type in M.DECISIONS:
                    decision = message
                    if message.type == M.MSG_SELECT_BATTLECMD:
                        self.attaquants = {(c.loc.controleur, c.loc.sequence): c.code for c in message.extra["attaques"]}
                    continue
                if self.journal is not None:
                    self.journal.append((self.appliques, message))
                if message.type == M.MSG_NEW_TURN:
                    self.tours += 1
                    if self.tours > self.dernier_tour:
                        return self._resultat("fin", raison="fin-de-tour")
                if message.type == M.MSG_WIN:
                    return self._resultat("fin", raison="victoire" if message.joueur == 0 else "defaite")
                if message.type == M.MSG_HINT and message.extra.get("type") == M.HINT_SELECTMSG:
                    self.indice[message.joueur] = message.extra["donnee"]
                evenement = self._evenement(message)
                if evenement:
                    self.evenements.append({**evenement, "apresChoix": self.appliques})
            if refus:
                # Le moteur attend une autre réponse à la même décision, sans la renvoyer.
                self.appliques -= 1
                decision, vue = self.repondue
                return self._resultat("invalide", self._vue_decision(decision, vue), "Choix refusé par le moteur de règles.")
            if statut == DUEL_STATUS_END or decision is None:
                return self._resultat("fin")
            auto = _reponse_automatique(decision, self.joueurs)
            if auto is not None:
                self.duel.repondre(auto)
                continue
            if decision.joueur not in self.joueurs:
                return self._resultat("fin")
            try:
                vue = decision_exposee(decision)
            except DecisionNonSupportee:
                return self._resultat("non-supporte", erreur=f"Décision du moteur non prise en charge (message {decision.type}).")
            indice = self.indice.get(decision.joueur, 0)
            if not vue.description and indice:
                vue = replace(vue, description=indice)
            if self.appliques < len(self.choix):
                choix = self.choix[self.appliques]
                if choix.empreinte and choix.empreinte != vue.empreinte():
                    return self._resultat("divergent", self._vue_decision(decision, vue))
                erreur = vue.erreur(choix)
                if erreur:
                    return self._resultat("invalide", self._vue_decision(decision, vue), erreur)
                reponse = vue.reponse(choix)
                if self.journal is not None:
                    self.journal.append((self.appliques, marqueur(decision, reponse)))
                self.reponses.append(reponse)
                self.duel.repondre(reponse)
                self.repondue = (decision, vue)
                self.appliques += 1
                self.indice[decision.joueur] = 0
                continue
            return self._resultat("decision", self._vue_decision(decision, vue))

    def _vue_decision(self, decision: M.Message, vue: _Decision) -> dict:
        resultat = {
            "type": vue.type,
            "joueur": decision.joueur,
            "min": vue.min,
            "max": vue.max,
            "empreinte": vue.empreinte(),
            "options": [self._vue_option(i, o) for i, o in enumerate(vue.options)],
            **(vue.infos or {}),
        }
        if vue.carte is not None:
            resultat["carte"] = {"code": vue.carte.code, "emplacement": emplacement(vue.carte.loc)}
        texte = libelle_description(self.engine, vue.description)
        if texte:
            resultat["texte"] = _completer(self.engine, texte, vue.carte)
        return resultat

    def _resultat(self, statut: str, decision: dict | None = None, erreur: str | None = None,
                  raison: str | None = None) -> dict:
        vue = {"tour": self.tours, "phase": self.phase, **plateau(self.engine, self.duel)}
        resultat = {"statut": statut, "choixAppliques": self.appliques, "plateau": vue, "evenements": self.evenements}
        if decision is not None:
            resultat["decision"] = decision
        if erreur:
            resultat["erreur"] = erreur
        if raison:
            resultat["raison"] = raison
        return resultat

    def _vue_option(self, index: int, option: _Option) -> dict:
        vue = {"index": index, "action": option.action, "code": option.code, **(option.details or {})}
        if option.loc is not None:
            vue["emplacement"] = emplacement(option.loc)
        effet = libelle_description(self.engine, option.description)
        if effet:
            vue["effet"] = effet
        if option.systeme:
            vue["libelle"] = self.engine.chaines.systeme(option.systeme)
        return vue


    def _evenement(self, message: M.Message) -> dict | None:
        if message.type in INVOCATIONS or message.type == M.MSG_SET:
            carte = message.cartes[0]
            return {"type": INVOCATIONS.get(message.type, "pose"), "code": carte.code,
                    "emplacement": emplacement(carte.loc, avec_position=True)}
        if message.type == M.MSG_MOVE:
            avant, apres = message.cartes
            raison = message.extra["raison"]
            return {"type": "deplacement", "code": avant.code, "de": emplacement(avant.loc, avec_position=True),
                    "vers": emplacement(apres.loc, avec_position=True), "raison": raison,
                    "raisons": [nom for bit, nom in RAISONS if raison & bit]}
        if message.type == M.MSG_CHAINING:
            carte = message.cartes[0]
            return {"type": "activation", "code": carte.code, "emplacement": emplacement(carte.loc, avec_position=True),
                    "effet": libelle_description(self.engine, carte.description), "maillon": message.extra["maillon"]}
        if message.type in MAILLONS:
            return {"type": MAILLONS[message.type], "maillon": message.extra["maillon"]}
        if message.type == M.MSG_CHAIN_END:
            return {"type": "fin-de-chaine"}
        if message.type == M.MSG_NEW_TURN:
            return {"type": "nouveau-tour", "joueur": message.joueur}
        if message.type == M.MSG_NEW_PHASE:
            self.phase = PHASES.get(message.extra["phase"], str(message.extra["phase"]))
            return {"type": "nouvelle-phase", "phase": self.phase}
        if message.type == M.MSG_DRAW:
            return {"type": "pioche", "joueur": message.joueur, "codes": [carte.code for carte in message.cartes]}
        if message.type in POINTS_DE_VIE:
            return {"type": POINTS_DE_VIE[message.type], "joueur": message.joueur, "montant": message.extra["montant"]}
        if message.type == M.MSG_SHUFFLE_DECK:
            return {"type": "melange-deck", "joueur": message.joueur}
        if message.type == M.MSG_BECOME_TARGET:
            return {"type": "ciblage", "emplacements": [emplacement(c.loc, avec_position=True) for c in message.cartes]}
        if message.type == M.MSG_EQUIP:
            carte, cible = message.cartes
            return {"type": "equipement", "emplacement": emplacement(carte.loc, avec_position=True),
                    "cible": emplacement(cible.loc, avec_position=True)}
        if message.type == M.MSG_POS_CHANGE:
            carte = message.cartes[0]
            return {"type": "changement-position", "code": carte.code, "emplacement": emplacement(carte.loc, avec_position=True),
                    "avant": _position(message.extra["avant"])}
        if message.type == M.MSG_ATTACK:
            attaquant, cible = (carte.loc for carte in message.cartes)
            evenement = {"type": "attaque", "de": emplacement(attaquant, avec_position=True), "direct": cible.location == 0}
            code = self.attaquants.get((attaquant.controleur, attaquant.sequence))
            if code:
                evenement["code"] = code
            if cible.location:
                evenement["vers"] = emplacement(cible, avec_position=True)
            return evenement
        return None


MAILLONS = {M.MSG_CHAIN_SOLVING: "resolution", M.MSG_CHAIN_SOLVED: "resolu",
            M.MSG_CHAIN_NEGATED: "activation-annulee", M.MSG_CHAIN_DISABLED: "effet-annule"}
PHASES = {0x1: "pioche", 0x2: "standby", 0x4: "main1", 0x8: "debut-combat", 0x10: "etape-combat",
          0x20: "etape-degats", 0x40: "calcul-degats", 0x80: "combat", 0x100: "main2", 0x200: "fin"}
# LPUPDATE donne les nouveaux LP ; les autres, la variation.
POINTS_DE_VIE = {M.MSG_PAY_LPCOST: "paiement-lp", M.MSG_DAMAGE: "degats", M.MSG_RECOVER: "gain-lp",
                 M.MSG_LPUPDATE: "lp"}


def emplacement(loc: M.Loc, avec_position: bool = False) -> dict:
    """Emplacement d'une carte ; les événements y ajoutent sa position quand le moteur la donne. Sous un Xyz, le
    moteur range dans ce champ le rang du matériau : un matériau n'a pas de position."""
    vue = {"controleur": loc.controleur, "zone": ZONES.get(loc.location, str(loc.location)), "sequence": loc.sequence}
    if avec_position and loc.position and not loc.location & LOCATION_OVERLAY:
        vue.update(_position(loc.position))
    return vue


def plateau(engine: Engine, duel) -> dict:
    terrain = duel.terrain()
    joueurs = []
    for joueur, comptes in enumerate(terrain["joueurs"]):
        vue = {"lp": comptes["lp"], "deck": comptes["deck"]}
        vue["monstres"] = [_vue_terrain(c, monstre=True) for c in duel.details(joueur, LOCATION_MZONE, FLAGS_CARTES)]
        vue["magiesPieges"] = [_vue_terrain(c, monstre=False) for c in duel.details(joueur, LOCATION_SZONE, FLAGS_CARTES)]
        for nom, location in PILES:
            vue[nom] = [_vue_pile(c, location) for c in duel.details(joueur, location, FLAGS_CARTES) if c]
        joueurs.append(vue)
    chaine = [{"maillon": i, "code": m["code"], "effet": libelle_description(engine, m["description"]),
               "emplacement": emplacement(M.Loc(m["controleur"], m["location"], m["sequence"]))}
              for i, m in enumerate(terrain["chaine"], start=1)]
    return {"joueurs": joueurs, "chaine": chaine}


def _face(carte: dict) -> str:
    return "recto" if carte.get("position", 0) & (POS_FACEUP_ATTACK | POS_FACEUP_DEFENSE) else "verso"


def _vue_terrain(carte: dict | None, monstre: bool) -> dict | None:
    if carte is None:
        return None
    vue = {"code": carte["code"], "face": _face(carte)}
    if carte.get("compteurs"):
        vue["compteurs"] = carte["compteurs"]
    if not monstre:
        return vue
    vue["position"] = "attaque" if carte.get("position", 0) & 0x3 else "defense"
    vue["atk"] = carte.get("atk")
    if carte.get("lien"):
        vue["lien"], vue["fleches"] = carte["lien"], carte["fleches"]
    else:
        vue["def"] = carte.get("def")
    for cle in ("niveau", "rang"):
        if carte.get(cle):
            vue[cle] = carte[cle]
    if carte.get("materiaux"):
        vue["materiaux"] = carte["materiaux"]
    return vue


def _vue_pile(carte: dict, location: int) -> dict:
    vue = {"code": carte["code"]}
    if location in (LOCATION_REMOVED, LOCATION_EXTRA):
        vue["face"] = _face(carte)
    # Extra Deck : Niveau, Rang ou valeur Lien, ce qu'une Invocation Synchro, Xyz ou Lien du monstre doit atteindre.
    if location == LOCATION_EXTRA:
        for cle in ("niveau", "rang", "lien"):
            if carte.get(cle):
                vue[cle] = carte[cle]
    return vue


def _reponse_automatique(decision: M.Message, joueurs: tuple[int, ...] = (0,)) -> bytes | None:
    """Décisions répondues à la place des joueurs : sans choix réel, ou pas encore exposées.

    Un joueur absent de `joueurs` (le joueur 1 sans adversaire) n'a aucune carte : la réponse par défaut
    du POC lui fait tout passer, et il finit son tour dès qu'il en a un (partie de plusieurs tours). Pour les
    joueurs exposés, l'ordre de résolution des déclencheurs simultanés, la répartition de compteurs, le
    combat et pierre-feuille-ciseaux reçoivent la réponse par défaut, déterministe, du POC.
    """
    if decision.joueur not in joueurs:
        if decision.type == M.MSG_SELECT_IDLECMD:
            return _finir_le_tour(decision)
        return reponse_automatique(decision)
    if decision.type == M.MSG_SELECT_CHAIN and not decision.cartes:
        return M.rep_i32(-1)
    if decision.type in AUTOMATIQUES:
        return reponse_automatique(decision)
    return None


# La commande de Battle Phase est exposée (décision « combat ») : elle ne reste automatique que pour un joueur absent.
AUTOMATIQUES = {M.MSG_SORT_CHAIN, M.MSG_SELECT_COUNTER, M.MSG_ROCK_PAPER_SCISSORS}


def _finir_le_tour(decision: M.Message) -> bytes | None:
    """Commande principale d'un joueur sans cartes : il va en End Phase (en Battle Phase s'il ne peut pas)."""
    if decision.extra["to_ep"]:
        return M.rep_i32(M.IDLE_TO_EP)
    if decision.extra["to_bp"]:
        return M.rep_i32(M.IDLE_TO_BP)
    return None


def decision_exposee(d: M.Message) -> _Decision:
    if d.type in (M.MSG_SELECT_PLACE, M.MSG_SELECT_DISFIELD):
        return _zones(d)
    if d.type == M.MSG_SELECT_EFFECTYN:
        carte = d.cartes[0]
        return _Decision("oui-non", _OUI_NON, carte=carte, description=carte.description)
    if d.type == M.MSG_SELECT_YESNO:
        return _Decision("oui-non", _OUI_NON, description=d.extra["description"])
    if d.type == M.MSG_SELECT_OPTION:
        return _Decision("option", [_Option("option", M.rep_i32(i), description=o) for i, o in enumerate(d.extra["options"])])
    if d.type in (M.MSG_SELECT_CARD, M.MSG_SELECT_TRIBUTE):
        options = [_Option("carte", code=carte.code, loc=carte.loc) for carte in d.cartes]
        return _Decision("cartes", options, d.extra["min"], d.extra["max"], encodeur=M.rep_cartes)
    if d.type == M.MSG_SELECT_CHAIN:
        options = [_Option("activation", M.rep_i32(i), c.code, c.loc, c.description) for i, c in enumerate(d.cartes)]
        if not d.extra["force"]:
            options.append(_Option("passer", M.rep_i32(-1)))
        # Activations déclenchées par ce qui vient de se passer ; à 0, la fenêtre est « libre » (Magie Jeu-Rapide,
        # Effet Rapide sans déclencheur) et EDOPro la passe par défaut. Hors empreinte : les replays restent valides.
        return _Decision("chaine", options, infos={"speciaux": d.extra["speciaux"]})
    if d.type == M.MSG_SELECT_UNSELECT_CARD:
        return _carte_par_carte(d)
    if d.type == M.MSG_ANNOUNCE_CARD:
        return _Decision("annonce-carte", [], 0, 0)
    if d.type == M.MSG_ANNOUNCE_ATTRIB:
        return _annonce(d, "annonce-attribut", "attribut", 7, 1010, "<I")
    if d.type == M.MSG_ANNOUNCE_RACE:
        return _annonce(d, "annonce-type", "type", 30, 1020, "<Q")
    if d.type == M.MSG_ANNOUNCE_NUMBER:
        return _Decision("annonce-nombre", [_Option("nombre", M.rep_i32(i), details={"valeur": v})
                                            for i, v in enumerate(d.extra["options"])])
    if d.type == M.MSG_SORT_CARD:
        options = [_Option("carte", code=carte.code, loc=carte.loc) for carte in d.cartes]
        return _Decision("ordre", options, len(options), len(options), encodeur=_ordre)
    if d.type == M.MSG_SELECT_SUM:
        return _somme(d)
    if d.type == M.MSG_SELECT_POSITION:
        options = [_Option("position", M.rep_i32(pos), d.extra["code"], details=_position(pos))
                   for pos in (POS_FACEUP_ATTACK, POS_FACEDOWN_ATTACK, POS_FACEUP_DEFENSE, POS_FACEDOWN_DEFENSE)
                   if d.extra["positions"] & pos]
        return _Decision("position", options)
    if d.type == M.MSG_SELECT_IDLECMD:
        return _commandes(d)
    if d.type == M.MSG_SELECT_BATTLECMD:
        return _combat(d)
    raise DecisionNonSupportee(d.type)


class DecisionNonSupportee(Exception):
    """Demande du moteur que le simulateur ne sait pas encore présenter au joueur."""


def _annonce(d: M.Message, type_: str, action: str, bits: int, texte: int, format_: str) -> _Decision:
    """Attributs ou Types déclarables : un bit par valeur, la réponse est le masque des valeurs choisies."""
    options = [_Option(action, details={"valeur": 1 << bit}, systeme=texte + bit)
               for bit in range(bits) if d.extra["disponibles"] >> bit & 1]
    nombre = d.extra["nombre"]
    return _Decision(type_, options, nombre, nombre,
                     encodeur=lambda indices: struct.pack(format_, sum(options[i].details["valeur"] for i in indices)))


def _somme(d: M.Message) -> _Decision:
    """Cartes dont la somme (Niveaux…) doit atteindre la cible ; le moteur vérifie le total.

    Les cartes obligatoires sont ajoutées par le moteur : seules les autres sont proposées.
    """
    options = []
    for carte in d.cartes:
        details = {"niveau": carte.param & 0xFFFF}
        if carte.param >> 16:
            details["niveauAlternatif"] = carte.param >> 16
        options.append(_Option("carte", code=carte.code, loc=carte.loc, details=details))
    obligatoires = [{"code": c.code, "emplacement": emplacement(c.loc)} for c in d.extra["obligatoires"]]
    return _Decision("somme", options, 1, len(options), encodeur=M.rep_cartes,
                     infos={"cible": d.extra["cible"], "obligatoires": obligatoires})


def _ordre(indices: list[int]) -> bytes:
    """Le choix liste les options du dessus vers le dessous ; le moteur attend la place de chaque carte proposée."""
    places = [0] * len(indices)
    for place, option in enumerate(indices):
        places[option] = place
    return bytes(places)


def _position(position: int) -> dict:
    return {"position": "attaque" if position & 0x3 else "defense",
            "face": "recto" if position & (POS_FACEUP_ATTACK | POS_FACEUP_DEFENSE) else "verso"}


def _carte_par_carte(d: M.Message) -> _Decision:
    """Sélection itérative : une carte ajoutée ou retirée par réponse, jusqu'à pouvoir terminer."""
    cartes = [("carte", c) for c in d.cartes] + [("retirer", c) for c in d.extra["deselection"]]
    options = [_Option(action, M.rep_unselect(i), c.code, c.loc) for i, (action, c) in enumerate(cartes)]
    if d.extra["finissable"]:
        options.append(_Option("terminer", M.rep_unselect(None)))
    elif d.extra["annulable"]:
        options.append(_Option("annuler", M.rep_unselect(None)))
    return _Decision("carte-par-carte", options, d.extra["min"], d.extra["max"])


_OUI_NON = [_Option("oui", M.rep_i32(1)), _Option("non", M.rep_i32(0))]


def _commandes(d: M.Message) -> _Decision:
    options = []
    for groupe, action, type_ in COMMANDES:
        for index, carte in enumerate(d.extra[groupe]):
            options.append(_Option(action, M.rep_idle(type_, index), carte.code, carte.loc, carte.description))
    if d.extra["to_bp"]:
        options.append(_Option("phase-de-combat", M.rep_i32(M.IDLE_TO_BP)))
    if d.extra["to_ep"]:
        options.append(_Option("fin-de-tour", M.rep_i32(M.IDLE_TO_EP)))
    return _Decision("commande", options)


def _combat(d: M.Message) -> _Decision:
    """Battle Phase : activer un effet, attaquer avec un monstre (`direct` s'il peut viser le joueur adverse), aller en
    Main Phase 2 ou finir le tour. Réponse : (index << 16) | type, 0 activer, 1 attaquer, 2 Main Phase 2, 3 End Phase."""
    options = [_Option("activation", M.rep_idle(0, i), c.code, c.loc, c.description) for i, c in enumerate(d.extra["activate"])]
    options += [_Option("attaque", M.rep_idle(1, i), c.code, c.loc, details={"direct": bool(c.param)})
                for i, c in enumerate(d.extra["attaques"])]
    if d.extra["to_m2"]:
        options.append(_Option("phase-principale-2", M.rep_idle(2, 0)))
    if d.extra["to_ep"]:
        options.append(_Option("fin-de-tour", M.rep_idle(3, 0)))
    return _Decision("combat", options)


def _zones(d: M.Message) -> _Decision:
    """Zones libres (bit à 0) : 0-6 monstres et 8-15 magies/pièges du joueur, +16 pour l'adversaire."""
    options = []
    for bit in [*range(7), *range(8, 16), *range(16, 23), *range(24, 32)]:
        if d.extra["drapeau"] >> bit & 1:
            continue
        cote, local = divmod(bit, 16)
        controleur, location, sequence = d.joueur ^ cote, LOCATION_MZONE if local < 8 else LOCATION_SZONE, local % 8
        options.append(_Option("zone", bytes((controleur, location, sequence)), loc=M.Loc(controleur, location, sequence)))
    return _Decision("zone", options, d.extra["nombre"], d.extra["nombre"])


def _completer(engine: Engine, texte: str, carte: M.CarteChoix | None) -> str:
    """Remplace les `%ls` des textes système par le nom de la carte puis celui de sa zone."""
    if carte is None:
        return texte
    zone = engine.chaines.systeme(1000 + carte.loc.location.bit_length() - 1) or ""
    for valeur in (engine.base.nom(carte.code), zone):
        texte = texte.replace("%ls", valeur, 1)
    return texte


def libelle_description(engine: Engine, description: int) -> str | None:
    """Texte d'une description d'effet : chaîne de carte (`aux.Stringid`) ou texte système."""
    if not description:
        return None
    if description >> 20:
        return engine.base.texte_effet(description)
    return engine.chaines.systeme(description)
