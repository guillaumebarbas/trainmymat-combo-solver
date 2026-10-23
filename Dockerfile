FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git g++ curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Moteur EDOPro (AGPL-3.0), scripts, base de cartes et strings.conf, épinglés dans build_core.sh.
COPY build_core.sh .
RUN ./build_core.sh \
    && rm -rf vendor/build vendor/ocgcore/.git vendor/scripts/.git vendor/cdb/.git

COPY solver ./solver
COPY app.py LICENSE NOTICE.md ./

# Commit du service déployé, exposé par GET /version (offre de source AGPL). Vide : app.py reprend SOURCE_COMMIT,
# que Coolify injecte au démarrage, sinon « dev ».
ARG COMBO_SOLVER_VERSION=""
ENV COMBO_SOLVER_VERSION=${COMBO_SOLVER_VERSION}

EXPOSE 8002
# Un moteur par processus : plusieurs workers pour servir plusieurs joueurs en parallèle.
CMD ["sh", "-c", "exec uvicorn app:app --host 0.0.0.0 --port 8002 --workers ${COMBO_SOLVER_WORKERS:-2}"]
