from celery import Celery

from app.core.config import get_settings

settings = get_settings()
celery = Celery(
    "workflow-execution", broker=settings.celery_broker_url, include=["app.worker"]
)
celery.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_ignore_result=True,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_reject_on_worker_lost=False,
    broker_connection_retry_on_startup=True,
    broker_transport_options={"visibility_timeout": 86400},
    task_soft_time_limit=settings.execution_max_seconds + 30,
    task_time_limit=settings.execution_max_seconds + 60,
)
