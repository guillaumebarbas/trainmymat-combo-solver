"""Simulation interactive : le joueur choisit chaque action parmi les options légales du moteur."""

import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from solver.ocg import VENDOR  # noqa: E402
from solver.simulateur import (  # noqa: E402
    Adversaire, Choix, PartieSimulee, preparer, preparer_adversaire, simuler,
)

STRATOS, LIQUID, POLY, SHADOW_MIST, BLAZEMAN, MALICIOUS, MASK_CHANGE, NEOS, HONEST_NEOS = (
    40044918, 59392529, 24094653, 50720316, 63060238, 9411399, 21143940, 89943723, 14124483)
SUNRISE, DARK_LAW, LINKURIBOH = 22908820, 58481572, 41999284
DECK = (STRATOS, LIQUID, POLY, SHADOW_MIST, BLAZEMAN, MALICIOUS, LIQUID, SHADOW_MIST, POLY, MASK_CHANGE, NEOS, HONEST_NEOS)
EXTRA = (DARK_LAW, LINKURIBOH, SUNRISE)
MAIN = (STRATOS, LIQUID, POLY)
HERO = PartieSimulee(deck=DECK, extra=EXTRA, main=MAIN, graine=1)

MOTEUR_DISPONIBLE = (VENDOR / "cdb" / "cards.cdb").is_file() and any(VENDOR.glob("libocgcore.*"))
pytestmark = pytest.mark.skipif(not MOTEUR_DISPONIBLE, reason="Moteur non construit : lancer ./build_core.sh")


@pytest.fixture(scope="module")
def engine():
    from solver.ocg import Engine
    return Engine()


def test_la_premiere_decision_propose_les_actions_legales_de_la_main(engine):
    etat = simuler(engine, HERO, [])

    assert etat["statut"] == "decision"
    decision = etat["decision"]
    assert decision["type"] == "commande" and decision["joueur"] == 0
    proposees = {(o["action"], o["code"], o.get("effet")) for o in decision["options"]}
    assert {
        ("invocation-normale", STRATOS, None),
        ("invocation-normale", LIQUID, None),
        ("poser-monstre", STRATOS, None),
        ("poser-magie-piege", POLY, None),
        ("activation", POLY, "Fusion Summon"),
        ("fin-de-tour", None, None),
    } <= proposees
    # Premier tour du joueur qui commence : pas de Battle Phase.
    assert all(o["action"] != "phase-de-combat" for o in decision["options"])
    assert [o["index"] for o in decision["options"]] == list(range(len(decision["options"])))


def index_option(etat: dict, action: str, code: int | None = None, libelle: str | None = None, **criteres) -> int:
    for option in etat["decision"]["options"]:
        emplacement = option.get("emplacement") or {}
        if (option["action"] == action and (code is None or option["code"] == code)
                and (libelle is None or option.get("libelle") == libelle)
                and all(emplacement.get(cle) == valeur for cle, valeur in criteres.items())):
            return option["index"]
    raise AssertionError(f"Option {action} {code} {libelle} {criteres} absente : {etat['decision']}")


def test_une_invocation_normale_demande_une_zone_de_monstre_principale(engine):
    invoquer = index_option(simuler(engine, HERO, []), "invocation-normale", STRATOS)

    etat = simuler(engine, HERO, [Choix((invoquer,))])

    assert etat["statut"] == "decision" and etat["choixAppliques"] == 1
    assert etat["decision"]["type"] == "zone"
    zones = [o["emplacement"] for o in etat["decision"]["options"]]
    assert sorted((z["controleur"], z["zone"], z["sequence"]) for z in zones) == [(0, "monstre", s) for s in range(5)]


def test_le_monstre_invoque_apparait_dans_la_zone_choisie_avec_ses_statistiques(engine):
    invoquer = index_option(simuler(engine, HERO, []), "invocation-normale", STRATOS)
    zone_centrale = index_option(simuler(engine, HERO, [Choix((invoquer,))]), "zone", sequence=2)

    etat = simuler(engine, HERO, [Choix((invoquer,)), Choix((zone_centrale,))])

    joueur = etat["plateau"]["joueurs"][0]
    stratos = joueur["monstres"][2]
    assert (stratos["code"], stratos["position"], stratos["face"]) == (STRATOS, "attaque", "recto")
    assert (stratos["atk"], stratos["def"], stratos["niveau"]) == (1800, 300, 4)
    assert [carte for i, carte in enumerate(joueur["monstres"]) if i != 2] == [None] * 6
    assert sorted(carte["code"] for carte in joueur["main"]) == sorted((LIQUID, POLY))
    assert (joueur["lp"], joueur["deck"]) == (8000, len(DECK) - len(MAIN))
    assert {"type": "invocation-normale", "code": STRATOS, "apresChoix": 2, "emplacement": {
        "controleur": 0, "zone": "monstre", "sequence": 2, "position": "attaque", "face": "recto"}} in etat["evenements"]


def stratos_invoque(engine) -> list[Choix]:
    invoquer = index_option(simuler(engine, HERO, []), "invocation-normale", STRATOS)
    zone_centrale = index_option(simuler(engine, HERO, [Choix((invoquer,))]), "zone", sequence=2)
    return [Choix((invoquer,)), Choix((zone_centrale,))]


def test_l_effet_declencheur_est_propose_avec_une_question_lisible_puis_ses_effets(engine):
    choix = stratos_invoque(engine)

    question = simuler(engine, HERO, choix)["decision"]
    assert question["type"] == "oui-non"
    assert question["carte"] == {"code": STRATOS, "emplacement": {"controleur": 0, "zone": "monstre", "sequence": 2}}
    assert question["texte"] == 'Activate the Trigger Effect of "Elemental HERO Stratos" from [Monster Zone]?'

    effets = simuler(engine, HERO, choix + [Choix((index_option({"decision": question}, "oui"),))])["decision"]
    # Aucune Magie/Piège sur le terrain : seul l'effet de recherche est activable.
    assert effets["type"] == "option"
    assert [o["effet"] for o in effets["options"]] == ['Add 1 "HERO" monster from your Deck to your hand']


def test_la_recherche_propose_les_heros_du_deck_et_ajoute_le_choix_a_la_main(engine):
    choix = stratos_invoque(engine) + [Choix((0,)), Choix((0,))]  # oui, puis l'effet de recherche

    selection = simuler(engine, HERO, choix)["decision"]
    assert selection["type"] == "cartes" and (selection["min"], selection["max"]) == (1, 1)
    assert selection["texte"] == "Select the card(s) to add to your hand"
    assert {o["code"] for o in selection["options"]} == {SHADOW_MIST, BLAZEMAN, MALICIOUS, LIQUID, NEOS, HONEST_NEOS}
    assert {o["emplacement"]["zone"] for o in selection["options"]} == {"deck"}

    etat = simuler(engine, HERO, choix + [Choix((index_option({"decision": selection}, "carte", BLAZEMAN),))])

    assert BLAZEMAN in [carte["code"] for carte in etat["plateau"]["joueurs"][0]["main"]]
    ajout = next(e for e in etat["evenements"] if e["type"] == "deplacement" and e["code"] == BLAZEMAN)
    assert (ajout["de"]["zone"], ajout["vers"]["zone"], ajout["apresChoix"]) == ("deck", "main", 5)
    assert "effet" in ajout["raisons"]


def test_poser_un_monstre_est_journalise_face_verso_en_defense(engine):
    poser = index_option(simuler(engine, HERO, []), "poser-monstre", STRATOS)
    zone = index_option(simuler(engine, HERO, [Choix((poser,))]), "zone", sequence=1)

    etat = simuler(engine, HERO, [Choix((poser,)), Choix((zone,))])

    pose = next(e for e in etat["evenements"] if e["type"] == "pose")
    assert (pose["code"], pose["emplacement"]) == (STRATOS, {
        "controleur": 0, "zone": "monstre", "sequence": 1, "position": "defense", "face": "verso"})
    deplacement = next(e for e in etat["evenements"] if e["type"] == "deplacement" and e["code"] == STRATOS)
    assert deplacement["vers"]["face"] == "verso"


def test_un_deplacement_garde_le_code_de_raison_du_moteur(engine):
    choix = stratos_invoque(engine) + [Choix((0,)), Choix((0,))]
    blazeman = index_option(simuler(engine, HERO, choix), "carte", BLAZEMAN)

    etat = simuler(engine, HERO, choix + [Choix((blazeman,))])

    ajout = next(e for e in etat["evenements"] if e["type"] == "deplacement" and e["code"] == BLAZEMAN)
    assert ajout["raison"] & 0x40  # REASON_EFFECT


def test_une_recherche_melange_le_deck(engine):
    choix = stratos_invoque(engine) + [Choix((0,)), Choix((0,))]
    blazeman = index_option(simuler(engine, HERO, choix), "carte", BLAZEMAN)

    etat = simuler(engine, HERO, choix + [Choix((blazeman,))])

    assert {"type": "melange-deck", "joueur": 0, "apresChoix": 5} in etat["evenements"]


def test_la_cible_d_une_activation_est_journalisee(engine):
    partie = PartieSimulee(DECK, EXTRA, (SHADOW_MIST, MASK_CHANGE, POLY), graine=1)
    choix = [Choix((index_option(simuler(engine, partie, []), "invocation-normale", SHADOW_MIST),))]
    choix.append(Choix((index_option(simuler(engine, partie, choix), "zone", sequence=0),)))
    choix.append(Choix((index_option(simuler(engine, partie, choix), "activation", MASK_CHANGE),)))
    etat = simuler(engine, partie, choix)
    while etat["decision"]["type"] != "cartes":  # zone de Mask Change
        choix.append(Choix((0,)))
        etat = simuler(engine, partie, choix)
    choix.append(Choix((index_option(etat, "carte", SHADOW_MIST),)))

    ciblage = next(e for e in simuler(engine, partie, choix)["evenements"] if e["type"] == "ciblage")

    assert [(c["zone"], c["sequence"]) for c in ciblage["emplacements"]] == [("monstre", 0)]


AXE_OF_DESPAIR, BOOK_OF_MOON = 40619825, 14087893


def stratos_seul_sur_le_terrain(engine, partie) -> list[Choix]:
    """Stratos invoqué en zone 2, son effet déclencheur refusé : retour en Main Phase."""
    choix = [Choix((index_option(simuler(engine, partie, []), "invocation-normale", STRATOS),))]
    choix.append(Choix((index_option(simuler(engine, partie, choix), "zone", sequence=2),)))
    choix.append(Choix((index_option(simuler(engine, partie, choix), "non"),)))
    return choix


def test_une_carte_equipement_est_journalisee_et_modifie_l_atk(engine):
    partie = PartieSimulee(DECK + (AXE_OF_DESPAIR,), EXTRA, (STRATOS, AXE_OF_DESPAIR), graine=1)
    choix = stratos_seul_sur_le_terrain(engine, partie)
    choix.append(Choix((index_option(simuler(engine, partie, choix), "activation", AXE_OF_DESPAIR),)))
    etat = simuler(engine, partie, choix)
    while etat["statut"] == "decision" and etat["decision"]["type"] != "commande":  # zone, puis cible
        cible = [o["index"] for o in etat["decision"]["options"] if o["code"] == STRATOS]
        choix.append(Choix((cible[0] if cible else 0,)))
        etat = simuler(engine, partie, choix)

    equipement = next(e for e in etat["evenements"] if e["type"] == "equipement")
    assert (equipement["cible"]["zone"], equipement["cible"]["sequence"]) == ("monstre", 2)
    assert etat["plateau"]["joueurs"][0]["monstres"][2]["atk"] == 2800


def test_un_changement_de_position_est_journalise(engine):
    partie = PartieSimulee(DECK + (BOOK_OF_MOON,), EXTRA, (STRATOS, BOOK_OF_MOON), graine=1)
    choix = stratos_seul_sur_le_terrain(engine, partie)
    choix.append(Choix((index_option(simuler(engine, partie, choix), "activation", BOOK_OF_MOON),)))
    etat = simuler(engine, partie, choix)
    while etat["statut"] == "decision" and etat["decision"]["type"] != "commande":
        cible = [o["index"] for o in etat["decision"]["options"] if o["code"] == STRATOS]
        choix.append(Choix((cible[0] if cible else 0,)))
        etat = simuler(engine, partie, choix)

    changement = next(e for e in etat["evenements"] if e["type"] == "changement-position")
    assert changement["code"] == STRATOS
    assert (changement["emplacement"]["position"], changement["emplacement"]["face"]) == ("defense", "verso")
    assert etat["plateau"]["joueurs"][0]["monstres"][2]["face"] == "verso"


def test_la_chaine_en_cours_et_sa_resolution_sont_visibles(engine):
    choix = stratos_invoque(engine) + [Choix((0,)), Choix((0,))]

    pendant = simuler(engine, HERO, choix)
    assert pendant["plateau"]["chaine"] == [{
        "maillon": 1, "code": STRATOS, "effet": "Activate 1 of these effects",
        "emplacement": {"controleur": 0, "zone": "monstre", "sequence": 2}}]
    activation = next(e for e in pendant["evenements"] if e["type"] == "activation")
    assert (activation["code"], activation["maillon"], activation["effet"]) == (STRATOS, 1, "Activate 1 of these effects")

    blazeman = index_option(pendant, "carte", BLAZEMAN)
    apres = simuler(engine, HERO, choix + [Choix((blazeman,))])
    types = [e["type"] for e in apres["evenements"]]
    assert types.index("activation") < types.index("resolution") < types.index("fin-de-chaine")
    assert apres["plateau"]["chaine"] == []


def test_une_fenetre_de_chaine_propose_les_cartes_activables_et_de_passer(engine):
    # Shadow Mist (TÉNÈBRES) sur le terrain et Masked HERO Dark Law (TÉNÈBRES) en Extra : Mask Change est activable.
    partie = PartieSimulee(DECK, EXTRA, (SHADOW_MIST, MASK_CHANGE, POLY), graine=1)
    invoquer = index_option(simuler(engine, partie, []), "invocation-normale", SHADOW_MIST)
    zone = index_option(simuler(engine, partie, [Choix((invoquer,))]), "zone", sequence=0)

    fenetre = simuler(engine, partie, [Choix((invoquer,)), Choix((zone,))])["decision"]

    assert fenetre["type"] == "chaine"
    assert {(o["action"], o["code"]) for o in fenetre["options"]} == {("activation", MASK_CHANGE), ("passer", None)}
    # Magie Jeu-Rapide à chaîne libre : rien ne la déclenche, la fenêtre est « libre » (EDOPro la passe par défaut).
    assert fenetre["speciaux"] == 0


def fusion_sunrise_demarree(engine) -> list[Choix]:
    """Polymerization activée dans la 1re zone Magie/Piège, Elemental HERO Sunrise choisi."""
    choix = [Choix((index_option(simuler(engine, HERO, []), "activation", POLY),)), Choix((0,))]
    return choix + [Choix((index_option(simuler(engine, HERO, choix), "carte", SUNRISE),))]


def actions(decision: dict) -> set:
    return {(o["action"], o["code"]) for o in decision["options"]}


def test_les_materiaux_de_fusion_se_choisissent_carte_par_carte(engine):
    choix = fusion_sunrise_demarree(engine)

    materiaux = simuler(engine, HERO, choix)["decision"]
    assert materiaux["type"] == "carte-par-carte" and (materiaux["min"], materiaux["max"]) == (2, 2)
    assert actions(materiaux) == {("carte", STRATOS), ("carte", LIQUID), ("annuler", None)}

    choix.append(Choix((index_option({"decision": materiaux}, "carte", STRATOS),)))
    suite = simuler(engine, HERO, choix)["decision"]
    assert suite["type"] == "carte-par-carte"
    assert actions(suite) >= {("carte", LIQUID), ("retirer", STRATOS)}


def test_le_monstre_de_fusion_arrive_dans_la_position_choisie_avec_son_atk_courante(engine):
    choix = fusion_sunrise_demarree(engine)
    for materiau in (STRATOS, LIQUID):
        choix.append(Choix((index_option(simuler(engine, HERO, choix), "carte", materiau),)))
    zones = simuler(engine, HERO, choix)
    # Un monstre de l'Extra Deck peut aussi aller en Zone Monstre Extra (séquences 5 et 6).
    assert sorted(o["emplacement"]["sequence"] for o in zones["decision"]["options"]) == list(range(7))
    choix.append(Choix((index_option(zones, "zone", sequence=5),)))

    positions = simuler(engine, HERO, choix)["decision"]
    assert positions["type"] == "position"
    assert {(o["position"], o["face"]) for o in positions["options"]} == {("attaque", "recto"), ("defense", "recto")}
    choix.append(Choix((next(o["index"] for o in positions["options"] if o["position"] == "defense"),)))

    sunrise = simuler(engine, HERO, choix)["plateau"]["joueurs"][0]["monstres"][5]
    # 2500 ATK + 200 pour l'unique Attribut (LUMIÈRE) contrôlé.
    assert (sunrise["code"], sunrise["position"], sunrise["face"], sunrise["atk"], sunrise["def"]) == (
        SUNRISE, "defense", "recto", 2700, 1200)


GOBLINDBERGH, GAGAGA_MAGICIAN, GAGAGA_COWBOY = 25259669, 26082117, 12014404
GAGAGA = PartieSimulee(
    deck=(GOBLINDBERGH, GAGAGA_MAGICIAN, STRATOS, LIQUID), extra=(GAGAGA_COWBOY,), main=(GOBLINDBERGH, GAGAGA_MAGICIAN), graine=1,
)


def test_un_materiau_xyz_n_a_ni_position_ni_face(engine):
    """Sous un Xyz, le moteur range le rang du matériau dans le champ position : ce n'est pas une position de combat."""
    choix: list[Choix] = []

    def jouer(choisir) -> None:
        choix.append(Choix((choisir(simuler(engine, GAGAGA, choix)),)))

    def position(voulue: str):
        return lambda etat: next(o["index"] for o in etat["decision"]["options"] if o["position"] == voulue)

    jouer(lambda etat: index_option(etat, "invocation-normale", GOBLINDBERGH))
    jouer(lambda etat: index_option(etat, "zone"))
    jouer(lambda etat: index_option(etat, "oui"))
    jouer(lambda etat: index_option(etat, "carte", GAGAGA_MAGICIAN))
    jouer(lambda etat: index_option(etat, "zone"))
    jouer(position("attaque"))
    # Invocation Xyz : les deux matériaux passent sous Gagaga Cowboy, encore dans l'Extra Deck (zone « 192 »).
    jouer(lambda etat: index_option(etat, "invocation-speciale", GAGAGA_COWBOY))
    jouer(lambda etat: index_option(etat, "carte", GOBLINDBERGH))
    jouer(lambda etat: index_option(etat, "carte", GAGAGA_MAGICIAN))
    jouer(lambda etat: index_option(etat, "zone"))
    jouer(position("defense"))
    # Son effet détache le 2e matériau, qui part de sous lui sur le terrain (zone « 132 »).
    jouer(lambda etat: index_option(etat, "activation", GAGAGA_COWBOY))
    jouer(lambda etat: index_option(etat, "carte", GAGAGA_MAGICIAN))

    deplacements = [e for e in simuler(engine, GAGAGA, choix)["evenements"] if e["type"] == "deplacement"]
    sous_le_xyz = [e["vers"] for e in deplacements if e["vers"]["zone"] == "192"]
    sous_le_xyz += [e["de"] for e in deplacements if e["de"]["zone"] == "132"]
    assert len(sous_le_xyz) == 3
    assert all("position" not in emplacement and "face" not in emplacement for emplacement in sous_le_xyz)
    cowboy = next(e["vers"] for e in deplacements if e["code"] == GAGAGA_COWBOY)
    assert (cowboy["zone"], cowboy["position"], cowboy["face"]) == ("monstre", "defense", "recto")


@pytest.mark.parametrize("mauvais", [(99,), (), (0, 1), (-1,)], ids=["hors-limites", "aucun", "trop", "negatif"])
def test_un_choix_impossible_est_refuse_sans_toucher_aux_choix_precedents(engine, mauvais):
    invoquer = index_option(simuler(engine, HERO, []), "invocation-normale", STRATOS)

    etat = simuler(engine, HERO, [Choix((invoquer,)), Choix(mauvais)])

    assert etat["statut"] == "invalide" and etat["choixAppliques"] == 1
    assert etat["erreur"]
    assert etat["decision"]["type"] == "zone"


def test_finir_son_tour_termine_la_simulation_sur_le_terrain_final(engine):
    choix = stratos_invoque(engine)
    fin = index_option(simuler(engine, HERO, choix + [Choix((1,))]), "fin-de-tour")  # « non » à l'effet de Stratos

    etat = simuler(engine, HERO, choix + [Choix((1,)), Choix((fin,))])

    assert (etat["statut"], etat["raison"], etat["choixAppliques"]) == ("fin", "fin-de-tour", 4)
    assert "decision" not in etat
    assert etat["plateau"]["joueurs"][0]["monstres"][2]["code"] == STRATOS


POT_OF_GREED = 55144522


def main_apres_pot_of_greed(engine, graine: int) -> list[int]:
    partie = PartieSimulee(DECK + (POT_OF_GREED,), EXTRA, (POT_OF_GREED,), graine)
    choix = [Choix((index_option(simuler(engine, partie, []), "activation", POT_OF_GREED),)), Choix((0,))]
    etat = simuler(engine, partie, choix)
    assert etat["decision"]["type"] == "commande"
    return [carte["code"] for carte in etat["plateau"]["joueurs"][0]["main"]]


GARBAGE_GOLEM = 28968609  # présent seulement dans release-betb.cdb de BabelCDB


def test_une_carte_des_bases_de_sortie_recente_est_jouable(engine):
    partie = PartieSimulee(DECK + (GARBAGE_GOLEM,), EXTRA, (GARBAGE_GOLEM, POLY), graine=1)

    assert ("invocation-normale", GARBAGE_GOLEM) in actions(simuler(engine, partie, [])["decision"])


def test_on_peut_ne_recevoir_que_les_evenements_des_derniers_choix(engine):
    choix = stratos_invoque(engine)

    complet = simuler(engine, HERO, choix)["evenements"]
    recents = simuler(engine, HERO, choix, evenements_depuis=2)["evenements"]

    assert recents == [e for e in complet if e["apresChoix"] >= 2]
    assert recents and len(recents) < len(complet)


def test_les_cartes_de_l_extra_deck_indiquent_leur_valeur_lien_ou_leur_niveau(engine):
    # Le simulateur en déduit les matériels qui manquent pendant une Invocation Lien, Synchro ou Xyz.
    etat = simuler(engine, HERO, [])

    extra = {carte["code"]: carte for carte in etat["plateau"]["joueurs"][0]["extra"]}
    assert extra[LINKURIBOH]["lien"] == 1
    assert (extra[DARK_LAW]["niveau"], extra[SUNRISE]["niveau"]) == (6, 7)
    assert "fleches" not in extra[LINKURIBOH]


def test_le_plateau_indique_le_tour_et_la_phase_en_cours(engine):
    etat = simuler(engine, HERO, [])

    assert (etat["plateau"]["tour"], etat["plateau"]["phase"]) == (1, "main1")
    assert [e["phase"] for e in etat["evenements"] if e["type"] == "nouvelle-phase"] == ["pioche", "standby", "main1"]


def test_une_pioche_est_journalisee_avec_les_cartes_piochees(engine):
    partie = PartieSimulee(DECK + (POT_OF_GREED,), EXTRA, (POT_OF_GREED,), graine=2)
    choix = [Choix((index_option(simuler(engine, partie, []), "activation", POT_OF_GREED),)), Choix((0,))]

    etat = simuler(engine, partie, choix)

    pioche = next(e for e in etat["evenements"] if e["type"] == "pioche")
    assert pioche["joueur"] == 0
    assert sorted(pioche["codes"]) == sorted(c["code"] for c in etat["plateau"]["joueurs"][0]["main"])
    assert len(pioche["codes"]) == 2


def test_la_graine_fixe_l_ordre_du_deck(engine):
    pioches = {graine: main_apres_pot_of_greed(engine, graine) for graine in range(1, 6)}

    assert all(len(main) == 2 for main in pioches.values())
    assert main_apres_pot_of_greed(engine, 3) == pioches[3]
    assert len({tuple(sorted(main)) for main in pioches.values()}) > 1


def test_un_choix_enregistre_sur_une_autre_decision_est_signale_divergent(engine):
    premiere = simuler(engine, HERO, [])["decision"]
    invoquer = index_option({"decision": premiere}, "invocation-normale", STRATOS)

    meme = simuler(engine, HERO, [Choix((invoquer,), empreinte=premiere["empreinte"])])
    autre = simuler(engine, HERO, [Choix((invoquer,), empreinte="0" * 16)])

    assert meme["statut"] == "decision" and meme["decision"]["empreinte"] != premiere["empreinte"]
    assert simuler(engine, HERO, [])["decision"]["empreinte"] == premiere["empreinte"]
    assert (autre["statut"], autre["choixAppliques"]) == ("divergent", 0)
    assert autre["decision"]["empreinte"] == premiere["empreinte"]


CROSSOUT = 65681983  # Crossout Designator : déclarer le nom d'une carte de son Main Deck


def test_un_nom_declare_est_valide_par_le_moteur(engine):
    partie = PartieSimulee(DECK + (CROSSOUT,), EXTRA, (CROSSOUT, STRATOS, POLY), graine=1)
    choix = [Choix((index_option(simuler(engine, partie, []), "activation", CROSSOUT),)), Choix((0,))]
    assert simuler(engine, partie, choix)["decision"]["type"] == "annonce-carte"

    refus = simuler(engine, partie, choix + [Choix(valeur=SUNRISE)])  # dans l'Extra Deck, pas dans le Main Deck
    accepte = simuler(engine, partie, choix + [Choix(valeur=BLAZEMAN)])

    assert (refus["statut"], refus["choixAppliques"], refus["decision"]["type"]) == ("invalide", 2, "annonce-carte")
    assert refus["erreur"]
    # Nom accepté : à la résolution, on choisit l'exemplaire déclaré à bannir du Deck.
    assert accepte["decision"]["texte"] == "Select the card(s) to banish"
    assert actions(accepte["decision"]) == {("carte", BLAZEMAN)}


ABYSSAL_DESIGNATOR = 89801755  # coût : 1000 LP ; déclare 1 Attribut puis 1 Type


def test_declarer_un_attribut_et_un_type_apres_avoir_paye_des_lp(engine):
    partie = PartieSimulee(DECK + (ABYSSAL_DESIGNATOR,), EXTRA, (ABYSSAL_DESIGNATOR, STRATOS), graine=1)
    choix = [Choix((index_option(simuler(engine, partie, []), "activation", ABYSSAL_DESIGNATOR),)), Choix((0,))]

    attribut = simuler(engine, partie, choix)
    assert attribut["plateau"]["joueurs"][0]["lp"] == 7000
    assert {"type": "paiement-lp", "joueur": 0, "montant": 1000, "apresChoix": 2} in attribut["evenements"]
    assert attribut["decision"]["type"] == "annonce-attribut" and attribut["decision"]["max"] == 1
    assert [o["libelle"] for o in attribut["decision"]["options"]] == [
        "EARTH", "WATER", "FIRE", "WIND", "LIGHT", "DARK", "DIVINE"]

    choix.append(Choix((index_option(attribut, "attribut", libelle="DARK"),)))
    type_ = simuler(engine, partie, choix)
    assert type_["decision"]["type"] == "annonce-type"
    assert {"Warrior", "Dragon", "Cyberse"} <= {o["libelle"] for o in type_["decision"]["options"]}

    choix.append(Choix((index_option(type_, "type", libelle="Dragon"),)))
    assert simuler(engine, partie, choix)["statut"] == "decision"


CARD_ADVANCE = 52112003  # déclare N (1 à 5) puis réordonne les N cartes du dessus du Deck


def test_declarer_un_nombre_puis_ordonner_le_dessus_du_deck(engine):
    partie = PartieSimulee(DECK + (CARD_ADVANCE, POT_OF_GREED), EXTRA, (CARD_ADVANCE, POT_OF_GREED), graine=1)
    choix = [Choix((index_option(simuler(engine, partie, []), "activation", CARD_ADVANCE),)), Choix((0,))]

    nombre = simuler(engine, partie, choix)["decision"]
    assert nombre["type"] == "annonce-nombre"
    assert [o["valeur"] for o in nombre["options"]] == [1, 2, 3, 4, 5]
    choix.append(Choix((next(o["index"] for o in nombre["options"] if o["valeur"] == 3),)))

    ordre = simuler(engine, partie, choix)["decision"]
    assert ordre["type"] == "ordre" and (ordre["min"], ordre["max"], len(ordre["options"])) == (3, 3, 3)
    dessus = [ordre["options"][i]["code"] for i in (2, 1, 0)]  # du dessus vers le dessous
    choix.append(Choix((2, 1, 0)))

    choix.append(Choix((index_option(simuler(engine, partie, choix), "activation", POT_OF_GREED),)))
    choix.append(Choix((0,)))
    etat = simuler(engine, partie, choix)
    pioche = [e for e in etat["evenements"] if e["type"] == "pioche"][-1]
    assert sorted(pioche["codes"]) == sorted(dessus[:2])


BLACK_LUSTER_SOLDIER, BLACK_LUSTER_RITUAL = 5405694, 55761792  # Rituel : Niveaux sacrifiés ≥ 8


def test_les_sacrifices_d_un_rituel_se_choisissent_par_somme_de_niveaux(engine):
    main = (BLACK_LUSTER_RITUAL, BLACK_LUSTER_SOLDIER, STRATOS, NEOS)
    partie = PartieSimulee(DECK + (BLACK_LUSTER_RITUAL, BLACK_LUSTER_SOLDIER), EXTRA, main, graine=1)
    choix = [Choix((index_option(simuler(engine, partie, []), "activation", BLACK_LUSTER_RITUAL),)), Choix((0,))]
    etat = simuler(engine, partie, choix)
    while etat["decision"]["type"] != "somme":  # choix du monstre Rituel s'il est demandé
        choix.append(Choix((index_option(etat, "carte", BLACK_LUSTER_SOLDIER),)))
        etat = simuler(engine, partie, choix)

    somme = etat["decision"]
    assert somme["cible"] == 8
    assert {(o["code"], o["niveau"]) for o in somme["options"]} == {(STRATOS, 4), (NEOS, 7)}
    stratos, neos = index_option(etat, "carte", STRATOS), index_option(etat, "carte", NEOS)

    assert simuler(engine, partie, choix + [Choix((stratos,))])["statut"] == "invalide"  # 4 < 8
    apres = simuler(engine, partie, choix + [Choix((stratos, neos))])
    assert apres["statut"] == "decision"
    assert {STRATOS, NEOS} <= {c["code"] for c in apres["plateau"]["joueurs"][0]["cimetiere"]}


def test_une_selection_de_cartes_respecte_les_bornes_et_refuse_les_doublons(engine):
    choix = stratos_invoque(engine) + [Choix((0,)), Choix((0,))]

    assert simuler(engine, HERO, choix + [Choix((0, 0))])["statut"] == "invalide"
    assert simuler(engine, HERO, choix + [Choix((0, 1))])["statut"] == "invalide"


# --- Adversaire : le joueur 1 reçoit un deck et une main, ses décisions sont exposées ---------------------------

ASH_BLOSSOM, EFFECT_VEILER, BLUE_EYES = 14558127, 97268402, 89631139


def contre(partie: PartieSimulee, main: tuple[int, ...], deck: tuple[int, ...] = ()) -> PartieSimulee:
    """La même partie face à un adversaire qui tient `main` ; `deck` complète son Main Deck."""
    return replace(partie, adversaire=Adversaire(deck=main + deck, main=main))


def test_le_deck_adverse_a_son_propre_melange_qui_laisse_intact_celui_du_joueur_0():
    adverse = contre(HERO, main=(ASH_BLOSSOM,), deck=tuple(range(1, 40)))

    assert preparer(adverse) == preparer(HERO)  # même deck mélangé et mêmes graines du duel
    melange = preparer_adversaire(adverse)
    assert sorted(melange) == list(range(1, 40))  # main de départ retirée
    assert melange == preparer_adversaire(adverse)
    assert melange != preparer_adversaire(replace(adverse, graine=2))


def test_un_adversaire_sans_aucune_carte_rejoue_la_partie_a_l_identique(engine):
    choix = stratos_invoque(engine) + [Choix((0,)), Choix((0,))]

    assert simuler(engine, replace(HERO, adversaire=Adversaire()), choix) == simuler(engine, HERO, choix)


def test_une_recherche_depuis_le_deck_ouvre_a_l_adversaire_une_fenetre_de_chaine_avec_ash_blossom(engine):
    partie = contre(HERO, main=(ASH_BLOSSOM,), deck=(BLUE_EYES, BLUE_EYES))
    choix = stratos_invoque(engine) + [Choix((0,)), Choix((0,))]  # oui, puis l'effet de recherche

    etat = simuler(engine, partie, choix)

    assert (etat["statut"], etat["choixAppliques"]) == ("decision", 4)
    fenetre = etat["decision"]
    assert (fenetre["type"], fenetre["joueur"]) == ("chaine", 1)
    assert actions(fenetre) == {("activation", ASH_BLOSSOM), ("passer", None)}
    ash = fenetre["options"][index_option(etat, "activation", ASH_BLOSSOM)]
    assert ash["emplacement"] == {"controleur": 1, "zone": "main", "sequence": 0}
    assert ash["effet"] == "Negate that effect"
    # Ash Blossom répond à la recherche : une activation déclenchée par l'événement, que le client ne passe jamais seul.
    assert fenetre["speciaux"] == 1
    assert [maillon["code"] for maillon in etat["plateau"]["chaine"]] == [STRATOS]
    adversaire = etat["plateau"]["joueurs"][1]
    assert (adversaire["main"], adversaire["deck"]) == ([{"code": ASH_BLOSSOM}], 2)


def test_si_l_adversaire_passe_la_recherche_se_resout_comme_sans_adversaire(engine):
    partie = contre(HERO, main=(ASH_BLOSSOM,))
    choix = stratos_invoque(engine) + [Choix((0,)), Choix((0,))]
    fenetre = simuler(engine, partie, choix)["decision"]
    passer = Choix((index_option({"decision": fenetre}, "passer"),), empreinte=fenetre["empreinte"])

    selection = simuler(engine, partie, choix + [passer])

    assert (selection["statut"], selection["choixAppliques"], selection["decision"]["joueur"]) == ("decision", 5, 0)
    # Mêmes cartes aux mêmes places du deck que sans adversaire : même empreinte.
    assert selection["decision"]["empreinte"] == simuler(engine, HERO, choix)["decision"]["empreinte"]
    etat = simuler(engine, partie, choix + [passer, Choix((index_option(selection, "carte", BLAZEMAN),))])
    assert BLAZEMAN in [carte["code"] for carte in etat["plateau"]["joueurs"][0]["main"]]
    assert etat["plateau"]["joueurs"][1]["main"] == [{"code": ASH_BLOSSOM}]


def test_ash_blossom_activee_par_l_adversaire_annule_la_recherche(engine):
    partie = contre(HERO, main=(ASH_BLOSSOM,))
    choix = stratos_invoque(engine) + [Choix((0,)), Choix((0,))]
    choix.append(Choix((index_option(simuler(engine, partie, choix), "activation", ASH_BLOSSOM),)))

    etat = simuler(engine, partie, choix)

    assert (etat["statut"], etat["choixAppliques"]) == ("decision", 5)
    assert (etat["decision"]["type"], etat["decision"]["joueur"]) == ("commande", 0)
    adversaire = etat["plateau"]["joueurs"][1]
    assert (adversaire["main"], adversaire["cimetiere"]) == ([], [{"code": ASH_BLOSSOM}])
    assert sorted(carte["code"] for carte in etat["plateau"]["joueurs"][0]["main"]) == sorted((LIQUID, POLY))
    defausse = next(e for e in etat["evenements"] if e["type"] == "deplacement" and e["code"] == ASH_BLOSSOM)
    assert (defausse["de"]["controleur"], defausse["de"]["zone"], defausse["vers"]["zone"]) == (1, "main", "cimetiere")
    assert {"cout", "defausse"} <= set(defausse["raisons"])
    assert {"type": "effet-annule", "maillon": 1, "apresChoix": 5} in etat["evenements"]


def test_la_selection_de_l_adversaire_est_exposee_avec_son_texte(engine):
    partie = contre(HERO, main=(EFFECT_VEILER,))
    choix = stratos_seul_sur_le_terrain(engine, partie)
    fenetre = simuler(engine, partie, choix)
    assert (fenetre["decision"]["type"], fenetre["decision"]["joueur"]) == ("chaine", 1)
    choix.append(Choix((index_option(fenetre, "activation", EFFECT_VEILER),)))

    cible = simuler(engine, partie, choix)["decision"]

    assert (cible["type"], cible["joueur"], cible["texte"]) == ("cartes", 1, "Select a face-up card(s)")
    assert [(o["code"], o["emplacement"]) for o in cible["options"]] == [
        (STRATOS, {"controleur": 0, "zone": "monstre", "sequence": 2})]


@pytest.mark.parametrize("graine", range(1, 6))
def test_un_adversaire_sans_reponse_ne_change_rien_au_tour_du_joueur_0(engine, graine):
    partie = PartieSimulee(DECK + (POT_OF_GREED,), EXTRA, (POT_OF_GREED,), graine)
    adverse = contre(partie, main=(BLUE_EYES,) * 3, deck=(ASH_BLOSSOM,) * 3)  # Ash Blossom reste dans son deck
    assert simuler(engine, adverse, [])["decision"]["empreinte"] == simuler(engine, partie, [])["decision"]["empreinte"]
    choix = [Choix((index_option(simuler(engine, partie, []), "activation", POT_OF_GREED),)), Choix((0,))]

    sans, avec = simuler(engine, partie, choix), simuler(engine, adverse, choix)

    pioches = [[e["codes"] for e in etat["evenements"] if e["type"] == "pioche"] for etat in (sans, avec)]
    assert pioches[0] == pioches[1] and len(pioches[0]) == 1  # mêmes cartes piochées : même ordre du deck
    assert avec["plateau"]["joueurs"][0] == sans["plateau"]["joueurs"][0]
    assert avec["decision"] == sans["decision"]
    assert (len(avec["plateau"]["joueurs"][1]["main"]), avec["plateau"]["joueurs"][1]["deck"]) == (3, 3)


def test_finir_son_tour_termine_la_simulation_meme_face_a_un_adversaire(engine):
    partie = contre(HERO, main=(ASH_BLOSSOM,))

    etat = simuler(engine, partie, [Choix((index_option(simuler(engine, partie, []), "fin-de-tour"),))])

    assert (etat["statut"], etat["raison"], etat["choixAppliques"]) == ("fin", "fin-de-tour", 1)


# --- Plusieurs tours : `tours` fixe le tour dont le début termine la simulation ------------------------------------


def avec_deck(partie: PartieSimulee) -> PartieSimulee:
    """Adversaire qui tient Ash Blossom et a de quoi piocher."""
    return contre(partie, main=(ASH_BLOSSOM,), deck=(BLUE_EYES,) * 5)


@pytest.mark.parametrize("partie", [HERO, avec_deck(HERO)], ids=["sans-adversaire", "avec-adversaire"])
def test_jouer_plus_de_tours_ne_change_rien_au_premier(engine, partie):
    choix = stratos_invoque(engine) + [Choix((0,)), Choix((0,))]  # recherche en cours, tour 1
    fin = stratos_seul_sur_le_terrain(engine, partie)
    fin.append(Choix((index_option(simuler(engine, partie, fin), "fin-de-tour"),)))

    for tours in (2, 3, 4):
        assert simuler(engine, replace(partie, tours=tours), choix) == simuler(engine, partie, choix)
        assert simuler(engine, replace(partie, tours=1), fin) == simuler(engine, partie, fin)


def test_avec_deux_tours_l_adversaire_pioche_puis_joue_son_tour(engine):
    partie = replace(avec_deck(HERO), tours=2)
    choix = [Choix((index_option(simuler(engine, partie, []), "fin-de-tour"),))]

    tour_adverse = simuler(engine, partie, choix)

    assert (tour_adverse["statut"], tour_adverse["choixAppliques"]) == ("decision", 1)
    decision = tour_adverse["decision"]
    assert (decision["type"], decision["joueur"]) == ("commande", 1)
    assert {("invocation-normale", ASH_BLOSSOM), ("phase-de-combat", None), ("fin-de-tour", None)} <= actions(decision)
    assert (tour_adverse["plateau"]["tour"], tour_adverse["plateau"]["phase"]) == (2, "main1")
    assert {"type": "nouveau-tour", "joueur": 1, "apresChoix": 1} in tour_adverse["evenements"]
    assert {"type": "pioche", "joueur": 1, "codes": [BLUE_EYES], "apresChoix": 1} in tour_adverse["evenements"]
    assert sorted(c["code"] for c in tour_adverse["plateau"]["joueurs"][1]["main"]) == sorted((ASH_BLOSSOM, BLUE_EYES))

    fin = simuler(engine, partie, choix + [Choix((index_option(tour_adverse, "fin-de-tour"),))])

    assert (fin["statut"], fin["raison"], fin["choixAppliques"], fin["plateau"]["tour"]) == ("fin", "fin-de-tour", 2, 3)


def test_sans_adversaire_le_tour_adverse_passe_tout_seul(engine):
    partie = replace(HERO, tours=2)
    choix = stratos_seul_sur_le_terrain(engine, partie)
    choix.append(Choix((index_option(simuler(engine, partie, choix), "fin-de-tour"),)))

    etat = simuler(engine, partie, choix)

    # Le joueur 1 n'a pas de deck : il ne pioche pas (il perdrait), puis finit son tour.
    assert (etat["statut"], etat["raison"], etat["choixAppliques"], etat["plateau"]["tour"]) == ("fin", "fin-de-tour", 4, 3)
    # Comme avec un seul tour, le début du tour qui termine la partie n'est pas journalisé.
    assert [e["joueur"] for e in etat["evenements"] if e["type"] == "nouveau-tour"] == [0, 1]
    assert not any(e["type"] == "pioche" for e in etat["evenements"])
    assert etat["plateau"]["joueurs"][0]["monstres"][2]["code"] == STRATOS


def test_au_troisieme_tour_le_joueur_0_pioche_et_peut_combattre(engine):
    partie = replace(HERO, tours=3)
    choix = [Choix((index_option(simuler(engine, partie, []), "fin-de-tour"),))]

    etat = simuler(engine, partie, choix)

    assert (etat["statut"], etat["plateau"]["tour"], etat["decision"]["joueur"]) == ("decision", 3, 0)
    assert ("phase-de-combat", None) in actions(etat["decision"])
    pioche = [e for e in etat["evenements"] if e["type"] == "pioche"]
    assert [(e["joueur"], len(e["codes"])) for e in pioche] == [(0, 1)]


def test_un_adversaire_sans_carte_a_piocher_perd_au_debut_de_son_tour(engine):
    partie = replace(contre(HERO, main=(ASH_BLOSSOM,)), tours=2)  # son deck n'est que sa main de départ

    etat = simuler(engine, partie, [Choix((index_option(simuler(engine, partie, []), "fin-de-tour"),))])

    assert (etat["statut"], etat["raison"], etat["plateau"]["tour"]) == ("fin", "victoire", 2)


# --- Combat : en Battle Phase, le joueur du tour déclare ses attaques -------------------------------------------------


def stratos_pret_a_combattre(engine) -> tuple[PartieSimulee, list[Choix]]:
    """Stratos posé au tour 1 ; le tour 2, sans adversaire, passe seul ; au tour 3, le joueur 0 entre en Battle Phase."""
    partie = replace(HERO, tours=3)
    choix = stratos_seul_sur_le_terrain(engine, partie)
    choix.append(Choix((index_option(simuler(engine, partie, choix), "fin-de-tour"),)))
    choix.append(Choix((index_option(simuler(engine, partie, choix), "phase-de-combat"),)))
    return partie, choix


def test_en_battle_phase_le_joueur_choisit_d_attaquer_d_aller_en_main_phase_2_ou_de_finir_son_tour(engine):
    partie, choix = stratos_pret_a_combattre(engine)

    etat = simuler(engine, partie, choix)

    decision = etat["decision"]
    assert (etat["statut"], decision["type"], decision["joueur"]) == ("decision", "combat", 0)
    assert {("attaque", STRATOS), ("phase-principale-2", None), ("fin-de-tour", None)} <= actions(decision)
    attaque = decision["options"][index_option(etat, "attaque", STRATOS)]
    assert (attaque["emplacement"], attaque["direct"]) == ({"controleur": 0, "zone": "monstre", "sequence": 2}, True)


def test_une_attaque_directe_est_journalisee_et_inflige_l_atk_en_degats(engine):
    partie, choix = stratos_pret_a_combattre(engine)
    choix.append(Choix((index_option(simuler(engine, partie, choix), "attaque", STRATOS),)))

    etat = simuler(engine, partie, choix)

    attaques = [e for e in etat["evenements"] if e["type"] == "attaque"]
    assert len(attaques) == 1
    assert (attaques[0]["code"], attaques[0]["direct"], attaques[0]["de"]["sequence"]) == (STRATOS, True, 2)
    assert "vers" not in attaques[0]
    assert any(e["type"] == "degats" and (e["joueur"], e["montant"]) == (1, 1800) for e in etat["evenements"])
    assert etat["plateau"]["joueurs"][1]["lp"] == 8000 - 1800
    # Stratos a attaqué : il reste à aller en Main Phase 2 ou à finir le tour.
    assert ("attaque", STRATOS) not in actions(etat["decision"])
    assert ("phase-principale-2", None) in actions(etat["decision"])


def test_aller_en_main_phase_2_rend_la_commande_du_tour(engine):
    partie, choix = stratos_pret_a_combattre(engine)
    choix.append(Choix((index_option(simuler(engine, partie, choix), "phase-principale-2"),)))

    etat = simuler(engine, partie, choix)

    assert (etat["decision"]["type"], etat["plateau"]["phase"]) == ("commande", "main2")


# --- Points de vie : le moteur peut descendre sous 0 ; l'écran s'arrête à 0 ------------------------------------------------


def test_des_points_de_vie_sous_zero_se_lisent_zero_et_non_comme_un_entier_non_signe():
    import struct

    from solver.ocg import lire_terrain

    def joueur(lp: int) -> bytes:
        zones_vides = bytes(7 + 8)
        return struct.pack("<i", lp) + zones_vides + struct.pack("<6I", 30, 5, 0, 0, 0, 0)

    brut = struct.pack("<I", 0) + joueur(8000) + joueur(-700) + struct.pack("<I", 0)

    assert [j["lp"] for j in lire_terrain(brut)["joueurs"]] == [8000, 0]


def test_la_mise_a_jour_des_points_de_vie_s_arrete_a_zero():
    import struct

    from solver import messages as M

    mise_a_jour = M.decoder(bytes([M.MSG_LPUPDATE]) + struct.pack("<Bi", 1, -700))
    degats = M.decoder(bytes([M.MSG_DAMAGE]) + struct.pack("<BI", 1, 2500))

    assert (mise_a_jour.joueur, mise_a_jour.extra["montant"]) == (1, 0)
    assert degats.extra["montant"] == 2500
