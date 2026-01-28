#!/usr/bin/env python3
"""
Payment Worker - Consumes messages from RabbitMQ and persists to PostgreSQL.
"""

import json
import logging
import os
import sys
import time
from datetime import datetime

import pika
import psycopg2
from psycopg2.extras import Json

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)


def get_env(name: str, default: str = None, required: bool = True) -> str:
    """Get environment variable with optional default."""
    value = os.environ.get(name, default)
    if required and value is None:
        logger.error(f"Required environment variable {name} is not set")
        sys.exit(1)
    return value


# Configuration from environment variables
RABBITMQ_HOST = get_env('RABBITMQ_HOST', 'localhost')
RABBITMQ_PORT = int(get_env('RABBITMQ_PORT', '5672'))
RABBITMQ_USER = get_env('RABBITMQ_USER', 'guest')
RABBITMQ_PASS = get_env('RABBITMQ_PASS', 'guest')
POSTGRES_HOST = get_env('POSTGRES_HOST', 'localhost')
POSTGRES_PORT = int(get_env('POSTGRES_PORT', '5432'))
POSTGRES_DB = get_env('POSTGRES_DB', 'payments_db')
POSTGRES_USER = get_env('POSTGRES_USER', 'postgres')
POSTGRES_PASS = get_env('POSTGRES_PASS', 'postgres')
QUEUE_NAME = get_env('QUEUE_NAME', 'payments')
MAX_MESSAGES = os.environ.get('MAX_MESSAGES')
if MAX_MESSAGES:
    MAX_MESSAGES = int(MAX_MESSAGES)


def get_postgres_connection():
    """Create and return a PostgreSQL connection."""
    logger.info(f"Connecting to PostgreSQL at {POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}")
    conn = psycopg2.connect(
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        dbname=POSTGRES_DB,
        user=POSTGRES_USER,
        password=POSTGRES_PASS
    )
    return conn


def run_migrations(conn):
    """Run database migrations - create payments table if not exists."""
    logger.info("Running database migrations...")
    create_table_sql = """
    CREATE TABLE IF NOT EXISTS payments (
        id SERIAL PRIMARY KEY,
        received_at TIMESTAMP NOT NULL DEFAULT NOW(),
        payload JSONB NOT NULL,
        message_id TEXT,
        source_queue TEXT NOT NULL
    );
    
    CREATE INDEX IF NOT EXISTS idx_payments_received_at ON payments(received_at);
    CREATE INDEX IF NOT EXISTS idx_payments_message_id ON payments(message_id);
    """
    with conn.cursor() as cur:
        cur.execute(create_table_sql)
    conn.commit()
    logger.info("Database migrations completed successfully")


def insert_payment(conn, payload: dict, message_id: str, source_queue: str) -> int:
    """Insert a payment record into the database."""
    insert_sql = """
    INSERT INTO payments (received_at, payload, message_id, source_queue)
    VALUES (%s, %s, %s, %s)
    RETURNING id;
    """
    with conn.cursor() as cur:
        cur.execute(insert_sql, (
            datetime.utcnow(),
            Json(payload),
            message_id,
            source_queue
        ))
        payment_id = cur.fetchone()[0]
    conn.commit()
    return payment_id


def get_rabbitmq_connection():
    """Create and return a RabbitMQ connection with retries."""
    credentials = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASS)
    parameters = pika.ConnectionParameters(
        host=RABBITMQ_HOST,
        port=RABBITMQ_PORT,
        credentials=credentials,
        heartbeat=600,
        blocked_connection_timeout=300
    )
    
    max_retries = 10
    retry_delay = 5
    
    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"Connecting to RabbitMQ at {RABBITMQ_HOST}:{RABBITMQ_PORT} (attempt {attempt}/{max_retries})")
            connection = pika.BlockingConnection(parameters)
            logger.info("Successfully connected to RabbitMQ")
            return connection
        except pika.exceptions.AMQPConnectionError as e:
            if attempt < max_retries:
                logger.warning(f"Failed to connect to RabbitMQ: {e}. Retrying in {retry_delay}s...")
                time.sleep(retry_delay)
            else:
                logger.error(f"Failed to connect to RabbitMQ after {max_retries} attempts")
                raise


class PaymentWorker:
    """Worker that consumes messages from RabbitMQ and persists to PostgreSQL."""
    
    def __init__(self):
        self.pg_conn = None
        self.rmq_conn = None
        self.channel = None
        self.messages_processed = 0
        
    def setup(self):
        """Initialize connections and run migrations."""
        # Connect to PostgreSQL with retries
        max_retries = 10
        retry_delay = 5
        
        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f"Connecting to PostgreSQL (attempt {attempt}/{max_retries})")
                self.pg_conn = get_postgres_connection()
                logger.info("Successfully connected to PostgreSQL")
                break
            except psycopg2.OperationalError as e:
                if attempt < max_retries:
                    logger.warning(f"Failed to connect to PostgreSQL: {e}. Retrying in {retry_delay}s...")
                    time.sleep(retry_delay)
                else:
                    logger.error(f"Failed to connect to PostgreSQL after {max_retries} attempts")
                    raise
        
        # Run migrations
        run_migrations(self.pg_conn)
        
        # Connect to RabbitMQ
        self.rmq_conn = get_rabbitmq_connection()
        self.channel = self.rmq_conn.channel()
        
        # Declare queue (idempotent)
        self.channel.queue_declare(queue=QUEUE_NAME, durable=True)
        
        # Set prefetch to 1 for fair dispatch
        self.channel.basic_qos(prefetch_count=1)
        
        logger.info(f"Worker setup complete. Listening on queue: {QUEUE_NAME}")
    
    def process_message(self, ch, method, properties, body):
        """Process a single message from the queue."""
        message_id = properties.message_id if properties.message_id else None
        delivery_tag = method.delivery_tag
        
        logger.info(f"Received message (delivery_tag={delivery_tag}, message_id={message_id})")
        
        try:
            # Parse JSON payload
            try:
                payload = json.loads(body.decode('utf-8'))
            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON in message: {e}")
                # Reject message without requeue for invalid JSON
                ch.basic_nack(delivery_tag=delivery_tag, requeue=False)
                return
            
            logger.info(f"Processing payload: {json.dumps(payload)[:200]}...")
            
            # Insert into PostgreSQL
            payment_id = insert_payment(
                self.pg_conn,
                payload,
                message_id,
                QUEUE_NAME
            )
            
            logger.info(f"Successfully inserted payment with id={payment_id}")
            
            # ACK the message only after successful insert
            ch.basic_ack(delivery_tag=delivery_tag)
            logger.info(f"Message acknowledged (delivery_tag={delivery_tag})")
            
            self.messages_processed += 1
            
            # Check if we've reached MAX_MESSAGES limit
            if MAX_MESSAGES and self.messages_processed >= MAX_MESSAGES:
                logger.info(f"Reached MAX_MESSAGES limit ({MAX_MESSAGES}). Stopping worker.")
                ch.stop_consuming()
                
        except psycopg2.Error as e:
            logger.error(f"Database error: {e}")
            # Don't ACK - message will be requeued
            ch.basic_nack(delivery_tag=delivery_tag, requeue=True)
            logger.warning(f"Message will be requeued (delivery_tag={delivery_tag})")
            
            # Try to reconnect to PostgreSQL
            try:
                self.pg_conn = get_postgres_connection()
                logger.info("Reconnected to PostgreSQL")
            except Exception as reconnect_error:
                logger.error(f"Failed to reconnect to PostgreSQL: {reconnect_error}")
                
        except Exception as e:
            logger.error(f"Unexpected error processing message: {e}")
            ch.basic_nack(delivery_tag=delivery_tag, requeue=True)
            logger.warning(f"Message will be requeued (delivery_tag={delivery_tag})")
    
    def run(self):
        """Start consuming messages."""
        logger.info("Starting message consumption...")
        
        self.channel.basic_consume(
            queue=QUEUE_NAME,
            on_message_callback=self.process_message,
            auto_ack=False
        )
        
        try:
            self.channel.start_consuming()
        except KeyboardInterrupt:
            logger.info("Received shutdown signal")
            self.channel.stop_consuming()
        finally:
            self.cleanup()
    
    def cleanup(self):
        """Clean up connections."""
        logger.info("Cleaning up connections...")
        
        if self.rmq_conn and self.rmq_conn.is_open:
            self.rmq_conn.close()
            logger.info("RabbitMQ connection closed")
            
        if self.pg_conn and not self.pg_conn.closed:
            self.pg_conn.close()
            logger.info("PostgreSQL connection closed")
        
        logger.info(f"Worker finished. Total messages processed: {self.messages_processed}")


def main():
    """Main entry point."""
    logger.info("=" * 60)
    logger.info("Payment Worker Starting")
    logger.info("=" * 60)
    logger.info(f"Configuration:")
    logger.info(f"  RabbitMQ: {RABBITMQ_HOST}:{RABBITMQ_PORT}")
    logger.info(f"  PostgreSQL: {POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}")
    logger.info(f"  Queue: {QUEUE_NAME}")
    logger.info(f"  Max Messages: {MAX_MESSAGES if MAX_MESSAGES else 'unlimited'}")
    logger.info("=" * 60)
    
    worker = PaymentWorker()
    
    try:
        worker.setup()
        worker.run()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)
    
    logger.info("Worker exited successfully")
    sys.exit(0)


if __name__ == '__main__':
    main()
