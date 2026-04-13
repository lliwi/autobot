from app.extensions import db
from app.models.message import Message
from app.models.session import Session


def get_or_create_session(agent_id, channel_type="web", session_id=None):
    if session_id:
        session = db.session.get(Session, session_id)
        if session and session.agent_id == agent_id:
            return session

    session = Session(agent_id=agent_id, channel_type=channel_type)
    db.session.add(session)
    db.session.commit()
    return session


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
