"""Token refresh must be serialized so concurrent cron tasks don't trigger
overlapping OAuth refreshes (which fail with 'refresh_token_reused')."""

import threading
import time

from app.services import codex_auth


def test_refresh_is_serialized(monkeypatch):
    active = {"now": 0, "max": 0}
    lock = threading.Lock()

    def fake_get_token(provider=None, storage=None):
        with lock:
            active["now"] += 1
            active["max"] = max(active["max"], active["now"])
        time.sleep(0.02)  # hold the "refresh" so overlaps would be observable
        with lock:
            active["now"] -= 1

        class _Tok:
            access = "tok"
        return _Tok()

    monkeypatch.setattr(codex_auth, "get_token", fake_get_token)
    monkeypatch.setattr(codex_auth, "_storage", lambda: None)

    threads = [threading.Thread(target=codex_auth.get_access_token) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # The lock must keep refreshes strictly serial.
    assert active["max"] == 1
