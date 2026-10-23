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


@pytest.mark.skipif(not MOTEUR_DISPONIBLE, reason="Moteur non construit : lancer ./build_core.sh")
def test_simulation_renvoie_la_decision_courante_et_les_versions(client):
    reponse = client.post("/simulation/etat", json={**HERO, "choix": [{"indices": [0]}]}, headers=JETON)

    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["statut"] == "decision" and corps["choixAppliques"] == 1
    assert corps["decision"]["type"] == "zone" and corps["decision"]["empreinte"]
    assert corps["versions"]["ocgcore"] == client.get("/version").json()["ocgcore"]
