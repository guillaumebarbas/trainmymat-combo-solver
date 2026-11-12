"""Service moteur de TrainMyMat : génération de graphes de combos et simulation interactive.

Moteur de règles : EDOPro ocgcore + CardScripts Project Ignis (AGPL-3.0).
Ce service est donc distribué sous AGPL-3.0, comme draw2-service.
"""

import hmac
import logging
import os
import time
from contextlib import asynccontextmanager
from threading import Lock, Thread
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field, field_validator

from solver.cartes import Donnees, MiseAJourImpossible, donnees_du_service
from solver.graphe_simulation import MAX_LIGNES, TITRE_PAR_DEFAUT, DemandeGraphe, LigneSimulee, graphe_des_lignes
from solver.ocg import Engine
from solver.service import RequeteInvalide, Requete, generer
from solver.simulateur import MAX_TOURS, Adversaire, Choix, PartieInvalide, PartieSimulee, simuler, valider

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("combo-solver")


@asynccontextmanager
async def cycle_de_vie(_app: FastAPI):
    # Un conteneur neuf démarre sur les données de l'image : il rattrape Project Ignis sans attendre la nuit.
    if os.environ.get("COMBO_SOLVER_CARTES_AUTO") == "1":
        Thread(target=_mettre_a_jour_les_cartes_au_demarrage, name="cartes-au-demarrage", daemon=True).start()
    yield


def _mettre_a_jour_les_cartes_au_demarrage():
    try:
        mettre_a_jour_les_cartes()
    except MiseAJourImpossible:
        pass  # déjà journalisé ; le service tourne sur les données de l'image
    except Exception:
        logger.exception("Données de cartes : mise à jour du démarrage interrompue")


app = FastAPI(title="TrainMyMat Combo Solver", lifespan=cycle_de_vie)
TOKEN = os.environ.get("COMBO_SOLVER_TOKEN", "")
SOURCE = "https://github.com/guillaumebarbas/trainmymat-combo-solver"
DONNEES = donnees_du_service()
_moteur: tuple[Donnees, Engine] | None = None
# Le moteur et ses callbacks ctypes ne sont pas prévus pour un usage concurrent : un moteur par processus
# (plusieurs workers uvicorn pour servir en parallèle).
_verrou = Lock()

Code = Annotated[int, Field(ge=1, le=0xFFFFFFFF)]


class LimitesGrapheDTO(BaseModel):
    maxCartes: int = Field(25, ge=1, le=10_000)
    maxLiens: int = Field(75, ge=1, le=30_000)


class LimitesDTO(LimitesGrapheDTO):
    maxLignes: int = Field(3, ge=1, le=30)


class GenerationDTO(BaseModel):
    titre: str = Field("Graphe généré", max_length=160)
    deck: list[int] = Field(..., max_length=60)
    extra: list[int] = Field(default_factory=list, max_length=15)
    main: list[int] = Field(..., min_length=1, max_length=6)
    limites: LimitesDTO = Field(default_factory=LimitesDTO)


class ChoixDTO(BaseModel):
    indices: list[int] = Field(default_factory=list, max_length=60)
    valeur: int | None = Field(None, ge=1, le=0xFFFFFFFF)
    empreinte: str | None = Field(None, pattern=r"^[0-9a-f]{16}$")


class AdversaireDTO(BaseModel):
    """Cartes du joueur 1 ; sa main de départ fait partie de son deck."""
    deck: list[Code] = Field(default_factory=list, max_length=60)
    extra: list[Code] = Field(default_factory=list, max_length=15)
    main: list[Code] = Field(default_factory=list, max_length=6)


class PartieDTO(BaseModel):
    """Partie rejouée par le simulateur, commune à `/simulation/etat` et `/simulation/graphe`."""
    deck: list[Code] = Field(..., min_length=1, max_length=60)
    extra: list[Code] = Field(default_factory=list, max_length=15)
    main: list[Code] = Field(..., min_length=1, max_length=6)
    # Entier 64 bits transmis en texte : JavaScript perd la précision au-delà de 2^53.
    graine: str = Field(..., pattern=r"^\d{1,20}$")
    # Absent, null ou sans aucune carte : le joueur 1 passe toujours, comme avant l'ajout de ce champ.
    adversaire: AdversaireDTO | None = None
    # Absent ou 1 : la partie s'arrête au début du tour 2, comme avant l'ajout de ce champ.
    tours: int = Field(1, ge=1, le=MAX_TOURS)

    @field_validator("graine")
    @classmethod
    def graine_sur_64_bits(cls, graine: str) -> str:
        if int(graine) >= 1 << 64:
            raise ValueError("La graine doit tenir sur 64 bits.")
        return graine

    def partie(self) -> PartieSimulee:
        adverse = self.adversaire
        adversaire = Adversaire(tuple(adverse.deck), tuple(adverse.extra), tuple(adverse.main)) if adverse else None
        return PartieSimulee(tuple(self.deck), tuple(self.extra), tuple(self.main), int(self.graine), adversaire, self.tours)


class SimulationDTO(PartieDTO):
    choix: list[ChoixDTO] = Field(default_factory=list, max_length=1500)
    evenementsDepuis: int = Field(0, ge=0)


class LigneDTO(BaseModel):
    """Ligne d'un arbre de simulation : ses choix depuis le début de la partie, comme `SimulationDTO.choix`."""
    nom: str = Field(..., min_length=1, max_length=20)
    choix: list[ChoixDTO] = Field(default_factory=list, max_length=1500)


class GrapheSimulationDTO(PartieDTO):
    titre: str = Field(TITRE_PAR_DEFAUT, max_length=160)
    # Joueur du moteur dont les cartes sont « à soi » dans le graphe : 1 quand l'utilisateur joue en second.
    joueur: int = Field(0, ge=0, le=1)
    lignes: list[LigneDTO] = Field(..., min_length=1, max_length=MAX_LIGNES)
    limites: LimitesGrapheDTO = Field(default_factory=LimitesGrapheDTO)


def _choix(choix: list[ChoixDTO]) -> list[Choix]:
    return [Choix(tuple(c.indices), c.valeur, c.empreinte) for c in choix]


def engine() -> Engine:
    """Moteur du processus, reconstruit quand d'autres données de cartes passent en service (autre worker compris)."""
    global _moteur
    donnees = DONNEES.actives()
    if _moteur is None or _moteur[0] != donnees:
        _moteur = (donnees, donnees.moteur())
    return _moteur[1]


def donnees_du_moteur() -> Donnees | None:
    """Données du moteur de ce processus : celles de la partie qui vient d'être jouée sous `_verrou`."""
    return _moteur[0] if _moteur else None


def verifier_jeton(jeton: str):
    if not TOKEN:
        raise HTTPException(status_code=503, detail="Service non configuré : COMBO_SOLVER_TOKEN manquant.")
    if not hmac.compare_digest(jeton, TOKEN):
        raise HTTPException(status_code=401, detail="Jeton invalide.")


def versions(donnees: Donnees | None = None) -> dict:
    """Commits du moteur (image) et des données de cartes en service, à stocker avec chaque replay.

    `source` + `service` forment l'offre de code source AGPL affichée par l'application : un fork déployé
    doit faire pointer `SOURCE` vers son propre dépôt. Sans COMBO_SOLVER_VERSION au build, le commit vient de
    SOURCE_COMMIT, injecté au démarrage par Coolify. Les données de cartes sont celles de Project Ignis à ces commits.
    """
    service = os.environ.get("COMBO_SOLVER_VERSION") or os.environ.get("SOURCE_COMMIT") or "dev"
    return {**DONNEES.versions(donnees), "service": service, "source": SOURCE}


def mettre_a_jour_les_cartes() -> dict:
    """Met les données de cartes sur les derniers commits de Project Ignis ; journalise le bilan."""
    debut = time.perf_counter()
    try:
        resultat = DONNEES.mettre_a_jour(verrou_moteur=_verrou)
    except MiseAJourImpossible as erreur:
        logger.error("Données de cartes : mise à jour impossible, données en service gardées (%s)", erreur)
        raise
    secondes = round(time.perf_counter() - debut, 1)
    if resultat["statut"] == "mise-a-jour":
        apres = versions()
        changes = ", ".join(f"{cle} {resultat['avant'].get(cle, '?')[:10]} → {apres[cle][:10]}"
                            for cle in ("scripts", "cartes", "chaines") if resultat["avant"].get(cle) != apres[cle])
        logger.info("Données de cartes mises à jour : %s (%s s)", changes, secondes)
    else:
        logger.info("Données de cartes : %s (%s s)", resultat["statut"], secondes)
    return {**resultat, "secondes": secondes}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/version")
def version():
    return versions()


@app.post("/cartes/mettre-a-jour")
def mettre_a_jour_cartes(x_solver_token: str = Header(default="")):
    """Amène les données de cartes sur les derniers commits de Project Ignis (appelé chaque nuit par le backend).

    `statut` : `a-jour`, `mise-a-jour` (avec `avant`), `en-cours` (un autre worker s'en charge) ou `desactivee`
    (COMBO_SOLVER_CARTES_AUTO≠1 : le service garde les données de l'image). 502 si les dépôts sont injoignables ou si
    les nouvelles données ratent le duel d'essai : les données en service restent les mêmes.
    """
    verifier_jeton(x_solver_token)
    if os.environ.get("COMBO_SOLVER_CARTES_AUTO") != "1":
        return {"statut": "desactivee", "versions": versions()}
    try:
        resultat = mettre_a_jour_les_cartes()
    except MiseAJourImpossible as erreur:
        raise HTTPException(status_code=502, detail=str(erreur)) from erreur
    return {**resultat, "versions": versions()}


@app.post("/generer")
def generer_graphe(requete: GenerationDTO, x_solver_token: str = Header(default="")):
    verifier_jeton(x_solver_token)
    try:
        with _verrou:
            resultat = generer(engine(), Requete(
                titre=requete.titre, deck=tuple(requete.deck), extra=tuple(requete.extra), main=tuple(requete.main),
                max_cartes=requete.limites.maxCartes, max_liens=requete.limites.maxLiens, max_lignes=requete.limites.maxLignes,
            ))
    except RequeteInvalide as erreur:
        raise HTTPException(status_code=400, detail=str(erreur)) from erreur
    stats = resultat["stats"]
    logger.info("Graphe généré : %s parties, %ss, %s lignes", stats["parties"], stats["secondes"], stats["lignes"])
    return resultat


@app.post("/simulation/etat")
def etat_simulation(requete: SimulationDTO, x_solver_token: str = Header(default="")):
    """Rejoue la partie (graine + choix) et renvoie la décision en cours, le plateau et les événements.

    Avec un adversaire, ses décisions (`joueur: 1`) sont exposées et répondues dans la même liste de choix.
    Avec `tours: 2`, le tour adverse est joué aussi ; le début du tour `tours + 1` termine la partie.
    """
    verifier_jeton(x_solver_token)
    partie = requete.partie()
    try:
        valider(partie)
        debut = time.perf_counter()
        with _verrou:
            attente = time.perf_counter() - debut
            resultat = simuler(engine(), partie, _choix(requete.choix), requete.evenementsDepuis)
            # Versions des données qui ont joué cette partie, même si d'autres passent en service juste après.
            donnees = donnees_du_moteur()
        duree = time.perf_counter() - debut
    except PartieInvalide as erreur:
        raise HTTPException(status_code=400, detail=str(erreur)) from erreur
    # Durée vue par le backend (attente comprise) : elle croît avec le nombre de choix, la partie étant rejouée en entier.
    logger.info("Simulation : %s choix, statut %s, %d ms (attente du moteur %d ms)",
                resultat["choixAppliques"], resultat["statut"], duree * 1000, attente * 1000)
    return {**resultat, "versions": versions(donnees)}


@app.post("/simulation/graphe")
def graphe_simulation(requete: GrapheSimulationDTO, x_solver_token: str = Header(default="")):
    """Graphe TrainMyMat des lignes d'un arbre de simulation : une branche par ligne, les débuts communs partagés.

    400 si une ligne ne se rejoue pas jusqu'à son dernier choix (choix refusé, empreinte divergente…).
    """
    verifier_jeton(x_solver_token)
    demande = DemandeGraphe(
        titre=requete.titre, partie=requete.partie(), joueur=requete.joueur,
        lignes=tuple(LigneSimulee(ligne.nom, tuple(_choix(ligne.choix))) for ligne in requete.lignes),
        max_cartes=requete.limites.maxCartes, max_liens=requete.limites.maxLiens,
    )
    try:
        valider(demande.partie)
        debut = time.perf_counter()
        with _verrou:
            attente = time.perf_counter() - debut
            resultat = graphe_des_lignes(engine(), demande)
    except PartieInvalide as erreur:
        raise HTTPException(status_code=400, detail=str(erreur)) from erreur
    stats = resultat["stats"]
    logger.info("Graphe de simulation : %s lignes dessinées, %s ignorées, %ss (attente du moteur %d ms)",
                stats["lignes"], len(stats["lignesIgnorees"]), stats["secondes"], attente * 1000)
    return resultat
