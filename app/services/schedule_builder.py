"""Translate a friendly schedule selection into a cron expression.

The scheduler UI lets users pick a frequency (every N minutes, hourly, daily,
weekly, monthly) instead of writing a raw cron string. We keep cron as the
execution format — the worker and ``scheduler_service.compute_next_run`` are
unchanged — and store the user's selection (``schedule_config``) so the edit
form can repopulate the selectors without lossy cron parsing.

A ``schedule_config`` dict looks like::

    {"freq_type": "weekly", "hour": 18, "minute": 30, "weekdays": [1, 3, 5]}
    {"freq_type": "daily", "hour": 9, "minute": 0}
    {"freq_type": "minutes", "interval": 15}
    {"freq_type": "hourly", "interval": 2, "minute": 0}
    {"freq_type": "monthly", "day": 1, "hour": 8, "minute": 0}
    {"freq_type": "cron", "expr": "*/15 * * * *"}
"""

FREQ_TYPES = ("minutes", "hourly", "daily", "weekly", "monthly", "cron")

# cron day-of-week: 0=Sunday .. 6=Saturday. Stored as cron numbers.
_WEEKDAY_NAMES = {
    0: "Domingo",
    1: "Lunes",
    2: "Martes",
    3: "Miércoles",
    4: "Jueves",
    5: "Viernes",
    6: "Sábado",
}


def _as_int(value, field, lo, hi):
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field}: se esperaba un número entero")
    if n < lo or n > hi:
        raise ValueError(f"{field}: debe estar entre {lo} y {hi}")
    return n


def build_cron(config):
    """Build a 5-field cron expression from a ``schedule_config`` dict.

    Raises ``ValueError`` with a human-readable (Spanish) message when the
    config is incomplete or out of range.
    """
    if not config:
        raise ValueError("Configuración de frecuencia vacía")

    freq = config.get("freq_type")
    if freq not in FREQ_TYPES:
        raise ValueError(f"Tipo de frecuencia no válido: {freq!r}")

    if freq == "cron":
        expr = (config.get("expr") or "").strip()
        if not expr:
            raise ValueError("La expresión cron no puede estar vacía")
        return expr

    if freq == "minutes":
        interval = _as_int(config.get("interval", 1), "Intervalo de minutos", 1, 59)
        return f"*/{interval} * * * *"

    if freq == "hourly":
        interval = _as_int(config.get("interval", 1), "Intervalo de horas", 1, 23)
        minute = _as_int(config.get("minute", 0), "Minuto", 0, 59)
        hour_field = f"*/{interval}" if interval > 1 else "*"
        return f"{minute} {hour_field} * * *"

    if freq == "daily":
        hour = _as_int(config.get("hour", 0), "Hora", 0, 23)
        minute = _as_int(config.get("minute", 0), "Minuto", 0, 59)
        return f"{minute} {hour} * * *"

    if freq == "weekly":
        hour = _as_int(config.get("hour", 0), "Hora", 0, 23)
        minute = _as_int(config.get("minute", 0), "Minuto", 0, 59)
        raw_days = config.get("weekdays") or []
        days = sorted({_as_int(d, "Día de la semana", 0, 6) for d in raw_days})
        if not days:
            raise ValueError("Selecciona al menos un día de la semana")
        return f"{minute} {hour} * * {','.join(str(d) for d in days)}"

    if freq == "monthly":
        day = _as_int(config.get("day", 1), "Día del mes", 1, 31)
        hour = _as_int(config.get("hour", 0), "Hora", 0, 23)
        minute = _as_int(config.get("minute", 0), "Minuto", 0, 59)
        return f"{minute} {hour} {day} * *"

    raise ValueError(f"Tipo de frecuencia no soportado: {freq!r}")  # pragma: no cover


def describe(config):
    """Return a human-readable (Spanish) description of a schedule_config."""
    if not config:
        return ""
    freq = config.get("freq_type")

    def _hhmm(c):
        return f"{int(c.get('hour', 0)):02d}:{int(c.get('minute', 0)):02d}"

    try:
        if freq == "minutes":
            n = int(config.get("interval", 1))
            return "Cada minuto" if n == 1 else f"Cada {n} minutos"
        if freq == "hourly":
            n = int(config.get("interval", 1))
            at = f"al minuto {int(config.get('minute', 0))}"
            return f"Cada hora {at}" if n == 1 else f"Cada {n} horas {at}"
        if freq == "daily":
            return f"Cada día a las {_hhmm(config)}"
        if freq == "weekly":
            days = sorted({int(d) for d in (config.get("weekdays") or [])})
            names = ", ".join(_WEEKDAY_NAMES.get(d, str(d)) for d in days) or "—"
            return f"{names} a las {_hhmm(config)}"
        if freq == "monthly":
            return f"El día {int(config.get('day', 1))} de cada mes a las {_hhmm(config)}"
        if freq == "cron":
            return f"Cron: {config.get('expr', '')}"
    except (TypeError, ValueError):
        return ""
    return ""


def config_from_form(form):
    """Build a schedule_config dict from a request form (werkzeug MultiDict).

    Returns ``None`` when the form does not carry selector fields (e.g. a
    non-cron task type), so callers can fall back to a raw ``schedule_expr``.
    """
    freq = form.get("freq_type")
    if not freq:
        return None

    if freq == "cron":
        return {"freq_type": "cron", "expr": (form.get("schedule_expr") or "").strip()}

    if freq == "minutes":
        return {"freq_type": "minutes", "interval": _form_int(form, "interval_minutes", 15)}

    if freq == "hourly":
        return {
            "freq_type": "hourly",
            "interval": _form_int(form, "interval_hours", 1),
            "minute": _form_int(form, "at_minute", 0),
        }

    if freq in ("daily", "weekly", "monthly"):
        hour, minute = _parse_time(form.get("at_time"))
        config = {"freq_type": freq, "hour": hour, "minute": minute}
        if freq == "weekly":
            config["weekdays"] = [int(d) for d in form.getlist("weekdays") if d != ""]
        if freq == "monthly":
            config["day"] = _form_int(form, "day_of_month", 1)
        return config

    return None


def _form_int(form, key, default):
    raw = form.get(key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _parse_time(value):
    """Parse an ``HH:MM`` string into (hour, minute); defaults to (0, 0)."""
    if not value:
        return 0, 0
    try:
        hh, mm = value.split(":", 1)
        return int(hh), int(mm)
    except (ValueError, AttributeError):
        return 0, 0
