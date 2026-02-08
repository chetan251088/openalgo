from __future__ import annotations

import os
import threading
from typing import Optional

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from utils.logging import get_logger

logger = get_logger(__name__)

JOB_ID = "ai_scalper_model_tuner"


class ModelTunerScheduler:
    _instance: Optional["ModelTunerScheduler"] = None
    _scheduler: BackgroundScheduler | None = None
    _lock = threading.Lock()
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def init(self, db_url: str | None = None) -> None:
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            if db_url is None:
                db_url = os.getenv("DATABASE_URL", "sqlite:///db/openalgo.db")
            jobstores = {"default": SQLAlchemyJobStore(url=db_url, tablename="model_tuner_apscheduler_jobs")}
            self._scheduler = BackgroundScheduler(
                jobstores=jobstores,
                job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 120},
            )
            self._scheduler.start()
            self._initialized = True
            logger.debug("Model tuner scheduler initialized")

    @property
    def scheduler(self) -> BackgroundScheduler:
        if self._scheduler is None:
            raise RuntimeError("Model tuner scheduler not initialized")
        return self._scheduler

    def schedule_interval(self, interval_s: int) -> None:
        self.remove_job(JOB_ID)
        trigger = IntervalTrigger(seconds=interval_s)
        self.scheduler.add_job(
            execute_model_tuner_schedule,
            trigger=trigger,
            id=JOB_ID,
            replace_existing=True,
            name="AI Scalper Model Tuning (interval)",
            kwargs={"schedule_type": "interval", "interval_s": interval_s},
        )

    def schedule_daily(self, time_of_day: str) -> None:
        self.remove_job(JOB_ID)
        hour, minute = map(int, time_of_day.split(":"))
        trigger = CronTrigger(hour=hour, minute=minute, timezone="Asia/Kolkata")
        self.scheduler.add_job(
            execute_model_tuner_schedule,
            trigger=trigger,
            id=JOB_ID,
            replace_existing=True,
            name="AI Scalper Model Tuning (daily)",
            kwargs={"schedule_type": "daily", "time_of_day": time_of_day},
        )

    def remove_job(self, job_id: str) -> None:
        try:
            self.scheduler.remove_job(job_id)
        except Exception:
            return

    def clear_schedule(self) -> None:
        self.remove_job(JOB_ID)

    def get_schedule_info(self) -> dict:
        if not self._initialized:
            return {"enabled": False}
        job = self.scheduler.get_job(JOB_ID)
        if not job:
            return {"enabled": False}
        kwargs = job.kwargs or {}
        schedule_type = kwargs.get("schedule_type")
        info = {
            "enabled": True,
            "type": schedule_type,
            "interval_s": kwargs.get("interval_s"),
            "time_of_day": kwargs.get("time_of_day"),
        }
        if job.next_run_time:
            info["next_run_time"] = (
                job.next_run_time if isinstance(job.next_run_time, str) else job.next_run_time.isoformat()
            )
        return info


def execute_model_tuner_schedule(**kwargs) -> None:
    try:
        from services.ai_scalper.manager import get_ai_scalper_manager
        from services.ai_scalper.model_tuner import get_model_tuning_service

        manager = get_ai_scalper_manager()
        service = get_model_tuning_service()
        ok, message, run_id = service.enqueue_run(
            manager=manager, objective="Scheduled tuning run", requested_by="schedule"
        )
        if not ok:
            logger.debug("Model tuner schedule skipped: %s", message)
        else:
            logger.info("Model tuner scheduled run queued: %s", run_id)
    except Exception as exc:
        logger.debug("Model tuner schedule failed: %s", exc)


model_tuner_scheduler = ModelTunerScheduler()


def get_model_tuner_scheduler() -> ModelTunerScheduler:
    return model_tuner_scheduler


def init_model_tuner_scheduler(db_url: str | None = None) -> ModelTunerScheduler:
    model_tuner_scheduler.init(db_url=db_url)
    return model_tuner_scheduler
