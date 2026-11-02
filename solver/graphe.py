"""Traduction des lignes explorées vers le contrat `DeckGraph` de TrainMyMat.

Une action du graphe correspond à une procédure d'invocation ou de pose choisie
en commande principale, ou à une activation d'effet (maillon de chaîne). Ses
entrées : PRIMARY (carte qui agit), MATERIAL, COST, REQUIREMENT (cible, ou carte
du maillon auquel l'activation répond : « En chaîne »). Ses sorties RESULT créent
une nouvelle instance visuelle de la carte déplacée.

Une activation est nommée par ce qu'elle fait (`cle_action`) : le premier résultat
de sa résolution donne la clé du catalogue d'actions de l'app.

Arbre des choix : une ligne alternative rejoue la ligne principale jusqu'à un
choix de carte, prend un autre candidat et se greffe sur la même action ; seules
ses MAX_ACTIONS_ALTERNATIVE actions suivantes sont dessinées.

Les identifiants sont dérivés de l'historique de réponses : deux lignes qui
partagent un préfixe partagent les mêmes nœuds et actions.

Les cartes des deux joueurs sont suivies : celles du joueur suivi (`joueur`, 0 pour
`/generer`) sont des nœuds ordinaires, celles de l'autre joueur des nœuds `adverse`.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass, field

from . import messages as M
from .simulation import SELECTIONS_CARTES
from .ocg import (
    LOCATION_DECK, LOCATION_EXTRA, LOCATION_GRAVE, LOCATION_HAND, LOCATION_MZONE, LOCATION_OVERLAY,
    LOCATION_REMOVED, LOCATION_SZONE, POS_FACEDOWN, POS_FACEUP_ATTACK, REASON_COST, REASON_DESTROY,
    REASON_DISCARD, REASON_MATERIAL, REASON_RELEASE, TYPE_FUSION, TYPE_LINK, TYPE_SPELL, TYPE_SYNCHRO, TYPE_TOKEN, TYPE_TRAP,
    TYPE_XYZ, CardDatabase,
)

CARTE_L, CARTE_H = 132, 192
ACTION_L, ACTION_H = 176, 56
PAS_ACTION = 440
PAS_RESULTAT = 232
# Une rangée = une ligne principale ou une alternative ; 3 résultats empilés au plus.
HAUTEUR_RANGEE = 3 * PAS_RESULTAT + 60
ENDBOARD_L, ENDBOARD_H = 1400, 840
MAX_ACTIONS_ALTERNATIVE = 5
MAX_BRANCHES = 30
MAX_ETAPES_BRANCHE = 100  # borne backend (MAX_ETAPES_BRANCHE_GRAPH_DECK)
MARGE_ZONE = 16
HAUTEUR_MAX_ZONE = 5000  # borne backend (MAX_DIMENSION_ZONE)
COULEUR_CHOIX = "#22C55E"

COULEURS_ROLES = {"MATERIAL": "#38BDF8", "COST": "#F97316", "REQUIREMENT": "#F43F5E"}
LIBELLES_ROLES = {"MATERIAL": "Matériel", "COST": "Coût", "REQUIREMENT": "Cible"}
LIBELLE_EN_CHAINE = "En chaîne"

# Ce qu'un effet fait d'une carte → clé du catalogue d'actions de l'app (`STEP_ACTIONS`), d'après la table du front
# (`mapZoneTransitionToAction`, combo/canvas/boardPlacement.ts). Pioche, Token, Extra Deck → zone monstre et envoi
# au Cimetière depuis la main ou le terrain dépendent de la carte ou de la raison : voir `cle_resultat`.
ZONES_DEPLACEMENT = {
    LOCATION_DECK: "deck", LOCATION_HAND: "main", LOCATION_MZONE: "monstre", LOCATION_SZONE: "magie-piege",
    LOCATION_GRAVE: "cimetiere", LOCATION_REMOVED: "bannis", LOCATION_EXTRA: "extra",
}
CLES_DEPLACEMENTS = {
    ("deck", "main"): "tuto",
    ("deck", "monstre"): "deck-to-field",
    ("deck", "magie-piege"): "poser-deck",
    ("deck", "cimetiere"): "foolish",
    ("main", "monstre"): "invocation-spe",
    ("main", "magie-piege"): "poser-carte",
    ("monstre", "main"): "retour-main",
    ("magie-piege", "main"): "retour-main",
    ("cimetiere", "main"): "gy-to-main",
    ("bannis", "main"): "gy-to-main",
    ("materiau", "main"): "gy-to-main",
    ("cimetiere", "monstre"): "gy-to-field",
    ("bannis", "monstre"): "gy-to-field",
    ("materiau", "monstre"): "gy-to-field",
    ("cimetiere", "magie-piege"): "poser-gy",
    ("materiau", "cimetiere"): "detacher-materiel",
}
CLES_DESTINATIONS = {"bannis": "ban", "deck": "renvoyer-deck", "extra": "renvoyer-extra", "materiau": "attacher-materiel"}
INVOCATIONS_EXTRA = ((TYPE_LINK, "link"), (TYPE_XYZ, "xyz"), (TYPE_SYNCHRO, "synchro"), (TYPE_FUSION, "fusion"))
LIEUX = {
    LOCATION_DECK: "le Deck", LOCATION_HAND: "la main", LOCATION_GRAVE: "le Cimetière",
    LOCATION_REMOVED: "le bannissement", LOCATION_EXTRA: "l'Extra Deck", LOCATION_MZONE: "le terrain",
    LOCATION_SZONE: "le terrain",
}
EMPLACEMENTS = {
    LOCATION_HAND: "main", LOCATION_MZONE: "field", LOCATION_SZONE: "field", LOCATION_GRAVE: "gy",
    LOCATION_REMOVED: "banish", LOCATION_DECK: "deck", LOCATION_EXTRA: "extra", LOCATION_OVERLAY: "xyz-material",
}
LISTES = (LOCATION_HAND, LOCATION_GRAVE, LOCATION_REMOVED)


def _emplacement(location: int) -> str:
    return "xyz-material" if location & LOCATION_OVERLAY else EMPLACEMENTS.get(location, "field")


def _hash(*parties) -> str:
    return hashlib.sha1(repr(parties).encode()).hexdigest()[:12]


class SuiviCartes:
    """Suit l'identité physique des cartes des deux joueurs à travers leurs déplacements.

    Main, Cimetière et bannissement sont des listes ordonnées, une par joueur (le moteur
    renumérote après chaque retrait) ; les zones du terrain sont des emplacements fixes. Le Deck
    et l'Extra Deck face verso ne sont pas suivis : une carte qui en sort reçoit une
    nouvelle identité. Une identité garde le côté où la carte est apparue, celui de son
    propriétaire, même si elle change de contrôleur.
    """

    def __init__(self, main: tuple[int, ...], joueur: int = 0, main_adverse: tuple[int, ...] = ()):
        self.codes: dict[int, int] = {}
        self.cotes: dict[int, int] = {}
        self.listes: dict[tuple[int, int], list[int]] = {}
        self.slots: dict[tuple[int, int, int], int] = {}
        self.unites: dict[tuple[int, int], list[int]] = {}
        self._suivant = 0
        # La main du joueur suivi d'abord : identités 1..n, celles de la main de départ du graphe.
        for cote, cartes in ((joueur, main), (1 - joueur, main_adverse)):
            for code in cartes:
                self._liste(cote, LOCATION_HAND).append(self._nouvelle(code, cote))

    def _liste(self, cote: int, location: int) -> list[int]:
        return self.listes.setdefault((cote, location), [])

    def _nouvelle(self, code: int, cote: int) -> int:
        self._suivant += 1
        self.codes[self._suivant] = code
        self.cotes[self._suivant] = cote
        return self._suivant

    def cote(self, ident: int) -> int:
        return self.cotes.get(ident, 0)

    def identite(self, code: int, loc: M.Loc, retirer: bool = False) -> int:
        location, cote = loc.location, loc.controleur
        if location & LOCATION_OVERLAY:
            pile = self.unites.get((cote, loc.sequence), [])
            ident = self._extraire(pile, code, loc.position, retirer)
        elif location in LISTES:
            ident = self._extraire(self._liste(cote, location), code, loc.sequence, retirer)
        elif location in (LOCATION_MZONE, LOCATION_SZONE):
            cle = (cote, location, loc.sequence)
            ident = self.slots.get(cle)
            if ident is not None and self.codes[ident] != code:
                ident = None
            if ident is not None and retirer:
                del self.slots[cle]
        else:
            ident = None
        if ident is None:
            ident = self._nouvelle(code, cote)
            if not retirer and location in (LOCATION_MZONE, LOCATION_SZONE):
                self.slots[(cote, location, loc.sequence)] = ident
        return ident

    def _extraire(self, liste: list[int], code: int, index: int, retirer: bool) -> int | None:
        if not (0 <= index < len(liste) and self.codes[liste[index]] == code):
            index = next((i for i, ident in enumerate(liste) if self.codes[ident] == code), -1)
        if index < 0:
            return None
        return liste.pop(index) if retirer else liste[index]

    def deplacer(self, code: int, avant: M.Loc, apres: M.Loc) -> int:
        # Sans emplacement d'origine (Token créé), la carte appartient au côté où elle arrive.
        ident = self.identite(code, avant, retirer=True) if avant.location else self._nouvelle(code, apres.controleur)
        self.codes[ident] = code
        location, cote = apres.location, apres.controleur
        if location & LOCATION_OVERLAY:
            pile = self.unites.setdefault((cote, apres.sequence), [])
            pile.insert(min(apres.position, len(pile)), ident)
        elif location in LISTES:
            liste = self._liste(cote, location)
            liste.insert(min(apres.sequence, len(liste)), ident)
        elif location in (LOCATION_MZONE, LOCATION_SZONE):
            self.slots[(cote, location, apres.sequence)] = ident
        return ident

    def piocher(self, code: int, joueur: int = 0) -> int:
        ident = self._nouvelle(code, joueur)
        self._liste(joueur, LOCATION_HAND).append(ident)
        return ident

    def melanger_main(self, codes: list[int], joueur: int = 0):
        restants = list(self._liste(joueur, LOCATION_HAND))
        nouvelle = []
        for code in codes:
            index = next((i for i, ident in enumerate(restants) if self.codes[ident] == code), None)
            nouvelle.append(restants.pop(index) if index is not None else self._nouvelle(code, joueur))
        self.listes[(joueur, LOCATION_HAND)] = nouvelle + restants


@dataclass
class Participation:
    role: str
    ident: int
    code: int
    aleatoire: bool = False  # carte du Deck non choisie (dessus du Deck…)


@dataclass
class Resultat:
    ident: int
    code: int
    origine: int
    location: int
    raison: int = 0
    position: int = 0
    pioche: bool = False
    aleatoire: bool = False


@dataclass
class Action:
    cle: str
    genre: str  # "summon", "spsummon", "mset", "sset", "activate"
    code: int
    ident: int
    description: int = 0
    participations: list[Participation] = field(default_factory=list)
    resultats: list[Resultat] = field(default_factory=list)
    tokens: set[int] = field(default_factory=set)
    choix: list[int] = field(default_factory=list)  # index des réponses de sélection de cartes
    fin: int = 0  # réponses consommées au dernier événement rattaché à l'action
    ordinal: int = 0
    location: int = 0  # zone d'où l'effet est activé
    annule_un_maillon: bool = False  # sa résolution a annulé un autre maillon (Ash Blossom, Effect Veiler…)
    # Maillon précédent de la même chaîne, auquel cette activation répond.
    en_reponse_a: Action | None = field(default=None, repr=False, compare=False)


def extraire_actions(evenements: list[tuple[int, M.Message]], main: tuple[int, ...], reponses: list[bytes],
                     cles_forcees: dict[int, str] | None = None, joueur: int = 0,
                     main_adverse: tuple[int, ...] = ()) -> tuple[list[Action], SuiviCartes]:
    """Découpe le journal d'une ligne en actions de graphe.

    Les actions des deux joueurs sont gardées : celles de `joueur`, dont `main` est la main de
    départ, et celles de son adversaire, dont `main_adverse` est la main de départ.
    `cles_forcees` (ordinal → clé) greffe une action d'une ligne alternative sur
    l'action correspondante de la ligne principale.
    """
    suivi = SuiviCartes(main, joueur, main_adverse)
    actions: list[Action] = []
    procedure: Action | None = None
    chaine: dict[int, Action] = {}     # maillons de la chaîne en cours, par numéro
    activation: Action | None = None   # maillon en cours d'activation (coûts, cibles)
    resolution: Action | None = None   # maillon en cours de résolution
    en_resolution = 0                  # son numéro
    choisis_deck: dict[int, Counter] = {}  # cartes du Deck explicitement choisies, par action
    derniere_progressive: dict[int, int] = {}  # premier choix de la sélection progressive en cours, par action

    def nouvelle(genre: str, code: int, ident: int, fin: int, description: int = 0) -> Action:
        action = Action("", genre, code, ident, description, fin=fin, ordinal=len(actions))
        actions.append(action)
        return action

    def depuis_deck_aleatoire(action: Action, code: int) -> bool:
        reserve = choisis_deck.get(id(action), Counter())
        if reserve[code] > 0:
            reserve[code] -= 1
            return False
        return True

    for index, message in evenements:
        for courante in (procedure, activation, resolution):
            if courante is not None:
                courante.fin = max(courante.fin, index)
        t = message.type
        if t == -M.MSG_SELECT_IDLECMD:
            procedure = None
            commande = message.extra.get("commande")
            genre = {M.IDLE_SUMMON: "summon", M.IDLE_SPSUMMON: "spsummon", M.IDLE_MSET: "mset", M.IDLE_SSET: "sset"}.get(commande)
            if genre and message.cartes:
                carte = message.cartes[0]
                # Le marqueur précède la réponse qu'il décrit : elle fait partie de l'action.
                procedure = nouvelle(genre, carte.code, suivi.identite(carte.code, carte.loc), index + 1)
        elif -t in SELECTIONS_CARTES:
            courante = procedure or activation or resolution
            if courante is not None:
                courante.fin = max(courante.fin, index + 1)
                # Une sélection progressive (une carte par décision) ne se ramifie qu'à sa première étape.
                progressive = t == -M.MSG_SELECT_UNSELECT_CARD
                if not (progressive and courante.choix and courante.choix[-1] == derniere_progressive.get(id(courante))):
                    courante.choix.append(index)
                if progressive:
                    derniere_progressive[id(courante)] = courante.choix[-1]
                reserve = choisis_deck.setdefault(id(courante), Counter())
                for choisi in message.extra.get("choisis", []):
                    if 0 <= choisi < len(message.cartes) and message.cartes[choisi].loc.location == LOCATION_DECK:
                        reserve[message.cartes[choisi].code] += 1
        elif t == M.MSG_CHAINING:
            procedure = None
            carte, maillon = message.cartes[0], message.extra["maillon"]
            if maillon == 1:
                chaine.clear()  # nouvelle chaîne
            activation = nouvelle("activate", carte.code, suivi.identite(carte.code, carte.loc), index, carte.description)
            activation.location = carte.loc.location
            activation.en_reponse_a = chaine.get(maillon - 1)
            chaine[maillon] = activation
        elif t == M.MSG_CHAIN_SOLVING:
            activation = None
            en_resolution = message.extra["maillon"]
            resolution = chaine.get(en_resolution)
        elif t in (M.MSG_CHAIN_SOLVED, M.MSG_CHAIN_END):
            resolution = None
        elif t in (M.MSG_CHAIN_NEGATED, M.MSG_CHAIN_DISABLED) and resolution is not None:
            auteur = _auteur_annulation(chaine, message.extra["maillon"], en_resolution, suivi)
            if auteur is not None:
                auteur.annule_un_maillon = True
        elif t == M.MSG_BECOME_TARGET and activation:
            for carte in message.cartes:
                code = _code_a(suivi, carte.loc)
                if code:
                    activation.participations.append(Participation("REQUIREMENT", suivi.identite(code, carte.loc), code))
        elif t == M.MSG_DRAW:
            for carte in message.cartes:
                ident = suivi.piocher(carte.code, message.joueur)
                if resolution:
                    resolution.resultats.append(Resultat(ident, carte.code, LOCATION_DECK, LOCATION_HAND, pioche=True))
        elif t == M.MSG_SHUFFLE_HAND:
            suivi.melanger_main(message.extra["codes"], message.joueur)
        elif t == M.MSG_MOVE:
            avant, apres = message.cartes[0].loc, message.cartes[1].loc
            code, raison = message.cartes[0].code, message.extra["raison"]
            ident = suivi.deplacer(code, avant, apres)
            courante = procedure or activation or resolution
            if courante is None:
                continue
            aleatoire = avant.location == LOCATION_DECK and depuis_deck_aleatoire(courante, code)
            if raison & REASON_MATERIAL or (raison & REASON_RELEASE and courante is procedure):
                courante.participations.append(Participation("MATERIAL", ident, code, aleatoire))
            elif raison & REASON_COST:
                courante.participations.append(Participation("COST", ident, code, aleatoire))
            elif courante is resolution or (courante is procedure and ident == courante.ident) or (courante is procedure and courante.genre == "spsummon"):
                if not avant.location:
                    courante.tokens.add(ident)
                courante.resultats.append(Resultat(ident, code, avant.location, apres.location, raison, apres.position, aleatoire=aleatoire))
                if courante is procedure and courante.genre == "spsummon":
                    courante.ident = ident
        elif t in (M.MSG_SUMMONING, M.MSG_SPSUMMONING) and procedure:
            procedure = None
    # Deux lignes partagent une action seulement si tous ses choix sont identiques.
    for action in actions:
        forcee = (cles_forcees or {}).get(action.ordinal)
        action.cle = forcee or _hash(tuple(reponses[:action.fin]), action.ordinal, action.genre, action.code)
    return actions, suivi


def _auteur_annulation(chaine: dict[int, Action], annule: int, en_resolution: int, suivi: SuiviCartes) -> Action | None:
    """Maillon qui a annulé le maillon `annule` (activation ou effet), signalé pendant la résolution d'`en_resolution`.

    Ash Blossom annule pendant sa propre résolution : l'auteur est le maillon qui se résout. Effect Veiler annule
    d'avance les effets de sa cible ; le moteur ne le signale qu'à la résolution du maillon annulé, au nom de ce
    maillon : l'auteur est alors un maillon plus haut de la même chaîne, de l'autre joueur, qui a ciblé sa carte.
    """
    if annule != en_resolution:
        return chaine.get(en_resolution)
    victime = chaine.get(annule)
    if victime is None:
        return None
    return next((maillon for numero, maillon in sorted(chaine.items()) if numero > annule
                 and suivi.cote(maillon.ident) != suivi.cote(victime.ident)
                 and any(p.role == "REQUIREMENT" and p.ident == victime.ident for p in maillon.participations)), None)


def _code_a(suivi: SuiviCartes, loc: M.Loc) -> int:
    if loc.location in (LOCATION_MZONE, LOCATION_SZONE):
        ident = suivi.slots.get((loc.controleur, loc.location, loc.sequence))
        return suivi.codes[ident] if ident else 0
    if loc.location in LISTES:
        liste = suivi.listes.get((loc.controleur, loc.location), [])
        return suivi.codes[liste[loc.sequence]] if 0 <= loc.sequence < len(liste) else 0
    return 0


def cle_action(action: Action, base: CardDatabase) -> str:
    """Clé d'action Combo (catalogue `STEP_ACTIONS` de l'app) : la procédure d'une invocation, ce que fait un effet.

    Une Magie ou un Piège joué depuis la main ou le terrain est « activer une carte ». Tout autre effet (monstre,
    effet au Cimetière…) est nommé par le premier résultat de sa résolution (`cle_resultat`) ; sans résultat, il est
    « annuler un effet » s'il a annulé un autre maillon, sinon « effet » (activer un effet), comme hors table.
    """
    if action.genre == "activate":
        if base.type(action.code) & (TYPE_SPELL | TYPE_TRAP) and action.location in (LOCATION_HAND, LOCATION_SZONE):
            return "activer-carte"
        if action.resultats:
            return cle_resultat(action.resultats[0], base) or "effet"
        return "effet-negate" if action.annule_un_maillon else "effet"
    if action.genre == "summon":
        return "invocation-sacrifice" if any(p.role == "MATERIAL" for p in action.participations) else "invocation"
    if action.genre in ("mset", "sset"):
        return "poser-carte"
    return _cle_invocation(action.code, base)


def _cle_invocation(code: int, base: CardDatabase) -> str:
    """Invocation Link, Xyz, Synchro ou Fusion selon le type du monstre ; Invocation Spéciale sinon."""
    type_ = base.type(code)
    return next((cle for drapeau, cle in INVOCATIONS_EXTRA if type_ & drapeau), "invocation-spe")


def _zone_deplacement(location: int) -> str | None:
    """Zone d'un déplacement pour la table des clés ; None hors des zones (Token créé)."""
    return "materiau" if location & LOCATION_OVERLAY else ZONES_DEPLACEMENT.get(location)


def cle_resultat(resultat: Resultat, base: CardDatabase) -> str | None:
    """Clé d'action de ce que l'effet a fait de la carte, d'après la table du front ; None si elle n'y correspond pas."""
    origine, destination = _zone_deplacement(resultat.origine), _zone_deplacement(resultat.location)
    if resultat.pioche:
        return "piocher"
    if destination == "monstre" and (origine is None or base.type(resultat.code) & TYPE_TOKEN):
        return "invocation-token"
    if (origine, destination) == ("extra", "monstre"):
        return _cle_invocation(resultat.code, base)
    if destination == "cimetiere" and origine in ("main", "monstre", "magie-piege"):
        for raison, cle in ((REASON_DESTROY, "destruction"), (REASON_DISCARD, "defausser"), (REASON_RELEASE, "sacrifice")):
            if resultat.raison & raison:
                return cle
        return "cimetiere"
    if origine is None or origine == destination:
        return None
    return CLES_DEPLACEMENTS.get((origine, destination)) or CLES_DESTINATIONS.get(destination)


def libelle_resultat(resultat: Resultat, base: CardDatabase) -> str | None:
    """Décrit ce que l'effet a fait de la carte (affiché sur le lien de sortie)."""
    origine, destination = resultat.origine, resultat.location
    if resultat.pioche:
        return "Pioché"
    if destination == LOCATION_MZONE:
        if not origine:
            return "Token invoqué"
        mode = "Invocation Fusion" if base.type(resultat.code) & TYPE_FUSION and origine == LOCATION_EXTRA else "Invoqué"
        return f"{mode} depuis {LIEUX.get(origine, 'le terrain')}"
    if destination == LOCATION_HAND:
        if origine in (LOCATION_MZONE, LOCATION_SZONE):
            return "Renvoyé en main"
        return f"Ajouté depuis {LIEUX.get(origine, 'le Deck')}"
    if destination == LOCATION_GRAVE:
        if resultat.raison & REASON_DISCARD:
            return "Défaussé"
        if resultat.raison & REASON_DESTROY:
            return "Détruit"
        return "Envoyé au Cimetière" if origine != LOCATION_DECK else "Envoyé du Deck au Cimetière"
    if destination == LOCATION_REMOVED:
        return "Banni face verso" if resultat.position & POS_FACEDOWN else "Banni"
    if destination == LOCATION_SZONE:
        return "Placé sur le terrain"
    return None


@dataclass(frozen=True)
class Branchement:
    """Point de greffe d'une alternative : choix `index_reponse` dans l'action `ordinal`."""
    index_reponse: int
    ordinal: int
    cle_action: str
    libelle: str
    nom_parent: str


@dataclass
class Ligne:
    nom: str
    score: float | None  # None : la branche porte le nom seul (ligne jouée dans le simulateur)
    reponses: list[bytes]
    evenements: list[tuple[int, M.Message]]
    plateau: dict | None = None
    branchement: Branchement | None = None


@dataclass
class ConstructeurGraphe:
    base: CardDatabase
    references: set[str]
    main: tuple[int, ...]
    max_cartes: int
    max_liens: int
    noeuds: dict[str, dict] = field(default_factory=dict)
    liens: dict[str, dict] = field(default_factory=dict)
    branches: list[dict] = field(default_factory=list)
    zones: list[dict] = field(default_factory=list)
    numeros: dict[str, dict[int, int]] = field(default_factory=dict)
    rangees: int = 0
    rangee_libre: int = 0
    tronque: bool = False
    # Nœuds de sortie de chaque action d'une ligne principale, et candidats de chaque choix développé.
    resultats_action: dict[str, list[str]] = field(default_factory=dict)
    choix: dict[tuple[str, int], dict[str, list[str]]] = field(default_factory=dict)
    # Joueur suivi (`main`, `references`) ; les cartes de l'autre joueur (`main_adverse`, `references_adverses`)
    # deviennent des nœuds `adverse`.
    joueur: int = 0
    main_adverse: tuple[int, ...] = ()
    references_adverses: set[str] = field(default_factory=set)
    # Branches dont les étapes dépassent MAX_ETAPES_BRANCHE : seules les premières sont gardées.
    etapes_tronquees: list[str] = field(default_factory=list)
    # Actions laissées libres avant l'endboard pour les alternatives greffées (`/generer`) ; 0 sans alternative.
    reserve_alternatives: int = MAX_ACTIONS_ALTERNATIVE

    def __post_init__(self):
        for i, code in enumerate(self.main):
            self._noeud(("main", i + 1), code, i + 1, LOCATION_HAND, 0, i * (CARTE_H + 40))

    # --- Budget -----------------------------------------------------------------------

    def _etat(self) -> tuple:
        return (dict(self.noeuds), dict(self.liens), list(self.branches), list(self.zones),
                {k: dict(v) for k, v in self.numeros.items()}, self.rangees, self.rangee_libre,
                {k: list(v) for k, v in self.resultats_action.items()},
                {k: {r: list(ids) for r, ids in v.items()} for k, v in self.choix.items()})

    def _restaurer(self, etat: tuple):
        (self.noeuds, self.liens, self.branches, self.zones, self.numeros, self.rangees, self.rangee_libre,
         self.resultats_action, self.choix) = etat

    def _hors_budget(self) -> bool:
        return len(self.noeuds) > self.max_cartes or len(self.liens) > self.max_liens

    def presque_plein(self) -> bool:
        """Plus de place pour une alternative (au moins une carte et deux liens)."""
        return len(self.noeuds) >= self.max_cartes or len(self.liens) + 2 > self.max_liens or len(self.branches) >= MAX_BRANCHES

    # --- Éléments -----------------------------------------------------------------------

    def _noeud(self, cle, code: int, ident: int, location: int, x: float, y: float,
               pioche: bool = False, token: bool = False, description: str | None = None, adverse: bool = False) -> str:
        noeud_id = "n-" + _hash(cle)
        if noeud_id in self.noeuds:
            return noeud_id
        reference = str(code)
        noeud = {"id": noeud_id, "x": round(x, 1), "y": round(y, 1), "emplacement": _emplacement(location)}
        # Une carte de l'autre joueur vient de son deck ; ses exemplaires sont numérotés à part (le backend
        # distingue l'Ash adverse de la mienne), avec des identifiants d'instance qui ne se mélangent pas.
        references, portee = (self.references_adverses, "a-") if adverse else (self.references, "")
        if description or pioche or token or reference not in references:
            prefixe = "Pioche (aléatoire)" if pioche else "Token" if token else "Carte hors deck"
            noeud.update(referenceCarte="", estGenerique=True, description=(description or f"{prefixe} : {self.base.nom(code)}")[:500])
        else:
            numeros = self.numeros.setdefault(portee + reference, {})
            numero = numeros.setdefault(ident, len(numeros) + 1)
            noeud.update(referenceCarte=reference, instanceId=f"i-{portee}{reference}-{numero}", numeroInstance=numero)
        if adverse:
            noeud["adverse"] = True
        self.noeuds[noeud_id] = noeud
        return noeud_id

    def _adverse(self, suivi: SuiviCartes, ident: int) -> bool:
        return suivi.cote(ident) != self.joueur

    def _lien(self, lien: dict) -> bool:
        cle = (lien["source"], lien["cible"], lien.get("actionId"), lien.get("role"))
        lien_id = "l-" + _hash(cle)
        if lien_id in self.liens:
            return False
        self.liens[lien_id] = {"id": lien_id, "anime": False, **lien}
        return True

    # --- Lignes ---------------------------------------------------------------------------

    def reserver_rangees(self, nombre: int) -> int:
        """Réserve des rangées pour des alternatives ; renvoie la première."""
        premiere = self.rangees
        self.rangees += nombre
        return premiere

    def ajouter_ligne(self, ligne: Ligne, rangee: int | None = None) -> list[Action] | None:
        """Ajoute une ligne principale (tronquée si c'est la première) ou une alternative (entière ou rien).

        Une alternative est dessinée sur la `rangee` réservée par l'appelant. Renvoie les
        actions dessinées, ou None si la ligne ne tient pas dans le budget.
        """
        branchement = ligne.branchement
        cles = {branchement.ordinal: branchement.cle_action} if branchement else None
        actions, suivi = extraire_actions(ligne.evenements, self.main, ligne.reponses, cles, self.joueur, self.main_adverse)
        if branchement:
            actions = [a for a in actions if a.ordinal <= branchement.ordinal + MAX_ACTIONS_ALTERNATIVE]
        sauvegarde = self._etat()
        if rangee is None:
            # Une ligne principale démarre sous l'endboard de la précédente.
            self.rangees = max(self.rangees, self.rangee_libre)
            rangee = self.rangees
        courants: dict[int, str] = {ident: "n-" + _hash(("main", ident)) for ident in range(1, len(self.main) + 1)}
        porteurs: dict[int, str] = {}  # nœud qui porte chaque action dessinée (son entrée PRIMARY), par ordinal
        etapes: list[dict] = []
        y_base = rangee * HAUTEUR_RANGEE
        dessinees: list[Action] = []
        for action in actions:
            avant, courants_avant, etapes_avant = self._etat(), dict(courants), list(etapes)
            alternative = bool(branchement) and action.ordinal == branchement.ordinal
            sorties = self._emettre(action, y_base, courants, etapes, suivi, alternative, porteurs)
            if not branchement:
                self.resultats_action.setdefault(action.cle, [noeud for noeud, _ in sorties])
            elif alternative:
                self._noter_choix(branchement, sorties)
            if self._hors_budget():
                self._restaurer(avant)
                courants, etapes = courants_avant, etapes_avant
                if branchement or not dessinees or self.branches:
                    self._restaurer(sauvegarde)
                    return None
                self.tronque = True
                break
            dessinees.append(action)
        if not dessinees or len(self.branches) >= MAX_BRANCHES:
            self._restaurer(sauvegarde)
            return None
        if branchement:
            nom = f"{branchement.nom_parent} · {branchement.libelle}"
        else:
            nom = ligne.nom if ligne.score is None else f"{ligne.nom} ({ligne.score:g})"
        if len(etapes) > MAX_ETAPES_BRANCHE:
            self.etapes_tronquees.append(nom[:80])
        self.branches.append({"id": f"b-{len(self.branches) + 1}", "nom": nom[:80], "etapes": etapes[:MAX_ETAPES_BRANCHE]})
        if ligne.plateau and not self.tronque:
            # L'endboard décrit le plateau final : il serait faux pour une ligne tronquée.
            self.zones.append(self._endboard(ligne.plateau, len(self.branches), y_base, len(actions)))
            self.rangee_libre = rangee + -(-(ENDBOARD_H + 80) // HAUTEUR_RANGEE)
        if not branchement:
            self.rangees = rangee + 1
        return dessinees

    def _noter_choix(self, branchement: Branchement, sorties: list[tuple[str, bool]]):
        """Candidats d'un choix : la carte retenue par la ligne principale et chaque autre carte possible.

        La carte retenue est la sortie principale qui n'existe pas dans l'alternative ;
        les nouvelles sorties de l'alternative sont les autres candidats.
        """
        communes = {noeud for noeud, nouveau in sorties if not nouveau}
        candidats = self.choix.setdefault((branchement.cle_action, branchement.index_reponse), {"choisis": [], "autres": []})
        for noeud in self.resultats_action.get(branchement.cle_action, []):
            if noeud not in communes and noeud not in candidats["choisis"]:
                candidats["choisis"].append(noeud)
        candidats["autres"].extend(noeud for noeud, nouveau in sorties if nouveau and noeud not in candidats["autres"])

    def _emettre(self, action: Action, y_base: float, courants: dict[int, str], etapes: list[dict],
                 suivi: SuiviCartes, alternative: bool, porteurs: dict[int, str]) -> list[tuple[str, bool]]:
        """Dessine une action ; renvoie ses nœuds de sortie et s'ils viennent d'être créés."""
        action_id = "a-" + action.cle
        action_x = 260 + action.ordinal * PAS_ACTION
        action_y = y_base + (CARTE_H - ACTION_H) / 2
        participations = list(action.participations)
        principal = courants.get(action.ident)
        materiels = [p for p in participations if p.role == "MATERIAL" and not p.aleatoire]
        if principal is None and action.genre == "spsummon" and materiels:
            # Invocation depuis l'Extra Deck : le premier matériel porte l'action.
            premier = materiels[0]
            principal = self._source(premier.ident, courants, suivi, action_x, y_base, 0)
            participations.remove(premier)
        if principal is None:
            principal = self._noeud(("source", action.cle), action.code, action.ident,
                                    LOCATION_EXTRA if action.genre == "spsummon" else LOCATION_HAND, action_x - PAS_ACTION / 2, y_base,
                                    adverse=self._adverse(suivi, action.ident))
            # La carte garde ce nœud : défaussée en coût de son propre effet (Ash Blossom…), elle n'en crée pas un second.
            courants[action.ident] = principal
        porteurs[action.ordinal] = principal
        texte = self.base.texte_effet(action.description) if action.description else None
        self._lien({"source": principal, "cible": action_id, "sourceType": "noeud", "cibleType": "action",
                    "libelle": texte[:200] if texte else None, "actionKey": cle_action(action, self.base),
                    "actionId": action_id, "role": "PRIMARY", "inputRule": "ALL", "style": "plein",
                    "actionX": round(action_x, 1), "actionY": round(action_y, 1)})
        _etape(etapes, "carte", principal)
        _etape(etapes, "action", action_id)

        rang = 1
        # Cartes du Deck non choisies, regroupées par rôle et par côté.
        aleatoires: dict[tuple[str, bool], list[Participation]] = {}
        for participation in participations:
            if participation.aleatoire:
                aleatoires.setdefault((participation.role, self._adverse(suivi, participation.ident)), []).append(participation)
                continue
            if participation.ident == action.ident and participation.role != "COST":
                continue
            source = self._source(participation.ident, courants, suivi, action_x, y_base, rang)
            rang += 1
            if source != principal:
                self._lien_role(source, action_id, participation.role)
        for (role, adverse), groupe in aleatoires.items():
            source = self._groupe_aleatoire(_cle_cote(("aleatoire", action.cle, role), adverse), groupe,
                                            f"{LIBELLES_ROLES[role].lower()}", *_position_participant(action_x, y_base, rang),
                                            adverse=adverse)
            for participation in groupe:
                courants[participation.ident] = source
            rang += 1
            self._lien_role(source, action_id, role)
        self._lien_en_chaine(action, porteurs, principal, action_id)

        place = 0
        sorties: list[tuple[str, bool]] = []
        resultats_aleatoires: dict[tuple[int, bool], list[Resultat]] = {}
        for resultat in action.resultats:
            if resultat.location in (LOCATION_DECK, LOCATION_EXTRA):
                continue
            adverse = self._adverse(suivi, resultat.ident)
            if resultat.aleatoire and not resultat.pioche:
                resultats_aleatoires.setdefault((resultat.location, adverse), []).append(resultat)
                continue
            cle = ("resultat", action.cle, resultat.ident, resultat.code)
            nouveau = "n-" + _hash(cle) not in self.noeuds
            noeud = self._noeud(cle, resultat.code, resultat.ident,
                                resultat.location, action_x + ACTION_L + 60, y_base + place * PAS_RESULTAT, pioche=resultat.pioche,
                                token=resultat.ident in action.tokens or bool(self.base.type(resultat.code) & TYPE_TOKEN),
                                adverse=adverse)
            place += 1
            courants[resultat.ident] = noeud
            sorties.append((noeud, nouveau))
            self._lien_resultat(action_id, noeud, libelle_resultat(resultat, self.base), alternative)
            _etape(etapes, "carte", noeud)
        for (location, adverse), groupe in resultats_aleatoires.items():
            libelle = libelle_resultat(groupe[0], self.base) or "Déplacé"
            cle = _cle_cote(("resultat-aleatoire", action.cle, location), adverse)
            nouveau = "n-" + _hash(cle) not in self.noeuds
            noeud = self._groupe_aleatoire(cle, groupe, libelle.lower(),
                                           action_x + ACTION_L + 60, y_base + place * PAS_RESULTAT, location, adverse=adverse)
            place += 1
            for resultat in groupe:
                courants[resultat.ident] = noeud
            sorties.append((noeud, nouveau))
            self._lien_resultat(action_id, noeud, libelle, alternative)
            _etape(etapes, "carte", noeud)
        return sorties

    def _lien_role(self, source: str, action_id: str, role: str, libelle: str | None = None):
        # Pas de style imposé : le front dessine les rôles secondaires en pointillé.
        self._lien({"source": source, "cible": action_id, "sourceType": "noeud", "cibleType": "action",
                    "libelle": libelle or LIBELLES_ROLES.get(role), "actionKey": None, "actionId": action_id, "role": role,
                    "inputRule": None, "couleur": COULEURS_ROLES.get(role)})

    def _lien_en_chaine(self, action: Action, porteurs: dict[int, str], principal: str, action_id: str):
        """Activation en chaîne : une entrée REQUIREMENT « En chaîne » depuis la carte du maillon auquel elle répond.

        Le lien part du nœud qui porte l'activation de ce maillon, la carte telle qu'elle était quand on y a répondu.
        Une carte déjà ciblée par l'activation garde son seul lien « Cible ».
        """
        repondu = action.en_reponse_a
        if repondu is None or repondu.ident == action.ident:
            return
        source = porteurs.get(repondu.ordinal)
        cible = any(p.role == "REQUIREMENT" and p.ident == repondu.ident for p in action.participations)
        if source is not None and source != principal and not cible:
            self._lien_role(source, action_id, "REQUIREMENT", LIBELLE_EN_CHAINE)

    def _lien_resultat(self, action_id: str, noeud: str, libelle: str | None, alternative: bool):
        lien = {"source": action_id, "cible": noeud, "sourceType": "action", "cibleType": "noeud", "libelle": libelle,
                "actionKey": None, "actionId": action_id, "role": "RESULT", "inputRule": None, "style": "plein"}
        if alternative:
            # Sortie qui n'existe que si l'on choisit une autre carte : trait propre au choix.
            lien.update(style="pointille", libelle=f"Si choisi · {libelle}" if libelle else "Si choisi")
        self._lien(lien)

    def _groupe_aleatoire(self, cle, cartes: list, action_libelle: str, x: float, y: float, location: int = LOCATION_DECK,
                          adverse: bool = False) -> str:
        """Un seul nœud générique pour des cartes du Deck que le joueur ne choisit pas."""
        noms = ", ".join(self.base.nom(c.code) for c in cartes)
        pluriel = "s" if len(cartes) > 1 else ""
        description = f"{len(cartes)} carte{pluriel} aléatoire{pluriel} du Deck ({action_libelle}). Ici : {noms}"
        return self._noeud(cle, cartes[0].code, cartes[0].ident, location, x, y, description=description, adverse=adverse)

    def _source(self, ident: int, courants: dict[int, str], suivi: SuiviCartes, action_x: float, y_base: float, rang: int) -> str:
        if ident not in courants:
            courants[ident] = self._noeud(("participant", ident, suivi.codes[ident]), suivi.codes[ident], ident,
                                          LOCATION_DECK, *_position_participant(action_x, y_base, rang),
                                          adverse=self._adverse(suivi, ident))
        return courants[ident]

    def _compacter_rangees(self):
        """Supprime les rangées réservées restées vides (alternatives hors budget ou sans suite)."""
        def rangees(y: float, hauteur: float) -> range:
            return range(int(y // HAUTEUR_RANGEE), int((y + hauteur) // HAUTEUR_RANGEE) + 1)

        primaires = [l for l in self.liens.values() if l.get("role") == "PRIMARY" and l.get("actionY") is not None]
        occupees: set[int] = set()
        for noeud in self.noeuds.values():
            occupees.update(rangees(noeud["y"], CARTE_H))
        for lien in primaires:
            occupees.update(rangees(lien["actionY"], ACTION_H))
        for zone in self.zones:
            occupees.update(rangees(zone["y"], zone["hauteur"]))
        nouvelles = {ancienne: rang for rang, ancienne in enumerate(sorted(occupees))}

        def decaler(y: float) -> float:
            ancienne = int(y // HAUTEUR_RANGEE)
            return round(y + (nouvelles.get(ancienne, ancienne) - ancienne) * HAUTEUR_RANGEE, 1)

        for noeud in self.noeuds.values():
            noeud["y"] = decaler(noeud["y"])
        for lien in primaires:
            lien["actionY"] = decaler(lien["actionY"])
        for zone in self.zones:
            zone["y"] = decaler(zone["y"])

    def _zones_choix(self, deja_en_zone: set[str]) -> list[dict]:
        """Une zone `target` par choix développé : la carte retenue (contour vert) et les autres candidats.

        Chaque carte garde son propre trait depuis l'action et sa propre suite.
        """
        zones = []
        for (cle_action, index), candidats in self.choix.items():
            membres = [n for n in candidats["choisis"] + candidats["autres"] if n in self.noeuds and n not in deja_en_zone]
            # Le backend borne la hauteur d'une zone : les candidats les plus éloignés restent hors du cadre.
            haut_zone = min((self.noeuds[n]["y"] for n in membres), default=0)
            membres = [n for n in membres if self.noeuds[n]["y"] + CARTE_H - haut_zone <= HAUTEUR_MAX_ZONE - 2 * MARGE_ZONE]
            if len(membres) < 2:
                continue
            deja_en_zone.update(membres)
            for noeud in candidats["choisis"]:
                if noeud in membres:
                    self.noeuds[noeud]["couleurContour"] = COULEUR_CHOIX
            positions = [self.noeuds[n] for n in membres]
            gauche, haut = min(n["x"] for n in positions), min(n["y"] for n in positions)
            droite, bas = max(n["x"] for n in positions) + CARTE_L, max(n["y"] for n in positions) + CARTE_H
            zones.append({
                "id": "z-choix-" + _hash(cle_action, index), "type": "target",
                "x": gauche - MARGE_ZONE, "y": haut - MARGE_ZONE,
                "largeur": droite - gauche + 2 * MARGE_ZONE, "hauteur": bas - haut + 2 * MARGE_ZONE, "idsNoeuds": membres,
            })
        return zones

    def _endboard(self, plateau: dict, numero: int, y_base: float, nombre_actions: int) -> dict:
        cartes = []
        for sequence, (code, position) in enumerate(plateau[LOCATION_MZONE]):
            if code and str(code) in self.references and sequence < 7:
                emplacement = f"monstre-{sequence + 1}" if sequence < 5 else f"emz-{sequence - 4}"
                cartes.append((code, emplacement, position, 0))
        for sequence, (code, position) in enumerate(plateau[LOCATION_SZONE]):
            if code and str(code) in self.references and sequence < 6:
                emplacement = f"magie-piege-{sequence + 1}" if sequence < 5 else "terrain"
                cartes.append((code, emplacement, position, 0))
        ordre = 0
        for code, position in plateau[LOCATION_GRAVE]:
            if str(code) in self.references:
                cartes.append((code, "gy", POS_FACEUP_ATTACK, ordre))
                ordre += 1
        return {
            # À droite des alternatives possibles (au plus `reserve_alternatives` actions après un choix).
            "id": f"z-endboard-{numero}", "type": "endboard",
            "x": 260 + (nombre_actions + self.reserve_alternatives) * PAS_ACTION + 200, "y": y_base,
            "largeur": ENDBOARD_L, "hauteur": ENDBOARD_H, "idsNoeuds": [], "joueurs": 1,
            "cartesEndboard": [
                {"id": f"eb-{numero}-{i}", "referenceCarte": str(code), "joueur": 1, "emplacement": emplacement,
                 "position": "verso" if position & POS_FACEDOWN else "attaque" if position & POS_FACEUP_ATTACK else "defense", "ordre": ordre_}
                for i, (code, emplacement, position, ordre_) in enumerate(cartes)
            ],
        }

    def graphe(self, titre: str) -> dict:
        self._compacter_rangees()
        ids_main = ["n-" + _hash(("main", i + 1)) for i in range(len(self.main))]
        ids_main = [i for i in ids_main if i in self.noeuds]
        zones = list(self.zones)
        if ids_main:
            zones.insert(0, {"id": "z-main-depart", "type": "main-depart", "x": -40, "y": -60, "largeur": CARTE_L + 80,
                             "hauteur": len(ids_main) * (CARTE_H + 40) + 80, "idsNoeuds": ids_main})
        zones.extend(self._zones_choix(set(ids_main)))
        return {
            "id": "gen-" + _hash(titre, tuple(self.main), len(self.noeuds)),
            "titre": titre[:160], "noeuds": list(self.noeuds.values()), "liens": list(self.liens.values()),
            "zones": zones, "convergences": [], "alternatives": [], "dossiers": [], "branches": self.branches,
        }


def _position_participant(action_x: float, y_base: float, rang: int) -> tuple[float, float]:
    """Entrée sans nœud existant : à gauche pour la carte principale (rang 0), sous l'action sinon.

    Sous l'action, la carte reste dans la colonne de son action et ne déborde pas sur
    la colonne des sorties, où se trouvent les zones de choix.
    """
    if rang == 0:
        return action_x - PAS_ACTION / 2, y_base
    return action_x + 20, y_base + CARTE_H + 60 + (rang - 1) * PAS_RESULTAT


def _cle_cote(cle: tuple, adverse: bool) -> tuple:
    """Clé d'un nœud groupé : inchangée pour le joueur suivi (identifiants de `/generer` stables), marquée sinon."""
    return (*cle, "adverse") if adverse else cle


def _etape(etapes: list[dict], type_: str, element_id: str):
    if not any(e["type"] == type_ and e["id"] == element_id for e in etapes):
        etapes.append({"type": type_, "id": element_id})
