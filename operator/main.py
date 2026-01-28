#!/usr/bin/env python3
"""
PaymentJob Operator - Manages PaymentJob custom resources using Kopf.

This operator watches PaymentJob resources and creates Kubernetes Jobs
to run payment workers that consume from RabbitMQ and persist to PostgreSQL.
"""

import datetime
import hashlib
import logging
import os

import kopf
import kubernetes
from kubernetes import client
from kubernetes.client.rest import ApiException

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s'
)
logger = logging.getLogger('paymentjob-operator')

# API Group and Version for PaymentJob
API_GROUP = 'payments.example.com'
API_VERSION = 'v1alpha1'
PLURAL = 'paymentjobs'


def get_job_name(paymentjob_name: str, namespace: str) -> str:
    """Generate a deterministic Job name from PaymentJob name."""
    # Use a short hash to ensure uniqueness and stay within 63 char limit
    hash_input = f"{namespace}/{paymentjob_name}"
    short_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:8]
    base_name = f"paymentjob-{paymentjob_name}"
    
    # Kubernetes names must be <= 63 characters
    if len(base_name) > 54:
        base_name = base_name[:54]
    
    return f"{base_name}-{short_hash}"


def get_current_timestamp() -> str:
    """Get current timestamp in ISO format."""
    return datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')


def build_job_spec(
    name: str,
    namespace: str,
    spec: dict,
    owner_reference: dict
) -> client.V1Job:
    """Build a Kubernetes Job spec from PaymentJob spec."""
    
    job_name = get_job_name(name, namespace)
    
    # Extract configuration from spec
    image = spec['image']
    queue_name = spec['queueName']
    max_messages = spec.get('maxMessages')
    
    rabbitmq = spec['rabbitmq']
    postgres = spec['postgres']
    
    # Build environment variables
    env_vars = [
        # Queue configuration
        client.V1EnvVar(name='QUEUE_NAME', value=queue_name),
        
        # RabbitMQ configuration
        client.V1EnvVar(name='RABBITMQ_HOST', value=rabbitmq['host']),
        client.V1EnvVar(
            name='RABBITMQ_PORT',
            value=str(rabbitmq.get('port', 5672))
        ),
        client.V1EnvVar(
            name='RABBITMQ_USER',
            value_from=client.V1EnvVarSource(
                secret_key_ref=client.V1SecretKeySelector(
                    name=rabbitmq['secretRef']['name'],
                    key='username'
                )
            )
        ),
        client.V1EnvVar(
            name='RABBITMQ_PASS',
            value_from=client.V1EnvVarSource(
                secret_key_ref=client.V1SecretKeySelector(
                    name=rabbitmq['secretRef']['name'],
                    key='password'
                )
            )
        ),
        
        # PostgreSQL configuration
        client.V1EnvVar(name='POSTGRES_HOST', value=postgres['host']),
        client.V1EnvVar(
            name='POSTGRES_PORT',
            value=str(postgres.get('port', 5432))
        ),
        client.V1EnvVar(name='POSTGRES_DB', value=postgres['database']),
        client.V1EnvVar(
            name='POSTGRES_USER',
            value_from=client.V1EnvVarSource(
                secret_key_ref=client.V1SecretKeySelector(
                    name=postgres['secretRef']['name'],
                    key='username'
                )
            )
        ),
        client.V1EnvVar(
            name='POSTGRES_PASS',
            value_from=client.V1EnvVarSource(
                secret_key_ref=client.V1SecretKeySelector(
                    name=postgres['secretRef']['name'],
                    key='password'
                )
            )
        ),
    ]
    
    # Add MAX_MESSAGES if specified
    if max_messages is not None:
        env_vars.append(
            client.V1EnvVar(name='MAX_MESSAGES', value=str(max_messages))
        )
    
    # Labels for the Job and Pod
    labels = {
        'app': 'payment-worker',
        'paymentjob': name,
        'managed-by': 'paymentjob-operator'
    }
    
    # Build container spec
    container = client.V1Container(
        name='payment-worker',
        image=image,
        image_pull_policy='IfNotPresent',
        env=env_vars,
        resources=client.V1ResourceRequirements(
            requests={'cpu': '100m', 'memory': '128Mi'},
            limits={'cpu': '500m', 'memory': '256Mi'}
        )
    )
    
    # Build Pod template spec
    pod_template = client.V1PodTemplateSpec(
        metadata=client.V1ObjectMeta(labels=labels),
        spec=client.V1PodSpec(
            containers=[container],
            restart_policy='OnFailure'
        )
    )
    
    # Build Job spec
    job_spec = client.V1JobSpec(
        template=pod_template,
        backoff_limit=3,
        ttl_seconds_after_finished=300  # Clean up after 5 minutes
    )
    
    # Build Job object
    job = client.V1Job(
        api_version='batch/v1',
        kind='Job',
        metadata=client.V1ObjectMeta(
            name=job_name,
            namespace=namespace,
            labels=labels,
            owner_references=[
                client.V1OwnerReference(
                    api_version=f"{API_GROUP}/{API_VERSION}",
                    kind='PaymentJob',
                    name=owner_reference['name'],
                    uid=owner_reference['uid'],
                    controller=True,
                    block_owner_deletion=True
                )
            ]
        ),
        spec=job_spec
    )
    
    return job


def update_paymentjob_status(
    name: str,
    namespace: str,
    phase: str,
    job_name: str = None,
    message: str = None
) -> dict:
    """Build status patch for PaymentJob."""
    status = {
        'phase': phase,
        'lastUpdateTime': get_current_timestamp()
    }
    
    if job_name:
        status['jobName'] = job_name
    
    if message:
        status['message'] = message
    
    return status


@kopf.on.startup()
def configure(settings: kopf.OperatorSettings, **_):
    """Configure operator settings on startup."""
    settings.posting.level = logging.INFO
    settings.watching.server_timeout = 270
    settings.persistence.finalizer = f'{API_GROUP}/finalizer'
    logger.info("PaymentJob Operator starting up...")


@kopf.on.create(API_GROUP, API_VERSION, PLURAL)
def create_paymentjob(spec, name, namespace, uid, logger, **kwargs):
    """Handle PaymentJob creation - create the underlying Kubernetes Job."""
    logger.info(f"Creating PaymentJob: {namespace}/{name}")
    
    # Load kubernetes config
    try:
        kubernetes.config.load_incluster_config()
    except kubernetes.config.ConfigException:
        kubernetes.config.load_kube_config()
    
    batch_api = client.BatchV1Api()
    custom_api = client.CustomObjectsApi()
    
    job_name = get_job_name(name, namespace)
    
    # Check if Job already exists (idempotency)
    try:
        existing_job = batch_api.read_namespaced_job(job_name, namespace)
        logger.info(f"Job {job_name} already exists, skipping creation")
        
        # Update status based on existing job
        phase = 'Pending'
        if existing_job.status.active and existing_job.status.active > 0:
            phase = 'Running'
        elif existing_job.status.succeeded and existing_job.status.succeeded > 0:
            phase = 'Succeeded'
        elif existing_job.status.failed and existing_job.status.failed > 0:
            phase = 'Failed'
        
        return {
            'status': update_paymentjob_status(name, namespace, phase, job_name)
        }
        
    except ApiException as e:
        if e.status != 404:
            logger.error(f"Error checking for existing Job: {e}")
            raise
    
    # Build owner reference for garbage collection
    owner_ref = {'name': name, 'uid': uid}
    
    # Build the Job
    job = build_job_spec(name, namespace, spec, owner_ref)
    
    # Create the Job
    try:
        batch_api.create_namespaced_job(namespace, job)
        logger.info(f"Created Job: {job_name}")
        
        # Update PaymentJob status
        status = update_paymentjob_status(
            name, namespace, 'Pending', job_name,
            message=f'Job {job_name} created'
        )
        
        # Post event
        kopf.info(
            {'apiVersion': f'{API_GROUP}/{API_VERSION}', 'kind': 'PaymentJob'},
            reason='JobCreated',
            message=f'Created Job {job_name}'
        )
        
        return {'status': status}
        
    except ApiException as e:
        logger.error(f"Failed to create Job: {e}")
        status = update_paymentjob_status(
            name, namespace, 'Failed', job_name,
            message=f'Failed to create Job: {e.reason}'
        )
        return {'status': status}


@kopf.on.delete(API_GROUP, API_VERSION, PLURAL)
def delete_paymentjob(name, namespace, logger, **kwargs):
    """Handle PaymentJob deletion - Job will be garbage collected via ownerReferences."""
    logger.info(f"PaymentJob {namespace}/{name} deleted - Job will be garbage collected")
    
    # The Job will be automatically deleted due to ownerReferences
    # No explicit action needed


@kopf.timer(API_GROUP, API_VERSION, PLURAL, interval=10.0, sharp=True)
def monitor_job_status(spec, name, namespace, status, logger, patch, **kwargs):
    """Periodically monitor the Job status and update PaymentJob status."""
    
    job_name = status.get('jobName') if status else None
    if not job_name:
        job_name = get_job_name(name, namespace)
    
    # Load kubernetes config
    try:
        kubernetes.config.load_incluster_config()
    except kubernetes.config.ConfigException:
        kubernetes.config.load_kube_config()
    
    batch_api = client.BatchV1Api()
    
    try:
        job = batch_api.read_namespaced_job(job_name, namespace)
    except ApiException as e:
        if e.status == 404:
            # Job doesn't exist yet or was deleted
            logger.debug(f"Job {job_name} not found")
            return
        raise
    
    # Determine phase from Job status
    job_status = job.status
    current_phase = status.get('phase', 'Pending') if status else 'Pending'
    new_phase = current_phase
    message = None
    
    if job_status.succeeded and job_status.succeeded > 0:
        new_phase = 'Succeeded'
        message = f'Job completed successfully'
    elif job_status.failed and job_status.failed > 0:
        new_phase = 'Failed'
        # Try to get failure reason
        if job_status.conditions:
            for condition in job_status.conditions:
                if condition.type == 'Failed' and condition.status == 'True':
                    message = condition.message or 'Job failed'
                    break
        if not message:
            message = f'Job failed after {job_status.failed} attempt(s)'
    elif job_status.active and job_status.active > 0:
        new_phase = 'Running'
        message = f'Job is running ({job_status.active} active pod(s))'
    else:
        new_phase = 'Pending'
        message = 'Waiting for pod to start'
    
    # Only update if phase changed
    if new_phase != current_phase:
        logger.info(f"PaymentJob {namespace}/{name} phase: {current_phase} -> {new_phase}")
        
        patch.status['phase'] = new_phase
        patch.status['lastUpdateTime'] = get_current_timestamp()
        patch.status['jobName'] = job_name
        
        if message:
            patch.status['message'] = message
        
        # Add timestamps for terminal phases
        if new_phase == 'Running' and not (status and status.get('startTime')):
            patch.status['startTime'] = get_current_timestamp()
        elif new_phase in ('Succeeded', 'Failed'):
            patch.status['completionTime'] = get_current_timestamp()
        
        # Post events for phase transitions
        if new_phase == 'Succeeded':
            kopf.info(
                {'apiVersion': f'{API_GROUP}/{API_VERSION}', 'kind': 'PaymentJob'},
                reason='JobSucceeded',
                message=f'Job {job_name} completed successfully'
            )
        elif new_phase == 'Failed':
            kopf.warn(
                {'apiVersion': f'{API_GROUP}/{API_VERSION}', 'kind': 'PaymentJob'},
                reason='JobFailed',
                message=message or f'Job {job_name} failed'
            )


@kopf.on.update(API_GROUP, API_VERSION, PLURAL, field='spec')
def update_paymentjob(old, new, name, namespace, logger, **kwargs):
    """Handle PaymentJob spec updates."""
    logger.info(f"PaymentJob {namespace}/{name} spec updated")
    
    # For simplicity, we don't support in-place updates of running jobs
    # The user should delete and recreate the PaymentJob
    logger.warning(
        f"PaymentJob {namespace}/{name} spec was modified. "
        "In-place updates are not supported. Delete and recreate the PaymentJob."
    )
    
    return {'status': {'message': 'Spec updates are not applied to running Jobs. Delete and recreate.'}}


if __name__ == '__main__':
    # Run the operator
    kopf.run()
