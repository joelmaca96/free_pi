# Imagen de la interfaz Streamlit. Build multi-arch (funciona igual en la
# Raspberry Pi arm64 que en una máquina de desarrollo amd64) — Python puro,
# sin paso de compilación propio.
#
# Contexto de build: la RAÍZ del repo (necesita `packages/` además de `app/`,
# ver docker-compose.yml → build.context).
FROM python:3.12-slim

WORKDIR /srv/baskonia

COPY app/requirements.txt ./app/requirements.txt
RUN pip install --no-cache-dir -r app/requirements.txt

# Solo lo que la interfaz necesita del dominio compartido: esquema + engine
# de solo lectura. `ingest/` no viaja en esta imagen — la ingesta es un
# proceso aparte (ver deploy/systemd/).
COPY packages/ ./packages/
COPY app/ ./app/

ENV PYTHONUNBUFFERED=1
EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')" || exit 1

CMD ["streamlit", "run", "app/Home.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true"]
