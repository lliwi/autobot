from flask import jsonify, request

from app.api import api_bp
from app.api.middleware import auth_required
from app.extensions import db
from app.models.run import Run


@api_bp.route("/runs")
@auth_required
def list_runs():
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)

    query = Run.query.order_by(Run.started_at.desc())

    agent_id = request.args.get("agent_id", type=int)
    if agent_id:
        query = query.filter_by(agent_id=agent_id)

    pagination = query.paginate(page=page, per_page=per_page)
    return jsonify(
        runs=[r.to_dict() for r in pagination.items],
        total=pagination.total,
        page=pagination.page,
        pages=pagination.pages,
    )


@api_bp.route("/runs/<int:run_id>")
@auth_required
def get_run(run_id):
    run = db.session.get(Run, run_id)
    if run is None:
        return jsonify(error="Run not found"), 404
    return jsonify(run.to_dict())
