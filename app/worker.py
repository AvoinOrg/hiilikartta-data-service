import os
import time
from celery import Celery

from app import config

global_settings = config.get_settings()

celery = Celery(__name__)
celery.conf.broker_url = global_settings.celery_broker_url
celery.conf.result_backend = global_settings.celery_result_backend


@celery.task(name="create_task")
def create_task(task_type):
    time.sleep(int(task_type) * 10)
    return True
