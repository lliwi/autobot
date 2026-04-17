from flask import jsonify, request

from app.api import api_bp
from app.api.middleware import auth_required
from app.services.codex_quota_service import get_latest_snapshot
from app.services.metrics_service import (
    error_counts,
    response_times,
    runs_per_day,
    usage_by_agent,
    usage_by_channel,
    usage_by_tool,
)


@api_bp.route("/metrics/runs-per-day")
@auth_required
def metrics_runs_per_day():
    days = request.args.get("days", 30, type=int)
    return jsonify(runs_per_day(days))


@api_bp.route("/metrics/response-times")
@auth_required
def metrics_response_times():
    days = request.args.get("days", 30, type=int)
    return jsonify(response_times(days))


@api_bp.route("/metrics/errors")
@auth_required
def metrics_errors():
    days = request.args.get("days", 30, type=int)
    return jsonify(error_counts(days))


@api_bp.route("/metrics/usage-by-agent")
@auth_required
def metrics_usage_by_agent():
    days = request.args.get("days", 30, type=int)
    return jsonify(usage_by_agent(days))


@api_bp.route("/metrics/usage-by-channel")
@auth_required
def metrics_usage_by_channel():
    days = request.args.get("days", 30, type=int)
    return jsonify(usage_by_channel(days))


@api_bp.route("/metrics/usage-by-tool")
@auth_required
def metrics_usage_by_tool():
    days = request.args.get("days", 30, type=int)
    return jsonify(usage_by_tool(days))


@api_bp.route("/metrics/codex-quota")
@auth_required
def metrics_codex_quota():
    snapshot = get_latest_snapshot()
    if snapshot is None:
        return jsonify({"available": False})
    return jsonify({"available": True, **snapshot})
