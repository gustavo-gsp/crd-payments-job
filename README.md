# Payment Worker

Worker Python que consome mensagens do RabbitMQ e persiste no PostgreSQL.

## Estrutura do Projeto

```
.
├── docker-compose.yml            # Orquestra RabbitMQ, PostgreSQL e Worker
├── worker/
│   ├── Dockerfile                # Imagem do worker
│   ├── main.py                   # Código do worker
│   └── requirements.txt          # Dependências Python
├── operator/
│   ├── Dockerfile                # Imagem do operator
│   ├── main.py                   # Código do operator (Kopf)
│   └── requirements.txt          # Dependências Python
├── scripts/
│   ├── publish_test_messages.py  # Script de teste
│   ├── build_and_load_kind_images.sh  # Build e load para kind
│   ├── e2e.sh                    # Teste end-to-end automatizado
│   └── requirements.txt          # Dependências do script
├── manifests/
│   ├── crd-paymentjob.yaml       # CRD PaymentJob para Kubernetes
│   ├── operator-deploy.yaml      # Deploy do operator (RBAC + Deployment)
│   └── paymentjob-sample.yaml    # Exemplo de PaymentJob
└── README.md
```

## Funcionalidades

- Conecta no RabbitMQ usando pika
- Consome mensagens JSON de uma fila configurável
- Persiste cada mensagem na tabela `payments` no PostgreSQL
- Cria a tabela automaticamente no startup (migration simples)
- ACK só após inserção bem-sucedida (requeue em caso de erro)
- Suporta limite de mensagens via `MAX_MESSAGES`
- Logs claros em stdout

## Configuração

### Variáveis de Ambiente

| Variável | Descrição | Padrão |
|----------|-----------|--------|
| `RABBITMQ_HOST` | Host do RabbitMQ | `localhost` |
| `RABBITMQ_PORT` | Porta do RabbitMQ | `5672` |
| `RABBITMQ_USER` | Usuário do RabbitMQ | `guest` |
| `RABBITMQ_PASS` | Senha do RabbitMQ | `guest` |
| `POSTGRES_HOST` | Host do PostgreSQL | `localhost` |
| `POSTGRES_PORT` | Porta do PostgreSQL | `5432` |
| `POSTGRES_DB` | Nome do banco | `payments_db` |
| `POSTGRES_USER` | Usuário do PostgreSQL | `postgres` |
| `POSTGRES_PASS` | Senha do PostgreSQL | `postgres` |
| `QUEUE_NAME` | Nome da fila para consumir | `payments` |
| `MAX_MESSAGES` | Limite de mensagens (opcional) | `unlimited` |

## Teste Local com Docker Compose

### Pré-requisitos

- Docker e Docker Compose instalados
- Python 3.8+ (para o script de publicação)

### Passo 1: Subir RabbitMQ e PostgreSQL

```bash
# Sobe apenas RabbitMQ e PostgreSQL
docker compose up -d rabbitmq postgres

# Aguarde os serviços ficarem healthy
docker compose ps
```

O RabbitMQ Management UI estará disponível em: http://localhost:15672 (guest/guest)

### Passo 2: Publicar Mensagens de Teste

```bash
# Instale a dependência (pika)
pip install pika

# Publique 10 mensagens de teste
python scripts/publish_test_messages.py --count 10

# Ou especifique a fila
python scripts/publish_test_messages.py --count 20 --queue payments
```

### Passo 3: Executar o Worker

**Opção A: Via Docker Compose (recomendado)**

```bash
# Build e executa o worker
docker compose --profile worker up --build worker

# Ou para processar apenas N mensagens e sair
docker compose run -e MAX_MESSAGES=10 worker
```

**Opção B: Localmente (para desenvolvimento)**

```bash
cd worker
pip install -r requirements.txt

# Configure as variáveis (os defaults apontam para localhost)
export RABBITMQ_HOST=localhost
export POSTGRES_HOST=localhost
export QUEUE_NAME=payments

# Execute
python main.py
```

### Passo 4: Verificar Inserts no PostgreSQL

**Via psql:**

```bash
# Conecte no PostgreSQL
docker compose exec postgres psql -U postgres -d payments_db

# Veja os registros
SELECT id, received_at, message_id, source_queue, payload->>'order_id' as order_id 
FROM payments 
ORDER BY id DESC 
LIMIT 10;

# Conte total de registros
SELECT COUNT(*) FROM payments;

# Veja detalhes completos
SELECT * FROM payments ORDER BY id DESC LIMIT 5;

# Saia do psql
\q
```

**Via docker exec one-liner:**

```bash
# Contar registros
docker compose exec postgres psql -U postgres -d payments_db -c "SELECT COUNT(*) FROM payments;"

# Listar últimos 10
docker compose exec postgres psql -U postgres -d payments_db -c "SELECT id, received_at, payload->>'order_id' as order_id, payload->>'amount' as amount FROM payments ORDER BY id DESC LIMIT 10;"
```

### Passo 5: Parar os Serviços

```bash
# Para todos os serviços
docker compose down

# Para limpar volumes (remove dados)
docker compose down -v
```

## Fluxo Completo de Teste

```bash
# 1. Subir infraestrutura
docker compose up -d rabbitmq postgres

# 2. Aguardar serviços (verifique que estão healthy)
docker compose ps

# 3. Publicar mensagens de teste
pip install pika
python scripts/publish_test_messages.py --count 15

# 4. Rodar worker para processar todas as mensagens
docker compose --profile worker up --build worker

# 5. Verificar no banco
docker compose exec postgres psql -U postgres -d payments_db -c "SELECT COUNT(*) FROM payments;"

# 6. Limpar
docker compose down -v
```

## Schema da Tabela `payments`

```sql
CREATE TABLE payments (
    id SERIAL PRIMARY KEY,
    received_at TIMESTAMP NOT NULL DEFAULT NOW(),
    payload JSONB NOT NULL,
    message_id TEXT,
    source_queue TEXT NOT NULL
);

CREATE INDEX idx_payments_received_at ON payments(received_at);
CREATE INDEX idx_payments_message_id ON payments(message_id);
```

## Comportamento de Erros

- **JSON inválido**: Mensagem rejeitada sem requeue (vai para DLQ se configurada)
- **Erro de banco**: Mensagem é requeued automaticamente (NACK)
- **Erro de conexão**: Worker tenta reconectar automaticamente

## Logs

O worker produz logs detalhados em stdout:

```
2026-01-28 10:30:00,123 - INFO - Payment Worker Starting
2026-01-28 10:30:00,124 - INFO - Configuration:
2026-01-28 10:30:00,124 - INFO -   RabbitMQ: rabbitmq:5672
2026-01-28 10:30:00,124 - INFO -   PostgreSQL: postgres:5432/payments_db
2026-01-28 10:30:00,124 - INFO -   Queue: payments
2026-01-28 10:30:01,456 - INFO - Successfully connected to PostgreSQL
2026-01-28 10:30:01,789 - INFO - Database migrations completed successfully
2026-01-28 10:30:02,012 - INFO - Successfully connected to RabbitMQ
2026-01-28 10:30:02,345 - INFO - Received message (delivery_tag=1, message_id=abc123)
2026-01-28 10:30:02,456 - INFO - Successfully inserted payment with id=1
2026-01-28 10:30:02,457 - INFO - Message acknowledged (delivery_tag=1)
```

---

## Kubernetes CRD: PaymentJob

O projeto inclui um Custom Resource Definition (CRD) para Kubernetes que permite criar workers de pagamento de forma declarativa.

### Estrutura do PaymentJob

```yaml
apiVersion: payments.example.com/v1alpha1
kind: PaymentJob
metadata:
  name: my-payment-job
spec:
  queueName: string       # (required) Nome da fila RabbitMQ
  image: string           # (required) Imagem do worker
  maxMessages: integer    # (optional) Limite de mensagens a processar
  rabbitmq:
    host: string          # Host do RabbitMQ
    port: integer         # Porta (default: 5672)
    secretRef:
      name: string        # Secret com keys 'username' e 'password'
  postgres:
    host: string          # Host do PostgreSQL
    port: integer         # Porta (default: 5432)
    database: string      # Nome do banco
    secretRef:
      name: string        # Secret com keys 'username' e 'password'
status:
  phase: Pending|Running|Succeeded|Failed
  jobName: string         # Nome do Job K8s criado
  lastUpdateTime: string  # Timestamp da última atualização
  message: string         # Mensagem de status (opcional)
```

### Aplicar o CRD no Cluster

```bash
# Aplicar o CRD
kubectl apply -f manifests/crd-paymentjob.yaml

# Verificar se o CRD foi criado
kubectl get crd paymentjobs.payments.example.com

# Ver detalhes do CRD
kubectl describe crd paymentjobs.payments.example.com
```

### Criar Secrets para Credenciais

```bash
# Secret do RabbitMQ
kubectl create secret generic rabbitmq-credentials \
  --from-literal=username=guest \
  --from-literal=password=guest

# Secret do PostgreSQL
kubectl create secret generic postgres-credentials \
  --from-literal=username=postgres \
  --from-literal=password=postgres
```

### Criar um PaymentJob

```bash
# Aplicar o exemplo completo (inclui secrets + PaymentJob)
kubectl apply -f manifests/paymentjob-sample.yaml

# Ou criar apenas o PaymentJob (se os secrets já existirem)
cat <<EOF | kubectl apply -f -
apiVersion: payments.example.com/v1alpha1
kind: PaymentJob
metadata:
  name: payment-processor-01
spec:
  queueName: payments
  image: payments-worker:latest
  maxMessages: 100
  rabbitmq:
    host: rabbitmq.default.svc.cluster.local
    port: 5672
    secretRef:
      name: rabbitmq-credentials
  postgres:
    host: postgres.default.svc.cluster.local
    port: 5432
    database: payments_db
    secretRef:
      name: postgres-credentials
EOF
```

### Gerenciar PaymentJobs

```bash
# Listar todos os PaymentJobs
kubectl get paymentjobs
# ou usando o shortname
kubectl get pj

# Ver detalhes de um PaymentJob
kubectl describe paymentjob payment-processor-01

# Ver o YAML completo
kubectl get paymentjob payment-processor-01 -o yaml

# Deletar um PaymentJob
kubectl delete paymentjob payment-processor-01
```

### PrinterColumns

O CRD inclui colunas customizadas para facilitar a visualização:

```bash
$ kubectl get paymentjobs
NAME                   PHASE     JOB                         QUEUE      AGE
payment-processor-01   Running   payment-processor-01-job    payments   5m
```

### Notas sobre o CRD

- **Subresource status**: O status é gerenciado separadamente do spec, permitindo que um controller atualize apenas o status
- **Validação**: O schema inclui validação de campos (minLength, maxLength, pattern, enum, etc.)
- **Shortnames**: Use `pj` ou `pjob` como atalhos para `paymentjobs`
- **Namespace-scoped**: PaymentJobs são criados dentro de um namespace específico

---

## Kubernetes Operator

O projeto inclui um operator escrito em Python usando [Kopf](https://kopf.readthedocs.io/) que gerencia recursos PaymentJob automaticamente.

### O que o Operator faz

1. **Observa** recursos PaymentJob no cluster
2. **Cria** um Kubernetes Job para cada PaymentJob
3. **Configura** o Job com variáveis de ambiente (RabbitMQ, PostgreSQL, etc.)
4. **Atualiza** o status do PaymentJob baseado no status do Job
5. **Garbage Collection**: Jobs são deletados automaticamente quando o PaymentJob é removido (ownerReferences)

### Fluxo de Status

```
PaymentJob criado → phase: Pending
       ↓
Job criado e pod inicia → phase: Running
       ↓
Job completa com sucesso → phase: Succeeded
       ou
Job falha → phase: Failed
```

### Deploy Completo no Kubernetes (kind)

Siga estes passos para testar o operator completo:

#### Passo 1: Criar cluster kind (se não existir)

```bash
# Criar cluster
kind create cluster --name payments-dev

# Verificar
kubectl cluster-info --context kind-payments-dev
```

#### Passo 2: Build e carregar imagens no kind

```bash
# Usar o script que builda worker e operator
./scripts/build_and_load_kind_images.sh payments-dev

# Ou manualmente:
docker build -t payments-worker:latest ./worker
docker build -t paymentjob-operator:latest ./operator
kind load docker-image payments-worker:latest --name payments-dev
kind load docker-image paymentjob-operator:latest --name payments-dev
```

#### Passo 3: Aplicar o CRD

```bash
kubectl apply -f manifests/crd-paymentjob.yaml

# Verificar
kubectl get crd paymentjobs.payments.example.com
```

#### Passo 4: Deploy do Operator

```bash
# Aplica Namespace, ServiceAccount, RBAC e Deployment
kubectl apply -f manifests/operator-deploy.yaml

# Verificar se o operator está rodando
kubectl get pods -n paymentjob-system
kubectl logs -n paymentjob-system -l app.kubernetes.io/name=paymentjob-operator -f
```

#### Passo 5: Deploy RabbitMQ e PostgreSQL no cluster

Para testar, você pode usar deployments simples:

```bash
# Criar namespace para a aplicação
kubectl create namespace payments

# Deploy RabbitMQ
kubectl apply -n payments -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: rabbitmq
spec:
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
        - containerPort: 15672
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
  - name: management
    port: 15672
EOF

# Deploy PostgreSQL
kubectl apply -n payments -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: postgres
spec:
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
EOF

# Aguardar pods ficarem prontos
kubectl wait -n payments --for=condition=ready pod -l app=rabbitmq --timeout=120s
kubectl wait -n payments --for=condition=ready pod -l app=postgres --timeout=120s
```

#### Passo 6: Criar Secrets para credenciais

```bash
# Secrets no namespace payments
kubectl create secret generic rabbitmq-credentials \
  -n payments \
  --from-literal=username=guest \
  --from-literal=password=guest

kubectl create secret generic postgres-credentials \
  -n payments \
  --from-literal=username=postgres \
  --from-literal=password=postgres
```

#### Passo 7: Publicar mensagens de teste

```bash
# Port-forward para acessar o RabbitMQ localmente
kubectl port-forward -n payments svc/rabbitmq 5672:5672 &

# Publicar mensagens
python3 scripts/publish_test_messages.py --count 10 --host localhost

# Parar o port-forward
kill %1
```

#### Passo 8: Criar um PaymentJob

```bash
kubectl apply -n payments -f - <<EOF
apiVersion: payments.example.com/v1alpha1
kind: PaymentJob
metadata:
  name: test-payment-job
spec:
  queueName: payments
  image: payments-worker:latest
  maxMessages: 10
  rabbitmq:
    host: rabbitmq.payments.svc.cluster.local
    port: 5672
    secretRef:
      name: rabbitmq-credentials
  postgres:
    host: postgres.payments.svc.cluster.local
    port: 5432
    database: payments_db
    secretRef:
      name: postgres-credentials
EOF
```

#### Passo 9: Observar o Job e Status

```bash
# Ver o PaymentJob
kubectl get paymentjobs -n payments

# Ver detalhes (incluindo status)
kubectl describe paymentjob test-payment-job -n payments

# Ver o Job criado pelo operator
kubectl get jobs -n payments

# Ver logs do worker
kubectl logs -n payments -l paymentjob=test-payment-job -f

# Ver logs do operator
kubectl logs -n paymentjob-system -l app.kubernetes.io/name=paymentjob-operator -f
```

#### Passo 10: Verificar dados no PostgreSQL

```bash
# Port-forward para PostgreSQL
kubectl port-forward -n payments svc/postgres 5432:5432 &

# Consultar (requer psql instalado)
PGPASSWORD=postgres psql -h localhost -U postgres -d payments_db -c "SELECT COUNT(*) FROM payments;"

# Ou via kubectl exec
kubectl exec -n payments deploy/postgres -- psql -U postgres -d payments_db -c "SELECT * FROM payments LIMIT 5;"

# Parar port-forward
kill %1
```

#### Passo 11: Limpar

```bash
# Deletar PaymentJob (Job será deletado automaticamente via ownerReferences)
kubectl delete paymentjob test-payment-job -n payments

# Deletar namespace de teste
kubectl delete namespace payments

# Deletar operator
kubectl delete -f manifests/operator-deploy.yaml

# Deletar CRD
kubectl delete -f manifests/crd-paymentjob.yaml

# Deletar cluster kind
kind delete cluster --name payments-dev
```

### Arquitetura do Operator

```
┌─────────────────┐     observa      ┌─────────────────┐
│   PaymentJob    │◄─────────────────│    Operator     │
│   (CRD)         │                  │    (Kopf)       │
└────────┬────────┘                  └────────┬────────┘
         │                                    │
         │ ownerRef                           │ cria
         ▼                                    ▼
┌─────────────────┐                  ┌─────────────────┐
│  Kubernetes     │◄─────────────────│   configura     │
│  Job            │                  │   env vars      │
└────────┬────────┘                  └─────────────────┘
         │
         │ executa
         ▼
┌─────────────────┐
│  Pod Worker     │──────┬──────────────────┐
│  (container)    │      │                  │
└─────────────────┘      ▼                  ▼
                  ┌─────────────┐    ┌─────────────┐
                  │  RabbitMQ   │    │  PostgreSQL │
                  └─────────────┘    └─────────────┘
```

### Troubleshooting

**Operator não inicia:**
```bash
kubectl logs -n paymentjob-system deploy/paymentjob-operator
kubectl describe pod -n paymentjob-system -l app.kubernetes.io/name=paymentjob-operator
```

**Job não é criado:**
```bash
# Verificar eventos
kubectl get events -n payments --sort-by='.lastTimestamp'

# Verificar se secrets existem
kubectl get secrets -n payments
```

**Worker falha:**
```bash
# Ver logs do pod do Job
kubectl logs -n payments -l paymentjob=<name>

# Verificar conectividade com RabbitMQ/PostgreSQL
kubectl exec -n payments -it deploy/rabbitmq -- rabbitmq-diagnostics check_port_connectivity
```

---

## Teste End-to-End Automatizado

O projeto inclui um script E2E que automatiza todo o fluxo de teste.

### O que o script faz

1. Cria cluster kind (se não existir)
2. Aplica o CRD PaymentJob
3. Deploya RabbitMQ e PostgreSQL no cluster
4. Cria secrets com credenciais
5. Builda e carrega imagens do worker e operator no kind
6. Deploya o operator
7. Publica mensagens de teste na fila RabbitMQ
8. Cria um PaymentJob
9. Aguarda o Job completar
10. Verifica se os dados foram inseridos no PostgreSQL
11. Exibe resultado do teste (PASS/FAIL)

### Executar o E2E

```bash
# Rodar teste E2E (usa cluster payments-e2e por padrão)
./scripts/e2e.sh

# Com configurações customizadas
CLUSTER_NAME=my-cluster MESSAGE_COUNT=20 ./scripts/e2e.sh

# Com timeout maior
TIMEOUT=180s ./scripts/e2e.sh
```

### Variáveis de Ambiente do E2E

| Variável | Descrição | Padrão |
|----------|-----------|--------|
| `CLUSTER_NAME` | Nome do cluster kind | `payments-e2e` |
| `MESSAGE_COUNT` | Quantidade de mensagens de teste | `10` |
| `TIMEOUT` | Timeout para operações | `120s` |

### Exemplo de Output

```
╔════════════════════════════════════════════════════════════╗
║        PaymentJob Operator - End-to-End Test               ║
╚════════════════════════════════════════════════════════════╝

Configuration:
  Cluster:        payments-e2e
  Namespace:      payments-test
  Message Count:  10
  Timeout:        120s

============================================================
STEP: Checking Prerequisites
============================================================
[SUCCESS] All prerequisites met

============================================================
STEP: Setting up Kind Cluster: payments-e2e
============================================================
[INFO] Creating new cluster 'payments-e2e'...
[SUCCESS] Cluster is ready

...

============================================================
STEP: Verifying Data in PostgreSQL
============================================================
[INFO] Records in payments table: 10
[SUCCESS] Verification PASSED: 10 >= 10 records found

============================================================
STEP: E2E Test Summary
============================================================
[SUCCESS] E2E Test PASSED!

To clean up:
  kind delete cluster --name payments-e2e
```

### Limpar após o teste

```bash
# Deletar o cluster de teste
kind delete cluster --name payments-e2e
```
