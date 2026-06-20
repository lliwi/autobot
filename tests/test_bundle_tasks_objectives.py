"""Bundle round-trip for scheduled tasks and open objectives."""
import os
import tempfile

from app.extensions import db
from app.models.objective import Objective
from app.models.scheduled_task import ScheduledTask
from app.services import bundle_service


def test_bundle_roundtrips_tasks_and_objectives(app, agent):
    db.session.add(ScheduledTask(
        agent_id=agent.id, name="nightly", task_type="cron",
        schedule_expr="0 3 * * *", timezone="UTC",
        payload_json={"prompt": "do x"}, enabled=True, max_retries=5,
    ))
    db.session.add(Objective(
        agent_id=agent.id, title="ship feature", status="active",
        context_json={"plan": [{"step": "a", "status": "pending"}]},
    ))
    db.session.add(Objective(agent_id=agent.id, title="old", status="done"))
    db.session.commit()

    fd, path = tempfile.mkstemp(suffix=".tar.gz")
    os.close(fd)
    try:
        rep = bundle_service.export_bundle(path)
        assert rep.scheduled_tasks == 1
        assert rep.objectives == 1  # only the active one, not the done one

        ScheduledTask.query.delete()
        Objective.query.delete()
        db.session.commit()

        irep = bundle_service.import_bundle(path, overwrite=True)
        assert irep.tasks_created == 1
        assert irep.objectives_created == 1

        t = ScheduledTask.query.filter_by(agent_id=agent.id).one()
        assert t.name == "nightly"
        assert t.schedule_expr == "0 3 * * *"
        assert t.max_retries == 5
        assert t.payload_json == {"prompt": "do x"}

        o = Objective.query.filter_by(agent_id=agent.id, status="active").one()
        assert o.title == "ship feature"
        assert o.context_json["plan"][0]["step"] == "a"
        assert Objective.query.filter_by(status="done").count() == 0

        # Re-import is idempotent (no duplicates).
        bundle_service.import_bundle(path, overwrite=True)
        assert ScheduledTask.query.count() == 1
        assert Objective.query.filter_by(status="active").count() == 1
    finally:
        os.remove(path)
