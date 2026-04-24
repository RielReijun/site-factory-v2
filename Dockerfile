FROM python:3.11-slim

WORKDIR /workspace

RUN apt-get update && apt-get install -y --no-install-recommends \
    wget curl git ca-certificates gnupg \
    && rm -rf /var/lib/apt/lists/*

# Node.js 20 LTS voor Next.js build
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && npm install -g npm@latest \
    && node --version && npm --version

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Playwright + Chromium voor screenshot-validatie
RUN playwright install-deps chromium 2>/dev/null || true
RUN playwright install chromium

CMD ["bash"]
