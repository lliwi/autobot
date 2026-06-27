"""Tests for the worker scheduler's job-sync behavior.

The critical invariant: an unchanged schedule must NOT be rescheduled on every
sync, because reschedule() resets next_run_time and can silently drop a run that
was due at that instant (the weekly-task-skipping bug, issue #24).
"""

from unittest.mock import MagicMock

import pytest

from app.worker import scheduler


class _FakeScheduler:
    def __init__(self):
        self.jobs = {}
        self.added = []

    def get_job(self, job_id):
        return self.jobs.get(job_id)

    def add_job(self, func, trigger=None, id=None, **kwargs):
        job = MagicMock()
        job.id = id
        job.trigger = trigger
        self.jobs[id] = job
        self.added.append(id)
        return job


@pytest.fixture
def fake_scheduler(monkeypatch):
    fake = _FakeScheduler()
    monkeypatch.setattr(scheduler, "_scheduler", fake)
    scheduler._job_signatures.clear()
    yield fake
    scheduler._job_signatures.clear()


def _ensure(job_id="cron_1", signature="cron:0 7 * * 1:UTC"):
    scheduler._ensure_job(
        job_id=job_id, func=lambda: None, trigger=object(),
        kwargs={}, signature=signature,
    )


class TestEnsureJob:
    def test_new_job_is_added(self, fake_scheduler):
        _ensure()
        assert fake_scheduler.added == ["cron_1"]
        assert scheduler._job_signatures["cron_1"] == "cron:0 7 * * 1:UTC"

    def test_unchanged_signature_does_not_reschedule(self, fake_scheduler):
        _ensure()
        job = fake_scheduler.jobs["cron_1"]
        # Second sync with the same signature must leave the pending run intact.
        _ensure()
        job.reschedule.assert_not_called()

    def test_repeated_syncs_never_reschedule(self, fake_scheduler):
        _ensure()
        job = fake_scheduler.jobs["cron_1"]
        for _ in range(10):  # simulate many 30s syncs
            _ensure()
        job.reschedule.assert_not_called()

    def test_changed_signature_reschedules(self, fake_scheduler):
        _ensure(signature="cron:0 7 * * 1:UTC")
        job = fake_scheduler.jobs["cron_1"]
        _ensure(signature="cron:0 8 * * 1:Europe/Madrid")
        job.reschedule.assert_called_once()
        assert scheduler._job_signatures["cron_1"] == "cron:0 8 * * 1:Europe/Madrid"


class TestInternalJobsProtected:
    """``_sync_jobs`` prunes any job not in ``expected_ids``. The long-lived
    internal jobs aren't backed by a DB row, so every one registered in
    ``init_scheduler`` must be in ``_INTERNAL_JOB_IDS`` or it gets removed on the
    first 30s tick — the bug that silently stopped the incident-autopilot drain
    (incidents stuck in ``new``, never diagnosed).
    """

    def test_drain_incidents_is_protected(self):
        assert "__drain_incidents" in scheduler._INTERNAL_JOB_IDS

    def test_every_registered_internal_job_is_protected(self, monkeypatch):
        # Run init_scheduler against a fake scheduler so we capture exactly which
        # internal ("__"-prefixed) jobs it registers, then assert the protected
        # set matches — guarding the whole class, not just one job id.
        fake = _FakeScheduler()
        fake.start = lambda *a, **k: None
        monkeypatch.setattr(scheduler, "BackgroundScheduler", lambda *a, **k: fake)
        monkeypatch.setattr(scheduler, "_sync_jobs", lambda app: None)
        monkeypatch.setattr(scheduler, "_scheduler", None)

        scheduler.init_scheduler(MagicMock())

        registered_internal = {jid for jid in fake.added if jid.startswith("__")}
        assert registered_internal == scheduler._INTERNAL_JOB_IDS
