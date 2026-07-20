FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# GMGN's official read-only activity feeds are exposed through gmgn-cli.
# This does not add a wallet or live-trading key; it only enables KOL and
# smart-money monitoring when GMGN_API_KEY is supplied at runtime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm \
    && npm install -g gmgn-cli \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p /data
EXPOSE 8080

CMD ["bash", "start.sh"]
