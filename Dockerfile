FROM python:3.11-slim

WORKDIR /workspace

RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Playwright + Chromium voor screenshot-validatie
RUN playwright install-deps chromium 2>/dev/null || true
RUN playwright install chromium

CMD ["bash"]
