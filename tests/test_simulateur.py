"""Simulation interactive : le joueur choisit chaque action parmi les options légales du moteur."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from solver.ocg import VENDOR  # noqa: E402
from solver.simulateur import Choix, PartieSimulee, simuler  # noqa: E402

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
