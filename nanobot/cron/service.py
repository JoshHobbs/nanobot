"""Cron service for scheduling agent tasks."""

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Coroutine

from loguru import logger

from nanobot.cron.types import CronJob, CronJobState, CronPayload, CronSchedule, CronStore


def _now_ms() -> int:
    return int(time.time() * 1000)


def _compute_next_run(schedule: CronSchedule, now_ms: int) -> int | None:
    """Compute next run time in ms."""
    if schedule.kind == "at":
        return schedule.at_ms if schedule.at_ms and schedule.at_ms > now_ms else None

    if schedule.kind == "every":
        if not schedule.every_ms or schedule.every_ms <= 0:
            return None
        # Next interval from now
        return now_ms + schedule.every_ms

    if schedule.kind == "cron" and schedule.expr:
        try:
            from croniter import croniter
            if schedule.tz:
                from datetime import datetime
                from zoneinfo import ZoneInfo

                base = datetime.fromtimestamp(now_ms / 1000, tz=ZoneInfo(schedule.tz))
                next_time = croniter(schedule.expr, base).get_next(datetime)
                return int(next_time.timestamp() * 1000)

            cron = croniter(schedule.expr, now_ms / 1000)
            next_time = cron.get_next(float)
            return int(next_time * 1000)
        except ImportError:
            logger.warning("croniter package not installed — cron expressions unavailable")
            return None
        except Exception as e:
            logger.warning(f"Failed to compute next cron run for '{schedule.expr}': {e}")
            return None

    return None


class CronService:
    """Service for managing and executing scheduled jobs."""

    def __init__(
        self,
        store_path: Path,
        on_job: Callable[[CronJob], Coroutine[Any, Any, str | None]] | None = None
    ):
        self.store_path = store_path
        self.on_job = on_job  # Callback to execute job, returns response text
        self._store: CronStore | None = None
        self._timer_task: asyncio.Task | None = None
        self._running = False
        self._store_lock: asyncio.Lock = asyncio.Lock()
        self._running_jobs: set[str] = set()  # Job IDs currently executing

    def _load_store(self) -> CronStore:
        """Load jobs from disk."""
        if self._store:
            return self._store

        if self.store_path.exists():
            try:
                data = json.loads(self.store_path.read_text())
                jobs = []
                for j in data.get("jobs", []):
                    jobs.append(CronJob(
                        id=j["id"],
                        name=j["name"],
                        enabled=j.get("enabled", True),
                        schedule=CronSchedule(
                            kind=j["schedule"]["kind"],
                            at_ms=j["schedule"].get("atMs"),
                            every_ms=j["schedule"].get("everyMs"),
                            expr=j["schedule"].get("expr"),
                            tz=j["schedule"].get("tz"),
                        ),
                        payload=CronPayload(
                            kind=j["payload"].get("kind", "agent_turn"),
                            message=j["payload"].get("message", ""),
                            deliver=j["payload"].get("deliver", False),
                            channel=j["payload"].get("channel"),
                            to=j["payload"].get("to"),
                        ),
                        state=CronJobState(
                            next_run_at_ms=j.get("state", {}).get("nextRunAtMs"),
                            last_run_at_ms=j.get("state", {}).get("lastRunAtMs"),
                            last_status=j.get("state", {}).get("lastStatus"),
                            last_error=j.get("state", {}).get("lastError"),
                            consecutive_failures=j.get("state", {}).get("consecutiveFailures", 0),
                        ),
                        created_at_ms=j.get("createdAtMs", 0),
                        updated_at_ms=j.get("updatedAtMs", 0),
                        delete_after_run=j.get("deleteAfterRun", False),
                    ))
                self._store = CronStore(jobs=jobs)
            except Exception as e:
                logger.warning(f"Failed to load cron store: {e}")
                self._store = CronStore()
        else:
            self._store = CronStore()

        return self._store

    def _save_store(self) -> None:
        """Save jobs to disk."""
        if not self._store:
            return

        self.store_path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "version": self._store.version,
            "jobs": [
                {
                    "id": j.id,
                    "name": j.name,
                    "enabled": j.enabled,
                    "schedule": {
                        "kind": j.schedule.kind,
                        "atMs": j.schedule.at_ms,
                        "everyMs": j.schedule.every_ms,
                        "expr": j.schedule.expr,
                        "tz": j.schedule.tz,
                    },
                    "payload": {
                        "kind": j.payload.kind,
                        "message": j.payload.message,
                        "deliver": j.payload.deliver,
                        "channel": j.payload.channel,
                        "to": j.payload.to,
                    },
                    "state": {
                        "nextRunAtMs": j.state.next_run_at_ms,
                        "lastRunAtMs": j.state.last_run_at_ms,
                        "lastStatus": j.state.last_status,
                        "lastError": j.state.last_error,
                        "consecutiveFailures": j.state.consecutive_failures,
                    },
                    "createdAtMs": j.created_at_ms,
                    "updatedAtMs": j.updated_at_ms,
                    "deleteAfterRun": j.delete_after_run,
                }
                for j in self._store.jobs
            ]
        }

        self.store_path.write_text(json.dumps(data, indent=2))

    async def start(self) -> None:
        """Start the cron service."""
        self._running = True
        async with self._store_lock:
            self._load_store()
            self._recompute_next_runs()
            self._save_store()
            self._arm_timer()
        logger.info(f"Cron service started with {len(self._store.jobs if self._store else [])} jobs")

    def stop(self) -> None:
        """Stop the cron service."""
        self._running = False
        if self._timer_task:
            self._timer_task.cancel()
            self._timer_task = None

    def _recompute_next_runs(self) -> None:
        """Recompute next run times for all enabled jobs."""
        if not self._store:
            return
        now = _now_ms()
        for job in self._store.jobs:
            if job.enabled:
                job.state.next_run_at_ms = _compute_next_run(job.schedule, now)

    def _get_next_wake_ms(self) -> int | None:
        """Get the earliest next run time across all jobs."""
        if not self._store:
            return None
        times = [j.state.next_run_at_ms for j in self._store.jobs
                 if j.enabled and j.state.next_run_at_ms]
        return min(times) if times else None

    def _arm_timer(self) -> None:
        """Schedule the next timer tick."""
        if self._timer_task:
            self._timer_task.cancel()

        next_wake = self._get_next_wake_ms()
        if not next_wake or not self._running:
            return

        delay_ms = max(0, next_wake - _now_ms())
        delay_s = delay_ms / 1000

        async def tick():
            await asyncio.sleep(delay_s)
            if self._running:
                await self._on_timer()

        self._timer_task = asyncio.create_task(tick())

    async def _on_timer(self) -> None:
        """Handle timer tick - run due jobs."""
        if not self._store:
            return

        # Collect due jobs under lock, marking them as running
        async with self._store_lock:
            now = _now_ms()
            due_jobs = [
                j for j in self._store.jobs
                if j.enabled and j.state.next_run_at_ms and now >= j.state.next_run_at_ms
                and j.id not in self._running_jobs
            ]
            for job in due_jobs:
                self._running_jobs.add(job.id)

        # Execute outside lock (on_job may call back into cron API)
        delete_ids: set[str] = set()
        for job in due_jobs:
            should_delete = await self._execute_job(job)
            if should_delete:
                delete_ids.add(job.id)

        # Save results under lock
        async with self._store_lock:
            for job in due_jobs:
                self._running_jobs.discard(job.id)
            if delete_ids and self._store:
                self._store.jobs = [j for j in self._store.jobs if j.id not in delete_ids]
            self._save_store()
            self._arm_timer()

    # Disable job after this many consecutive failures
    MAX_CONSECUTIVE_FAILURES = 3

    async def _execute_job(self, job: CronJob) -> bool:
        """Execute a single job. Returns True if job should be deleted from store."""
        start_ms = _now_ms()
        logger.info(f"Cron: executing job '{job.name}' ({job.id})")

        try:
            response = None
            if self.on_job:
                response = await self.on_job(job)

            job.state.last_status = "ok"
            job.state.last_error = None
            job.state.consecutive_failures = 0
            logger.info(f"Cron: job '{job.name}' completed")

        except Exception as e:
            job.state.last_status = "error"
            job.state.last_error = str(e)
            job.state.consecutive_failures += 1
            logger.error(f"Cron: job '{job.name}' failed ({job.state.consecutive_failures}/{self.MAX_CONSECUTIVE_FAILURES}): {e}")

        job.state.last_run_at_ms = start_ms
        job.updated_at_ms = _now_ms()

        # Circuit breaker: disable after repeated failures
        if job.state.consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
            job.enabled = False
            job.state.next_run_at_ms = None
            logger.warning(f"Cron: disabled job '{job.name}' ({job.id}) after {job.state.consecutive_failures} consecutive failures")
            return False

        # Handle one-shot jobs
        if job.schedule.kind == "at":
            if job.delete_after_run and job.state.last_status == "ok":
                return True  # Signal caller to remove from store
            else:
                job.enabled = False
                job.state.next_run_at_ms = None
        else:
            # Compute next run
            job.state.next_run_at_ms = _compute_next_run(job.schedule, _now_ms())

        return False

    # ========== Public API ==========

    async def list_jobs(self, include_disabled: bool = False) -> list[CronJob]:
        """List all jobs."""
        async with self._store_lock:
            store = self._load_store()
            jobs = store.jobs if include_disabled else [j for j in store.jobs if j.enabled]
            return sorted(jobs, key=lambda j: j.state.next_run_at_ms or float('inf'))

    async def add_job(
        self,
        name: str,
        schedule: CronSchedule,
        message: str,
        deliver: bool = False,
        channel: str | None = None,
        to: str | None = None,
        delete_after_run: bool = False,
    ) -> CronJob:
        """Add a new job."""
        async with self._store_lock:
            store = self._load_store()
            now = _now_ms()

            # Use full UUID to avoid ID collisions
            job = CronJob(
                id=str(uuid.uuid4()),
                name=name,
                enabled=True,
                schedule=schedule,
                payload=CronPayload(
                    kind="agent_turn",
                    message=message,
                    deliver=deliver,
                    channel=channel,
                    to=to,
                ),
                state=CronJobState(next_run_at_ms=_compute_next_run(schedule, now)),
                created_at_ms=now,
                updated_at_ms=now,
                delete_after_run=delete_after_run,
            )

            store.jobs.append(job)
            self._save_store()
            self._arm_timer()

            logger.info(f"Cron: added job '{name}' ({job.id})")
            return job

    async def remove_job(self, job_id: str) -> bool:
        """Remove a job by ID."""
        async with self._store_lock:
            store = self._load_store()
            before = len(store.jobs)
            store.jobs = [j for j in store.jobs if j.id != job_id]
            removed = len(store.jobs) < before

            if removed:
                self._save_store()
                self._arm_timer()
                logger.info(f"Cron: removed job {job_id}")

            return removed

    async def enable_job(self, job_id: str, enabled: bool = True) -> CronJob | None:
        """Enable or disable a job."""
        async with self._store_lock:
            store = self._load_store()
            for job in store.jobs:
                if job.id == job_id:
                    job.enabled = enabled
                    job.updated_at_ms = _now_ms()
                    if enabled:
                        job.state.consecutive_failures = 0
                        next_run = _compute_next_run(job.schedule, _now_ms())
                        if next_run is None and job.schedule.kind == "at":
                            logger.warning(f"Cron: cannot re-enable one-shot job '{job.name}' — scheduled time has passed")
                            job.enabled = False
                            self._save_store()
                            return None
                        job.state.next_run_at_ms = next_run
                    else:
                        job.state.next_run_at_ms = None
                    self._save_store()
                    self._arm_timer()
                    return job
            return None

    async def run_job(self, job_id: str, force: bool = False) -> bool:
        """Manually run a job."""
        async with self._store_lock:
            store = self._load_store()
            job = next((j for j in store.jobs if j.id == job_id), None)
            if not job:
                return False
            if not force and not job.enabled:
                return False
            if job.id in self._running_jobs:
                logger.warning(f"Cron: job '{job.name}' is already running")
                return False
            self._running_jobs.add(job.id)

        try:
            should_delete = await self._execute_job(job)
        finally:
            async with self._store_lock:
                self._running_jobs.discard(job.id)
                if should_delete and self._store:
                    self._store.jobs = [j for j in self._store.jobs if j.id != job.id]
                self._save_store()
                self._arm_timer()

        return True

    def status(self) -> dict:
        """Get service status."""
        store = self._load_store()
        return {
            "enabled": self._running,
            "jobs": len(store.jobs),
            "next_wake_at_ms": self._get_next_wake_ms(),
        }
