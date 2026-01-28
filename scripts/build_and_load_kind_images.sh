#!/bin/bash
#
# Build Docker images and load them into kind cluster
#
# Usage: ./scripts/build_and_load_kind_images.sh [CLUSTER_NAME]
#

set -euo pipefail

# Configuration
CLUSTER_NAME="${1:-payments-dev}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Image names
WORKER_IMAGE="payments-worker:latest"
OPERATOR_IMAGE="paymentjob-operator:latest"

echo "============================================================"
echo "Building and Loading Images to Kind Cluster: ${CLUSTER_NAME}"
echo "============================================================"

# Check if kind cluster exists
if ! kind get clusters 2>/dev/null | grep -q "^${CLUSTER_NAME}$"; then
    echo "ERROR: Kind cluster '${CLUSTER_NAME}' not found."
    echo "Available clusters:"
    kind get clusters 2>/dev/null || echo "  (none)"
    echo ""
    echo "Create a cluster with: kind create cluster --name ${CLUSTER_NAME}"
    exit 1
fi

echo ""
echo ">>> Building Worker Image: ${WORKER_IMAGE}"
echo "------------------------------------------------------------"
docker build -t "${WORKER_IMAGE}" "${PROJECT_ROOT}/worker"
echo "Worker image built successfully."

echo ""
echo ">>> Building Operator Image: ${OPERATOR_IMAGE}"
echo "------------------------------------------------------------"
docker build -t "${OPERATOR_IMAGE}" "${PROJECT_ROOT}/operator"
echo "Operator image built successfully."

echo ""
echo ">>> Loading Worker Image into Kind"
echo "------------------------------------------------------------"
kind load docker-image "${WORKER_IMAGE}" --name "${CLUSTER_NAME}"
echo "Worker image loaded successfully."

echo ""
echo ">>> Loading Operator Image into Kind"
echo "------------------------------------------------------------"
kind load docker-image "${OPERATOR_IMAGE}" --name "${CLUSTER_NAME}"
echo "Operator image loaded successfully."

echo ""
echo "============================================================"
echo "All images built and loaded successfully!"
echo "============================================================"
echo ""
echo "Images loaded:"
echo "  - ${WORKER_IMAGE}"
echo "  - ${OPERATOR_IMAGE}"
echo ""
echo "Next steps:"
echo "  1. Apply CRD:      kubectl apply -f manifests/crd-paymentjob.yaml"
echo "  2. Deploy operator: kubectl apply -f manifests/operator-deploy.yaml"
echo "  3. Create secrets:  kubectl apply -f manifests/paymentjob-sample.yaml"
echo "  4. Create PaymentJob and observe!"
echo ""
