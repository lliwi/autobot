"""Bridge standard crontab day-of-week semantics onto APScheduler.

APScheduler's ``CronTrigger`` numbers the day-of-week field ``0=Monday ..
6=Sunday`` and rejects ``7``. Standard crontab (and ``croniter``, which we use
for ``compute_next_run``) numbers it ``0=Sunday .. 6=Saturday`` with ``7`` also
meaning Sunday. ``CronTrigger.from_crontab`` does *not* reconcile the two, so a
cron string like ``0 14 * * 5`` (Friday) was scheduled for Saturday and ``* * 7``
crashed (issue #27).

This module rewrites only the DOW field — every other field shares the same
convention — so the worker fires on the weekday the cron string actually means
and stays consistent with ``scheduler_service.compute_next_run``.
"""

from apscheduler.triggers.cron import CronTrigger

# cron day number (0=Sun..6=Sat) -> APScheduler day number (0=Mon..6=Sun).
_CRON_TO_APS = {0: 6, 1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5}


def _expand_token(token):
    """Expand one DOW token into a set of cron day ints (0..6, 7 -> 0)."""
    token = token.strip()
    step = 1
    base = token
    if "/" in token:
        base, step_str = token.split("/", 1)
        step = int(step_str)

    base = base.strip()
    if base == "*":
        lo, hi = 0, 7
    elif "-" in base:
        lo_str, hi_str = base.split("-", 1)
        lo, hi = int(lo_str), int(hi_str)
    else:
        return {int(base) % 7}  # single value; 7 normalizes to Sunday (0)

    return {x % 7 for x in range(lo, hi + 1, step)}


def cron_dow_to_apscheduler(field):
    """Translate a standard cron DOW field to APScheduler's day_of_week field.

    ``*`` passes through, alphabetic fields (``mon``, ``mon-fri``) pass through
    unchanged because APScheduler already interprets weekday *names* correctly.
    Numeric tokens — singles, lists, ranges and steps — are expanded and remapped
    to APScheduler's Monday-based numbering, emitted as a plain comma list so
    boundary cases (Sunday, ``7``) never form an invalid wrapped range.
    """
    field = field.strip()
    if field in ("*", "?"):
        return "*"
    if any(c.isalpha() for c in field):
        return field

    cron_days = set()
    for tok in field.split(","):
        if tok.strip() == "*":
            return "*"
        cron_days |= _expand_token(tok)

    aps_days = sorted(_CRON_TO_APS[d] for d in cron_days)
    return ",".join(str(d) for d in aps_days)


def build_cron_trigger(expr, timezone="UTC"):
    """Build a ``CronTrigger`` from a 5-field cron string with correct DOW.

    Falls back to APScheduler's own parser for non 5-field inputs (e.g. the
    ``@hourly`` shortcuts), which don't carry an ambiguous numeric DOW field.
    """
    parts = expr.split()
    if len(parts) != 5:
        return CronTrigger.from_crontab(expr, timezone=timezone)

    minute, hour, dom, month, dow = parts
    return CronTrigger(
        minute=minute,
        hour=hour,
        day=dom,
        month=month,
        day_of_week=cron_dow_to_apscheduler(dow),
        timezone=timezone,
    )
