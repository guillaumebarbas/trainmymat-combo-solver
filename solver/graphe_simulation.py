"""Graphe TrainMyMat tiré des lignes d'un arbre de simulation (`POST /simulation/graphe`).

Une ligne est une suite de choix depuis le début de la partie, comme la liste `choix` de
`/simulation/etat`. Le simulateur la rejoue en tenant un journal au format de `simulation.py`
(messages du moteur et marqueur de chaque choix), puis `ConstructeurGraphe` la dessine comme une
ligne de `/generer` : les lignes qui partagent leur début partagent les mêmes actions (mêmes clés),
et chacune ajoute sa branche, au nom de la ligne. Les cartes du joueur suivi (`joueur`) sont des
nœuds ordinaires, celles de l'autre joueur des nœuds `adverse`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .graphe import MAX_BRANCHES, ConstructeurGraphe, Ligne
from .ocg import Engine
from .service import PLAFOND_CARTES, PLAFOND_LIENS, PLANCHER_CARTES, PLANCHER_LIENS
from .simulateur import Adversaire, Choix, Journal, PartieInvalide, PartieSimulee, journaliser, valider

TITRE_PAR_DEFAUT = "Combo"
MAX_LIGNES = MAX_BRANCHES  # une branche par ligne


@dataclass(frozen=True)
class LigneSimulee:
    nom: str
    choix: tuple[Choix, ...]


@dataclass(frozen=True)
class DemandeGraphe:
    titre: str
    partie: PartieSimulee
    lignes: tuple[LigneSimulee, ...]
    joueur: int = 0  # joueur du moteur dont les cartes sont « à soi » (1 quand on joue en second)
    max_cartes: int = 25
    max_liens: int = 75


def graphe_des_lignes(engine: Engine, demande: DemandeGraphe) -> dict:
    """Rejoue chaque ligne et l'ajoute au graphe, dans l'ordre, tant que le budget le permet.

    Comme pour `/generer`, la première ligne est coupée si elle dépasse le budget (et l'on s'arrête là) ;
    une ligne suivante est dessinée entière ou pas du tout. Toutes les lignes sont rejouées : une ligne
    qui ne se rejoue pas rend la demande invalide, même si elle n'aurait pas été dessinée.
    """
    debut = time.monotonic()
    partie, joueur = demande.partie, demande.joueur
    valider(partie)
    if not 1 <= len(demande.lignes) <= MAX_LIGNES:
        raise PartieInvalide(f"Le graphe se construit à partir de 1 à {MAX_LIGNES} lignes.")
    if joueur not in (0, 1):
        raise PartieInvalide("Le joueur suivi doit être 0 ou 1.")
    if joueur == 1 and not partie.avec_adversaire:
        raise PartieInvalide("Le joueur 1 n'a aucune carte : précisez l'adversaire pour suivre ses lignes.")
    adversaire = partie.adversaire or Adversaire()
    camps = ((partie.deck, partie.extra, partie.main), (adversaire.deck, adversaire.extra, adversaire.main))
    (deck, extra, main), (deck_adverse, extra_adverse, main_adverse) = camps[joueur], camps[1 - joueur]
    constructeur = ConstructeurGraphe(
        engine.base, {str(code) for code in (*deck, *extra)}, main,
        # Mêmes bornes que `/generer`, quelles que soient les limites demandées.
        max(PLANCHER_CARTES, min(demande.max_cartes, PLAFOND_CARTES)),
        max(PLANCHER_LIENS, min(demande.max_liens, PLAFOND_LIENS)),
        joueur=joueur, main_adverse=main_adverse,
        references_adverses={str(code) for code in (*deck_adverse, *extra_adverse)},
        # Les lignes jouées n'ont pas d'alternative greffée : l'endboard suit leur dernière action.
        reserve_alternatives=0,
    )
    ignorees: list[str] = []
    for numero, ligne in enumerate(demande.lignes, start=1):
        nom = ligne.nom.strip() or f"Ligne {numero}"
        journal = journaliser(engine, partie, list(ligne.choix), joueur)
        _verifier_rejeu(nom, journal, len(ligne.choix))
        dessin = Ligne(nom, None, journal.reponses, journal.evenements, journal.plateau)
        if constructeur.tronque or constructeur.ajouter_ligne(dessin) is None:
            ignorees.append(nom)
    titre = (demande.titre or "").strip()[:160] or TITRE_PAR_DEFAUT
    return {
        "graphe": constructeur.graphe(titre),
        "stats": {
            "lignes": len(constructeur.branches),
            # Lignes non dessinées : hors budget (cartes, liens), sans action, ou après une ligne coupée.
            "lignesIgnorees": ignorees,
            # Branches gardées à leurs 100 premières étapes (borne backend) ; le graphe, lui, est complet.
            "etapesTronquees": constructeur.etapes_tronquees,
            "grapheTronque": constructeur.tronque,
            "secondes": round(time.monotonic() - debut, 2),
        },
    }


def _verifier_rejeu(nom: str, journal: Journal, attendus: int):
    """Une ligne doit se rejouer en entier : sinon le graphe montrerait une autre partie que celle jouée."""
    resultat = journal.resultat
    statut, appliques = resultat["statut"], resultat["choixAppliques"]
    choix = f"Ligne « {nom} », choix n° {appliques + 1}"
    if statut == "invalide":
        raise PartieInvalide(f"{choix} : {resultat.get('erreur')}")
    if statut == "divergent":
        raise PartieInvalide(f"{choix} : la partie rejouée a divergé (la décision n'a plus la même empreinte).")
    if statut == "non-supporte":
        raise PartieInvalide(f"{choix} : décision du moteur non prise en charge.")
    if resultat.get("raison") == "limite-atteinte":
        raise PartieInvalide(f"Ligne « {nom} » : limite de messages du moteur atteinte.")
    if appliques < attendus:
        raise PartieInvalide(f"Ligne « {nom} » : la partie s'arrête après {appliques} choix sur {attendus}.")
