from flask import current_app, jsonify, request, session

from app.api import api_bp
from app.api.middleware import auth_required


@api_bp.route("/oauth/openai/start")
@auth_required
def oauth_start():
    from app.services.oauth_service import get_authorize_url

    url, state = get_authorize_url()
    session["oauth_state"] = state
    return jsonify(authorize_url=url)


@api_bp.route("/oauth/openai/callback")
def oauth_callback():
    code = request.args.get("code")
    state = request.args.get("state")

    if not code or state != session.pop("oauth_state", None):
        return jsonify(error="Invalid OAuth callback"), 400

    from app.services.oauth_service import handle_callback

    profile = handle_callback(code)
    return jsonify(id=profile.id, provider=profile.provider, account_label=profile.account_label)


@api_bp.route("/oauth/openai/refresh", methods=["POST"])
@auth_required
def oauth_refresh():
    data = request.get_json()
    profile_id = data.get("profile_id") if data else None
    if not profile_id:
        return jsonify(error="profile_id required"), 400

    from app.services.oauth_service import refresh_tokens

    profile = refresh_tokens(profile_id)
    return jsonify(id=profile.id, refresh_status=profile.refresh_status)


@api_bp.route("/oauth/profiles")
@auth_required
def list_oauth_profiles():
    from app.models.oauth_profile import OAuthProfile

    profiles = OAuthProfile.query.all()
    return jsonify([p.to_dict() for p in profiles])
