"""Binding ctypes minimal du moteur de règles EDOPro (ocgcore, AGPL-3.0).

Le moteur n'expose aucun clonage d'état : une position de recherche est
reconstruite en rejouant la même suite de réponses depuis un duel neuf
(graine fixe, donc déterministe).
"""

from __future__ import annotations

import ctypes
import os
import sqlite3
import struct
import sys
from pathlib import Path

VENDOR = Path(os.environ.get("COMBO_SOLVER_VENDOR", Path(__file__).resolve().parent.parent / "vendor"))

# --- Constantes ocgapi_constants.h ------------------------------------------------
LOCATION_DECK = 0x01
LOCATION_HAND = 0x02
LOCATION_MZONE = 0x04
LOCATION_SZONE = 0x08
LOCATION_GRAVE = 0x10
LOCATION_REMOVED = 0x20
LOCATION_EXTRA = 0x40
LOCATION_OVERLAY = 0x80

POS_FACEUP_ATTACK = 0x1
POS_FACEDOWN_ATTACK = 0x2
POS_FACEUP_DEFENSE = 0x4
POS_FACEDOWN_DEFENSE = 0x8
POS_FACEDOWN = POS_FACEDOWN_ATTACK | POS_FACEDOWN_DEFENSE

TYPE_MONSTER = 0x1
TYPE_SPELL = 0x2
TYPE_TRAP = 0x4
TYPE_NORMAL = 0x10
TYPE_FUSION = 0x40
TYPE_RITUAL = 0x80
TYPE_SYNCHRO = 0x2000
TYPE_TOKEN = 0x4000
TYPE_XYZ = 0x800000
TYPE_PENDULUM = 0x1000000
TYPE_LINK = 0x4000000
TYPES_EXTRA = TYPE_FUSION | TYPE_SYNCHRO | TYPE_XYZ | TYPE_LINK

REASON_DESTROY = 0x1
REASON_RELEASE = 0x2
REASON_MATERIAL = 0x8
REASON_COST = 0x80
REASON_DISCARD = 0x4000

QUERY_CODE = 0x1
QUERY_POSITION = 0x2
QUERY_TYPE = 0x8
QUERY_LEVEL = 0x10
QUERY_RANK = 0x20
QUERY_ATTACK = 0x100
QUERY_DEFENSE = 0x200
QUERY_OVERLAY_CARD = 0x10000
QUERY_COUNTERS = 0x20000
QUERY_OWNER = 0x40000
QUERY_IS_PUBLIC = 0x100000
QUERY_LINK = 0x800000
QUERY_END = 0x80000000

DUEL_STATUS_END = 0
DUEL_STATUS_AWAITING = 1
DUEL_STATUS_CONTINUE = 2

DUEL_PSEUDO_SHUFFLE = 0x10
DUEL_MODE_MR5 = 0x800 | 0x2000 | 0x4000 | 0x8000 | 0x20000


class OCGCardData(ctypes.Structure):
    _fields_ = [
        ("code", ctypes.c_uint32),
        ("alias", ctypes.c_uint32),
        ("setcodes", ctypes.POINTER(ctypes.c_uint16)),
        ("type", ctypes.c_uint32),
        ("level", ctypes.c_uint32),
        ("attribute", ctypes.c_uint32),
        ("race", ctypes.c_uint64),
        ("attack", ctypes.c_int32),
        ("defense", ctypes.c_int32),
        ("lscale", ctypes.c_uint32),
        ("rscale", ctypes.c_uint32),
        ("link_marker", ctypes.c_uint32),
    ]


class OCGPlayer(ctypes.Structure):
    _fields_ = [("startingLP", ctypes.c_uint32), ("startingDrawCount", ctypes.c_uint32), ("drawCountPerTurn", ctypes.c_uint32)]


DataReader = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(OCGCardData))
DataReaderDone = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.POINTER(OCGCardData))
ScriptReader = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p)
LogHandler = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int)


class OCGDuelOptions(ctypes.Structure):
    _fields_ = [
        ("seed", ctypes.c_uint64 * 4),
        ("flags", ctypes.c_uint64),
        ("team1", OCGPlayer),
        ("team2", OCGPlayer),
        ("cardReader", DataReader),
        ("payload1", ctypes.c_void_p),
        ("scriptReader", ScriptReader),
        ("payload2", ctypes.c_void_p),
        ("logHandler", LogHandler),
        ("payload3", ctypes.c_void_p),
        ("cardReaderDone", DataReaderDone),
        ("payload4", ctypes.c_void_p),
        ("enableUnsafeLibraries", ctypes.c_uint8),
    ]


class OCGNewCardInfo(ctypes.Structure):
    _fields_ = [
        ("team", ctypes.c_uint8),
        ("duelist", ctypes.c_uint8),
        ("code", ctypes.c_uint32),
        ("con", ctypes.c_uint8),
        ("loc", ctypes.c_uint32),
        ("seq", ctypes.c_uint32),
        ("pos", ctypes.c_uint32),
    ]


class OCGQueryInfo(ctypes.Structure):
    _fields_ = [
        ("flags", ctypes.c_uint32),
        ("con", ctypes.c_uint8),
        ("loc", ctypes.c_uint32),
        ("seq", ctypes.c_uint32),
        ("overlay_seq", ctypes.c_uint32),
    ]


def _charger_bibliotheque() -> ctypes.CDLL:
    nom = "libocgcore.dylib" if sys.platform == "darwin" else "libocgcore.so"
    lib = ctypes.CDLL(str(VENDOR / nom))
    lib.OCG_CreateDuel.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(OCGDuelOptions)]
    lib.OCG_CreateDuel.restype = ctypes.c_int
    lib.OCG_DestroyDuel.argtypes = [ctypes.c_void_p]
    lib.OCG_DuelNewCard.argtypes = [ctypes.c_void_p, ctypes.POINTER(OCGNewCardInfo)]
    lib.OCG_StartDuel.argtypes = [ctypes.c_void_p]
    lib.OCG_DuelProcess.argtypes = [ctypes.c_void_p]
    lib.OCG_DuelProcess.restype = ctypes.c_int
    lib.OCG_DuelGetMessage.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    lib.OCG_DuelGetMessage.restype = ctypes.c_void_p
    lib.OCG_DuelSetResponse.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    lib.OCG_LoadScript.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32, ctypes.c_char_p]
    lib.OCG_LoadScript.restype = ctypes.c_int
    lib.OCG_DuelQueryLocation.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(OCGQueryInfo)]
    lib.OCG_DuelQueryLocation.restype = ctypes.c_void_p
    lib.OCG_DuelQueryField.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    lib.OCG_DuelQueryField.restype = ctypes.c_void_p
    return lib


class CardDatabase:
    """Lit les données de cartes BabelCDB et les textes d'effets (anglais).

    `cards.cdb` ne contient pas les sorties les plus récentes : elles sont dans `release-*.cdb`, puis
    `prerelease-*.cdb`. La première base qui connaît un code l'emporte.
    """

    def __init__(self, chemins: list[Path] | None = None):
        dossier = VENDOR / "cdb"
        self.chemins = chemins or [dossier / "cards.cdb", *sorted(dossier.glob("release-*.cdb")),
                                   *sorted(dossier.glob("prerelease-*.cdb"))]
        self._connexions = [sqlite3.connect(chemin, check_same_thread=False) for chemin in self.chemins]
        self._cache: dict[int, tuple | None] = {}
        self._textes: dict[int, tuple] = {}

    def donnees(self, code: int) -> tuple | None:
        if code not in self._cache:
            self._cache[code] = self._premiere(
                "select id, alias, setcode, type, atk, def, level, race, attribute from datas where id = ?", code)
        return self._cache[code]

    def _premiere(self, requete: str, code: int) -> tuple | None:
        for connexion in self._connexions:
            ligne = connexion.execute(requete, (code,)).fetchone()
            if ligne:
                return ligne
        return None

    def existe(self, code: int) -> bool:
        return self.donnees(code) is not None

    def type(self, code: int) -> int:
        ligne = self.donnees(code)
        return ligne[3] if ligne else 0

    def nom(self, code: int) -> str:
        return self._texte(code)[0] or str(code)

    def texte_effet(self, description: int) -> str | None:
        """Résout `aux.Stringid(code, index)` vers la chaîne strN de la carte."""
        code, index = description >> 20, description & 0xFFFFF
        if not code or index > 15:
            return None
        return self._texte(code)[2 + index] or None

    def _texte(self, code: int) -> tuple:
        if code not in self._textes:
            ligne = self._premiere("select * from texts where id = ?", code)
            self._textes[code] = ligne[1:] if ligne else (None,) * 18
        return self._textes[code]


class SystemStrings:
    """Textes système d'EDOPro (`strings.conf` de ProjectIgnis/Distribution, anglais)."""

    def __init__(self, chemin: Path | None = None):
        self.chemin = chemin or VENDOR / "strings.conf"
        self._systeme: dict[int, str] = {}
        if self.chemin.is_file():
            for ligne in self.chemin.read_text(encoding="utf-8").splitlines():
                parties = ligne.split(" ", 2)
                if len(parties) == 3 and parties[0] == "!system" and parties[1].isdigit():
                    self._systeme[int(parties[1])] = parties[2].strip()

    def systeme(self, identifiant: int) -> str | None:
        return self._systeme.get(identifiant)


class ScriptLibrary:
    """Résout le nom d'un script (cXXXX.lua, utility.lua…) vers son contenu."""

    DOSSIERS = ("", "official", "pre-release", "unofficial", "pre-errata")

    def __init__(self, racine: Path | None = None):
        self.racine = racine or VENDOR / "scripts"
        self._cache: dict[str, bytes | None] = {}

    def lire(self, nom: str) -> bytes | None:
        if nom not in self._cache:
            self._cache[nom] = None
            base = os.path.basename(nom)
            for dossier in self.DOSSIERS:
                chemin = self.racine / dossier / base
                if chemin.is_file():
                    self._cache[nom] = chemin.read_bytes()
                    break
        return self._cache[nom]


class Engine:
    """Ressources partagées entre duels : bibliothèque native, CDB et scripts."""

    def __init__(self, base: CardDatabase | None = None, scripts: ScriptLibrary | None = None):
        self.lib = _charger_bibliotheque()
        self.base = base or CardDatabase()
        self.scripts = scripts or ScriptLibrary()
        self.chaines = SystemStrings()
        self._setcodes: dict[int, ctypes.Array] = {}
        # Les callbacks doivent rester référencés tant que des duels existent.
        self._lecteur_cartes = DataReader(self._lire_carte)
        self._lecteur_cartes_fini = DataReaderDone(lambda payload, data: None)
        self._lecteur_scripts = ScriptReader(self._lire_script)
        self._journal = LogHandler(lambda payload, texte, type_: None)

    def _lire_carte(self, payload, code, data_ptr):
        ligne = self.base.donnees(code)
        data = data_ptr.contents
        data.code = code
        data.setcodes = self._codes_archetype(code, ligne[2] if ligne else 0)
        if not ligne:
            return
        _, alias, _, type_, atk, def_, level, race, attribute = ligne
        data.alias = alias
        data.type = type_
        data.level = level & 0xFF
        data.lscale = (level >> 24) & 0xFF
        data.rscale = (level >> 16) & 0xFF
        data.attribute = attribute
        data.race = race
        data.attack = atk
        data.defense = 0 if type_ & TYPE_LINK else def_
        data.link_marker = def_ if type_ & TYPE_LINK else 0

    def _codes_archetype(self, code: int, setcode: int):
        if code not in self._setcodes:
            valeurs = [v for v in ((setcode >> (16 * i)) & 0xFFFF for i in range(4)) if v] + [0]
            self._setcodes[code] = (ctypes.c_uint16 * len(valeurs))(*valeurs)
        return ctypes.cast(self._setcodes[code], ctypes.POINTER(ctypes.c_uint16))

    def _lire_script(self, payload, duel, nom):
        contenu = self.scripts.lire(nom.decode())
        if contenu is None:
            return 0
        return self.lib.OCG_LoadScript(duel, contenu, len(contenu), nom)

    def creer_duel(self, seed: int = 1, *, graines: tuple[int, int, int, int] | None = None,
                   flags: int = DUEL_MODE_MR5 | DUEL_PSEUDO_SHUFFLE) -> "Duel":
        options = OCGDuelOptions()
        options.seed[:] = list(graines) if graines else [seed, seed * 3 + 1, seed * 7 + 2, seed * 11 + 3]
        options.flags = flags
        for equipe in (options.team1, options.team2):
            equipe.startingLP, equipe.startingDrawCount, equipe.drawCountPerTurn = 8000, 0, 1
        options.cardReader = self._lecteur_cartes
        options.scriptReader = self._lecteur_scripts
        options.logHandler = self._journal
        options.cardReaderDone = self._lecteur_cartes_fini
        pointeur = ctypes.c_void_p()
        statut = self.lib.OCG_CreateDuel(ctypes.byref(pointeur), ctypes.byref(options))
        if statut != 0:
            raise RuntimeError(f"Création du duel impossible (statut {statut}).")
        duel = Duel(self, pointeur)
        for script in ("constant.lua", "utility.lua"):
            contenu = self.scripts.lire(script)
            if contenu is None or not self.lib.OCG_LoadScript(pointeur, contenu, len(contenu), script.encode()):
                duel.fermer()
                raise RuntimeError(f"Script {script} introuvable ou invalide.")
        return duel


class Duel:
    def __init__(self, engine: Engine, pointeur: ctypes.c_void_p):
        self.engine = engine
        self.lib = engine.lib
        self.pointeur = pointeur

    def ajouter_carte(self, code: int, joueur: int, location: int, sequence: int = 0, position: int = POS_FACEDOWN_DEFENSE):
        info = OCGNewCardInfo(team=joueur, duelist=0, code=code, con=joueur, loc=location, seq=sequence, pos=position)
        self.lib.OCG_DuelNewCard(self.pointeur, ctypes.byref(info))

    def demarrer(self):
        self.lib.OCG_StartDuel(self.pointeur)

    def avancer(self) -> tuple[int, list[bytes]]:
        """Fait tourner le moteur jusqu'à une attente ou la fin ; renvoie les messages."""
        messages: list[bytes] = []
        while True:
            statut = self.lib.OCG_DuelProcess(self.pointeur)
            messages.extend(self._messages())
            if statut != DUEL_STATUS_CONTINUE:
                return statut, messages

    def repondre(self, reponse: bytes):
        self.lib.OCG_DuelSetResponse(self.pointeur, reponse, len(reponse))

    def _messages(self) -> list[bytes]:
        longueur = ctypes.c_uint32()
        adresse = self.lib.OCG_DuelGetMessage(self.pointeur, ctypes.byref(longueur))
        brut = ctypes.string_at(adresse, longueur.value) if longueur.value else b""
        messages, curseur = [], 0
        while curseur + 4 <= len(brut):
            (taille,) = struct.unpack_from("<I", brut, curseur)
            curseur += 4
            messages.append(brut[curseur:curseur + taille])
            curseur += taille
        return messages

    def cartes(self, joueur: int, location: int) -> list[tuple[int, int]]:
        """Liste (code, position) d'une zone ; code 0 pour un emplacement vide."""
        return lire_requete_location(self._requete_location(joueur, location, QUERY_CODE | QUERY_POSITION))

    def details(self, joueur: int, location: int, flags: int) -> list[dict | None]:
        """Cartes d'une zone avec les champs demandés ; None pour un emplacement vide."""
        return lire_details_location(self._requete_location(joueur, location, flags))

    def terrain(self) -> dict:
        """Points de vie, nombre de cartes par zone et chaîne en cours (OCG_DuelQueryField)."""
        longueur = ctypes.c_uint32()
        adresse = self.lib.OCG_DuelQueryField(self.pointeur, ctypes.byref(longueur))
        return lire_terrain(ctypes.string_at(adresse, longueur.value))

    def _requete_location(self, joueur: int, location: int, flags: int) -> bytes:
        info = OCGQueryInfo(flags=flags, con=joueur, loc=location, seq=0, overlay_seq=0)
        longueur = ctypes.c_uint32()
        adresse = self.lib.OCG_DuelQueryLocation(self.pointeur, ctypes.byref(longueur), ctypes.byref(info))
        return ctypes.string_at(adresse, longueur.value) if longueur.value else b""

    def fermer(self):
        if self.pointeur:
            self.lib.OCG_DestroyDuel(self.pointeur)
            self.pointeur = None


def lire_requete_location(brut: bytes) -> list[tuple[int, int]]:
    """Décode le buffer de OCG_DuelQueryLocation (QUERY_CODE | QUERY_POSITION)."""
    # Format : u32 taille totale, puis par carte une suite de champs
    # [u16 taille][u32 drapeau][valeur] terminée par QUERY_END ; u16 0 = vide.
    cartes: list[tuple[int, int]] = []
    curseur = 4
    while curseur + 2 <= len(brut):
        (taille_champ,) = struct.unpack_from("<H", brut, curseur)
        if taille_champ == 0:
            cartes.append((0, 0))
            curseur += 2
            continue
        code = position = 0
        while curseur + 6 <= len(brut):
            taille_champ, drapeau = struct.unpack_from("<HI", brut, curseur)
            if drapeau == QUERY_CODE:
                (code,) = struct.unpack_from("<I", brut, curseur + 6)
            elif drapeau == QUERY_POSITION:
                (position,) = struct.unpack_from("<I", brut, curseur + 6)
            curseur += 2 + taille_champ
            if drapeau == QUERY_END:
                break
        cartes.append((code, position))
    return cartes


def lire_details_location(brut: bytes) -> list[dict | None]:
    """Décode OCG_DuelQueryLocation champ par champ (`card::get_infos` d'ocgcore)."""
    cartes: list[dict | None] = []
    curseur = 4
    while curseur + 2 <= len(brut):
        (taille,) = struct.unpack_from("<H", brut, curseur)
        if taille == 0:
            cartes.append(None)
            curseur += 2
            continue
        carte: dict = {}
        while curseur + 6 <= len(brut):
            taille, drapeau = struct.unpack_from("<HI", brut, curseur)
            valeur = brut[curseur + 6:curseur + 2 + taille]
            curseur += 2 + taille
            if drapeau == QUERY_END:
                break
            if drapeau in (QUERY_OVERLAY_CARD, QUERY_COUNTERS):
                (nombre,) = struct.unpack_from("<I", valeur)
                elements = struct.unpack_from(f"<{nombre}I", valeur, 4)
                if drapeau == QUERY_OVERLAY_CARD:
                    carte["materiaux"] = list(elements)
                else:
                    carte["compteurs"] = {e & 0xFFFF: e >> 16 for e in elements}
            elif drapeau == QUERY_LINK:
                carte["lien"], carte["fleches"] = struct.unpack_from("<II", valeur)
            elif drapeau in (QUERY_ATTACK, QUERY_DEFENSE):
                carte["atk" if drapeau == QUERY_ATTACK else "def"] = struct.unpack_from("<i", valeur)[0]
            elif drapeau in _CHAMPS_ENTIERS:
                carte[_CHAMPS_ENTIERS[drapeau]] = int.from_bytes(valeur, "little")
        cartes.append(carte)
    return cartes


_CHAMPS_ENTIERS = {
    QUERY_CODE: "code", QUERY_POSITION: "position", QUERY_TYPE: "type", QUERY_LEVEL: "niveau",
    QUERY_RANK: "rang", QUERY_OWNER: "proprietaire", QUERY_IS_PUBLIC: "public",
}


def lire_terrain(brut: bytes) -> dict:
    """Décode OCG_DuelQueryField (voir ocgapi.cpp)."""
    lecteur = _Curseur(brut)
    lecteur.lire("I")  # options du duel
    joueurs = []
    for _ in range(2):
        joueur = {"lp": lecteur.lire("I")}
        for _ in range(7 + 8):
            if lecteur.lire("B"):
                lecteur.lire("BI")
        joueur["deck"], joueur["main"], joueur["cimetiere"], joueur["bannis"], joueur["extra"], joueur["extra_recto"] = (
            lecteur.lire("6I"))
        joueurs.append(joueur)
    chaine = []
    for _ in range(lecteur.lire("I")):
        code, controleur, location, sequence, _position, _tc, _tl, _ts, description = lecteur.lire("IBBIIBBIQ")
        chaine.append({"code": code, "controleur": controleur, "location": location, "sequence": sequence,
                       "description": description})
    return {"joueurs": joueurs, "chaine": chaine}


class _Curseur:
    def __init__(self, brut: bytes):
        self.brut, self.position = brut, 0

    def lire(self, format_: str):
        valeurs = struct.unpack_from("<" + format_, self.brut, self.position)
        self.position += struct.calcsize("<" + format_)
        return valeurs if len(valeurs) > 1 else valeurs[0]
