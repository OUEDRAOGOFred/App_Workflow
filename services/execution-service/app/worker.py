import asyncio

from app.core.celery_app import celery
from app.services.engine import run_execution


@celery.task(name="execution.run", ignore_result=True)
def execute(execution_id: str):
    # Only the record ID is sent to the broker; credentials never enter task data.
    asyncio.run(run_execution(execution_id))
