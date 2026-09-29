FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DB_PATH=/data/reclutamiento.db

WORKDIR /app

RUN groupadd --system --gid 1001 chatbot \
 && useradd  --system --uid 1001 --gid chatbot --home /app --shell /usr/sbin/nologin chatbot \
 && mkdir -p /data \
 && chown chatbot:chatbot /data

COPY pyproject.toml ./
COPY app ./app
RUN pip install --upgrade pip && pip install . \
 && chown -R chatbot:chatbot /app

# /data guarda la base SQLite (sesiones y temporizadores). En EasyPanel se
# monta como volumen para que sobreviva a los redeploys.
VOLUME ["/data"]

# Sin root en runtime. El puerto 8000 no requiere privilegios.
USER chatbot

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
