"""Service moteur de TrainMyMat : génération de graphes de combos et simulation interactive.

Moteur de règles : EDOPro ocgcore + CardScripts Project Ignis (AGPL-3.0).
Ce service est donc distribué sous AGPL-3.0, comme draw2-service.
"""

import hmac
import json
import logging
import os
from threading import Lock
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field, field_validator

from solver.ocg import VENDOR, Engine
from solver.service import RequeteInvalide, Requete, generer
from solver.simulateur import Choix, PartieInvalide, PartieSimulee, simuler, valider

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("combo-solver")

app = FastAPI(title="TrainMyMat Combo Solver")
TOKEN = os.environ.get("COMBO_SOLVER_TOKEN", "")
SOURCE = "https://github.com/guillaumebarbas/trainmymat-combo-solver"
_engine: Engine | None = None
# Le moteur et ses callbacks ctypes ne sont pas prévus pour un usage concurrent : un moteur par processus
# (plusieurs workers uvicorn pour servir en parallèle).
_verrou = Lock()

Code = Annotated[int, Field(ge=1, le=0xFFFFFFFF)]


class LimitesDTO(BaseModel):
    maxCartes: int = Field(25, ge=1, le=10_000)
    maxLiens: int = Field(75, ge=1, le=30_000)
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


class SimulationDTO(BaseModel):
    deck: list[Code] = Field(..., min_length=1, max_length=60)
    extra: list[Code] = Field(default_factory=list, max_length=15)
    main: list[Code] = Field(..., min_length=1, max_length=6)
    # Entier 64 bits transmis en texte : JavaScript perd la précision au-delà de 2^53.
    graine: str = Field(..., pattern=r"^\d{1,20}$")
    choix: list[ChoixDTO] = Field(default_factory=list, max_length=1500)
    evenementsDepuis: int = Field(0, ge=0)

    @field_validator("graine")
    @classmethod
    def graine_sur_64_bits(cls, graine: str) -> str:
        if int(graine) >= 1 << 64:
            raise ValueError("La graine doit tenir sur 64 bits.")
        return graine


def engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = Engine()
    return _engine


def verifier_jeton(jeton: str):
    if not TOKEN:
        raise HTTPException(status_code=503, detail="Service non configuré : COMBO_SOLVER_TOKEN manquant.")
    if not hmac.compare_digest(jeton, TOKEN):
        raise HTTPException(status_code=401, detail="Jeton invalide.")


def versions() -> dict:
    """Commits du moteur et des données (écrits par build_core.sh), à stocker avec chaque replay.

    `source` + `service` forment l'offre de code source AGPL affichée par l'application : un fork déployé
    doit faire pointer `SOURCE` vers son propre dépôt.
    """
    fichier = VENDOR / "versions.json"
    epinglees = json.loads(fichier.read_text()) if fichier.is_file() else {}
    return {**epinglees, "service": os.environ.get("COMBO_SOLVER_VERSION", "dev"), "source": SOURCE}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/version")
def version():
    return versions()


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
    """Rejoue la partie (graine + choix) et renvoie la décision en cours, le plateau et les événements."""
    verifier_jeton(x_solver_token)
    partie = PartieSimulee(tuple(requete.deck), tuple(requete.extra), tuple(requete.main), int(requete.graine))
    choix = [Choix(tuple(c.indices), c.valeur, c.empreinte) for c in requete.choix]
    try:
        valider(partie)
        with _verrou:
            resultat = simuler(engine(), partie, choix, requete.evenementsDepuis)
    except PartieInvalide as erreur:
        raise HTTPException(status_code=400, detail=str(erreur)) from erreur
    logger.info("Simulation : %s choix, statut %s", resultat["choixAppliques"], resultat["statut"])
    return {**resultat, "versions": versions()}
