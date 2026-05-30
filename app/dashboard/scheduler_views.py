from flask import render_template, redirect, url_for, request, flash, current_app
from flask_login import login_required

from app.dashboard import dashboard_bp
from app.extensions import db
from app.models.agent import Agent
from app.services import schedule_builder
from app.services.scheduler_service import (
    create_task,
    delete_task,
    get_task,
    list_tasks,
    toggle_task,
    update_task,
)


def _default_timezone():
    return current_app.config.get("APP_TIMEZONE") or "UTC"


@dashboard_bp.route("/scheduler")
@login_required
def scheduler_list():
    tasks = list_tasks()
    return render_template(
        "dashboard/scheduler_list.html", tasks=tasks, describe=schedule_builder.describe
    )


@dashboard_bp.route("/scheduler/create", methods=["GET", "POST"])
@login_required
def scheduler_create():
    if request.method == "POST":
        task_type = request.form["task_type"]
        schedule_config = schedule_builder.config_from_form(request.form) if task_type == "cron" else None
        try:
            task = create_task(
                agent_id=int(request.form["agent_id"]),
                name=request.form.get("name", ""),
                task_type=task_type,
                schedule_expr=request.form.get("schedule_expr") or None,
                schedule_config=schedule_config,
                timezone_str=request.form.get("timezone") or _default_timezone(),
                payload_json={"message": request.form.get("payload_message", "")} if request.form.get("payload_message") else None,
                max_retries=int(request.form.get("max_retries", 3)),
            )
        except ValueError as e:
            flash(f"Frecuencia no válida: {e}", "danger")
            agents = Agent.query.order_by(Agent.name).all()
            return render_template("dashboard/scheduler_create.html", agents=agents, default_tz=_default_timezone())
        label = task.name or f"ID {task.id}"
        flash(f"Scheduled task '{label}' created.", "success")
        return redirect(url_for("dashboard.scheduler_list"))

    agents = Agent.query.order_by(Agent.name).all()
    return render_template("dashboard/scheduler_create.html", agents=agents, default_tz=_default_timezone())


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
        task_type = request.form["task_type"]
        schedule_config = schedule_builder.config_from_form(request.form) if task_type == "cron" else None
        try:
            update_task(
                task_id,
                name=request.form.get("name", ""),
                agent_id=int(request.form["agent_id"]),
                task_type=task_type,
                schedule_expr=request.form.get("schedule_expr") or None,
                schedule_config=schedule_config,
                timezone=request.form.get("timezone") or _default_timezone(),
                payload_json=payload,
                max_retries=int(request.form.get("max_retries", 3)),
            )
        except ValueError as e:
            flash(f"Frecuencia no válida: {e}", "danger")
            agents = Agent.query.order_by(Agent.name).all()
            return render_template("dashboard/scheduler_edit.html", task=task, agents=agents)
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
