"""Génération d'un graphe de combos depuis un deck et une main de départ.

Ordre de remplissage du budget : ligne 1, puis son arbre de choix (jusqu'à
MAX_CANDIDATS cartes par choix, chacune avec sa meilleure suite), puis ligne 2
et ses alternatives, etc.
"""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass

from .explorer import Exploration, Limites, explorer, meilleur_prolongement, meilleures_lignes
from .graphe import Action, Branchement, ConstructeurGraphe, Ligne
from .ocg import TYPE_NORMAL, Engine
from .simulation import SELECTIONS_CARTES, Partie, Simulation, options

MAX_DECK, MAX_EXTRA, MAX_MAIN = 60, 15, 6
PLAFOND_CARTES, PLAFOND_LIENS = 100, 300
PLAFOND_LIGNES = 5
MAX_CANDIDATS = 5  # par choix, carte retenue comprise
LIMITES_SUITE = Limites(max_parties=80, max_secondes=1.0, max_actions=6)
SECONDES_ALTERNATIVES = 10.0


class RequeteInvalide(ValueError):
    pass


@dataclass(frozen=True)
class Requete:
    titre: str
    deck: tuple[int, ...]
    extra: tuple[int, ...]
    main: tuple[int, ...]
    max_cartes: int = 25
    max_liens: int = 75
    max_lignes: int = 3
    max_parties: int = 2000
    max_secondes: float = 20.0


def valider(requete: Requete) -> Requete:
    if not 1 <= len(requete.main) <= MAX_MAIN:
        raise RequeteInvalide(f"La main de départ doit contenir entre 1 et {MAX_MAIN} cartes.")
    if len(requete.deck) > MAX_DECK or len(requete.extra) > MAX_EXTRA:
        raise RequeteInvalide("Deck trop grand pour la génération.")
    if any(code <= 0 for code in (*requete.deck, *requete.extra, *requete.main)):
        raise RequeteInvalide("Code de carte invalide.")
    if Counter(requete.main) - Counter(requete.deck):
        raise RequeteInvalide("Chaque carte de la main doit provenir du Main Deck.")
    # Les limites demandées sont bornées par un plafond « raisonnable » côté service.
    return Requete(
        titre=(requete.titre or "Graphe généré").strip()[:160],
        deck=requete.deck, extra=requete.extra, main=requete.main,
        max_cartes=max(8, min(requete.max_cartes, PLAFOND_CARTES)),
        max_liens=max(16, min(requete.max_liens, PLAFOND_LIENS)),
        max_lignes=max(1, min(requete.max_lignes, PLAFOND_LIGNES)),
        max_parties=max(50, min(requete.max_parties, 5000)),
        max_secondes=max(1.0, min(requete.max_secondes, 45.0)),
    )


def generer(engine: Engine, requete: Requete) -> dict:
    requete = valider(requete)
    deck = list(requete.deck)
    for code in requete.main:
        deck.remove(code)
    partie = Partie(deck=tuple(deck), extra=requete.extra, main=requete.main)
    simulation = Simulation(engine, partie)
    exploration = explorer(simulation, Limites(max_parties=requete.max_parties, max_secondes=requete.max_secondes))

    references = {str(code) for code in (*requete.deck, *requete.extra)}
    constructeur = ConstructeurGraphe(engine.base, references, requete.main, requete.max_cartes, requete.max_liens)
    fin_alternatives = time.monotonic() + SECONDES_ALTERNATIVES
    choix_traites: set[tuple[bytes, ...]] = set()
    retenues = alternatives = 0
    for candidat in meilleures_lignes(exploration, requete.max_lignes * 2):
        if retenues >= requete.max_lignes:
            break
        rejeu = simulation.jouer(candidat.reponses, journal=True)
        nom = f"Ligne {retenues + 1}"
        ligne = Ligne(nom, candidat.score, candidat.reponses, rejeu.evenements, _plateau_final(simulation, candidat.reponses))
        actions = constructeur.ajouter_ligne(ligne)
        if actions is None:
            continue
        retenues += 1
        alternatives += _ajouter_alternatives(simulation, exploration, constructeur, ligne, actions, fin_alternatives, choix_traites)
        if constructeur.tronque:
            break

    sans_donnees = sorted({c for c in (*requete.deck, *requete.extra) if not engine.base.existe(c)})
    sans_script = sorted({
        c for c in (*requete.deck, *requete.extra)
        if engine.base.existe(c) and not engine.base.type(c) & TYPE_NORMAL and engine.scripts.lire(f"c{c}.lua") is None
    })
    return {
        "graphe": constructeur.graphe(requete.titre),
        "stats": {
            "parties": simulation.parties_jouees,
            "secondes": exploration.secondes,
            "etatsExplores": len(exploration.candidats),
            "etatsFusionnes": exploration.etats_fusionnes,
            "explorationInterrompue": exploration.interrompue,
            "lignes": retenues,
            "alternatives": alternatives,
            "grapheTronque": constructeur.tronque,
            "cartesInconnues": [str(c) for c in sans_donnees],
            "cartesSansScript": [str(c) for c in sans_script],
        },
    }


def _ajouter_alternatives(simulation: Simulation, exploration: Exploration, constructeur: ConstructeurGraphe,
                          ligne: Ligne, actions: list[Action], fin: float, choix_traites: set[tuple[bytes, ...]]) -> int:
    """Greffe, pour chaque choix de carte de la ligne, les autres candidats et leur meilleure suite.

    Un choix situé dans le début commun à plusieurs lignes n'est développé qu'une fois.
    """
    reponses = ligne.reponses
    base = simulation.engine.base
    plan: list[tuple[Action, int, list]] = []
    for action in actions:
        for index in action.choix:
            if tuple(reponses[:index]) in choix_traites:
                continue
            choix_traites.add(tuple(reponses[:index]))
            decision = simulation.jouer(reponses[:index]).decision
            if decision is None or decision.type not in SELECTIONS_CARTES:
                continue
            autres = [o for o in options(decision, MAX_CANDIDATS + 1) if o.reponse != reponses[index]][:MAX_CANDIDATS - 1]
            if autres:
                plan.append((action, index, autres))
    # Rangées : le dernier choix au plus près de la ligne. La colonne de sortie d'un choix
    # ne traverse alors que des rangées dont le contenu est plus à droite, ce qui laisse
    # sa zone de candidats libre. Le remplissage reste prioritaire du premier au dernier choix.
    debut = constructeur.reserver_rangees(sum(len(autres) for _, _, autres in plan))
    premieres: dict[int, int] = {}
    for position, (_, _, autres) in sorted(enumerate(plan), key=lambda item: (-item[1][0].ordinal, -item[1][1])):
        premieres[position] = debut
        debut += len(autres)

    ajoutees = 0
    for position, (action, index, autres) in enumerate(plan):
        for rang, option in enumerate(autres):
            if constructeur.presque_plein() or time.monotonic() > fin:
                return ajoutees
            prefixe = reponses[:index] + [option.reponse]
            suite = meilleur_prolongement(exploration, prefixe)
            if suite is None:
                suite = meilleur_prolongement(explorer(simulation, LIMITES_SUITE, depart=prefixe), prefixe)
            reponses_alt = suite.reponses if suite else prefixe
            rejeu = simulation.jouer(reponses_alt, journal=True)
            if rejeu.statut == "invalide":
                continue
            choix = base.nom(option.code) if option.code else option.type
            branchement = Branchement(index, action.ordinal, action.cle, f"{base.nom(action.code)} → {choix}", ligne.nom)
            alternative = Ligne(ligne.nom, suite.score if suite else 0, reponses_alt, rejeu.evenements, branchement=branchement)
            if constructeur.ajouter_ligne(alternative, premieres[position] + rang) is not None:
                ajoutees += 1
    return ajoutees


def _plateau_final(simulation: Simulation, reponses: list[bytes]) -> dict:
    resultat = simulation.jouer(reponses)
    return resultat.plateau or {}
