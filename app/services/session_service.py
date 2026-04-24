from app.extensions import db
from app.models.message import Message
from app.models.session import Session


def get_or_create_session(agent_id, channel_type="web", session_id=None, external_chat_id=None, external_user_id=None):
    if session_id:
        session = db.session.get(Session, session_id)
        if session and session.agent_id == agent_id:
            return session

    # For Matrix/external channels, reuse session by external_chat_id
    if external_chat_id:
        session = Session.query.filter_by(
            agent_id=agent_id,
            channel_type=channel_type,
            external_chat_id=external_chat_id,
        ).first()
        if session:
            return session

    session = Session(
        agent_id=agent_id,
        channel_type=channel_type,
        external_chat_id=external_chat_id,
        external_user_id=external_user_id,
    )
    db.session.add(session)
    db.session.commit()
    return session


def close_session(session_id: int) -> None:
    session = db.session.get(Session, session_id)
    if session and session.status != "closed":
        session.status = "closed"
        db.session.commit()


def add_message(session_id, role, content, metadata=None, token_count=None):
    msg = Message(
        session_id=session_id,
        role=role,
        content=content,
        metadata_json=metadata,
        token_count=token_count,
    )
    db.session.add(msg)
    db.session.commit()
    return msg
