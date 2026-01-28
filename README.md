# PaymentJob Operator (Python + Kopf) — RabbitMQ ➜ PostgreSQL

Projeto de desafio: um **operator simplificado** observa um **CRD `PaymentJob`** e cria um **Kubernetes Job** que executa um **worker**.  
O worker consome mensagens do **RabbitMQ** e persiste o payload no **PostgreSQL**.

## Componentes

- **worker/**: container que consome RabbitMQ e grava no Postgres
- **operator/**: controller (Kopf) que observa `PaymentJob` e cria `batch/v1 Job`
- **manifests/**: CRD + sample + RBAC/Deployment do operator
- **scripts/**: helpers (build/load no kind, e2e)

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

> Este fluxo testa só o worker localmente (sem Kubernetes).

### 1) Subir RabbitMQ e Postgres
```bash
docker compose up -d rabbitmq postgres
docker compose ps
```

RabbitMQ Management UI: http://localhost:15672 (guest/guest)

### 2) Publicar mensagens de teste

**Opção A (sem instalar nada fora do Docker):** se existir um serviço/container de publisher no `docker-compose.yml`, rode:
```bash
docker compose run --rm publisher --count 10 --queue payments
```

**Opção B (com Python local):**
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

## Como funciona (alto nível)

1. Você aplica um `PaymentJob` (recurso customizado).
2. O **operator** reconcilia e cria um **Kubernetes Job** com `ownerReferences` apontando para o `PaymentJob`.
3. O Job executa o **worker** com env vars (Rabbit/Postgres/Queue/MaxMessages).
4. O operator atualiza `status.phase` do `PaymentJob` conforme o Job roda (`Pending → Running → Succeeded/Failed`).

Tabela criada no Postgres (se não existir):
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

kubectl create secret generic rabbitmq-credentials -n payments \
  --from-literal=username=guest \
  --from-literal=password=guest

kubectl create secret generic postgres-credentials -n payments \
  --from-literal=username=postgres \
  --from-literal=password=postgres
```

### 6) Criar um PaymentJob (exemplo)
```bash
kubectl apply -n payments -f manifests/paymentjob-sample.yaml
kubectl get paymentjobs -n payments
kubectl get jobs -n payments
```

Logs:
```bash
kubectl logs -n payments -l paymentjob=<nome-do-paymentjob> -f
kubectl logs -n paymentjob-system -l app.kubernetes.io/name=paymentjob-operator -f
```

---

## Pré-requisitos (instalação opcional)

> Se você rodar somente o **E2E**, normalmente basta ter Docker + kubectl + kind.  
> Python local só é necessário se você preferir rodar o publisher fora de containers.

### Docker + Docker Compose
Verificar:
```bash
docker --version
docker compose version
```

### kubectl
Verificar:
```bash
kubectl version --client
```

Instalação (exemplos):
- macOS (Homebrew): `brew install kubectl`
- Ubuntu (snap): `sudo snap install kubectl --classic`

### kind
Verificar:
```bash
kind version
```

Instalação (exemplos):
- macOS (Homebrew): `brew install kind`
- Linux (binário):
```bash
curl -Lo kind https://kind.sigs.k8s.io/dl/latest/kind-linux-amd64
chmod +x kind
sudo mv kind /usr/local/bin/kind
```

### Python 3 (somente se for usar scripts localmente)
Verificar:
```bash
python3 --version
pip3 --version
```

Instalação (exemplos):
- macOS (Homebrew): `brew install python`
- Ubuntu/Debian:
```bash
sudo apt-get update && sudo apt-get install -y python3 python3-pip
```

---

## Troubleshooting (curto)

- Operator não sobe:
```bash
kubectl logs -n paymentjob-system deploy/paymentjob-operator
kubectl describe pod -n paymentjob-system -l app.kubernetes.io/name=paymentjob-operator
```

- PaymentJob criado mas Job não aparece:
```bash
kubectl get events -n <ns> --sort-by='.lastTimestamp'
kubectl get paymentjob -n <ns> <name> -o yaml
```

- Worker falha:
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
