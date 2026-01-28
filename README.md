# PaymentJob Operator (Python + Kopf) — RabbitMQ ➜ PostgreSQL

Este projeto implementa um **operator simplificado** que observa um **CRD `PaymentJob`** e cria um **Kubernetes Job** que executa um **worker**.  
O worker consome mensagens do **RabbitMQ** e persiste o payload no **PostgreSQL**.

## O que tem aqui

- **worker/**: container que consome RabbitMQ e grava no Postgres
- **operator/**: controller (Kopf) que observa `PaymentJob` e cria `batch/v1 Job`
- **manifests/**: CRD + sample + RBAC/Deployment do operator
- **scripts/**: helpers (publicar mensagens, build/load no kind, e2e)

---

## Pré-requisitos

- Docker + Docker Compose
- kubectl
- kind
- Python 3.8+ (somente para rodar scripts locais como publish)

> Se o avaliador tiver apenas Docker/kind/kubectl, o caminho E2E abaixo cobre tudo.

---

## Quickstart (recomendado): E2E no kind

O E2E automatiza:
1) cluster kind  
2) CRD `PaymentJob`  
3) RabbitMQ + Postgres no cluster  
4) secrets (credenciais)  
5) build + load das imagens no kind  
6) deploy operator  
7) publica mensagens de teste  
8) cria `PaymentJob`  
9) espera o Job finalizar  
10) valida inserts no Postgres

```bash
./scripts/e2e.sh
```

Variáveis (opcionais):
```bash
CLUSTER_NAME=payments-e2e MESSAGE_COUNT=10 TIMEOUT=120s ./scripts/e2e.sh
```

---

## Quickstart (local): testar apenas o worker com Docker Compose

### 1) Subir RabbitMQ e Postgres
```bash
docker compose up -d rabbitmq postgres
docker compose ps
```

RabbitMQ Management UI: http://localhost:15672 (guest/guest)

### 2) Publicar mensagens de teste
```bash
python3 -m pip install -r scripts/requirements.txt
python3 scripts/publish_test_messages.py --count 10 --queue payments --host localhost
```

### 3) Rodar o worker para consumir N mensagens e sair
```bash
docker compose build worker
docker compose run --rm -e QUEUE_NAME=payments -e MAX_MESSAGES=10 worker
```

### 4) Conferir inserts no Postgres
```bash
docker compose exec postgres psql -U postgres -d payments_db -c "SELECT COUNT(*) FROM payments;"
docker compose exec postgres psql -U postgres -d payments_db -c "SELECT id, received_at, payload FROM payments ORDER BY id DESC LIMIT 5;"
```

### 5) Limpar
```bash
docker compose down -v
```

---

## Como funciona

### Fluxo (alto nível)

1. Você aplica um `PaymentJob` (recurso customizado).
2. O **operator** reconcilia e cria um **Kubernetes Job** com `ownerReferences` apontando para o `PaymentJob`.
3. O Job executa o **worker** com env vars (Rabbit/Postgres/Queue/MaxMessages).
4. O operator atualiza `status.phase` do `PaymentJob` conforme o Job roda (`Pending → Running → Succeeded/Failed`).

### Tabela no Postgres

O worker cria a tabela se não existir:

- `payments(id serial pk, received_at timestamp, payload jsonb, message_id text, source_queue text)`

---

## Kubernetes: executar manualmente (sem E2E)

### 1) Criar cluster kind
```bash
kind create cluster --name payments-dev
kubectl cluster-info --context kind-payments-dev
```

### 2) Build + load das imagens no kind
```bash
./scripts/build_and_load_kind_images.sh payments-dev
```

### 3) Aplicar CRD
```bash
kubectl apply -f manifests/crd-paymentjob.yaml
kubectl get crd paymentjobs.payments.example.com
```

### 4) Deploy do operator (RBAC + Deployment)
```bash
kubectl apply -f manifests/operator-deploy.yaml
kubectl get pods -n paymentjob-system
kubectl logs -n paymentjob-system -l app.kubernetes.io/name=paymentjob-operator -f
```

### 5) Criar secrets (no namespace onde você vai criar o PaymentJob)
Exemplo usando namespace `payments`:
```bash
kubectl create namespace payments

kubectl create secret generic rabbitmq-credentials -n payments   --from-literal=username=guest   --from-literal=password=guest

kubectl create secret generic postgres-credentials -n payments   --from-literal=username=postgres   --from-literal=password=postgres
```

### 6) Criar um PaymentJob (exemplo)
Ajuste hosts/namespace conforme seus Services no cluster.

```bash
kubectl apply -n payments -f manifests/paymentjob-sample.yaml
kubectl get paymentjobs -n payments
kubectl get jobs -n payments
```

Acompanhar logs:
```bash
kubectl logs -n payments -l paymentjob=<nome-do-paymentjob> -f
kubectl logs -n paymentjob-system -l app.kubernetes.io/name=paymentjob-operator -f
```

---

## Variáveis de ambiente do worker (referência)

- RabbitMQ:
  - `RABBITMQ_HOST`, `RABBITMQ_PORT`, `RABBITMQ_USER`, `RABBITMQ_PASS`
- Postgres:
  - `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASS`
- Execução:
  - `QUEUE_NAME`
  - `MAX_MESSAGES` (se vazio/não setado, roda contínuo)

---

## Troubleshooting (curto)

- **Operator não sobe**
  ```bash
  kubectl logs -n paymentjob-system deploy/paymentjob-operator
  kubectl describe pod -n paymentjob-system -l app.kubernetes.io/name=paymentjob-operator
  ```

- **PaymentJob criado mas Job não aparece**
  ```bash
  kubectl get events -n <namespace-do-paymentjob> --sort-by='.lastTimestamp'
  kubectl get paymentjob -n <ns> <name> -o yaml
  ```

- **Worker falha**
  ```bash
  kubectl logs -n <ns> -l paymentjob=<name> --all-containers=true
  ```

---

## Limpeza do kind

```bash
kind delete cluster --name payments-dev
# ou (se rodou E2E)
kind delete cluster --name payments-e2e
```
