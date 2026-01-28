#!/bin/bash
#
# End-to-End Test Script for PaymentJob Operator
#
# This script automates the complete test flow:
# 1. Create kind cluster
# 2. Apply CRD
# 3. Deploy RabbitMQ and PostgreSQL
# 4. Create secrets
# 5. Build and load images
# 6. Deploy operator
# 7. Publish test messages
# 8. Create PaymentJob
# 9. Wait for completion
# 10. Verify data in PostgreSQL
#

set -euo pipefail

# Configuration
CLUSTER_NAME="${CLUSTER_NAME:-payments-e2e}"
NAMESPACE="payments-test"
MESSAGE_COUNT="${MESSAGE_COUNT:-10}"
TIMEOUT="${TIMEOUT:-120s}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

log_step() {
    echo ""
    echo -e "${GREEN}============================================================${NC}"
    echo -e "${GREEN}STEP: $1${NC}"
    echo -e "${GREEN}============================================================${NC}"
}

# Cleanup function
cleanup() {
    local exit_code=$?
    if [[ $exit_code -ne 0 ]]; then
        log_error "Script failed with exit code $exit_code"
        log_info "Collecting debug information..."
        
        echo ""
        echo "--- Operator Logs ---"
        kubectl logs -n paymentjob-system -l app.kubernetes.io/name=paymentjob-operator --tail=50 2>/dev/null || true
        
        echo ""
        echo "--- PaymentJob Status ---"
        kubectl get paymentjobs -n "$NAMESPACE" -o yaml 2>/dev/null || true
        
        echo ""
        echo "--- Jobs ---"
        kubectl get jobs -n "$NAMESPACE" 2>/dev/null || true
        
        echo ""
        echo "--- Pods ---"
        kubectl get pods -n "$NAMESPACE" 2>/dev/null || true
        
        echo ""
        echo "--- Events ---"
        kubectl get events -n "$NAMESPACE" --sort-by='.lastTimestamp' 2>/dev/null | tail -20 || true
    fi
}

trap cleanup EXIT

# Check prerequisites
check_prerequisites() {
    log_step "Checking Prerequisites"
    
    local missing=()
    
    command -v docker &>/dev/null || missing+=("docker")
    command -v kubectl &>/dev/null || missing+=("kubectl")
    command -v kind &>/dev/null || missing+=("kind")
    
    if [[ ${#missing[@]} -gt 0 ]]; then
        log_error "Missing required tools: ${missing[*]}"
        exit 1
    fi
    
    # Check Docker is running
    if ! docker info &>/dev/null; then
        log_error "Docker is not running"
        exit 1
    fi
    
    log_success "All prerequisites met"
}

# Create or use existing kind cluster
setup_cluster() {
    log_step "Setting up Kind Cluster: $CLUSTER_NAME"
    
    if kind get clusters 2>/dev/null | grep -q "^${CLUSTER_NAME}$"; then
        log_info "Cluster '$CLUSTER_NAME' already exists, using it"
    else
        log_info "Creating new cluster '$CLUSTER_NAME'..."
        kind create cluster --name "$CLUSTER_NAME" --wait 60s
    fi
    
    # Set context
    kubectl cluster-info --context "kind-${CLUSTER_NAME}"
    log_success "Cluster is ready"
}

# Apply CRD
apply_crd() {
    log_step "Applying PaymentJob CRD"
    
    kubectl apply -f "$PROJECT_ROOT/manifests/crd-paymentjob.yaml"
    
    # Wait for CRD to be established
    kubectl wait --for=condition=Established crd/paymentjobs.payments.example.com --timeout=30s
    
    log_success "CRD applied and established"
}

# Deploy infrastructure (RabbitMQ + PostgreSQL)
deploy_infrastructure() {
    log_step "Deploying Infrastructure (RabbitMQ + PostgreSQL)"
    
    # Create namespace
    kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -
    
    # Deploy RabbitMQ
    log_info "Deploying RabbitMQ..."
    kubectl apply -n "$NAMESPACE" -f - <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: rabbitmq
  labels:
    app: rabbitmq
spec:
  replicas: 1
  selector:
    matchLabels:
      app: rabbitmq
  template:
    metadata:
      labels:
        app: rabbitmq
    spec:
      containers:
      - name: rabbitmq
        image: rabbitmq:3.12-management-alpine
        ports:
        - containerPort: 5672
          name: amqp
        - containerPort: 15672
          name: management
        readinessProbe:
          exec:
            command: ["rabbitmq-diagnostics", "check_port_connectivity"]
          initialDelaySeconds: 10
          periodSeconds: 5
          timeoutSeconds: 10
        resources:
          requests:
            cpu: 100m
            memory: 256Mi
          limits:
            cpu: 500m
            memory: 512Mi
---
apiVersion: v1
kind: Service
metadata:
  name: rabbitmq
spec:
  selector:
    app: rabbitmq
  ports:
  - name: amqp
    port: 5672
    targetPort: 5672
  - name: management
    port: 15672
    targetPort: 15672
EOF

    # Deploy PostgreSQL
    log_info "Deploying PostgreSQL..."
    kubectl apply -n "$NAMESPACE" -f - <<'EOF'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: postgres
  labels:
    app: postgres
spec:
  replicas: 1
  selector:
    matchLabels:
      app: postgres
  template:
    metadata:
      labels:
        app: postgres
    spec:
      containers:
      - name: postgres
        image: postgres:15-alpine
        env:
        - name: POSTGRES_USER
          value: postgres
        - name: POSTGRES_PASSWORD
          value: postgres
        - name: POSTGRES_DB
          value: payments_db
        ports:
        - containerPort: 5432
        readinessProbe:
          exec:
            command: ["pg_isready", "-U", "postgres", "-d", "payments_db"]
          initialDelaySeconds: 5
          periodSeconds: 5
          timeoutSeconds: 5
        resources:
          requests:
            cpu: 100m
            memory: 256Mi
          limits:
            cpu: 500m
            memory: 512Mi
---
apiVersion: v1
kind: Service
metadata:
  name: postgres
spec:
  selector:
    app: postgres
  ports:
  - port: 5432
    targetPort: 5432
EOF

    # Wait for pods to be ready
    log_info "Waiting for RabbitMQ to be ready..."
    kubectl wait -n "$NAMESPACE" --for=condition=ready pod -l app=rabbitmq --timeout="$TIMEOUT"
    
    log_info "Waiting for PostgreSQL to be ready..."
    kubectl wait -n "$NAMESPACE" --for=condition=ready pod -l app=postgres --timeout="$TIMEOUT"
    
    log_success "Infrastructure deployed and ready"
}

# Create secrets
create_secrets() {
    log_step "Creating Secrets"
    
    # RabbitMQ credentials
    kubectl create secret generic rabbitmq-credentials \
        -n "$NAMESPACE" \
        --from-literal=username=guest \
        --from-literal=password=guest \
        --dry-run=client -o yaml | kubectl apply -f -
    
    # PostgreSQL credentials
    kubectl create secret generic postgres-credentials \
        -n "$NAMESPACE" \
        --from-literal=username=postgres \
        --from-literal=password=postgres \
        --dry-run=client -o yaml | kubectl apply -f -
    
    log_success "Secrets created"
}

# Build and load images
build_and_load_images() {
    log_step "Building and Loading Docker Images"
    
    # Build worker image
    log_info "Building worker image..."
    docker build -t payments-worker:latest "$PROJECT_ROOT/worker"
    
    # Build operator image
    log_info "Building operator image..."
    docker build -t paymentjob-operator:latest "$PROJECT_ROOT/operator"
    
    # Load images into kind
    log_info "Loading images into kind cluster..."
    kind load docker-image payments-worker:latest --name "$CLUSTER_NAME"
    kind load docker-image paymentjob-operator:latest --name "$CLUSTER_NAME"
    
    log_success "Images built and loaded"
}

# Deploy operator
deploy_operator() {
    log_step "Deploying PaymentJob Operator"
    
    kubectl apply -f "$PROJECT_ROOT/manifests/operator-deploy.yaml"
    
    # Wait for operator to be ready
    log_info "Waiting for operator to be ready..."
    kubectl wait -n paymentjob-system --for=condition=ready pod -l app.kubernetes.io/name=paymentjob-operator --timeout="$TIMEOUT"
    
    # Give operator a moment to initialize
    sleep 5
    
    log_success "Operator deployed and ready"
}

# Publish test messages
publish_messages() {
    log_step "Publishing $MESSAGE_COUNT Test Messages to RabbitMQ"
    
    # Create a one-shot pod to publish messages
    kubectl apply -n "$NAMESPACE" -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: message-publisher
  labels:
    app: message-publisher
spec:
  restartPolicy: Never
  containers:
  - name: publisher
    image: python:3.11-slim
    command:
    - /bin/bash
    - -c
    - |
      pip install pika --quiet
      python3 << 'PYTHON_SCRIPT'
import json
import uuid
import pika
from datetime import datetime

RABBITMQ_HOST = "rabbitmq"
QUEUE_NAME = "payments"
MESSAGE_COUNT = $MESSAGE_COUNT

print(f"Connecting to RabbitMQ at {RABBITMQ_HOST}...")
credentials = pika.PlainCredentials('guest', 'guest')
connection = pika.BlockingConnection(
    pika.ConnectionParameters(host=RABBITMQ_HOST, credentials=credentials)
)
channel = connection.channel()
channel.queue_declare(queue=QUEUE_NAME, durable=True)

print(f"Publishing {MESSAGE_COUNT} messages...")
for i in range(1, MESSAGE_COUNT + 1):
    payload = {
        'transaction_id': str(uuid.uuid4()),
        'order_id': f'ORD-E2E-{i:04d}',
        'amount': round(10.0 + (i * 5.5), 2),
        'currency': 'BRL',
        'created_at': datetime.utcnow().isoformat()
    }
    channel.basic_publish(
        exchange='',
        routing_key=QUEUE_NAME,
        body=json.dumps(payload),
        properties=pika.BasicProperties(
            delivery_mode=2,
            message_id=str(uuid.uuid4())
        )
    )
    print(f"  [{i}/{MESSAGE_COUNT}] Published order_id={payload['order_id']}")

connection.close()
print(f"Done! Published {MESSAGE_COUNT} messages.")
PYTHON_SCRIPT
EOF

    # Wait for publisher pod to complete
    log_info "Waiting for message publisher to complete..."
    kubectl wait -n "$NAMESPACE" --for=condition=ready pod/message-publisher --timeout=60s || true
    
    # Wait for pod to finish (not just ready)
    local max_wait=60
    local waited=0
    while [[ $waited -lt $max_wait ]]; do
        local phase=$(kubectl get pod -n "$NAMESPACE" message-publisher -o jsonpath='{.status.phase}' 2>/dev/null || echo "Unknown")
        if [[ "$phase" == "Succeeded" ]]; then
            break
        elif [[ "$phase" == "Failed" ]]; then
            log_error "Publisher pod failed"
            kubectl logs -n "$NAMESPACE" message-publisher
            exit 1
        fi
        sleep 2
        waited=$((waited + 2))
    done
    
    # Show publisher logs
    log_info "Publisher output:"
    kubectl logs -n "$NAMESPACE" message-publisher
    
    # Cleanup publisher pod
    kubectl delete pod -n "$NAMESPACE" message-publisher --ignore-not-found
    
    log_success "Messages published"
}

# Create PaymentJob
create_paymentjob() {
    log_step "Creating PaymentJob"
    
    kubectl apply -n "$NAMESPACE" -f - <<EOF
apiVersion: payments.example.com/v1alpha1
kind: PaymentJob
metadata:
  name: e2e-test-job
  labels:
    app: e2e-test
spec:
  queueName: payments
  image: payments-worker:latest
  maxMessages: $MESSAGE_COUNT
  rabbitmq:
    host: rabbitmq.$NAMESPACE.svc.cluster.local
    port: 5672
    secretRef:
      name: rabbitmq-credentials
  postgres:
    host: postgres.$NAMESPACE.svc.cluster.local
    port: 5432
    database: payments_db
    secretRef:
      name: postgres-credentials
EOF

    log_success "PaymentJob created"
}

# Wait for job completion
wait_for_job() {
    log_step "Waiting for PaymentJob to Complete"
    
    # Get job name from PaymentJob status
    local max_wait=30
    local waited=0
    local job_name=""
    
    while [[ -z "$job_name" && $waited -lt $max_wait ]]; do
        job_name=$(kubectl get paymentjob -n "$NAMESPACE" e2e-test-job -o jsonpath='{.status.jobName}' 2>/dev/null || true)
        if [[ -z "$job_name" ]]; then
            log_info "Waiting for operator to create Job..."
            sleep 2
            waited=$((waited + 2))
        fi
    done
    
    if [[ -z "$job_name" ]]; then
        log_error "Job was not created within $max_wait seconds"
        kubectl describe paymentjob -n "$NAMESPACE" e2e-test-job
        exit 1
    fi
    
    log_info "Job name: $job_name"
    
    # Wait for job to complete
    log_info "Waiting for Job to complete..."
    kubectl wait -n "$NAMESPACE" --for=condition=complete job/"$job_name" --timeout="$TIMEOUT" || {
        log_warn "Job did not complete, checking status..."
        kubectl describe job -n "$NAMESPACE" "$job_name"
        kubectl logs -n "$NAMESPACE" -l job-name="$job_name" --tail=50
    }
    
    # Show PaymentJob status
    log_info "PaymentJob status:"
    kubectl get paymentjob -n "$NAMESPACE" e2e-test-job
    kubectl get paymentjob -n "$NAMESPACE" e2e-test-job -o jsonpath='{.status}' | jq . 2>/dev/null || \
        kubectl get paymentjob -n "$NAMESPACE" e2e-test-job -o jsonpath='{.status}'
    echo ""
    
    # Show worker logs
    log_info "Worker logs (last 20 lines):"
    kubectl logs -n "$NAMESPACE" -l paymentjob=e2e-test-job --tail=20 || true
    
    log_success "Job completed"
}

# Verify data in PostgreSQL
verify_data() {
    log_step "Verifying Data in PostgreSQL"
    
    # Run a one-shot pod to query PostgreSQL
    local count=$(kubectl run psql-check \
        -n "$NAMESPACE" \
        --rm \
        --restart=Never \
        --image=postgres:15-alpine \
        -i \
        --env="PGPASSWORD=postgres" \
        -- psql -h postgres -U postgres -d payments_db -t -c "SELECT COUNT(*) FROM payments;" 2>/dev/null | tr -d ' \n')
    
    log_info "Records in payments table: $count"
    
    # Show sample data
    log_info "Sample records:"
    kubectl run psql-sample \
        -n "$NAMESPACE" \
        --rm \
        --restart=Never \
        --image=postgres:15-alpine \
        -i \
        --env="PGPASSWORD=postgres" \
        -- psql -h postgres -U postgres -d payments_db -c \
        "SELECT id, received_at, payload->>'order_id' as order_id, payload->>'amount' as amount FROM payments ORDER BY id LIMIT 5;" 2>/dev/null || true
    
    # Verify count
    if [[ "$count" -ge "$MESSAGE_COUNT" ]]; then
        log_success "Verification PASSED: $count >= $MESSAGE_COUNT records found"
        return 0
    else
        log_error "Verification FAILED: Expected >= $MESSAGE_COUNT records, found $count"
        return 1
    fi
}

# Print summary
print_summary() {
    log_step "E2E Test Summary"
    
    echo ""
    echo "Cluster:       $CLUSTER_NAME"
    echo "Namespace:     $NAMESPACE"
    echo "Messages:      $MESSAGE_COUNT"
    echo ""
    echo "Resources created:"
    echo "  - RabbitMQ deployment and service"
    echo "  - PostgreSQL deployment and service"
    echo "  - PaymentJob CRD"
    echo "  - PaymentJob Operator"
    echo "  - PaymentJob 'e2e-test-job'"
    echo ""
    
    log_success "E2E Test PASSED!"
    echo ""
    echo "To clean up:"
    echo "  kind delete cluster --name $CLUSTER_NAME"
    echo ""
}

# Main function
main() {
    echo ""
    echo -e "${GREEN}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║        PaymentJob Operator - End-to-End Test               ║${NC}"
    echo -e "${GREEN}╚════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo "Configuration:"
    echo "  Cluster:        $CLUSTER_NAME"
    echo "  Namespace:      $NAMESPACE"
    echo "  Message Count:  $MESSAGE_COUNT"
    echo "  Timeout:        $TIMEOUT"
    echo ""
    
    check_prerequisites
    setup_cluster
    apply_crd
    deploy_infrastructure
    create_secrets
    build_and_load_images
    deploy_operator
    publish_messages
    create_paymentjob
    wait_for_job
    verify_data
    print_summary
}

# Run main
main "$@"
