FROM python:3.11-slim

WORKDIR /app

# Install system dependencies (git, curl)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy and install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . .

# Create data directory for SQLite database
RUN mkdir -p data

EXPOSE 8085

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8085/health || exit 1

CMD ["python", "-m", "deploy_automation.main"]
