FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src/ src/

RUN pip install --no-cache-dir -e ".[dev,redis]"

EXPOSE 8000

CMD ["python", "-m", "fraudsim.cli", "api", "--host", "0.0.0.0", "--port", "8000"]
