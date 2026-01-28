FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY main.py .

# Run as non-root user
RUN useradd -m -u 1000 -g operator appuser
USER appuser

# Run kopf operator
CMD ["kopf", "run", "--liveness=http://0.0.0.0:8080/healthz", "--all-namespaces", "main.py"]
