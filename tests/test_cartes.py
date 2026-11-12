"""Données de cartes du moteur tenues à jour depuis Project Ignis, sans reconstruire l'image."""

import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from solver.cartes import DonneesCartes, MiseAJourImpossible, essayer_un_duel  # noqa: E402
from solver.ocg import VENDOR  # noqa: E402

MOTEUR_DISPONIBLE = (VENDOR / "cdb" / "cards.cdb").is_file() and any(VENDOR.glob("libocgcore.*"))
ANCIENS = {"scripts": "a" * 40, "cartes": "b" * 40, "chaines": "c" * 40}
NOUVEAUX = {"scripts": "d" * 40, "cartes": "e" * 40, "chaines": "f" * 40}
OCGCORE = "0" * 40


def image(tmp_path: Path, commits: dict) -> Path:
    """Données embarquées dans l'image, comme les écrit build_core.sh."""
    dossier = tmp_path / "vendor"
    dossier.mkdir()
    (dossier / "versions.json").write_text(json.dumps({"ocgcore": OCGCORE, **commits}))
    return dossier


def telechargement_note(appels: list):
    def telecharger(commits: dict, dossier: Path):
        appels.append(commits)
        (dossier / "scripts").mkdir()
        (dossier / "cdb").mkdir()
        (dossier / "strings.conf").write_text("!system 1 Normal Summon\n")
    return telecharger


def test_rien_n_est_telecharge_quand_les_donnees_sont_aux_derniers_commits(tmp_path):
    appels = []
    vendor = image(tmp_path, ANCIENS)
    donnees = DonneesCartes(vendor, tmp_path / "maj", derniers_commits=lambda: ANCIENS,
                            telecharger=telechargement_note(appels), essayer=lambda nouvelles, actives: None)

    resultat = donnees.mettre_a_jour()

    assert resultat["statut"] == "a-jour"
    assert appels == []
    assert donnees.actives().dossier == vendor


def test_les_nouvelles_donnees_passent_en_service_apres_un_duel_d_essai_reussi(tmp_path):
    vendor = image(tmp_path, ANCIENS)
    essais = []

    def essayer(nouvelles, actives):
        # L'essai a lieu sur les fichiers téléchargés, avant la mise en service.
        assert (nouvelles.dossier / "strings.conf").is_file()
        assert donnees.actives() == actives and actives.dossier == vendor
        essais.append(nouvelles.dossier)

    donnees = DonneesCartes(vendor, tmp_path / "maj", derniers_commits=lambda: NOUVEAUX,
                            telecharger=telechargement_note([]), essayer=essayer)

    resultat = donnees.mettre_a_jour()

    assert resultat["statut"] == "mise-a-jour"
    assert resultat["avant"] == {"ocgcore": OCGCORE, **ANCIENS}
    assert len(essais) == 1
    assert donnees.actives().dossier != vendor
    assert (donnees.actives().dossier / "strings.conf").is_file()
    assert donnees.versions() == {"ocgcore": OCGCORE, **NOUVEAUX}


def test_un_duel_d_essai_rate_garde_les_donnees_en_service(tmp_path):
    vendor = image(tmp_path, ANCIENS)

    def essayer(nouvelles, actives):
        raise RuntimeError("Script constant.lua introuvable ou invalide.")

    donnees = DonneesCartes(vendor, tmp_path / "maj", derniers_commits=lambda: NOUVEAUX,
                            telecharger=telechargement_note([]), essayer=essayer)

    with pytest.raises(MiseAJourImpossible, match="constant.lua"):
        donnees.mettre_a_jour()

    assert donnees.actives().dossier == vendor
    assert [p.name for p in (tmp_path / "maj").iterdir() if p.is_dir()] == []


def test_un_telechargement_rate_garde_les_donnees_en_service(tmp_path):
    vendor = image(tmp_path, ANCIENS)

    def telecharger(commits, dossier):
        (dossier / "scripts").mkdir()
        raise OSError("github.com injoignable")

    donnees = DonneesCartes(vendor, tmp_path / "maj", derniers_commits=lambda: NOUVEAUX,
                            telecharger=telecharger, essayer=lambda nouvelles, actives: None)

    with pytest.raises(MiseAJourImpossible, match="injoignable"):
        donnees.mettre_a_jour()

    assert donnees.actives().dossier == vendor
    assert [p.name for p in (tmp_path / "maj").iterdir() if p.is_dir()] == []


def test_des_depots_injoignables_gardent_les_donnees_en_service(tmp_path):
    vendor = image(tmp_path, ANCIENS)

    def derniers_commits():
        raise OSError("git ls-remote : délai dépassé")

    donnees = DonneesCartes(vendor, tmp_path / "maj", derniers_commits=derniers_commits,
                            telecharger=telechargement_note([]), essayer=lambda nouvelles, actives: None)

    with pytest.raises(MiseAJourImpossible, match="ls-remote"):
        donnees.mettre_a_jour()

    assert donnees.actives().dossier == vendor


def copie_du_vendor(avec_scripts: bool = True):
    """Téléchargement simulé par les données de l'image (liens), sans réseau."""
    def telecharger(commits: dict, dossier: Path):
        (dossier / "cdb").symlink_to(VENDOR / "cdb")
        (dossier / "strings.conf").symlink_to(VENDOR / "strings.conf")
        if avec_scripts:
            (dossier / "scripts").symlink_to(VENDOR / "scripts")
        else:
            (dossier / "scripts").mkdir()
    return telecharger


@pytest.mark.skipif(not MOTEUR_DISPONIBLE, reason="Moteur non construit : lancer ./build_core.sh")
def test_le_duel_d_essai_accepte_des_donnees_completes(tmp_path):
    donnees = DonneesCartes(image(tmp_path, ANCIENS), tmp_path / "maj", derniers_commits=lambda: NOUVEAUX,
                            telecharger=copie_du_vendor(), essayer=essayer_un_duel)

    assert donnees.mettre_a_jour()["statut"] == "mise-a-jour"


@pytest.mark.skipif(not MOTEUR_DISPONIBLE, reason="Moteur non construit : lancer ./build_core.sh")
def test_le_duel_d_essai_refuse_des_donnees_sans_scripts(tmp_path):
    vendor = image(tmp_path, ANCIENS)
    donnees = DonneesCartes(vendor, tmp_path / "maj", derniers_commits=lambda: NOUVEAUX,
                            telecharger=copie_du_vendor(avec_scripts=False), essayer=essayer_un_duel)

    with pytest.raises(MiseAJourImpossible):
        donnees.mettre_a_jour()

    assert donnees.actives().dossier == vendor


def test_le_duel_d_essai_attend_que_le_moteur_soit_libre(tmp_path):
    # Le moteur et ses callbacks ctypes ne supportent pas deux duels à la fois dans un processus.
    verrou = threading.Lock()
    pendant_l_essai = []
    donnees = DonneesCartes(image(tmp_path, ANCIENS), tmp_path / "maj", derniers_commits=lambda: NOUVEAUX,
                            telecharger=telechargement_note([]),
                            essayer=lambda nouvelles, actives: pendant_l_essai.append(verrou.locked()))

    donnees.mettre_a_jour(verrou_moteur=verrou)

    assert pendant_l_essai == [True]
    assert not verrou.locked()


def test_seules_les_donnees_en_service_et_les_precedentes_restent_sur_le_disque(tmp_path):
    # Un worker qui n'a pas encore rechargé son moteur finit sa requête sur les données précédentes.
    vendor = image(tmp_path, ANCIENS)
    commits = iter([{cle: chiffre * 40 for cle in ANCIENS} for chiffre in "123"])
    donnees = DonneesCartes(vendor, tmp_path / "maj", derniers_commits=lambda: next(commits),
                            telecharger=telechargement_note([]), essayer=lambda nouvelles, actives: None)

    precedentes = [donnees.mettre_a_jour() and donnees.actives().dossier for _ in range(3)]

    restants = sorted(p for p in (tmp_path / "maj").iterdir() if p.is_dir() and not p.is_symlink())
    assert restants == sorted(precedentes[1:])
    assert donnees.actives().dossier == precedentes[2]


def test_un_seul_worker_met_a_jour_a_la_fois(tmp_path):
    vendor = image(tmp_path, ANCIENS)
    en_telechargement, liberer = threading.Event(), threading.Event()
    appels = []

    def telecharger_lentement(commits, dossier):
        en_telechargement.set()
        liberer.wait(5)
        telechargement_note(appels)(commits, dossier)

    def worker(telecharger):
        # Chaque worker uvicorn est un processus avec ses propres objets ; seul le dossier est partagé.
        return DonneesCartes(vendor, tmp_path / "maj", derniers_commits=lambda: NOUVEAUX,
                             telecharger=telecharger, essayer=lambda nouvelles, actives: None)

    premier = threading.Thread(target=worker(telecharger_lentement).mettre_a_jour)
    premier.start()
    assert en_telechargement.wait(5)

    second = worker(telechargement_note(appels)).mettre_a_jour()

    liberer.set()
    premier.join(5)
    assert second["statut"] == "en-cours"
    assert appels == [NOUVEAUX]
    assert worker(telechargement_note(appels)).versions() == {"ocgcore": OCGCORE, **NOUVEAUX}
