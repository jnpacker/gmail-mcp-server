# syntax=docker/dockerfile:1
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates jq \
    && rm -rf /var/lib/apt/lists/*

# Node.js 22 via NodeSource
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Claude Code CLI
RUN npm install -g @anthropic-ai/claude-code

WORKDIR /app

COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.txt && pip install --no-cache-dir -e .

COPY . .

# Defense-in-depth: strip any accidentally copied auth files
RUN rm -f credentials.json token.json .pincode .flask_secret

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 5000
ENTRYPOINT ["/entrypoint.sh"]
