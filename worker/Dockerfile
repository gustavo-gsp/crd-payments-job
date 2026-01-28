FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY main.py .

# Run as non-root user
RUN useradd -m -u 1000 worker
USER worker

# Default environment variables
ENV RABBITMQ_HOST=rabbitmq
ENV RABBITMQ_PORT=5672
ENV RABBITMQ_USER=guest
ENV RABBITMQ_PASS=guest
ENV POSTGRES_HOST=postgres
ENV POSTGRES_PORT=5432
ENV POSTGRES_DB=payments_db
ENV POSTGRES_USER=postgres
ENV POSTGRES_PASS=postgres
ENV QUEUE_NAME=payments

CMD ["python", "-u", "main.py"]
