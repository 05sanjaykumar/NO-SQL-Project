# Stage 1: build the React dashboard (web/dist)
FROM node:20-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# Stage 2: the API, which serves the dashboard at /ui
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src/ src/
COPY configs/ configs/
COPY models/ models/
RUN pip install --no-cache-dir -e ".[dev,redis]"
COPY --from=web /web/dist web/dist

EXPOSE 8000

CMD ["python", "-m", "fraudsim.cli", "api", "--host", "0.0.0.0", "--port", "8000"]
