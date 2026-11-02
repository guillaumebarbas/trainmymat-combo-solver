import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from solver import messages as M  # noqa: E402
from solver.graphe import (  # noqa: E402
    Action, ConstructeurGraphe, Ligne, Resultat, SuiviCartes, cle_action, cle_resultat, extraire_actions,
)
from solver.ocg import (  # noqa: E402
    LOCATION_DECK, LOCATION_EXTRA, LOCATION_GRAVE, LOCATION_HAND, LOCATION_MZONE, LOCATION_OVERLAY, LOCATION_REMOVED,
    LOCATION_SZONE, POS_FACEUP_ATTACK, REASON_COST, REASON_DESTROY, REASON_DISCARD, REASON_RELEASE, TYPE_FUSION,
    TYPE_LINK, TYPE_MONSTER, TYPE_PENDULUM, TYPE_SPELL, TYPE_SYNCHRO, TYPE_TOKEN, TYPE_TRAP, TYPE_XYZ, VENDOR,
    lire_requete_location,
)
from solver.service import Requete, RequeteInvalide, valider  # noqa: E402
from test_graphe_simulation import verifier_contrat_backend  # noqa: E402

STRATOS, LIQUID, POLY, SHADOW_MIST, BLAZEMAN, MALICIOUS, MASK_CHANGE, NEOS, HONEST_NEOS = (
    40044918, 59392529, 24094653, 50720316, 63060238, 9411399, 21143940, 89943723, 14124483)
SUNRISE, DARK_LAW, LINKURIBOH = 22908820, 58481572, 41999284
DECK = (STRATOS, LIQUID, POLY, SHADOW_MIST, BLAZEMAN, MALICIOUS, LIQUID, SHADOW_MIST, POLY, MASK_CHANGE, NEOS, HONEST_NEOS)
EXTRA = (DARK_LAW, LINKURIBOH, SUNRISE)
MAIN = (STRATOS, LIQUID, POLY)
MOTEUR_DISPONIBLE = (VENDOR / "cdb" / "cards.cdb").is_file() and any(VENDOR.glob("libocgcore.*"))


def champ(drapeau: int, valeur: int) -> bytes:
    return struct.pack("<HII", 8, drapeau, valeur)


def test_lecture_requete_location_gere_emplacements_vides():
    fin = struct.pack("<HI", 4, 0x80000000)
    carte = champ(0x1, 1234) + champ(0x2, 0x8) + fin
    corps = struct.pack("<H", 0) + carte
    assert lire_requete_location(struct.pack("<I", len(corps)) + corps) == [(0, 0), (1234, 0x8)]


def test_suivi_cartes_renumerote_la_main_apres_un_retrait():
    suivi = SuiviCartes((STRATOS, LIQUID, POLY))
    liquid = suivi.identite(LIQUID, M.Loc(0, LOCATION_HAND, 1))
    suivi.deplacer(STRATOS, M.Loc(0, LOCATION_HAND, 0), M.Loc(0, LOCATION_MZONE, 2))
    # Liquid Soldier est passé en séquence 0 : son identité ne change pas.
    assert suivi.identite(LIQUID, M.Loc(0, LOCATION_HAND, 0)) == liquid
    assert suivi.deplacer(LIQUID, M.Loc(0, LOCATION_HAND, 0), M.Loc(0, LOCATION_GRAVE, 0)) == liquid
    assert suivi.identite(STRATOS, M.Loc(0, LOCATION_MZONE, 2)) == 1


def test_suivi_cartes_distingue_les_cartes_des_deux_joueurs():
    suivi = SuiviCartes((STRATOS,), joueur=1, main_adverse=(LIQUID,))
    stratos, liquid = suivi.identite(STRATOS, M.Loc(1, LOCATION_HAND, 0)), suivi.identite(LIQUID, M.Loc(0, LOCATION_HAND, 0))
    # La main du joueur suivi d'abord : ses cartes ont les premières identités.
    assert (stratos, liquid, suivi.cote(stratos), suivi.cote(liquid)) == (1, 2, 1, 0)
    # La même zone de monstre de chaque côté : deux cartes distinctes.
    suivi.deplacer(STRATOS, M.Loc(1, LOCATION_HAND, 0), M.Loc(1, LOCATION_MZONE, 2))
    suivi.deplacer(LIQUID, M.Loc(0, LOCATION_HAND, 0), M.Loc(0, LOCATION_MZONE, 2))
    assert suivi.identite(STRATOS, M.Loc(1, LOCATION_MZONE, 2)) == stratos
    assert suivi.identite(LIQUID, M.Loc(0, LOCATION_MZONE, 2)) == liquid
    # Un changement de contrôle garde l'identité et le côté du propriétaire.
    assert suivi.deplacer(LIQUID, M.Loc(0, LOCATION_MZONE, 2), M.Loc(1, LOCATION_MZONE, 3)) == liquid
    assert suivi.cote(liquid) == 0
    # Un Token arrive sans emplacement d'origine : il est du côté où il arrive.
    assert suivi.cote(suivi.deplacer(POLY, M.Loc(0, 0, 0), M.Loc(1, LOCATION_MZONE, 0))) == 1


# --- Clés d'action des effets (sans moteur) ---------------------------------------------------------------------

ASH_BLOSSOM, EFFECT_VEILER, CALLED_BY = 14558127, 97268402, 24224830
SYNCHRO, XYZ, PENDULE, TOKEN, PIEGE = 1001, 1002, 1003, 1004, 1005


class BaseFactice:
    """Base de cartes réduite aux types et aux noms : les clés d'action se testent sans le moteur."""

    TYPES = {POLY: TYPE_SPELL, MASK_CHANGE: TYPE_SPELL, PIEGE: TYPE_TRAP, SUNRISE: TYPE_MONSTER | TYPE_FUSION,
             SYNCHRO: TYPE_MONSTER | TYPE_SYNCHRO, XYZ: TYPE_MONSTER | TYPE_XYZ, LINKURIBOH: TYPE_MONSTER | TYPE_LINK,
             PENDULE: TYPE_MONSTER | TYPE_PENDULUM, TOKEN: TYPE_MONSTER | TYPE_TOKEN}

    def type(self, code: int) -> int:
        return self.TYPES.get(code, TYPE_MONSTER)

    def nom(self, code: int) -> str:
        return f"Carte {code}"

    def texte_effet(self, description: int) -> None:
        return None


BASE = BaseFactice()
MATERIAU = LOCATION_MZONE | LOCATION_OVERLAY


@pytest.mark.parametrize("origine, destination, code, raison, attendue", [
    (LOCATION_DECK, LOCATION_HAND, BLAZEMAN, 0, "tuto"),
    (LOCATION_DECK, LOCATION_MZONE, MALICIOUS, 0, "deck-to-field"),
    (LOCATION_DECK, LOCATION_GRAVE, BLAZEMAN, 0, "foolish"),
    (LOCATION_DECK, LOCATION_REMOVED, BLAZEMAN, 0, "ban"),
    (LOCATION_DECK, LOCATION_SZONE, POLY, 0, "poser-deck"),
    (LOCATION_GRAVE, LOCATION_HAND, BLAZEMAN, 0, "gy-to-main"),
    (LOCATION_REMOVED, LOCATION_HAND, BLAZEMAN, 0, "gy-to-main"),
    (LOCATION_GRAVE, LOCATION_MZONE, BLAZEMAN, 0, "gy-to-field"),
    (LOCATION_REMOVED, LOCATION_MZONE, BLAZEMAN, 0, "gy-to-field"),
    (LOCATION_GRAVE, LOCATION_SZONE, PIEGE, 0, "poser-gy"),
    (LOCATION_HAND, LOCATION_MZONE, BLAZEMAN, 0, "invocation-spe"),
    (LOCATION_HAND, LOCATION_SZONE, PIEGE, 0, "poser-carte"),
    (LOCATION_EXTRA, LOCATION_MZONE, SUNRISE, 0, "fusion"),
    (LOCATION_EXTRA, LOCATION_MZONE, SYNCHRO, 0, "synchro"),
    (LOCATION_EXTRA, LOCATION_MZONE, XYZ, 0, "xyz"),
    (LOCATION_EXTRA, LOCATION_MZONE, LINKURIBOH, 0, "link"),
    (LOCATION_EXTRA, LOCATION_MZONE, PENDULE, 0, "invocation-spe"),
    (0, LOCATION_MZONE, TOKEN, 0, "invocation-token"),
    (LOCATION_MZONE, LOCATION_HAND, BLAZEMAN, 0, "retour-main"),
    (LOCATION_SZONE, LOCATION_HAND, PIEGE, 0, "retour-main"),
    (LOCATION_MZONE, LOCATION_GRAVE, BLAZEMAN, REASON_DESTROY, "destruction"),
    (LOCATION_HAND, LOCATION_GRAVE, BLAZEMAN, REASON_DISCARD, "defausser"),
    (LOCATION_MZONE, LOCATION_GRAVE, BLAZEMAN, REASON_RELEASE, "sacrifice"),
    (LOCATION_SZONE, LOCATION_GRAVE, PIEGE, 0, "cimetiere"),
    (LOCATION_HAND, LOCATION_GRAVE, BLAZEMAN, 0, "cimetiere"),
    (LOCATION_MZONE, LOCATION_REMOVED, BLAZEMAN, 0, "ban"),
    (LOCATION_GRAVE, LOCATION_REMOVED, BLAZEMAN, 0, "ban"),
    (LOCATION_MZONE, LOCATION_DECK, BLAZEMAN, 0, "renvoyer-deck"),
    (LOCATION_HAND, LOCATION_DECK, BLAZEMAN, 0, "renvoyer-deck"),
    (LOCATION_MZONE, LOCATION_EXTRA, SUNRISE, 0, "renvoyer-extra"),
    (MATERIAU, LOCATION_GRAVE, BLAZEMAN, 0, "detacher-materiel"),
    (LOCATION_HAND, MATERIAU, BLAZEMAN, 0, "attacher-materiel"),
    # Rien dans la table : l'action reste « effet ».
    (LOCATION_EXTRA, LOCATION_GRAVE, SUNRISE, 0, None),
    (LOCATION_REMOVED, LOCATION_GRAVE, BLAZEMAN, 0, None),
    (LOCATION_MZONE, LOCATION_MZONE, BLAZEMAN, 0, None),
])
def test_ce_qu_un_effet_fait_d_une_carte_donne_la_cle_du_catalogue_de_l_app(origine, destination, code, raison, attendue):
    assert cle_resultat(Resultat(1, code, origine, destination, raison), BASE) == attendue


def test_une_pioche_par_effet_est_piocher():
    assert cle_resultat(Resultat(1, BLAZEMAN, LOCATION_DECK, LOCATION_HAND, pioche=True), BASE) == "piocher"


def effet(*resultats: Resultat, code: int = STRATOS, location: int = LOCATION_MZONE, annule: bool = False) -> Action:
    action = Action("a", "activate", code, 1, resultats=list(resultats), location=location)
    action.annule_un_maillon = annule
    return action


@pytest.mark.parametrize("action, attendue", [
    # Le premier résultat de la résolution, dans l'ordre chronologique, nomme l'effet.
    (effet(Resultat(2, BLAZEMAN, LOCATION_DECK, LOCATION_HAND, pioche=True),
           Resultat(2, BLAZEMAN, LOCATION_HAND, LOCATION_GRAVE, REASON_DISCARD)), "piocher"),
    (effet(Resultat(2, BLAZEMAN, LOCATION_DECK, LOCATION_HAND)), "tuto"),
    (effet(Resultat(2, SUNRISE, LOCATION_EXTRA, LOCATION_GRAVE),
           Resultat(3, BLAZEMAN, LOCATION_DECK, LOCATION_HAND)), "effet"),
    (effet(), "effet"),
    (effet(annule=True), "effet-negate"),
    # Avec un résultat, l'annulation ne change pas le nom.
    (effet(Resultat(2, BLAZEMAN, LOCATION_MZONE, LOCATION_GRAVE, REASON_DESTROY), annule=True), "destruction"),
    # Une Magie ou un Piège joué depuis la main ou le terrain reste « activer une carte ».
    (effet(Resultat(2, SUNRISE, LOCATION_EXTRA, LOCATION_MZONE), code=POLY, location=LOCATION_HAND), "activer-carte"),
    (effet(code=PIEGE, location=LOCATION_SZONE, annule=True), "activer-carte"),
    # L'effet d'une Magie depuis le Cimetière est nommé comme celui d'un monstre.
    (effet(Resultat(2, BLAZEMAN, LOCATION_DECK, LOCATION_HAND), code=POLY, location=LOCATION_GRAVE), "tuto"),
], ids=["premier-resultat", "recherche", "premier-resultat-hors-table", "sans-resultat", "annulation",
        "resultat-avant-annulation", "magie-jouee", "piege-joue", "magie-au-cimetiere"])
def test_un_effet_est_nomme_par_son_premier_resultat(action, attendue):
    assert cle_action(action, BASE) == attendue


def test_les_cles_des_procedures_ne_changent_pas():
    invocation = Action("a", "summon", STRATOS, 1)
    fusion = Action("b", "spsummon", SUNRISE, 2, resultats=[Resultat(2, SUNRISE, LOCATION_EXTRA, LOCATION_MZONE)])
    pose = Action("c", "sset", POLY, 3)
    assert [cle_action(a, BASE) for a in (invocation, fusion, pose)] == ["invocation", "fusion", "poser-carte"]


STRATOS_MAIN, STRATOS_TERRAIN = M.Loc(0, LOCATION_HAND, 0), M.Loc(0, LOCATION_MZONE, 2, POS_FACEUP_ATTACK)
MAIN_ADVERSE, CIMETIERE_ADVERSE = M.Loc(1, LOCATION_HAND, 0), M.Loc(1, LOCATION_GRAVE, 0)
REPONSES = [bytes([i]) for i in range(10)]


def deplacement(code: int, avant: M.Loc, apres: M.Loc, raison: int = 0) -> M.Message:
    return M.Message(M.MSG_MOVE, cartes=[M.CarteChoix(code, avant), M.CarteChoix(code, apres)], extra={"raison": raison})


def chainage(code: int, loc: M.Loc, maillon: int) -> M.Message:
    return M.Message(M.MSG_CHAINING, cartes=[M.CarteChoix(code, loc)], extra={"maillon": maillon})


def maillon(type_: int, numero: int) -> M.Message:
    return M.Message(type_, extra={"maillon": numero})


def stratos_puis_son_effet() -> list[tuple[int, M.Message]]:
    """Invocation Normale de Stratos, puis son effet déclencheur en maillon 1 (journal du moteur, simplifié)."""
    return [
        (0, M.Message(-M.MSG_SELECT_IDLECMD, cartes=[M.CarteChoix(STRATOS, STRATOS_MAIN)],
                      extra={"commande": M.IDLE_SUMMON})),
        (2, deplacement(STRATOS, STRATOS_MAIN, STRATOS_TERRAIN)),
        (2, M.Message(M.MSG_SUMMONING, cartes=[M.CarteChoix(STRATOS, STRATOS_TERRAIN)])),
        (3, chainage(STRATOS, STRATOS_TERRAIN, 1)),
    ]


def resolution(*evenements: M.Message) -> list[tuple[int, M.Message]]:
    return [(5, evenement) for evenement in evenements]


def journal_ash() -> list[tuple[int, M.Message]]:
    """Ash Blossom (adversaire) en maillon 2 : elle annule l'effet du maillon 1 pendant sa propre résolution."""
    return stratos_puis_son_effet() + resolution(
        chainage(ASH_BLOSSOM, MAIN_ADVERSE, 2),
        deplacement(ASH_BLOSSOM, MAIN_ADVERSE, CIMETIERE_ADVERSE, REASON_COST | REASON_DISCARD),
        maillon(M.MSG_CHAIN_SOLVING, 2), maillon(M.MSG_CHAIN_DISABLED, 1), maillon(M.MSG_CHAIN_SOLVED, 2),
        maillon(M.MSG_CHAIN_SOLVING, 1), maillon(M.MSG_CHAIN_SOLVED, 1), M.Message(M.MSG_CHAIN_END))


def journal_veiler(cible: bool = True) -> list[tuple[int, M.Message]]:
    """Effect Veiler (adversaire) en maillon 2 : le moteur ne signale l'annulation qu'à la résolution du maillon 1."""
    ciblage = [M.Message(M.MSG_BECOME_TARGET, cartes=[M.CarteChoix(0, STRATOS_TERRAIN)])] if cible else []
    return stratos_puis_son_effet() + resolution(
        chainage(EFFECT_VEILER, MAIN_ADVERSE, 2),
        deplacement(EFFECT_VEILER, MAIN_ADVERSE, CIMETIERE_ADVERSE, REASON_COST),
        *ciblage,
        maillon(M.MSG_CHAIN_SOLVING, 2), maillon(M.MSG_CHAIN_SOLVED, 2),
        maillon(M.MSG_CHAIN_SOLVING, 1), maillon(M.MSG_CHAIN_DISABLED, 1), maillon(M.MSG_CHAIN_SOLVED, 1),
        M.Message(M.MSG_CHAIN_END))


def cles(journal: list[tuple[int, M.Message]], main_adverse: tuple[int, ...]) -> list[str]:
    actions, _ = extraire_actions(journal, (STRATOS,), REPONSES, main_adverse=main_adverse)
    return [cle_action(action, BASE) for action in actions]


def test_ash_blossom_annule_un_autre_maillon_pendant_sa_resolution():
    assert cles(journal_ash(), (ASH_BLOSSOM,)) == ["invocation", "effet", "effet-negate"]


def test_effect_veiler_annule_d_avance_l_effet_de_la_carte_qu_il_cible():
    # L'annulation est signalée à la résolution du maillon 1 : elle revient au maillon adverse qui a ciblé sa carte.
    assert cles(journal_veiler(), (EFFECT_VEILER,)) == ["invocation", "effet", "effet-negate"]


def test_une_annulation_sans_auteur_identifiable_n_est_attribuee_a_personne():
    assert cles(journal_veiler(cible=False), (EFFECT_VEILER,)) == ["invocation", "effet", "effet"]


def test_le_ciblage_par_un_maillon_du_meme_joueur_n_explique_pas_une_annulation():
    # Called by the Grave ciblant son propre Stratos (maillon 2 du même joueur) n'est pas l'auteur de l'annulation.
    journal = stratos_puis_son_effet() + resolution(
        chainage(CALLED_BY, M.Loc(0, LOCATION_HAND, 0), 2),
        M.Message(M.MSG_BECOME_TARGET, cartes=[M.CarteChoix(0, STRATOS_TERRAIN)]),
        maillon(M.MSG_CHAIN_SOLVING, 2), maillon(M.MSG_CHAIN_SOLVED, 2),
        maillon(M.MSG_CHAIN_SOLVING, 1), maillon(M.MSG_CHAIN_DISABLED, 1), maillon(M.MSG_CHAIN_SOLVED, 1),
        M.Message(M.MSG_CHAIN_END))
    actions, _ = extraire_actions(journal, (STRATOS, CALLED_BY), REPONSES)
    assert [action.annule_un_maillon for action in actions] == [False, False, False]


def test_un_maillon_ne_repond_qu_aux_maillons_de_sa_propre_chaine():
    cibleur = 1006  # carte adverse qui cible Stratos sans rien annuler
    journal = stratos_puis_son_effet() + resolution(
        chainage(cibleur, MAIN_ADVERSE, 2),
        M.Message(M.MSG_BECOME_TARGET, cartes=[M.CarteChoix(0, STRATOS_TERRAIN)]),
        maillon(M.MSG_CHAIN_SOLVING, 2), maillon(M.MSG_CHAIN_SOLVED, 2),
        maillon(M.MSG_CHAIN_SOLVING, 1), maillon(M.MSG_CHAIN_SOLVED, 1), M.Message(M.MSG_CHAIN_END),
        # Nouvelle chaîne, d'un seul maillon : un autre effet de Stratos, annulé à sa résolution.
        chainage(STRATOS, STRATOS_TERRAIN, 1),
        maillon(M.MSG_CHAIN_SOLVING, 1), maillon(M.MSG_CHAIN_DISABLED, 1), maillon(M.MSG_CHAIN_SOLVED, 1),
        M.Message(M.MSG_CHAIN_END))

    actions, _ = extraire_actions(journal, (STRATOS,), REPONSES, main_adverse=(cibleur,))

    invocation, premier, ciblage, second = actions
    assert [a.en_reponse_a for a in (premier, ciblage, second)] == [None, premier, None]
    # Le maillon qui a ciblé Stratos dans la chaîne précédente n'est pour rien dans cette annulation.
    assert [a.annule_un_maillon for a in actions] == [False] * 4


def construire(journal: list[tuple[int, M.Message]], main_adverse: tuple[int, ...], max_liens: int = 300) -> dict:
    constructeur = ConstructeurGraphe(BASE, {str(STRATOS)}, (STRATOS,), 100, max_liens, main_adverse=main_adverse,
                                      references_adverses={str(code) for code in main_adverse}, reserve_alternatives=0)
    constructeur.ajouter_ligne(Ligne("Ligne", None, REPONSES, journal))
    graphe = constructeur.graphe("Combo")
    verifier_contrat_backend(graphe, {str(STRATOS)}, {str(code) for code in main_adverse})
    return graphe


def primaires(graphe: dict) -> list[dict]:
    return [lien for lien in graphe["liens"] if lien["role"] == "PRIMARY"]


def test_une_activation_en_chaine_part_aussi_de_la_carte_a_laquelle_elle_repond():
    graphe = construire(journal_ash(), (ASH_BLOSSOM,))

    invocation, effet_stratos, ash = primaires(graphe)
    entrees = [lien for lien in graphe["liens"] if lien["actionId"] == ash["actionId"] and lien["role"] != "PRIMARY"]
    # Depuis le nœud qui porte l'effet de Stratos (son entrée PRIMARY) : la carte à laquelle Ash Blossom répond.
    assert [(l["source"], l["sourceType"], l["cibleType"], l["role"], l["libelle"], l["couleur"], l["inputRule"])
            for l in entrees] == [(effet_stratos["source"], "noeud", "action", "REQUIREMENT", "En chaîne", "#F43F5E", None)]
    assert ash["actionKey"] == "effet-negate"


def test_une_carte_deja_ciblee_par_l_activation_en_chaine_n_a_que_son_lien_cible():
    graphe = construire(journal_veiler(), (EFFECT_VEILER,))

    invocation, effet_stratos, veiler = primaires(graphe)
    entrees = [lien for lien in graphe["liens"] if lien["actionId"] == veiler["actionId"] and lien["role"] != "PRIMARY"]
    assert [(l["source"], l["role"], l["libelle"]) for l in entrees] == [(effet_stratos["source"], "REQUIREMENT", "Cible")]
    assert veiler["actionKey"] == "effet-negate"


def test_une_carte_ciblee_n_a_pas_de_lien_en_chaine_meme_si_son_noeud_a_change():
    # L'effet de Stratos le renvoie en main : son nœud courant n'est plus celui qui porte son activation.
    journal = stratos_puis_son_effet() + resolution(
        chainage(EFFECT_VEILER, MAIN_ADVERSE, 2),
        M.Message(M.MSG_BECOME_TARGET, cartes=[M.CarteChoix(0, STRATOS_TERRAIN)]),
        maillon(M.MSG_CHAIN_SOLVING, 2), maillon(M.MSG_CHAIN_SOLVED, 2),
        maillon(M.MSG_CHAIN_SOLVING, 1), deplacement(STRATOS, STRATOS_TERRAIN, STRATOS_MAIN), maillon(M.MSG_CHAIN_SOLVED, 1),
        M.Message(M.MSG_CHAIN_END))

    graphe = construire(journal, (EFFECT_VEILER,))

    invocation, effet_stratos, veiler = primaires(graphe)
    assert [(l["role"], l["libelle"]) for l in graphe["liens"]
            if l["actionId"] == veiler["actionId"] and l["role"] != "PRIMARY"] == [("REQUIREMENT", "Cible")]


def test_le_lien_en_chaine_compte_dans_le_budget_de_liens():
    # 5 liens au complet : invocation (2), effet de Stratos (1), Ash Blossom (PRIMARY + « En chaîne »).
    assert len(construire(journal_ash(), (ASH_BLOSSOM,))["liens"]) == 5

    graphe = construire(journal_ash(), (ASH_BLOSSOM,), max_liens=4)

    assert len(graphe["liens"]) == 3 and not any(lien.get("libelle") == "En chaîne" for lien in graphe["liens"])


def test_validation_refuse_une_main_hors_du_deck():
    with pytest.raises(RequeteInvalide):
        valider(Requete("T", DECK, EXTRA, (STRATOS, SUNRISE)))


def test_validation_borne_les_limites_demandees():
    requete = valider(Requete("T", DECK, EXTRA, MAIN, max_cartes=10_000, max_liens=10_000, max_lignes=30))
    assert (requete.max_cartes, requete.max_liens, requete.max_lignes) == (100, 300, 5)


@pytest.fixture(scope="module")
def engine():
    from solver.ocg import Engine
    return Engine()


@pytest.mark.skipif(not MOTEUR_DISPONIBLE, reason="Moteur non construit : lancer ./build_core.sh")
def test_generation_produit_un_graphe_valide_et_borne(engine):
    from solver.service import generer
    resultat = generer(engine, Requete("HERO", DECK, EXTRA, MAIN, max_cartes=25, max_liens=75, max_parties=800))
    graphe = resultat["graphe"]
    assert 0 < len(graphe["noeuds"]) <= 25 and len(graphe["liens"]) <= 75
    assert resultat["stats"]["lignes"] >= 1

    references = {str(code) for code in DECK + EXTRA}
    noeuds = {n["id"]: n for n in graphe["noeuds"]}
    for noeud in noeuds.values():
        assert noeud.get("estGenerique") or noeud["referenceCarte"] in references
    actions = {}
    for lien in graphe["liens"]:
        actions.setdefault(lien["actionId"], []).append(lien)
        if lien["role"] == "RESULT":
            assert lien["sourceType"] == "action" and lien["cible"] in noeuds
        else:
            assert lien["cibleType"] == "action" and lien["source"] in noeuds
    for liens in actions.values():
        assert sum(1 for lien in liens if lien["role"] == "PRIMARY") == 1
    verifier_contrat_backend(graphe, references)

    # Stratos est invoqué, son effet nommé par ce qu'il fait (recherche dans le Deck) ; Polymerization est activée.
    cles = {lien["actionKey"] for lien in graphe["liens"] if lien["role"] == "PRIMARY"}
    assert {"invocation", "tuto", "activer-carte"} <= cles
    libelles = {lien.get("libelle") for lien in graphe["liens"] if lien["role"] == "RESULT"}
    assert {"Ajouté depuis le Deck", "Invocation Fusion depuis l'Extra Deck"} <= libelles
    zones = {zone["type"] for zone in graphe["zones"]}
    assert {"main-depart", "endboard"} <= zones
    for branche in graphe["branches"]:
        ids = [(e["type"], e["id"]) for e in branche["etapes"]]
        assert len(ids) == len(set(ids)) <= 100


@pytest.mark.skipif(not MOTEUR_DISPONIBLE, reason="Moteur non construit : lancer ./build_core.sh")
def test_arbre_des_choix_greffe_au_plus_cinq_candidats_avec_leur_suite(engine):
    from solver.graphe import MAX_ACTIONS_ALTERNATIVE
    from solver.service import generer
    resultat = generer(engine, Requete("HERO", DECK, EXTRA, MAIN, max_cartes=100, max_liens=300, max_parties=800))
    graphe = resultat["graphe"]
    verifier_contrat_backend(graphe, {str(code) for code in DECK + EXTRA})
    assert resultat["stats"]["alternatives"] >= 1
    noeuds = {n["id"]: n for n in graphe["noeuds"]}
    alternatives = [l for l in graphe["liens"] if l["role"] == "RESULT" and (l.get("libelle") or "").startswith("Si choisi")]
    assert alternatives and all(l["style"] == "pointille" for l in alternatives)
    # La recherche de Stratos : la carte retenue + au plus 4 autres candidats, chacun avec son trait.
    recherche = {l["actionId"] for l in alternatives if noeuds[l["cible"]]["referenceCarte"] == str(BLAZEMAN)}
    assert recherche
    action = recherche.pop()
    sorties = [l for l in graphe["liens"] if l["actionId"] == action and l["role"] == "RESULT"]
    assert 2 <= len(sorties) <= 5
    branches = [b for b in graphe["branches"] if "→" in b["nom"]]
    assert branches and len(graphe["branches"]) <= 30
    # Une alternative ne dessine que MAX_ACTIONS_ALTERNATIVE actions après le choix.
    for branche in branches:
        actions = [e for e in branche["etapes"] if e["type"] == "action"]
        principale = next(b for b in graphe["branches"] if b["nom"].startswith(branche["nom"].split(" · ")[0] + " ("))
        communes = [e for e in actions if e in principale["etapes"]]
        assert len(actions) - len(communes) <= MAX_ACTIONS_ALTERNATIVE


@pytest.mark.skipif(not MOTEUR_DISPONIBLE, reason="Moteur non construit : lancer ./build_core.sh")
def test_generation_respecte_un_budget_tres_serre(engine):
    from solver.service import generer
    resultat = generer(engine, Requete("HERO", DECK, EXTRA, MAIN, max_cartes=8, max_liens=16, max_parties=300))
    graphe = resultat["graphe"]
    assert len(graphe["noeuds"]) <= 8 and len(graphe["liens"]) <= 16


@pytest.mark.skipif(not MOTEUR_DISPONIBLE, reason="Moteur non construit : lancer ./build_core.sh")
def test_cartes_aleatoires_du_deck_regroupees_en_un_noeud(engine):
    """Junoldo bannit les 3 cartes du dessus du Deck : un seul nœud générique, pas trois cartes « choisies »."""
    from solver.service import generer
    medius, junoldo, jupredo, nervedo = 97556336, 10266279, 43871165, 17473466
    deck = (medius,) * 3 + (junoldo,) * 2 + (jupredo,) * 2 + (nervedo,) * 2 + (23829452, 12375297, 81650695) + (10045474,) * 3 + (48739627,) * 2
    extra = (5914858, 68231287, 53589300, 31822037, 70488851)
    graphe = generer(engine, Requete("Patron", deck, extra, (medius,), max_cartes=100, max_liens=300, max_parties=400))["graphe"]
    noeuds = {n["id"]: n for n in graphe["noeuds"]}
    couts = [l for l in graphe["liens"] if l["role"] == "COST"]
    assert couts
    for lien in couts:
        source = noeuds[lien["source"]]
        if source.get("emplacement") == "deck":
            assert source.get("estGenerique") and "aléatoire" in source["description"]
        assert lien["libelle"] == "Coût" and lien["couleur"] == "#F97316" and "style" not in lien
