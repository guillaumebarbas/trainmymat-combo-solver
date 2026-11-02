import logging
import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as service  # noqa: E402
from solver.ocg import VENDOR  # noqa: E402

JETON = {"X-Solver-Token": "secret"}
STRATOS, LIQUID, POLY = 40044918, 59392529, 24094653
HERO = {"deck": [STRATOS, LIQUID, POLY, 50720316, 63060238, 9411399], "extra": [22908820],
        "main": [STRATOS, LIQUID, POLY], "graine": "18446744073709551615"}
MOTEUR_DISPONIBLE = (VENDOR / "cdb" / "cards.cdb").is_file() and any(VENDOR.glob("libocgcore.*"))


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(service, "TOKEN", "secret")
    return TestClient(service.app)


def test_main_hors_deck_renvoie_400(client, monkeypatch):
    monkeypatch.setattr(service, "engine", lambda: None)
    reponse = client.post("/generer", json={"deck": [1, 2], "main": [3]}, headers=JETON)
    assert reponse.status_code == 400


def test_jeton_obligatoire_si_configure(client):
    reponse = client.post("/generer", json={"deck": [1], "main": [1]}, headers={"X-Solver-Token": "faux"})
    assert reponse.status_code == 401


def test_sans_jeton_configure_le_service_refuse_tout_calcul(monkeypatch):
    monkeypatch.setattr(service, "TOKEN", "")
    client = TestClient(service.app)
    assert client.post("/generer", json={"deck": [1], "main": [1]}).status_code == 503
    assert client.post("/simulation/etat", json=HERO).status_code == 503


def test_simulation_refuse_une_main_absente_du_deck(client):
    reponse = client.post("/simulation/etat", json={**HERO, "main": [STRATOS, 1234]}, headers=JETON)
    assert reponse.status_code == 400


@pytest.mark.parametrize("champ, valeur", [
    ("graine", "-1"), ("graine", "18446744073709551616"), ("graine", "abc"),
    ("main", []), ("deck", list(range(1, 62))), ("choix", [{"indices": [0]}] * 1501),
])
def test_simulation_borne_et_valide_ses_entrees(client, champ, valeur):
    reponse = client.post("/simulation/etat", json={**HERO, champ: valeur}, headers=JETON)
    assert reponse.status_code == 422


def test_version_indique_le_depot_public_et_le_commit_deploye(client, monkeypatch):
    monkeypatch.setenv("COMBO_SOLVER_VERSION", "8e2a04fc")

    versions = client.get("/version").json()

    assert versions["source"] == "https://github.com/guillaumebarbas/trainmymat-combo-solver"
    assert versions["service"] == "8e2a04fc"


def test_version_reprend_le_commit_que_la_plateforme_de_deploiement_injecte(client, monkeypatch):
    # Image construite sans argument COMBO_SOLVER_VERSION ; Coolify fournit SOURCE_COMMIT au démarrage.
    monkeypatch.setenv("COMBO_SOLVER_VERSION", "")
    monkeypatch.setenv("SOURCE_COMMIT", "5c9199cf061ea8c43f69e7bfaf9674c5947835ab")

    assert client.get("/version").json()["service"] == "5c9199cf061ea8c43f69e7bfaf9674c5947835ab"


@pytest.mark.skipif(not MOTEUR_DISPONIBLE, reason="Moteur non construit : lancer ./build_core.sh")
def test_simulation_renvoie_la_decision_courante_et_les_versions(client):
    reponse = client.post("/simulation/etat", json={**HERO, "choix": [{"indices": [0]}]}, headers=JETON)

    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["statut"] == "decision" and corps["choixAppliques"] == 1
    assert corps["decision"]["type"] == "zone" and corps["decision"]["empreinte"]
    assert corps["versions"]["ocgcore"] == client.get("/version").json()["ocgcore"]


@pytest.mark.skipif(not MOTEUR_DISPONIBLE, reason="Moteur non construit : lancer ./build_core.sh")
def test_le_journal_d_une_simulation_donne_sa_duree_et_l_attente_du_moteur(client, caplog):
    caplog.set_level(logging.INFO, logger="combo-solver")

    reponse = client.post("/simulation/etat", json={**HERO, "choix": [{"indices": [0]}]}, headers=JETON)

    assert reponse.status_code == 200
    lignes = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Simulation :")]
    assert len(lignes) == 1
    assert re.fullmatch(r"Simulation : 1 choix, statut decision, \d+ ms \(attente du moteur \d+ ms\)", lignes[0])


ASH_BLOSSOM, BLUE_EYES, SUNRISE = 14558127, 89631139, 22908820


def test_simulation_refuse_une_main_adverse_absente_du_deck_adverse(client):
    adversaire = {"deck": [ASH_BLOSSOM], "main": [ASH_BLOSSOM, BLUE_EYES]}

    reponse = client.post("/simulation/etat", json={**HERO, "adversaire": adversaire}, headers=JETON)

    assert reponse.status_code == 400


@pytest.mark.parametrize("adversaire", [
    {"deck": list(range(1, 62))}, {"extra": list(range(1, 17))}, {"deck": [BLUE_EYES] * 7, "main": [BLUE_EYES] * 7},
    {"deck": [0]}, {"main": [1 << 32]}, {"deck": "abc"},
], ids=["deck-61", "extra-16", "main-7", "code-nul", "code-64-bits", "pas-une-liste"])
def test_simulation_borne_et_valide_les_cartes_de_l_adversaire(client, adversaire):
    reponse = client.post("/simulation/etat", json={**HERO, "adversaire": adversaire}, headers=JETON)

    assert reponse.status_code == 422


@pytest.mark.skipif(not MOTEUR_DISPONIBLE, reason="Moteur non construit : lancer ./build_core.sh")
@pytest.mark.parametrize("adversaire", [None, {}, {"deck": [], "extra": [], "main": []}],
                         ids=["null", "objet-vide", "listes-vides"])
def test_simulation_sans_carte_adverse_repond_comme_sans_adversaire(client, adversaire):
    requete = {**HERO, "choix": [{"indices": [0]}]}

    sans = client.post("/simulation/etat", json=requete, headers=JETON)
    avec = client.post("/simulation/etat", json={**requete, "adversaire": adversaire}, headers=JETON)

    assert avec.status_code == 200 and avec.json() == sans.json()


@pytest.mark.skipif(not MOTEUR_DISPONIBLE, reason="Moteur non construit : lancer ./build_core.sh")
def test_simulation_place_les_cartes_de_l_adversaire(client):
    adversaire = {"deck": [ASH_BLOSSOM, BLUE_EYES, BLUE_EYES], "extra": [SUNRISE], "main": [ASH_BLOSSOM]}

    reponse = client.post("/simulation/etat", json={**HERO, "adversaire": adversaire}, headers=JETON)

    assert reponse.status_code == 200
    joueur = reponse.json()["plateau"]["joueurs"][1]
    assert (joueur["main"], joueur["deck"]) == ([{"code": ASH_BLOSSOM}], 2)
    assert [carte["code"] for carte in joueur["extra"]] == [SUNRISE]


# --- Plusieurs tours, graphe d'un arbre de simulation ------------------------------------------------------------

@pytest.mark.skipif(not MOTEUR_DISPONIBLE, reason="Moteur non construit : lancer ./build_core.sh")
def test_simulation_sur_un_tour_par_defaut_repond_comme_avant(client):
    requete = {**HERO, "choix": [{"indices": [0]}, {"indices": [0]}]}

    sans = client.post("/simulation/etat", json=requete, headers=JETON)
    avec = client.post("/simulation/etat", json={**requete, "tours": 1}, headers=JETON)

    assert avec.status_code == 200 and avec.json() == sans.json()


@pytest.mark.parametrize("tours", [0, 100, -1, "deux", 1.5])
def test_simulation_borne_le_nombre_de_tours(client, tours):
    reponse = client.post("/simulation/etat", json={**HERO, "tours": tours}, headers=JETON)

    assert reponse.status_code == 422


LIGNE = {"nom": "A", "choix": [{"indices": [0]}, {"indices": [0]}]}  # invocation normale de Stratos, puis sa zone
GRAPHE = {**HERO, "titre": "Combo HERO", "lignes": [LIGNE]}


def test_graphe_de_simulation_exige_le_jeton(client, monkeypatch):
    assert client.post("/simulation/graphe", json=GRAPHE, headers={"X-Solver-Token": "faux"}).status_code == 401
    monkeypatch.setattr(service, "TOKEN", "")
    assert client.post("/simulation/graphe", json=GRAPHE).status_code == 503


@pytest.mark.parametrize("champ, valeur", [
    ("lignes", []), ("lignes", [LIGNE] * 31), ("lignes", [{**LIGNE, "nom": ""}]), ("lignes", [{**LIGNE, "nom": "x" * 21}]),
    ("lignes", [{"choix": []}]), ("lignes", [{**LIGNE, "choix": [{"indices": [0]}] * 1501}]),
    ("tours", 0), ("tours", 100), ("joueur", -1), ("joueur", 2), ("titre", "x" * 161),
    ("limites", {"maxCartes": 0}), ("limites", {"maxLiens": 30_001}), ("graine", "abc"), ("main", []),
], ids=["sans-ligne", "31-lignes", "nom-vide", "nom-21", "sans-nom", "1501-choix", "tours-0", "tours-100", "joueur-moins-1",
        "joueur-2", "titre-161", "max-cartes-0", "max-liens", "graine", "main-vide"])
def test_graphe_de_simulation_borne_et_valide_ses_entrees(client, champ, valeur):
    reponse = client.post("/simulation/graphe", json={**GRAPHE, champ: valeur}, headers=JETON)

    assert reponse.status_code == 422


def test_graphe_de_simulation_refuse_de_suivre_le_joueur_1_sans_adversaire(client, monkeypatch):
    monkeypatch.setattr(service, "engine", lambda: None)

    reponse = client.post("/simulation/graphe", json={**GRAPHE, "joueur": 1}, headers=JETON)

    assert reponse.status_code == 400 and "Le joueur 1 n'a aucune carte" in reponse.json()["detail"]


@pytest.mark.skipif(not MOTEUR_DISPONIBLE, reason="Moteur non construit : lancer ./build_core.sh")
def test_graphe_de_simulation_renvoie_une_branche_par_ligne(client):
    lignes = [LIGNE, {"nom": "B", "choix": LIGNE["choix"] + [{"indices": [0]}]}]

    reponse = client.post("/simulation/graphe", json={**GRAPHE, "lignes": lignes, "limites": {"maxCartes": 50}},
                          headers=JETON)

    assert reponse.status_code == 200
    corps = reponse.json()
    assert set(corps) == {"graphe", "stats"}
    assert corps["graphe"]["titre"] == "Combo HERO"
    assert [branche["nom"] for branche in corps["graphe"]["branches"]] == ["A", "B"]
    assert corps["stats"]["lignes"] == 2 and corps["stats"]["lignesIgnorees"] == []


@pytest.mark.skipif(not MOTEUR_DISPONIBLE, reason="Moteur non construit : lancer ./build_core.sh")
def test_graphe_de_simulation_refuse_une_ligne_qui_ne_se_rejoue_pas(client):
    lignes = [LIGNE, {"nom": "B", "choix": [{"indices": [99]}]}]

    reponse = client.post("/simulation/graphe", json={**GRAPHE, "lignes": lignes}, headers=JETON)

    assert reponse.status_code == 400
    assert reponse.json()["detail"].startswith("Ligne « B », choix n° 1 : ")
