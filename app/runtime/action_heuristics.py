"""Shallow text heuristics that bias the runtime toward action-first behavior.

Two independent tests:
- ``is_task_like(text)``: does this user message ask the agent to *do* something?
- ``looks_like_promise(text)``: does this assistant response announce future work
  instead of executing it?

Heuristics, not NLP — cheap regex checks against a curated verb list in ES+EN.
Used by ``agent_runner`` (to re-prompt when the model stalls on a task) and by
``chat_service`` (to auto-create an Objective so the heartbeat can chase
unfinished tasks across turns).
"""
import re

_TASK_VERBS_ES = (
    r"crea|haz|haga|ejecuta|dame|busca|instala|programa|revisa|analiza|"
    r"implementa|actualiza|elimina|borra|agrega|añade|prepara|genera|"
    r"env[ií]a|manda|publica|prueba|descarga|sube|configura|resuelve|arregla|"
    r"construye|despliega|compra|llama|contacta|reserva|escribe|redacta|"
    r"traduce|resume|organiza|sincroniza|lista|encuentra|verifica|valida|"
    r"arranca|lanza|inicia|detén|para|sigue|continúa|termina|completa|"
    r"guarda|carga|copia|mueve|renombra|exporta|importa|abre|cierra|"
    r"monitoriza|mide|reporta|documenta|corrige|migra|despliega|depura|"
    r"autoriza|aprueba|rechaza|notifica|avisa|procesa|transforma|extrae"
)
_TASK_VERBS_EN = (
    r"create|run|execute|build|install|schedule|check|review|"
    r"analyze|analyse|implement|update|delete|remove|prepare|generate|"
    r"send|publish|download|upload|configure|resolve|deploy|"
    r"translate|summarize|summarise|organize|organise|"
    r"verify|validate|rename|export|import|"
    r"monitor|measure|document|migrate|authorize|"
    r"approve|reject|notify|transform|extract|retrieve"
)

_TASK_PATTERN = re.compile(
    rf"\b(?:{_TASK_VERBS_ES}|{_TASK_VERBS_EN})\w*\b",
    re.IGNORECASE,
)

# Politeness wrappers around a request still count ("¿puedes crear...?")
_TASK_WRAPPER_PATTERN = re.compile(
    r"\b(?:puedes|podr[íi]as|necesito|quiero|me gustar[íi]a|por favor|"
    r"can you|could you|please|i need|i want|i'd like|would you)\b",
    re.IGNORECASE,
)

_PROMISE_PATTERN = re.compile(
    r"(?:\bvoy a\b|\bvamos a\b|"
    # "lo haré" / "lo ejecuto (yo)" / "lo creo" / "lo hago" …
    r"\b(?:lo|la|te lo|se lo)\s+(?:har[ée]|ejecut[aoe]\w*|cre[ao]\w*|hago|hag[ao]|"
    r"llevo|llevar[ée]|pongo|pondr[ée]|paso|pasar[ée]|miro|reviso|intento|intentar[ée])\b|"
    r"\bproceder[ée]\b|\bprocedo a\b|\bpaso a\b|\bpasar[ée] a\b|"
    r"\bahora (?:lo )?(?:ejecuto|creo|hago|procedo|lanzo|pongo|miro|reviso)\b|"
    r"\ben cuanto (?:pueda|termine)\b|"
    r"\bI will\b|\bI'll\b|\bI am going to\b|\bI'm going to\b|\blet me\b|"
    r"\bI shall\b|\bgoing to\b|\bI intend to\b|\bI plan to\b|"
    r"\bwill proceed\b|\bwill handle\b|\bwill do\b)",
    re.IGNORECASE,
)


def is_task_like(text: str) -> bool:
    """Heuristic: does ``text`` look like the user asking the agent to act?

    Matches imperative verbs (crear, ejecutar, install, run…) plus common
    politeness wrappers ("¿puedes…?", "please…"). Returns False for short
    greetings, questions about opinion/state, or pure acknowledgements.
    """
    if not text:
        return False
    if len(text) < 4:
        return False
    if _TASK_PATTERN.search(text):
        return True
    # Polite wrapper alone is strong enough ("¿puedes…?", "please…") even
    # without a verb we indexed — the model should still try to act.
    return bool(_TASK_WRAPPER_PATTERN.search(text))


def looks_like_promise(text: str) -> bool:
    """Heuristic: does the assistant's reply announce future work?

    Matches phrases like "voy a", "lo ejecuto", "I will", "let me" that
    signal intent without delivery.
    """
    if not text:
        return False
    return bool(_PROMISE_PATTERN.search(text))


def summarize_task(text: str, max_len: int = 120) -> str:
    """Truncate ``text`` to a single-line title suitable for an Objective."""
    if not text:
        return "(untitled task)"
    first_line = text.strip().splitlines()[0]
    if len(first_line) <= max_len:
        return first_line
    return first_line[: max_len - 1].rstrip() + "…"
