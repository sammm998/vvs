# Webbapp för mängdning av VVS/VA-ritningar.
# Byggs och driftsätts på t.ex. Railway, Render eller Fly.io
# (plattformar som klarar långkörande Python-jobb med Tesseract –
# serverless-plattformar som Vercel har för korta tidsgränser för OCR:en).

FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tesseract-ocr tesseract-ocr-swe \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8000 \
    JOBS_DIR=/data/jobs \
    PYTHONUNBUFFERED=1

EXPOSE 8000

# Railway/Render sätter PORT själva; shell-form så variabeln expanderas.
CMD uvicorn webapp.app:app --host 0.0.0.0 --port ${PORT}
