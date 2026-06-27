"""Gunicorn configuration for Autobot in production.

Tuned for the SSE streaming chat endpoint (``/api/chat``). That endpoint holds a
long-lived ``text/event-stream`` response and runs the blocking agent loop in a
background thread that feeds a queue (see ``app/api/chat.py``). ``gthread``
workers fit this perfectly: each streaming response occupies one worker thread
while the arbiter heartbeat runs on its own thread, so a chat turn that takes
minutes (LLM + tool rounds) never trips the worker timeout.

Every value can be overridden via the matching ``GUNICORN_*`` env var without a
rebuild.
"""
import os

# Bind inside the container. With macvlan the container has its own LAN IP, so
# clients reach gunicorn at <container-ip>:5000 directly (no port mapping).
bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:5000")

# Process model. The APScheduler lives in the separate worker.py process, so
# running multiple web workers does NOT double-schedule anything; shared state
# is in postgres/redis. Threads carry the concurrent SSE streams.
workers = int(os.environ.get("GUNICORN_WORKERS", "2"))
worker_class = "gthread"
threads = int(os.environ.get("GUNICORN_THREADS", "8"))

# Long request timeout so a streaming chat turn is never killed mid-response.
# The SSE generator emits a keepalive comment every 20s, so the connection
# stays warm through NAT/proxies even while the agent is thinking.
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "300"))
graceful_timeout = int(os.environ.get("GUNICORN_GRACEFUL_TIMEOUT", "30"))
keepalive = int(os.environ.get("GUNICORN_KEEPALIVE", "75"))

# Never enable the reloader in production: a code change should be a deliberate
# redeploy, and an auto-reload would cut in-flight SSE streams (the exact bug
# this setup replaces).
reload = False

# Log to stdout/stderr so `docker compose logs` captures everything.
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("GUNICORN_LOGLEVEL", "info")
