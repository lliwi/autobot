from flask import jsonify, request

from app.api import api_bp
from app.api.middleware import auth_required
from app.services.scheduler_service import (
    create_task,
    delete_task,
    get_task,
    list_tasks,
    toggle_task,
    update_task,
)


@api_bp.route("/scheduled-tasks")
@auth_required
def list_scheduled_tasks():
    agent_id = request.args.get("agent_id", type=int)
    tasks = list_tasks(agent_id=agent_id)
    return jsonify([t.to_dict() for t in tasks])


@api_bp.route("/scheduled-tasks", methods=["POST"])
@auth_required
def create_scheduled_task():
    data = request.get_json()
    if not data or "agent_id" not in data or "task_type" not in data:
        return jsonify(error="agent_id and task_type required"), 400

    if data["task_type"] not in ("cron", "heartbeat", "one_shot"):
        return jsonify(error="Invalid task_type"), 400

    if data["task_type"] == "cron" and not data.get("schedule_expr"):
        return jsonify(error="schedule_expr required for cron tasks"), 400

    task = create_task(
        agent_id=data["agent_id"],
        task_type=data["task_type"],
        schedule_expr=data.get("schedule_expr"),
        timezone_str=data.get("timezone", "UTC"),
        payload_json=data.get("payload_json"),
        enabled=data.get("enabled", True),
        max_retries=data.get("max_retries", 3),
    )
    return jsonify(task.to_dict()), 201


@api_bp.route("/scheduled-tasks/<int:task_id>")
@auth_required
def get_scheduled_task(task_id):
    task = get_task(task_id)
    if task is None:
        return jsonify(error="Task not found"), 404
    return jsonify(task.to_dict())


@api_bp.route("/scheduled-tasks/<int:task_id>", methods=["PUT"])
@auth_required
def update_scheduled_task(task_id):
    data = request.get_json()
    if not data:
        return jsonify(error="No data provided"), 400

    allowed = {"schedule_expr", "timezone", "payload_json", "enabled", "max_retries"}
    updates = {k: v for k, v in data.items() if k in allowed}
    if "timezone" in updates:
        updates["timezone_str"] = updates.pop("timezone")

    task = update_task(task_id, **updates)
    if task is None:
        return jsonify(error="Task not found"), 404
    return jsonify(task.to_dict())


@api_bp.route("/scheduled-tasks/<int:task_id>", methods=["DELETE"])
@auth_required
def delete_scheduled_task(task_id):
    if not delete_task(task_id):
        return jsonify(error="Task not found"), 404
    return jsonify(ok=True)


@api_bp.route("/scheduled-tasks/<int:task_id>/toggle", methods=["POST"])
@auth_required
def toggle_scheduled_task(task_id):
    task = toggle_task(task_id)
    if task is None:
        return jsonify(error="Task not found"), 404
    return jsonify(task.to_dict())
