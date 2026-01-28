#!/usr/bin/env python3
"""
Script to publish test messages to RabbitMQ for testing the payment worker.

Usage:
    python publish_test_messages.py [--count N] [--queue QUEUE_NAME]

Environment variables:
    RABBITMQ_HOST: RabbitMQ host (default: localhost)
    RABBITMQ_PORT: RabbitMQ port (default: 5672)
    RABBITMQ_USER: RabbitMQ user (default: guest)
    RABBITMQ_PASS: RabbitMQ password (default: guest)
    QUEUE_NAME: Queue name (default: payments)
"""

import argparse
import json
import os
import sys
import uuid
from datetime import datetime
from decimal import Decimal

import pika


def get_env(name: str, default: str) -> str:
    """Get environment variable with default."""
    return os.environ.get(name, default)


def generate_test_payment(index: int) -> dict:
    """Generate a random test payment payload."""
    payment_types = ['credit_card', 'debit_card', 'pix', 'boleto', 'transfer']
    currencies = ['BRL', 'USD', 'EUR']
    statuses = ['pending', 'approved', 'processing']
    
    return {
        'transaction_id': str(uuid.uuid4()),
        'order_id': f'ORD-{index:06d}',
        'customer': {
            'id': f'CUST-{(index % 100):04d}',
            'name': f'Customer {index}',
            'email': f'customer{index}@example.com'
        },
        'amount': round(10.0 + (index * 7.5 % 1000), 2),
        'currency': currencies[index % len(currencies)],
        'payment_method': payment_types[index % len(payment_types)],
        'status': statuses[index % len(statuses)],
        'metadata': {
            'source': 'test_script',
            'test_index': index,
            'generated_at': datetime.utcnow().isoformat()
        },
        'created_at': datetime.utcnow().isoformat()
    }


def main():
    parser = argparse.ArgumentParser(description='Publish test messages to RabbitMQ')
    parser.add_argument(
        '--count', '-n',
        type=int,
        default=10,
        help='Number of messages to publish (default: 10)'
    )
    parser.add_argument(
        '--queue', '-q',
        type=str,
        default=None,
        help='Queue name (default: from QUEUE_NAME env or "payments")'
    )
    parser.add_argument(
        '--host',
        type=str,
        default=None,
        help='RabbitMQ host (default: from RABBITMQ_HOST env or "localhost")'
    )
    parser.add_argument(
        '--port',
        type=int,
        default=None,
        help='RabbitMQ port (default: from RABBITMQ_PORT env or 5672)'
    )
    
    args = parser.parse_args()
    
    # Configuration
    host = args.host or get_env('RABBITMQ_HOST', 'localhost')
    port = args.port or int(get_env('RABBITMQ_PORT', '5672'))
    user = get_env('RABBITMQ_USER', 'guest')
    password = get_env('RABBITMQ_PASS', 'guest')
    queue_name = args.queue or get_env('QUEUE_NAME', 'payments')
    message_count = args.count
    
    print(f"=" * 60)
    print(f"Publishing Test Messages")
    print(f"=" * 60)
    print(f"RabbitMQ: {host}:{port}")
    print(f"Queue: {queue_name}")
    print(f"Message count: {message_count}")
    print(f"=" * 60)
    
    # Connect to RabbitMQ
    try:
        credentials = pika.PlainCredentials(user, password)
        parameters = pika.ConnectionParameters(
            host=host,
            port=port,
            credentials=credentials
        )
        connection = pika.BlockingConnection(parameters)
        channel = connection.channel()
        print(f"Connected to RabbitMQ")
    except Exception as e:
        print(f"ERROR: Failed to connect to RabbitMQ: {e}")
        sys.exit(1)
    
    # Declare queue (idempotent)
    channel.queue_declare(queue=queue_name, durable=True)
    print(f"Queue '{queue_name}' declared")
    
    # Publish messages
    published = 0
    for i in range(1, message_count + 1):
        try:
            payload = generate_test_payment(i)
            message_id = str(uuid.uuid4())
            
            channel.basic_publish(
                exchange='',
                routing_key=queue_name,
                body=json.dumps(payload),
                properties=pika.BasicProperties(
                    delivery_mode=2,  # Persistent
                    content_type='application/json',
                    message_id=message_id
                )
            )
            
            print(f"[{i}/{message_count}] Published message_id={message_id[:8]}... "
                  f"order_id={payload['order_id']} amount={payload['amount']} {payload['currency']}")
            published += 1
            
        except Exception as e:
            print(f"ERROR publishing message {i}: {e}")
    
    # Close connection
    connection.close()
    
    print(f"=" * 60)
    print(f"Done! Published {published}/{message_count} messages to queue '{queue_name}'")
    print(f"=" * 60)


if __name__ == '__main__':
    main()
