"""Signed audit chain for PatchProposal records.

Each patch stores a SHA-256 content_hash computed from its own fields plus the
previous patch's hash, forming a tamper-evident chain. A single altered record
breaks every subsequent hash, making retrospective manipulation detectable.
"""
import hashlib

from app.models.patch_proposal import PatchProposal

_GENESIS = "genesis"


def _compute_hash(agent_id: int, target_path: str, diff_text: str,
                  created_at_iso: str, previous_hash: str) -> str:
    payload = "\n".join([
        str(agent_id),
        target_path,
        diff_text or "",
        created_at_iso,
        previous_hash,
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def get_previous_hash(agent_id: int) -> str:
    """Return the content_hash of the most recent patch for this agent, or 'genesis'."""
    last = (
        PatchProposal.query
        .filter_by(agent_id=agent_id)
        .filter(PatchProposal.content_hash.isnot(None))
        .order_by(PatchProposal.created_at.desc())
        .first()
    )
    return last.content_hash if last else _GENESIS


def stamp(patch: PatchProposal) -> None:
    """Compute and assign content_hash and previous_hash on a new patch.

    Must be called before the patch is committed so created_at is already set.
    """
    prev = get_previous_hash(patch.agent_id)
    patch.previous_hash = prev
    patch.content_hash = _compute_hash(
        agent_id=patch.agent_id,
        target_path=patch.target_path,
        diff_text=patch.diff_text,
        created_at_iso=patch.created_at.isoformat(),
        previous_hash=prev,
    )


def verify_chain(agent_id: int) -> dict:
    """Verify the audit chain for all patches of an agent.

    Returns {"ok": bool, "total": int, "broken_at": patch_id | None,
             "first_break": str | None}.
    """
    patches = (
        PatchProposal.query
        .filter_by(agent_id=agent_id)
        .filter(PatchProposal.content_hash.isnot(None))
        .order_by(PatchProposal.created_at.asc())
        .all()
    )
    expected_previous = _GENESIS
    for patch in patches:
        expected = _compute_hash(
            agent_id=patch.agent_id,
            target_path=patch.target_path,
            diff_text=patch.diff_text,
            created_at_iso=patch.created_at.isoformat(),
            previous_hash=expected_previous,
        )
        if patch.content_hash != expected or patch.previous_hash != expected_previous:
            return {
                "ok": False,
                "total": len(patches),
                "broken_at": patch.id,
                "first_break": (
                    f"patch #{patch.id}: stored hash {patch.content_hash[:12]}… "
                    f"does not match expected {expected[:12]}…"
                ),
            }
        expected_previous = patch.content_hash

    return {"ok": True, "total": len(patches), "broken_at": None, "first_break": None}
