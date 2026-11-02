"""Graphe TrainMyMat tiré des lignes d'un arbre de simulation (POST /simulation/graphe)."""

import re
import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from solver import graphe as module_graphe, graphe_simulation  # noqa: E402
from solver.graphe_simulation import DemandeGraphe, LigneSimulee, graphe_des_lignes  # noqa: E402
from solver.ocg import VENDOR  # noqa: E402
from solver.simulateur import Adversaire, Choix, PartieInvalide, PartieSimulee, simuler  # noqa: E402

STRATOS, LIQUID, POLY, SHADOW_MIST, BLAZEMAN, MALICIOUS, MASK_CHANGE, NEOS, HONEST_NEOS = (
    40044918, 59392529, 24094653, 50720316, 63060238, 9411399, 21143940, 89943723, 14124483)
SUNRISE, DARK_LAW, LINKURIBOH = 22908820, 58481572, 41999284
ASH_BLOSSOM, EFFECT_VEILER, BLUE_EYES, FOOLISH_BURIAL = 14558127, 97268402, 89631139, 81439173
DECK = (STRATOS, LIQUID, POLY, SHADOW_MIST, BLAZEMAN, MALICIOUS, LIQUID, SHADOW_MIST, POLY, MASK_CHANGE, NEOS, HONEST_NEOS)
EXTRA = (DARK_LAW, LINKURIBOH, SUNRISE)
MAIN = (STRATOS, LIQUID, POLY)
HERO = PartieSimulee(deck=DECK, extra=EXTRA, main=MAIN, graine=1)
REFERENCES = {str(code) for code in DECK + EXTRA}

MOTEUR_DISPONIBLE = (VENDOR / "cdb" / "cards.cdb").is_file() and any(VENDOR.glob("libocgcore.*"))
pytestmark = pytest.mark.skipif(not MOTEUR_DISPONIBLE, reason="Moteur non construit : lancer ./build_core.sh")

# --- Contrat backend : règles de BreakdownService.normaliserGraphe (decky-back) que ces graphes doivent tenir -------

EMPLACEMENTS = {"main", "field", "gy", "deck", "banish", "extra", "xyz-material", "removed", "effect"}
EMPLACEMENTS_ENDBOARD = {f"monstre-{i}" for i in range(1, 6)} | {f"magie-piege-{i}" for i in range(1, 6)} | {
    "emz-1", "emz-2", "terrain", "deck", "gy", "banish"}
PILES_ENDBOARD = {"deck", "gy", "banish"}


def verifier_contrat_backend(graphe: dict, references: set[str], references_adverses: set[str] = frozenset()):
    """Échoue là où le backend refuserait le graphe : références hors deck, instances incohérentes, actions
    sans PRIMARY unique, liens ou branches invalides. Une carte `adverse` vient du deck de l'autre joueur."""
    assert 0 < len(graphe["id"]) <= 80 and 0 < len(graphe["titre"].strip()) <= 160
    noeuds, instances, portees, numeros = {}, {}, {}, {}
    for noeud in graphe["noeuds"]:
        assert 0 < len(noeud["id"]) <= 80 and noeud["id"] not in noeuds
        noeuds[noeud["id"]] = noeud
        assert abs(noeud["x"]) <= 1_000_000 and abs(noeud["y"]) <= 1_000_000
        assert noeud["emplacement"] in EMPLACEMENTS
        generique, adverse = bool(noeud.get("estGenerique")), bool(noeud.get("adverse"))
        if generique:
            assert not noeud["referenceCarte"] and 0 < len(noeud["description"]) <= 500
        else:
            assert noeud["referenceCarte"] in (references_adverses if adverse else references), noeud
        instance, numero = noeud.get("instanceId"), noeud.get("numeroInstance")
        assert (instance is None) == (numero is None)
        if instance is not None:
            assert len(instance) <= 80 and 1 <= numero <= 999
            portee = ("adverse:" if adverse else "") + ("generique" if generique else "carte:" + noeud["referenceCarte"])
            assert generique or instances.setdefault((portee, numero), instance) == instance
            assert portees.setdefault(instance, portee) == portee
            assert numeros.setdefault((portee, instance), numero) == numero
    roles_par_action, identifiants, paires = {}, set(), set()
    for lien in graphe["liens"]:
        assert 0 < len(lien["id"]) <= 80 and lien["id"] not in identifiants
        identifiants.add(lien["id"])
        role, action = lien["role"], lien["actionId"]
        paire = (lien["source"], lien["sourceType"], lien["cible"], lien["cibleType"], action, role, lien.get("actionKey"))
        assert paire not in paires
        paires.add(paire)
        assert lien.get("libelle") is None or len(lien["libelle"]) <= 200
        assert lien.get("style") in (None, "plein", "pointille")
        assert lien.get("couleur") is None or re.fullmatch(r"#[0-9A-Fa-f]{6}", lien["couleur"])
        if role == "RESULT":
            assert (lien["sourceType"], lien["source"], lien["cibleType"]) == ("action", action, "noeud")
            assert lien["cible"] in noeuds and lien.get("inputRule") is None
        else:
            assert role in ("PRIMARY", "MATERIAL", "COST", "REQUIREMENT")
            assert (lien["sourceType"], lien["cibleType"], lien["cible"]) == ("noeud", "action", action)
            assert lien["source"] in noeuds
            assert lien.get("inputRule") == ("ALL" if role == "PRIMARY" else None)
        if role == "PRIMARY":
            assert lien["actionKey"] and abs(lien["actionX"]) <= 1_000_000 and abs(lien["actionY"]) <= 1_000_000
        roles_par_action.setdefault(action, []).append(role)
    assert all(roles.count("PRIMARY") == 1 for roles in roles_par_action.values())
    zones, affectes = set(), set()
    assert len(graphe["zones"]) <= 100
    for zone in graphe["zones"]:
        assert zone["id"] not in zones and zone["type"] in ("main-depart", "target", "endboard")
        zones.add(zone["id"])
        assert 0 < zone["largeur"] <= 5000 and 0 < zone["hauteur"] <= 5000 and len(zone["idsNoeuds"]) <= 100
        for noeud in zone["idsNoeuds"]:
            assert noeud in noeuds and noeud not in affectes
            affectes.add(noeud)
        if zone["type"] != "endboard":
            assert "joueurs" not in zone
            continue
        assert zone["joueurs"] in (1, 2) and not zone["idsNoeuds"] and len(zone["cartesEndboard"]) <= 200
        emplacements, piles = set(), {}
        for carte in zone["cartesEndboard"]:
            assert carte["referenceCarte"] in references and 1 <= carte["joueur"] <= zone["joueurs"]
            assert carte["emplacement"] in EMPLACEMENTS_ENDBOARD and carte["position"] in ("attaque", "defense", "verso")
            if carte["emplacement"] in PILES_ENDBOARD:
                piles.setdefault(carte["emplacement"], set()).add(carte["ordre"])
            else:
                assert carte["ordre"] == 0 and carte["emplacement"] not in emplacements
                emplacements.add(carte["emplacement"])
        assert all(ordres == set(range(len(ordres))) for ordres in piles.values())
    assert len(graphe["branches"]) <= 30
    for branche in graphe["branches"]:
        assert 0 < len(branche["nom"].strip()) <= 80 and len(branche["etapes"]) <= 100
        etapes = [(etape["type"], etape["id"]) for etape in branche["etapes"]]
        assert len(etapes) == len(set(etapes))
        for type_, identifiant in etapes:
            assert identifiant in (noeuds if type_ == "carte" else roles_par_action), (type_, identifiant)


# --- Lignes jouées dans le simulateur ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def engine():
    from solver.ocg import Engine
    return Engine()


def jouer(engine, partie: PartieSimulee, etapes: list) -> list[Choix]:
    """Choix d'une ligne : chaque étape (action[, code[, emplacement]]) désigne une option ; None, la première."""
    choix = []
    for etape in etapes:
        etat = simuler(engine, partie, choix)
        assert etat["statut"] == "decision", (etape, etat["statut"], etat.get("erreur"))
        index = 0
        if etape is not None:
            action, code, emplacement = etape[0], etape[1] if len(etape) > 1 else None, etape[2] if len(etape) > 2 else {}
            index = next(o["index"] for o in etat["decision"]["options"]
                         if o["action"] == action and (code is None or o["code"] == code)
                         and all((o.get("emplacement") or {}).get(cle) == valeur for cle, valeur in emplacement.items()))
        choix.append(Choix((index,), empreinte=etat["decision"]["empreinte"]))
    return choix


STRATOS_CHERCHE = [("invocation-normale", STRATOS), ("zone", None, {"sequence": 2}), ("oui",), None]  # effet de recherche


def ligne_sunrise(engine) -> list[Choix]:
    """Stratos cherche Blazeman, puis Polymerization : Sunrise avec Stratos et Liquid Soldier."""
    return jouer(engine, HERO, STRATOS_CHERCHE + [
        ("carte", BLAZEMAN), ("activation", POLY), None, ("carte", SUNRISE), ("carte", STRATOS), ("carte", LIQUID),
        ("zone", None, {"sequence": 5}), ("position", None)])


def demande(partie: PartieSimulee, *lignes: tuple[str, list[Choix]], **options) -> DemandeGraphe:
    options = {"max_cartes": 100, "max_liens": 300, **options}
    return DemandeGraphe("Combo", partie, tuple(LigneSimulee(nom, tuple(choix)) for nom, choix in lignes), **options)


def actions_primaires(graphe: dict) -> dict[str, dict]:
    return {lien["actionId"]: lien for lien in graphe["liens"] if lien["role"] == "PRIMARY"}


def test_une_ligne_donne_une_branche_dont_chaque_etape_existe(engine):
    resultat = graphe_des_lignes(engine, demande(HERO, ("A", ligne_sunrise(engine))))

    graphe, stats = resultat["graphe"], resultat["stats"]
    verifier_contrat_backend(graphe, REFERENCES)
    assert (stats["lignes"], stats["lignesIgnorees"], stats["etapesTronquees"], stats["grapheTronque"]) == (1, [], [], False)
    (branche,) = graphe["branches"]
    assert branche["nom"] == "A"
    actions = actions_primaires(graphe)
    assert [e["id"] for e in branche["etapes"] if e["type"] == "action"] == list(actions)
    # Invocation de Stratos, son effet de recherche (nommé par ce qu'il fait), puis Polymerization et ses matériaux.
    assert [lien["actionKey"] for lien in actions.values()] == ["invocation", "tuto", "activer-carte"]
    assert {"MATERIAL", "RESULT"} <= {lien["role"] for lien in graphe["liens"]}
    assert "Invocation Fusion depuis l'Extra Deck" in {lien.get("libelle") for lien in graphe["liens"]}
    assert not any(noeud.get("adverse") for noeud in graphe["noeuds"])
    assert {zone["type"] for zone in graphe["zones"]} == {"main-depart", "endboard"}
    endboard = next(zone for zone in graphe["zones"] if zone["type"] == "endboard")
    assert ("emz-1", str(SUNRISE)) in {(c["emplacement"], c["referenceCarte"]) for c in endboard["cartesEndboard"]}


def test_l_endboard_suit_la_derniere_action_sans_place_pour_des_alternatives(engine):
    graphe = graphe_des_lignes(engine, demande(HERO, ("A", ligne_sunrise(engine))))["graphe"]

    endboard = next(zone for zone in graphe["zones"] if zone["type"] == "endboard")
    droite_actions = max(lien["actionX"] for lien in actions_primaires(graphe).values())
    droite_cartes = max(noeud["x"] for noeud in graphe["noeuds"])
    # Une ligne du simulateur n'a pas d'alternative greffée : l'endboard vient juste après son dernier élément.
    assert max(droite_actions, droite_cartes) < endboard["x"] <= max(droite_actions, droite_cartes) + module_graphe.PAS_ACTION


def test_deux_lignes_au_debut_commun_partagent_ses_actions_et_ont_chacune_leur_branche(engine):
    blazeman = jouer(engine, HERO, STRATOS_CHERCHE + [("carte", BLAZEMAN)])
    shadow_mist = jouer(engine, HERO, STRATOS_CHERCHE + [("carte", SHADOW_MIST)])

    seule = graphe_des_lignes(engine, demande(HERO, ("A", blazeman)))["graphe"]
    resultat = graphe_des_lignes(engine, demande(HERO, ("A", blazeman), ("B", shadow_mist)))

    graphe = resultat["graphe"]
    verifier_contrat_backend(graphe, REFERENCES)
    assert [b["nom"] for b in graphe["branches"]] == ["A", "B"] and resultat["stats"]["lignes"] == 2
    # Les éléments de la première ligne sont repris tels quels (identifiants stables), sans doublon.
    assert {n["id"] for n in seule["noeuds"]} <= {n["id"] for n in graphe["noeuds"]}
    assert {lien["id"] for lien in seule["liens"]} <= {lien["id"] for lien in graphe["liens"]}
    a, b = ([e["id"] for e in branche["etapes"] if e["type"] == "action"] for branche in graphe["branches"])
    invocation = a[0]
    assert b[0] == invocation and actions_primaires(graphe)[invocation]["actionKey"] == "invocation"
    # La recherche diffère : une action par ligne, chacune avec la carte qu'elle ajoute.
    assert a[1] != b[1]
    resultats = {action: {lien["cible"] for lien in graphe["liens"] if lien["actionId"] == action and lien["role"] == "RESULT"}
                 for action in (a[1], b[1])}
    noeuds = {n["id"]: n for n in graphe["noeuds"]}
    assert [{noeuds[n]["referenceCarte"] for n in cibles} for cibles in resultats.values()] == [
        {str(BLAZEMAN)}, {str(SHADOW_MIST)}]
    assert len(graphe["noeuds"]) == len(MAIN) + 3  # main, Stratos sur le terrain, Blazeman, Shadow Mist


@pytest.mark.parametrize("fautif, motif", [
    (Choix((99,)), "Ligne « B », choix n° 2 : Option inconnue"),
    (Choix((0,), empreinte="0" * 16), "Ligne « B », choix n° 2 : la partie rejouée a divergé"),
], ids=["choix-refuse", "empreinte-divergente"])
def test_une_ligne_qui_ne_se_rejoue_pas_rend_la_demande_invalide(engine, fautif, motif):
    valide = jouer(engine, HERO, STRATOS_CHERCHE)

    with pytest.raises(PartieInvalide, match=motif):
        graphe_des_lignes(engine, demande(HERO, ("A", valide), ("B", valide[:1] + [fautif])))


def test_des_choix_au_dela_de_la_fin_de_la_partie_sont_refuses(engine):
    fin_de_tour = jouer(engine, HERO, [("fin-de-tour",)])

    with pytest.raises(PartieInvalide, match="s'arrête après 1 choix sur 2"):
        graphe_des_lignes(engine, demande(HERO, ("A", fin_de_tour + [Choix((0,))])))


def test_les_cartes_de_l_adversaire_sont_des_noeuds_adverses_meme_si_le_deck_suivi_les_joue_aussi(engine):
    # Le joueur 0 a aussi Ash Blossom dans son deck : l'exemplaire adverse reste adverse, numéroté à part.
    partie = PartieSimulee(DECK + (ASH_BLOSSOM,), EXTRA, MAIN, graine=1,
                           adversaire=Adversaire(deck=(ASH_BLOSSOM, BLUE_EYES), main=(ASH_BLOSSOM,)))
    ligne = jouer(engine, partie, STRATOS_CHERCHE + [("activation", ASH_BLOSSOM)])

    graphe = graphe_des_lignes(engine, demande(partie, ("Ash", ligne)))["graphe"]

    verifier_contrat_backend(graphe, REFERENCES | {str(ASH_BLOSSOM)}, {str(ASH_BLOSSOM), str(BLUE_EYES)})
    adverses = [n for n in graphe["noeuds"] if n.get("adverse")]
    assert [(n["referenceCarte"], n["instanceId"], n["emplacement"]) for n in adverses] == [
        (str(ASH_BLOSSOM), f"i-a-{ASH_BLOSSOM}-1", "main")]
    ash = next(lien for lien in actions_primaires(graphe).values() if lien["source"] == adverses[0]["id"])
    assert (ash["actionKey"], ash["libelle"]) == ("effet-negate", "Negate that effect")
    assert ash["actionId"] in {e["id"] for e in graphe["branches"][0]["etapes"]}


# --- Effets nommés par ce qu'ils font, cartes qui chaînent ----------------------------------------------------------


def entrees_secondaires(graphe: dict, action_id: str) -> list[tuple[str, str, str | None, str | None]]:
    return [(lien["source"], lien["role"], lien.get("libelle"), lien.get("couleur")) for lien in graphe["liens"]
            if lien["actionId"] == action_id and lien["role"] not in ("PRIMARY", "RESULT")]


def test_ash_blossom_en_chaine_annule_la_recherche_et_part_de_stratos_auquel_elle_repond(engine):
    partie = replace(HERO, adversaire=Adversaire(deck=(ASH_BLOSSOM, BLUE_EYES), main=(ASH_BLOSSOM,)))
    ligne = jouer(engine, partie, STRATOS_CHERCHE + [("activation", ASH_BLOSSOM)])

    graphe = graphe_des_lignes(engine, demande(partie, ("Ash", ligne)))["graphe"]

    verifier_contrat_backend(graphe, REFERENCES, {str(ASH_BLOSSOM), str(BLUE_EYES)})
    invocation, recherche, ash = actions_primaires(graphe).values()
    # La recherche annulée n'a aucun résultat : elle reste « effet » ; Ash Blossom, qui l'annule, « effet-negate ».
    assert [lien["actionKey"] for lien in (invocation, recherche, ash)] == ["invocation", "effet", "effet-negate"]
    # Ash Blossom répond à l'effet de Stratos : lien d'entrée depuis Stratos sur le terrain, qui porte cet effet.
    assert entrees_secondaires(graphe, ash["actionId"]) == [(recherche["source"], "REQUIREMENT", "En chaîne", "#F43F5E")]


def test_effect_veiler_qui_cible_la_carte_en_chaine_l_annule_sans_lien_en_double(engine):
    partie = replace(HERO, adversaire=Adversaire(deck=(EFFECT_VEILER, BLUE_EYES), main=(EFFECT_VEILER,)))
    ligne = jouer(engine, partie, STRATOS_CHERCHE + [("activation", EFFECT_VEILER), ("carte", STRATOS)])

    graphe = graphe_des_lignes(engine, demande(partie, ("Veiler", ligne)))["graphe"]

    verifier_contrat_backend(graphe, REFERENCES, {str(EFFECT_VEILER), str(BLUE_EYES)})
    invocation, recherche, veiler = actions_primaires(graphe).values()
    # Le moteur ne signale l'annulation qu'à la résolution de l'effet de Stratos : elle revient à Veiler, qui l'a ciblé.
    assert [lien["actionKey"] for lien in (invocation, recherche, veiler)] == ["invocation", "effet", "effet-negate"]
    # Stratos, déjà cible de Veiler, n'a que son lien « Cible » vers lui.
    assert entrees_secondaires(graphe, veiler["actionId"]) == [(recherche["source"], "REQUIREMENT", "Cible", "#F43F5E")]


def test_une_invocation_speciale_depuis_le_deck_par_un_effet_est_deck_to_field(engine):
    partie = PartieSimulee(deck=(FOOLISH_BURIAL, MALICIOUS, MALICIOUS, MALICIOUS), extra=(), main=(FOOLISH_BURIAL,),
                           graine=1)
    # Foolish Burial envoie Malicious au Cimetière ; Malicious se bannit pour invoquer un autre Malicious du Deck.
    ligne = jouer(engine, partie, [("activation", FOOLISH_BURIAL), None, ("carte", MALICIOUS),
                                   ("activation", MALICIOUS), None, None, None])

    graphe = graphe_des_lignes(engine, demande(partie, ("Malicious", ligne)))["graphe"]

    verifier_contrat_backend(graphe, {str(FOOLISH_BURIAL), str(MALICIOUS)})
    foolish, malicious = actions_primaires(graphe).values()
    assert (foolish["actionKey"], malicious["actionKey"]) == ("activer-carte", "deck-to-field")
    sorties = {lien.get("libelle") for lien in graphe["liens"]
               if lien["actionId"] == malicious["actionId"] and lien["role"] == "RESULT"}
    assert sorties == {"Invoqué depuis le Deck"}  # le libellé de la sortie ne change pas
    assert entrees_secondaires(graphe, malicious["actionId"]) == []  # nouvelle chaîne : aucun lien « En chaîne »


def test_du_point_de_vue_du_joueur_1_ses_cartes_sont_les_siennes_et_celles_du_joueur_0_adverses(engine):
    partie = replace(HERO, tours=2, adversaire=Adversaire(deck=DECK, extra=EXTRA, main=MAIN))
    ligne = jouer(engine, partie, [
        ("invocation-normale", STRATOS), ("zone", None, {"sequence": 2}), ("non",), ("fin-de-tour",),  # tour 1 : joueur 0
        *STRATOS_CHERCHE, ("carte", BLAZEMAN)])  # tour 2 : joueur 1

    resultat = graphe_des_lignes(engine, demande(partie, ("Second", ligne), joueur=1))

    graphe = resultat["graphe"]
    verifier_contrat_backend(graphe, REFERENCES, REFERENCES)
    noeuds = {n["id"]: n for n in graphe["noeuds"]}
    main_depart = next(zone for zone in graphe["zones"] if zone["type"] == "main-depart")
    assert [(noeuds[n]["referenceCarte"], noeuds[n].get("adverse")) for n in main_depart["idsNoeuds"]] == [
        (str(code), None) for code in MAIN]
    invocations = [(lien["actionKey"], bool(noeuds[lien["source"]].get("adverse")))
                   for lien in actions_primaires(graphe).values()]
    assert invocations == [("invocation", True), ("invocation", False), ("tuto", False)]
    endboard = next(zone for zone in graphe["zones"] if zone["type"] == "endboard")
    assert ("monstre-3", str(STRATOS)) in {(c["emplacement"], c["referenceCarte"]) for c in endboard["cartesEndboard"]}


def test_le_joueur_1_sans_cartes_ne_peut_pas_etre_suivi(engine):
    with pytest.raises(PartieInvalide, match="Le joueur 1 n'a aucune carte"):
        graphe_des_lignes(engine, demande(HERO, ("A", []), joueur=1))


def test_les_limites_demandees_sont_bornees_comme_pour_generer(engine):
    resultat = graphe_des_lignes(engine, demande(HERO, ("A", ligne_sunrise(engine)), max_cartes=1, max_liens=1))

    # Plancher du service (8 cartes, 16 liens) : la ligne Sunrise (6 cartes, 8 liens) tient entière.
    assert (resultat["stats"]["grapheTronque"], len(resultat["graphe"]["noeuds"])) == (False, 6)


@pytest.fixture
def sans_plancher(monkeypatch):
    monkeypatch.setattr(graphe_simulation, "PLANCHER_CARTES", 1)
    monkeypatch.setattr(graphe_simulation, "PLANCHER_LIENS", 1)


def test_le_budget_coupe_la_premiere_ligne_puis_ignore_les_suivantes(engine, sans_plancher):
    ligne = ligne_sunrise(engine)

    resultat = graphe_des_lignes(engine, demande(HERO, ("A", ligne), ("B", ligne[:4]), max_cartes=5, max_liens=300))

    graphe, stats = resultat["graphe"], resultat["stats"]
    verifier_contrat_backend(graphe, REFERENCES)
    assert len(graphe["noeuds"]) <= 5
    assert (stats["grapheTronque"], stats["lignes"], stats["lignesIgnorees"]) == (True, 1, ["B"])
    assert not any(zone["type"] == "endboard" for zone in graphe["zones"])  # plateau final faux pour une ligne coupée


def test_une_ligne_suivante_hors_budget_est_ignoree_entiere(engine, sans_plancher):
    sunrise = ligne_sunrise(engine)  # 6 cartes : la main, Stratos sur le terrain, Blazeman, Sunrise
    shadow_mist = jouer(engine, HERO, STRATOS_CHERCHE + [("carte", SHADOW_MIST)])  # une carte de plus

    resultat = graphe_des_lignes(engine, demande(HERO, ("A", sunrise), ("B", shadow_mist), ("C", sunrise[:4]),
                                              max_cartes=6, max_liens=300))

    graphe, stats = resultat["graphe"], resultat["stats"]
    assert [b["nom"] for b in graphe["branches"]] == ["A", "C"] and len(graphe["noeuds"]) == 6
    assert (stats["grapheTronque"], stats["lignesIgnorees"]) == (False, ["B"])
    assert str(SHADOW_MIST) not in {n.get("referenceCarte") for n in graphe["noeuds"]}


def test_une_ligne_sans_action_n_ajoute_pas_de_branche(engine):
    resultat = graphe_des_lignes(engine, demande(HERO, ("Vide", []), ("A", jouer(engine, HERO, STRATOS_CHERCHE))))

    assert [b["nom"] for b in resultat["graphe"]["branches"]] == ["A"]
    assert resultat["stats"]["lignesIgnorees"] == ["Vide"]


def test_une_branche_trop_longue_garde_ses_premieres_etapes_et_le_signale(engine, monkeypatch):
    monkeypatch.setattr(module_graphe, "MAX_ETAPES_BRANCHE", 4)

    resultat = graphe_des_lignes(engine, demande(HERO, ("A", ligne_sunrise(engine))))

    (branche,) = resultat["graphe"]["branches"]
    assert len(branche["etapes"]) == 4 and resultat["stats"]["etapesTronquees"] == ["A"]
    # Le graphe reste complet : seules les étapes de la branche sont coupées.
    assert len(actions_primaires(resultat["graphe"])) == 3


def test_le_titre_vide_et_un_nom_blanc_prennent_une_valeur_par_defaut(engine):
    graphe = graphe_des_lignes(engine, replace(demande(HERO, ("  ", jouer(engine, HERO, STRATOS_CHERCHE))), titre=" "))["graphe"]

    assert graphe["titre"] == "Combo" and graphe["branches"][0]["nom"] == "Ligne 1"


def test_une_ligne_qui_depasse_la_limite_de_messages_du_moteur_est_refusee(engine, monkeypatch):
    from solver import simulateur
    monkeypatch.setattr(simulateur, "LIMITE_MESSAGES", 5)

    with pytest.raises(PartieInvalide, match="Ligne « A » : limite de messages du moteur atteinte"):
        graphe_des_lignes(engine, demande(HERO, ("A", [Choix((0,))])))
