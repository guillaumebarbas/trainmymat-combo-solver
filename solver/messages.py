"""Décodage des messages ocgcore utiles à l'exploration.

Les formats suivent `playerop.cpp`, `field.cpp` et `processor.cpp` d'ocgcore.
Un `loc_info` vaut : u8 contrôleur, u8 location, u32 séquence, u32 position.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

MSG_RETRY = 1
MSG_HINT = 2
MSG_WIN = 5
MSG_SELECT_BATTLECMD = 10
MSG_SELECT_IDLECMD = 11
MSG_SELECT_EFFECTYN = 12
MSG_SELECT_YESNO = 13
MSG_SELECT_OPTION = 14
MSG_SELECT_CARD = 15
MSG_SELECT_CHAIN = 16
MSG_SELECT_PLACE = 18
MSG_SELECT_POSITION = 19
MSG_SELECT_TRIBUTE = 20
MSG_SORT_CHAIN = 21
MSG_SELECT_COUNTER = 22
MSG_SELECT_SUM = 23
MSG_SELECT_DISFIELD = 24
MSG_SORT_CARD = 25
MSG_SELECT_UNSELECT_CARD = 26
MSG_SHUFFLE_DECK = 32
MSG_SHUFFLE_HAND = 33
MSG_NEW_TURN = 40
MSG_NEW_PHASE = 41
MSG_MOVE = 50
MSG_POS_CHANGE = 53
MSG_SET = 54
MSG_SUMMONING = 60
MSG_SPSUMMONING = 62
MSG_FLIPSUMMONING = 64
MSG_CHAINING = 70
MSG_CHAIN_SOLVING = 72
MSG_CHAIN_SOLVED = 73
MSG_CHAIN_END = 74
MSG_CHAIN_NEGATED = 75
MSG_CHAIN_DISABLED = 76
MSG_BECOME_TARGET = 83
MSG_DRAW = 90
MSG_DAMAGE = 91
MSG_RECOVER = 92
MSG_EQUIP = 93
MSG_LPUPDATE = 94
MSG_PAY_LPCOST = 100
MSG_ROCK_PAPER_SCISSORS = 132
MSG_ANNOUNCE_RACE = 140
MSG_ANNOUNCE_ATTRIB = 141
MSG_ANNOUNCE_CARD = 142
MSG_ANNOUNCE_NUMBER = 143

DECISIONS = {
    MSG_SELECT_BATTLECMD, MSG_SELECT_IDLECMD, MSG_SELECT_EFFECTYN, MSG_SELECT_YESNO, MSG_SELECT_OPTION,
    MSG_SELECT_CARD, MSG_SELECT_CHAIN, MSG_SELECT_PLACE, MSG_SELECT_POSITION, MSG_SELECT_TRIBUTE,
    MSG_SORT_CHAIN, MSG_SELECT_COUNTER, MSG_SELECT_SUM, MSG_SELECT_DISFIELD, MSG_SORT_CARD,
    MSG_SELECT_UNSELECT_CARD, MSG_ROCK_PAPER_SCISSORS, MSG_ANNOUNCE_RACE, MSG_ANNOUNCE_ATTRIB,
    MSG_ANNOUNCE_CARD, MSG_ANNOUNCE_NUMBER,
}

HINT_SELECTMSG = 3

# Types de commande renvoyés à MSG_SELECT_IDLECMD : (index << 16) | type.
IDLE_SUMMON, IDLE_SPSUMMON, IDLE_REPOS, IDLE_MSET, IDLE_SSET, IDLE_ACTIVATE, IDLE_TO_BP, IDLE_TO_EP = range(8)


@dataclass(frozen=True)
class Loc:
    controleur: int
    location: int
    sequence: int
    position: int = 0


@dataclass(frozen=True)
class CarteChoix:
    code: int
    loc: Loc
    description: int = 0
    param: int = 0


@dataclass
class Message:
    type: int
    joueur: int = 0
    cartes: list[CarteChoix] = field(default_factory=list)
    extra: dict = field(default_factory=dict)


class Lecteur:
    def __init__(self, data: bytes):
        self.data, self.pos = data, 0

    def _lire(self, fmt: str):
        valeur = struct.unpack_from("<" + fmt, self.data, self.pos)[0]
        self.pos += struct.calcsize("<" + fmt)
        return valeur

    def u8(self): return self._lire("B")
    def u16(self): return self._lire("H")
    def u32(self): return self._lire("I")
    def i32(self): return self._lire("i")
    def u64(self): return self._lire("Q")

    def loc(self) -> Loc:
        return Loc(self.u8(), self.u8(), self.u32(), self.u32())

    def loc_courte(self, seq32: bool = True) -> Loc:
        return Loc(self.u8(), self.u8(), self.u32() if seq32 else self.u8())


def decoder(brut: bytes) -> Message:
    """Décode un message ; les types non utilisés ne gardent que leur identifiant."""
    lecteur = Lecteur(brut[1:])
    type_ = brut[0]
    message = Message(type_)
    try:
        _DECODEURS.get(type_, lambda m, l: None)(message, lecteur)
    except struct.error:
        message.extra["tronque"] = True
    return message


def _hint(m: Message, l: Lecteur):
    m.extra["type"], m.joueur, m.extra["donnee"] = l.u8(), l.u8(), l.u64()


def _new_turn(m: Message, l: Lecteur):
    m.joueur = l.u8()


def _new_phase(m: Message, l: Lecteur):
    m.extra["phase"] = l.u16()


def _lp(m: Message, l: Lecteur):
    m.joueur, m.extra["montant"] = l.u8(), l.u32()


def _win(m: Message, l: Lecteur):
    m.joueur, m.extra["raison"] = l.u8(), l.u8()


def _idle(m: Message, l: Lecteur):
    m.joueur = l.u8()
    groupes = {}
    for nom, seq32 in (("summon", True), ("spsummon", True), ("repos", False), ("mset", True), ("sset", True)):
        groupes[nom] = [CarteChoix(l.u32(), l.loc_courte(seq32)) for _ in range(l.u32())]
    groupes["activate"] = []
    for _ in range(l.u32()):
        code, loc = l.u32(), l.loc_courte()
        groupes["activate"].append(CarteChoix(code, loc, l.u64(), l.u8()))
    m.extra.update(groupes)
    m.extra["to_bp"], m.extra["to_ep"] = l.u8(), l.u8()


def _battle(m: Message, l: Lecteur):
    m.joueur = l.u8()


def _effectyn(m: Message, l: Lecteur):
    m.joueur = l.u8()
    code, loc = l.u32(), l.loc()
    m.cartes = [CarteChoix(code, loc, l.u64())]


def _yesno(m: Message, l: Lecteur):
    m.joueur = l.u8()
    m.extra["description"] = l.u64()


def _option(m: Message, l: Lecteur):
    m.joueur = l.u8()
    m.extra["options"] = [l.u64() for _ in range(l.u8())]


def _select_card(m: Message, l: Lecteur):
    m.joueur = l.u8()
    m.extra["annulable"] = l.u8()
    m.extra["min"], m.extra["max"] = l.u32(), l.u32()
    m.cartes = [CarteChoix(l.u32(), l.loc()) for _ in range(l.u32())]


def _tribute(m: Message, l: Lecteur):
    m.joueur = l.u8()
    m.extra["annulable"] = l.u8()
    m.extra["min"], m.extra["max"] = l.u32(), l.u32()
    cartes = []
    for _ in range(l.u32()):
        code, loc = l.u32(), l.loc_courte()
        cartes.append(CarteChoix(code, loc, param=l.u8()))
    m.cartes = cartes


def _unselect(m: Message, l: Lecteur):
    m.joueur = l.u8()
    m.extra["finissable"], m.extra["annulable"] = l.u8(), l.u8()
    m.extra["min"], m.extra["max"] = l.u32(), l.u32()
    m.cartes = [CarteChoix(l.u32(), l.loc()) for _ in range(l.u32())]
    m.extra["deselection"] = [CarteChoix(l.u32(), l.loc()) for _ in range(l.u32())]


def _chain(m: Message, l: Lecteur):
    m.joueur = l.u8()
    m.extra["speciaux"], m.extra["force"] = l.u8(), l.u8()
    l.u32(), l.u32()
    cartes = []
    for _ in range(l.u32()):
        code, loc = l.u32(), l.loc()
        cartes.append(CarteChoix(code, loc, l.u64(), l.u8()))
    m.cartes = cartes


def _place(m: Message, l: Lecteur):
    m.joueur = l.u8()
    m.extra["nombre"], m.extra["drapeau"] = l.u8(), l.u32()


def _position(m: Message, l: Lecteur):
    m.joueur = l.u8()
    m.extra["code"], m.extra["positions"] = l.u32(), l.u8()


def _sort(m: Message, l: Lecteur):
    m.joueur = l.u8()
    # Ici la location et la séquence sont sur 32 bits, sans position (playerop.cpp, SortCard).
    m.cartes = [CarteChoix(code, Loc(controleur, location, sequence))
                for code, controleur, location, sequence in ((l.u32(), l.u8(), l.u32(), l.u32()) for _ in range(l.u32()))]


def _counter(m: Message, l: Lecteur):
    m.joueur = l.u8()
    m.extra["type"], m.extra["nombre"] = l.u16(), l.u16()
    cartes = []
    for _ in range(l.u32()):
        code, loc = l.u32(), l.loc_courte(False)
        cartes.append(CarteChoix(code, loc, param=l.u16()))
    m.cartes = cartes


def _sum(m: Message, l: Lecteur):
    m.joueur = l.u8()
    m.extra["sans_max"] = l.u8()
    m.extra["cible"], m.extra["min"], m.extra["max"] = l.u32(), l.u32(), l.u32()
    obligatoires = []
    for _ in range(l.u32()):
        code, loc = l.u32(), l.loc()
        obligatoires.append(CarteChoix(code, loc, param=l.u32()))
    cartes = []
    for _ in range(l.u32()):
        code, loc = l.u32(), l.loc()
        cartes.append(CarteChoix(code, loc, param=l.u32()))
    m.extra["obligatoires"] = obligatoires
    m.cartes = cartes


def _announce(m: Message, l: Lecteur):
    m.joueur = l.u8()
    m.extra["nombre"] = l.u8()


def _announce_race(m: Message, l: Lecteur):
    _announce(m, l)
    m.extra["disponibles"] = l.u64()


def _announce_attrib(m: Message, l: Lecteur):
    _announce(m, l)
    m.extra["disponibles"] = l.u32()


def _announce_number(m: Message, l: Lecteur):
    m.joueur = l.u8()
    m.extra["options"] = [l.u64() for _ in range(l.u8())]


def _rps(m: Message, l: Lecteur):
    m.joueur = l.u8()


def _move(m: Message, l: Lecteur):
    code, avant, apres = l.u32(), l.loc(), l.loc()
    m.cartes = [CarteChoix(code, avant), CarteChoix(code, apres)]
    m.extra["raison"] = l.u32()


def _summoning(m: Message, l: Lecteur):
    m.cartes = [CarteChoix(l.u32(), l.loc())]


def _equip(m: Message, l: Lecteur):
    m.cartes = [CarteChoix(0, l.loc()), CarteChoix(0, l.loc())]


def _pos_change(m: Message, l: Lecteur):
    code, controleur, location, sequence = l.u32(), l.u8(), l.u8(), l.u8()
    m.extra["avant"], apres = l.u8(), l.u8()
    m.cartes = [CarteChoix(code, Loc(controleur, location, sequence, apres))]


def _shuffle_deck(m: Message, l: Lecteur):
    m.joueur = l.u8()


def _chaining(m: Message, l: Lecteur):
    code, loc = l.u32(), l.loc()
    l.u8(), l.u8(), l.u32()
    m.cartes = [CarteChoix(code, loc, l.u64())]
    m.extra["maillon"] = l.u32()


def _chain_count(m: Message, l: Lecteur):
    m.extra["maillon"] = l.u8()


def _target(m: Message, l: Lecteur):
    m.cartes = [CarteChoix(0, l.loc()) for _ in range(l.u32())]


def _draw(m: Message, l: Lecteur):
    m.joueur = l.u8()
    m.cartes = [CarteChoix(code, Loc(m.joueur, 0x02, 0, position)) for code, position in ((l.u32(), l.u32()) for _ in range(l.u32()))]


def _shuffle_hand(m: Message, l: Lecteur):
    m.joueur = l.u8()
    m.extra["codes"] = [l.u32() for _ in range(l.u32())]


_DECODEURS = {
    MSG_HINT: _hint,
    MSG_WIN: _win,
    MSG_NEW_TURN: _new_turn,
    MSG_NEW_PHASE: _new_phase,
    MSG_DAMAGE: _lp,
    MSG_RECOVER: _lp,
    MSG_LPUPDATE: _lp,
    MSG_PAY_LPCOST: _lp,
    MSG_SELECT_BATTLECMD: _battle,
    MSG_SELECT_IDLECMD: _idle,
    MSG_SELECT_EFFECTYN: _effectyn,
    MSG_SELECT_YESNO: _yesno,
    MSG_SELECT_OPTION: _option,
    MSG_SELECT_CARD: _select_card,
    MSG_SELECT_CHAIN: _chain,
    MSG_SELECT_PLACE: _place,
    MSG_SELECT_DISFIELD: _place,
    MSG_SELECT_POSITION: _position,
    MSG_SELECT_TRIBUTE: _tribute,
    MSG_SORT_CHAIN: _sort,
    MSG_SORT_CARD: _sort,
    MSG_SELECT_COUNTER: _counter,
    MSG_SELECT_SUM: _sum,
    MSG_SELECT_UNSELECT_CARD: _unselect,
    MSG_ROCK_PAPER_SCISSORS: _rps,
    MSG_ANNOUNCE_RACE: _announce_race,
    MSG_ANNOUNCE_ATTRIB: _announce_attrib,
    MSG_ANNOUNCE_CARD: _announce,
    MSG_ANNOUNCE_NUMBER: _announce_number,
    MSG_MOVE: _move,
    MSG_SET: _summoning,
    MSG_POS_CHANGE: _pos_change,
    MSG_EQUIP: _equip,
    MSG_SHUFFLE_DECK: _shuffle_deck,
    MSG_SUMMONING: _summoning,
    MSG_SPSUMMONING: _summoning,
    MSG_FLIPSUMMONING: _summoning,
    MSG_CHAINING: _chaining,
    MSG_CHAIN_SOLVING: _chain_count,
    MSG_CHAIN_SOLVED: _chain_count,
    MSG_CHAIN_NEGATED: _chain_count,
    MSG_CHAIN_DISABLED: _chain_count,
    MSG_BECOME_TARGET: _target,
    MSG_DRAW: _draw,
    MSG_SHUFFLE_HAND: _shuffle_hand,
}


# --- Réponses -----------------------------------------------------------------------

def rep_i32(valeur: int) -> bytes:
    return struct.pack("<i", valeur)


def rep_idle(type_: int, index: int) -> bytes:
    return rep_i32((index << 16) | type_)


def rep_cartes(indices: list[int]) -> bytes:
    return struct.pack("<iI", 0, len(indices)) + b"".join(struct.pack("<I", i) for i in indices)


def rep_unselect(index: int | None) -> bytes:
    return rep_i32(-1) if index is None else struct.pack("<ii", 1, index)
