from flask import render_template, redirect, url_for, request, flash
from flask_login import login_required

from app.dashboard import dashboard_bp
from app.extensions import db
from app.models.agent import Agent
from app.services.scheduler_service import (
    create_task,
    delete_task,
    get_task,
    list_tasks,
    toggle_task,
    update_task,
)


@dashboard_bp.route("/scheduler")
@login_required
def scheduler_list():
    tasks = list_tasks()
    return render_template("dashboard/scheduler_list.html", tasks=tasks)


@dashboard_bp.route("/scheduler/create", methods=["GET", "POST"])
@login_required
def scheduler_create():
    if request.method == "POST":
        task = create_task(
            agent_id=int(request.form["agent_id"]),
            task_type=request.form["task_type"],
            schedule_expr=request.form.get("schedule_expr") or None,
            timezone_str=request.form.get("timezone", "UTC"),
            payload_json={"message": request.form.get("payload_message", "")} if request.form.get("payload_message") else None,
            max_retries=int(request.form.get("max_retries", 3)),
        )
        flash(f"Scheduled task created (ID {task.id}).", "success")
        return redirect(url_for("dashboard.scheduler_list"))

    agents = Agent.query.order_by(Agent.name).all()
    return render_template("dashboard/scheduler_create.html", agents=agents)


@dashboard_bp.route("/scheduler/<int:task_id>/edit", methods=["GET", "POST"])
@login_required
def scheduler_edit(task_id):
    task = get_task(task_id)
    if task is None:
        flash("Task not found.", "danger")
        return redirect(url_for("dashboard.scheduler_list"))

    if request.method == "POST":
        payload_message = request.form.get("payload_message", "").strip()
        payload = {"message": payload_message} if payload_message else None
        update_task(
            task_id,
            agent_id=int(request.form["agent_id"]),
            task_type=request.form["task_type"],
            schedule_expr=request.form.get("schedule_expr") or None,
            timezone=request.form.get("timezone", "UTC"),
            payload_json=payload,
            max_retries=int(request.form.get("max_retries", 3)),
        )
        flash("Scheduled task updated.", "success")
        return redirect(url_for("dashboard.scheduler_list"))

    agents = Agent.query.order_by(Agent.name).all()
    return render_template("dashboard/scheduler_edit.html", task=task, agents=agents)


@dashboard_bp.route("/scheduler/<int:task_id>/toggle", methods=["POST"])
@login_required
def scheduler_toggle(task_id):
    task = toggle_task(task_id)
    if task is None:
        flash("Task not found.", "danger")
    return redirect(url_for("dashboard.scheduler_list"))


@dashboard_bp.route("/scheduler/<int:task_id>/delete", methods=["POST"])
@login_required
def scheduler_delete(task_id):
    if not delete_task(task_id):
        flash("Task not found.", "danger")
    else:
        flash("Task deleted.", "success")
    return redirect(url_for("dashboard.scheduler_list"))
