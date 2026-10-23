import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from solver import messages as M  # noqa: E402
from solver.graphe import SuiviCartes  # noqa: E402
from solver.ocg import LOCATION_GRAVE, LOCATION_HAND, LOCATION_MZONE, VENDOR, lire_requete_location  # noqa: E402
from solver.service import Requete, RequeteInvalide, valider  # noqa: E402

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

    # Stratos est invoqué ; ses effets et Polymerization sont des activations.
    cles = {lien["actionKey"] for lien in graphe["liens"] if lien["role"] == "PRIMARY"}
    assert {"invocation", "activer-carte"} <= cles
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
