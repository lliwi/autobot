import asyncio
import logging
import threading
import time

logger = logging.getLogger(__name__)


class MatrixBot:
    def __init__(self, app):
        self.app = app
        self._thread = None
        self._running = False
        # Populated once the bot's event loop is up. Used by send_dm to
        # schedule outbound sends from other threads (e.g. the scheduler
        # worker running an agent cron task).
        self._loop = None
        self._client = None
        # Resolves only after login succeeds, so send_dm can block briefly
        # at startup instead of failing with "bot not ready".
        self._ready = threading.Event()

    def start(self):
        """Start the Matrix bot in a daemon thread."""
        homeserver = self.app.config.get("MATRIX_HOMESERVER")
        user_id = self.app.config.get("MATRIX_USER_ID")
        password = self.app.config.get("MATRIX_PASSWORD")

        if not all([homeserver, user_id, password]):
            logger.info("Matrix not configured, skipping")
            return

        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info(f"Matrix bot starting for {user_id} on {homeserver}")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def _run_loop(self):
        """Run the async Matrix client in its own event loop."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._async_main())
        except Exception as e:
            logger.error(f"Matrix bot crashed: {e}")
        finally:
            loop.close()

    async def _async_main(self):
        from nio import (AsyncClient, InviteMemberEvent, LoginResponse,
                         MegolmEvent, RoomMessageAudio, RoomMessageFile,
                         RoomMessageText)

        homeserver = self.app.config["MATRIX_HOMESERVER"]
        user_id = self.app.config["MATRIX_USER_ID"]
        password = self.app.config["MATRIX_PASSWORD"]

        client = AsyncClient(homeserver, user_id)
        self._client = client

        # Login
        retry_delay = 1
        while self._running:
            try:
                response = await client.login(password)
                if isinstance(response, LoginResponse):
                    logger.info(f"Matrix logged in as {user_id}")
                    self._ready.set()
                    break
                else:
                    logger.error(f"Matrix login failed: {response}")
            except Exception as e:
                logger.error(f"Matrix connection error: {e}")

            await asyncio.sleep(min(retry_delay, 60))
            retry_delay *= 2

        if not self._running:
            return

        # Register message callback
        client.add_event_callback(
            lambda room, event: self._handle_message(client, room, event),
            RoomMessageText,
        )

        # Auto-accept room invites
        client.add_event_callback(
            lambda room, event: self._handle_invite(client, room, event),
            InviteMemberEvent,
        )

        # Warn on encrypted messages — bot has no E2E crypto store
        client.add_event_callback(
            lambda room, event: self._handle_encrypted(client, room, event),
            MegolmEvent,
        )

        # Audio ingest — m.audio and m.file (audio/*) events
        client.add_event_callback(
            lambda room, event: self._handle_audio(client, room, event),
            RoomMessageAudio,
        )
        client.add_event_callback(
            lambda room, event: self._handle_audio(client, room, event),
            RoomMessageFile,
        )

        # Restore sync token from Redis so we resume from where we left off
        # instead of replaying all events on every restart.
        since = self._redis_get_sync_token()
        if since:
            logger.info("Matrix resuming sync from saved token (since=%s…)", since[:16])
            client.next_batch = since

        # Initial sync — with a saved token this processes only new events.
        # Without one it does a full sync; we limit timeline to avoid replaying
        # old messages by setting a small timeout.
        resp = await client.sync(timeout=10000, full_state=True)
        if hasattr(resp, "next_batch") and resp.next_batch:
            self._redis_set_sync_token(resp.next_batch)

        # Proactively redirect users stuck in encrypted DM rooms
        asyncio.ensure_future(self._redirect_encrypted_dms(client))

        # Start outbox drain loop and heartbeat as concurrent tasks
        drain_task = asyncio.ensure_future(self._drain_loop())
        hb_task = asyncio.ensure_future(self._heartbeat_loop(client))

        # Sync loop
        retry_delay = 1
        while self._running:
            try:
                resp = await client.sync(timeout=30000)
                retry_delay = 1
                if hasattr(resp, "next_batch") and resp.next_batch:
                    self._redis_set_sync_token(resp.next_batch)
            except Exception as e:
                logger.error(f"Matrix sync error: {e}")
                await asyncio.sleep(min(retry_delay, 60))
                retry_delay *= 2

        drain_task.cancel()
        hb_task.cancel()
        await client.close()

    async def _drain_loop(self):
        """Poll the Redis outbox every 2 s and dispatch queued messages.

        Calls the async send methods directly — do NOT call the synchronous
        send_dm/send_to_room wrappers here, as those use
        run_coroutine_threadsafe(...).result() which deadlocks when called
        from inside the event loop.
        """
        import json as _json
        _QUEUE_KEY = "autobot:matrix_outbox"
        _MAX_RETRIES = 3

        while self._running:
            try:
                with self.app.app_context():
                    try:
                        import redis as _redis
                        r = _redis.Redis.from_url(
                            self.app.config.get("REDIS_URL", "redis://localhost:6379/0"),
                            socket_timeout=1.0, decode_responses=True,
                        )
                    except Exception:
                        await asyncio.sleep(2)
                        continue

                    requeued = []
                    sent = 0
                    for _ in range(20):
                        raw = r.lpop(_QUEUE_KEY)
                        if raw is None:
                            break
                        try:
                            item = _json.loads(raw)
                            target = item.get("target", "")
                            body = item.get("body", "")
                            attempts = int(item.get("attempts", 0))
                            if not target or not body:
                                continue
                            if target.startswith("!"):
                                result = await self._send_to_room_async(target, body)
                            elif target.startswith("@"):
                                result = await self._send_dm_async(target, body)
                            else:
                                logger.warning("matrix_outbox: unknown target %r, skipping", target)
                                continue
                            if result.get("ok"):
                                sent += 1
                            else:
                                err = result.get("error", "unknown")
                                attempts += 1
                                if attempts < _MAX_RETRIES:
                                    logger.warning(
                                        "matrix_outbox: delivery failed for %s (attempt %d/%d): %s",
                                        target, attempts, _MAX_RETRIES, err,
                                    )
                                    requeued.append(_json.dumps({"target": target, "body": body, "attempts": attempts}))
                                else:
                                    logger.error(
                                        "matrix_outbox: DROPPED message for %s after %d attempts. Error: %s",
                                        target, attempts, err,
                                    )
                        except Exception:
                            logger.exception("matrix_outbox drain: error on item %r", raw)

                    if requeued:
                        pipe = r.pipeline()
                        for payload in requeued:
                            pipe.rpush(_QUEUE_KEY, payload)
                        pipe.execute()

                    if sent:
                        logger.info("matrix_outbox: dispatched %d message(s)", sent)

                    # Process room-leave queue
                    for _ in range(10):
                        room_id = r.lpop("autobot:matrix:leave_queue")
                        if not room_id:
                            break
                        try:
                            await self._client.room_leave(room_id)
                            # Remove from in-memory state immediately so the
                            # next heartbeat write no longer includes this room.
                            self._client.rooms.pop(room_id, None)
                            logger.info("Left and removed room %s", room_id)
                        except Exception:
                            logger.exception("Failed to leave room %s", room_id)

            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("matrix_outbox drain error")
            await asyncio.sleep(2)

    async def _handle_invite(self, client, room, event):
        """Auto-join rooms the bot is invited to, respecting allowlists.

        The bot only accepts invites from users allowed to DM it (for 2-member
        rooms) or allowed in general (for groups). Invites from blocked users
        are simply ignored — they'll sit in the invite list on the homeserver.
        """
        if event.state_key != client.user_id:
            return

        with self.app.app_context():
            from app.services.matrix_service import (
                is_dm_user_allowed,
                is_user_allowed,
            )

            inviter = event.sender
            is_dm = room.member_count <= 2
            allowed = is_dm_user_allowed(inviter) if is_dm else is_user_allowed(inviter)
            if not allowed:
                logger.info(f"Matrix invite from {inviter} for {room.room_id} — blocked by allowlist")
                return

        logger.info(f"Matrix auto-joining {room.room_id} (invited by {event.sender})")
        for _ in range(3):
            result = await client.join(room.room_id)
            if hasattr(result, "room_id"):
                # After joining, check if the room is encrypted and redirect if needed
                await asyncio.sleep(1)
                joined_room = client.rooms.get(room.room_id)
                if joined_room and getattr(joined_room, "encrypted", False) and joined_room.member_count <= 2:
                    logger.info(
                        "Joined encrypted DM room %s from %s — redirecting to unencrypted room",
                        room.room_id, event.sender,
                    )
                    new_room_id = await self._create_dm_room(client, event.sender)
                    if new_room_id and new_room_id != room.room_id:
                        try:
                            await client.room_send(
                                room_id=room.room_id,
                                message_type="m.room.message",
                                content={"msgtype": "m.text", "body": (
                                    "Hola! Este chat tiene cifrado E2E activado y no puedo recibir tus mensajes.\n\n"
                                    "He creado un nuevo chat sin cifrado, acepta la invitación:\n"
                                    f"https://matrix.to/#/{new_room_id}"
                                )},
                            )
                        except Exception as e:
                            logger.error("Failed to send redirect after join: %s", e)
                        try:
                            await client.room_send(
                                room_id=new_room_id,
                                message_type="m.room.message",
                                content={"msgtype": "m.text", "body": "¡Hola! Usa este chat para hablar conmigo — sin cifrado, puedo responderte."},
                            )
                        except Exception as e:
                            logger.error("Failed to send welcome to new room: %s", e)
                return
            await asyncio.sleep(2)
        logger.error(f"Failed to join {room.room_id} after retries")

    async def _heartbeat_loop(self, client):
        """Write bot status to Redis every 15 s so the dashboard can read it."""
        import json as _json
        while self._running:
            try:
                rooms = []
                for room_id, room in client.rooms.items():
                    members = list(getattr(room, "users", {}).keys())
                    rooms.append({
                        "room_id": room_id,
                        "encrypted": getattr(room, "encrypted", False),
                        "member_count": getattr(room, "member_count", len(members)),
                        "display_name": getattr(room, "display_name", None) or getattr(room, "name", None),
                        "members": members,
                    })
                status = {
                    "connected": self._ready.is_set(),
                    "user_id": self.app.config.get("MATRIX_USER_ID", ""),
                    "homeserver": self.app.config.get("MATRIX_HOMESERVER", ""),
                    "rooms": rooms,
                    "last_seen": __import__("datetime").datetime.utcnow().isoformat() + "Z",
                }
                import redis as _redis
                r = _redis.Redis.from_url(
                    self.app.config.get("REDIS_URL", "redis://localhost:6379/0"),
                    socket_timeout=0.5, decode_responses=True,
                )
                r.set("autobot:matrix:status", _json.dumps(status), ex=60)
            except asyncio.CancelledError:
                return
            except Exception:
                pass
            await asyncio.sleep(15)

    async def _redirect_encrypted_dms(self, client):
        """On startup, find all encrypted 2-person rooms and create unencrypted replacements.

        Sends a redirect message into the encrypted room and a welcome into the
        new one so the user knows where to go even if they can't send messages
        in the encrypted room.
        """
        await asyncio.sleep(2)  # let the sync settle
        for room_id, room in list(client.rooms.items()):
            if not getattr(room, "encrypted", False):
                continue
            members = getattr(room, "users", {}) or {}
            if len(members) != 2:
                continue
            # Find the other member (not the bot)
            other = next((uid for uid in members if uid != client.user_id), None)
            if other is None:
                continue
            logger.info("Startup: encrypted DM room %s with %s — creating unencrypted redirect", room_id, other)
            new_room_id = await self._create_dm_room(client, other)
            if not new_room_id or new_room_id == room_id:
                continue
            try:
                await client.room_send(
                    room_id=room_id,
                    message_type="m.room.message",
                    content={"msgtype": "m.text", "body": (
                        "Este chat tiene cifrado E2E activado y no puedo recibir tus mensajes.\n\n"
                        f"He creado un nuevo chat sin cifrado. Acepta la invitación o únete aquí:\n"
                        f"https://matrix.to/#/{new_room_id}"
                    )},
                )
            except Exception as e:
                logger.error("Failed to send redirect to encrypted room %s: %s", room_id, e)
            try:
                await client.room_send(
                    room_id=new_room_id,
                    message_type="m.room.message",
                    content={"msgtype": "m.text", "body": "¡Hola! Usa este chat para hablar conmigo — sin cifrado, puedo responderte correctamente."},
                )
            except Exception as e:
                logger.error("Failed to send welcome to new room %s: %s", new_room_id, e)

    async def _handle_encrypted(self, client, room, event):
        """Create an unencrypted DM room and redirect the user there."""
        if event.sender == client.user_id:
            return

        logger.warning(
            "Matrix encrypted message from %s in %s — creating unencrypted redirect room.",
            event.sender, room.room_id,
        )

        # Create (or reuse) an unencrypted room for this user.
        new_room_id = await self._create_dm_room(client, event.sender)

        if new_room_id and new_room_id != room.room_id:
            body = (
                "Este room tiene cifrado E2E activado y no puedo descifrar tus mensajes.\n\n"
                f"Te he creado un nuevo chat sin cifrado. Úsalo para hablar conmigo:\n"
                f"https://matrix.to/#/{new_room_id}"
            )
            # Also say hello in the new room so the user knows it's ready.
            try:
                await client.room_send(
                    room_id=new_room_id,
                    message_type="m.room.message",
                    content={"msgtype": "m.text", "body": "¡Hola! Usa este chat para hablar conmigo — aquí no hay cifrado y puedo responderte correctamente."},
                )
            except Exception as e:
                logger.error("Failed to send welcome to new room %s: %s", new_room_id, e)
        else:
            body = (
                "Este room tiene cifrado E2E activado y no puedo descifrar tus mensajes. "
                "Por favor, crea un nuevo chat directo desactivando el cifrado, o usa la Room ID sin cifrado que ya existe."
            )

        try:
            await client.room_send(
                room_id=room.room_id,
                message_type="m.room.message",
                content={"msgtype": "m.text", "body": body},
            )
        except Exception as e:
            logger.error("Failed to send encryption redirect to %s: %s", room.room_id, e)

    async def _handle_audio(self, client, room, event):
        """Handle an incoming Matrix audio or audio-file message."""
        if event.sender == client.user_id:
            return

        logger.debug("Matrix audio event from %s in %s", event.sender, room.room_id)

        with self.app.app_context():
            from app.services.matrix_audio_ingest import handle_audio_event, is_audio_event
            from app.services.matrix_service import get_agent_for_room, is_dm_user_allowed, is_room_allowed, is_user_allowed

            is_dm = room.member_count <= 2
            if is_dm:
                if not is_dm_user_allowed(event.sender):
                    return
            else:
                if not is_room_allowed(room.room_id) or not is_user_allowed(event.sender):
                    return

            agent = get_agent_for_room(room.room_id)
            if agent is None:
                logger.warning("No active agent for audio in room %s", room.room_id)
                return

            logger.info(
                "Matrix audio from %s in %s → agent %s",
                event.sender, room.room_id, agent.slug,
            )

            try:
                result = await handle_audio_event(event, room.room_id, agent, client)
                if result is None:
                    return  # not an audio event after type check
                response_text = result.get("response", "")
                if result.get("error"):
                    response_text = response_text or f"Error al procesar el audio: {result['error']}"
            except Exception as e:
                logger.exception("Audio ingest failed for %s in %s", event.sender, room.room_id)
                response_text = f"No he podido procesar el audio: {e}"

            if response_text:
                try:
                    await client.room_send(
                        room_id=room.room_id,
                        message_type="m.room.message",
                        content={"msgtype": "m.text", "body": response_text},
                    )
                except Exception as e:
                    logger.error("Failed to send audio response: %s", e)

    async def _handle_message(self, client, room, event):
        """Handle an incoming Matrix message."""
        from nio import RoomSendResponse

        # Ignore own messages
        if event.sender == client.user_id:
            return

        logger.debug(
            "Matrix inbound: room=%s sender=%s members=%s body=%.60s",
            room.room_id, event.sender, room.member_count, event.body,
        )

        with self.app.app_context():
            from app.services.matrix_service import (
                get_agent_for_room,
                is_dm_user_allowed,
                is_room_allowed,
                is_user_allowed,
                should_respond,
            )

            is_dm = room.member_count <= 2

            # For DMs the room ID is dynamic and unknown in advance — skip the
            # room allowlist and check only the user allowlist.
            # For group rooms both checks apply.
            if is_dm:
                if not is_dm_user_allowed(event.sender):
                    logger.info(
                        "Matrix DM from %s blocked by DM allowlist (room=%s)",
                        event.sender, room.room_id,
                    )
                    return
            else:
                if not is_room_allowed(room.room_id):
                    logger.info(
                        "Matrix message in room %s blocked by room allowlist (sender=%s)",
                        room.room_id, event.sender,
                    )
                    return
                if not is_user_allowed(event.sender):
                    logger.info(
                        "Matrix message from %s blocked by user allowlist (room=%s)",
                        event.sender, room.room_id,
                    )
                    return

            # Slash commands (approve/reject/pending) bypass the agent runtime.
            from app.services import matrix_command_service

            if matrix_command_service.is_command(event.body):
                reply = matrix_command_service.handle_command(
                    sender=event.sender, body=event.body, is_dm=is_dm,
                )
                if reply:
                    try:
                        await client.room_send(
                            room_id=room.room_id,
                            message_type="m.room.message",
                            content={"msgtype": "m.text", "body": reply},
                        )
                    except Exception as e:
                        logger.error(f"Failed to send Matrix command reply: {e}")
                return

            # Get agent for this room
            agent = get_agent_for_room(room.room_id)
            if agent is None:
                logger.warning(
                    "Matrix message in room %s has no matching active agent — ignoring. "
                    "Set MATRIX_DEFAULT_AGENT_SLUG or configure sync_matrix_room on an agent.",
                    room.room_id,
                )
                return

            # Check group response policy
            if not should_respond(room.member_count, event.body, client.user_id, agent):
                logger.debug(
                    "Matrix message from %s in room %s skipped by group_response_policy=%s",
                    event.sender, room.room_id, agent.group_response_policy,
                )
                return

            logger.info(
                "Matrix message from %s in room %s → agent %s: %.80s",
                event.sender, room.room_id, agent.slug, event.body,
            )

            # Run agent
            from app.services.chat_service import run_agent_non_streaming

            result = run_agent_non_streaming(
                agent_id=agent.id,
                message=event.body,
                channel_type="matrix",
                trigger_type="message",
                external_chat_id=room.room_id,
                external_user_id=event.sender,
            )

            response_text = result.get("response", "")
            if result.get("error"):
                response_text = response_text or f"Error: {result['error']}"

            if response_text:
                try:
                    await client.room_send(
                        room_id=room.room_id,
                        message_type="m.room.message",
                        content={"msgtype": "m.text", "body": response_text},
                    )
                except Exception as e:
                    logger.error(f"Failed to send Matrix response: {e}")

            # Sync to web session if this room is configured for it
            _sync_to_web_session(self.app, agent, room.room_id, event.body, response_text)

    def send_dm(self, user_id: str, body: str, timeout: float = 30.0) -> dict:
        """Send a DM to ``user_id``. Safe to call from any thread.

        Finds an existing 1:1 room with ``user_id`` or creates a new one, then
        sends ``body`` as a plain text message. Returns ``{"ok": bool, ...}``.
        The caller (agent tool handler) is synchronous, so we marshal the
        coroutine onto the bot's own event loop via ``run_coroutine_threadsafe``.
        """
        if not self._running or self._loop is None:
            return {"ok": False, "error": "Matrix bot not running (check MATRIX_* env vars)"}
        # Wait briefly for login if the bot just started.
        if not self._ready.wait(timeout=10):
            return {"ok": False, "error": "Matrix bot not logged in yet"}

        future = asyncio.run_coroutine_threadsafe(
            self._send_dm_async(user_id, body), self._loop,
        )
        try:
            return future.result(timeout=timeout)
        except Exception as e:
            logger.exception("send_dm failed")
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    async def _send_dm_async(self, user_id: str, body: str) -> dict:
        """Resolve a DM room with ``user_id`` and post ``body`` into it."""
        client = self._client
        if client is None:
            return {"ok": False, "error": "Matrix client not initialised"}

        room_id = self._find_dm_room(client, user_id)
        if room_id is None:
            room_id = await self._create_dm_room(client, user_id)
            if room_id is None:
                return {"ok": False, "error": f"could not create DM room with {user_id}"}

        from nio import RoomSendResponse

        resp = await client.room_send(
            room_id=room_id,
            message_type="m.room.message",
            content={"msgtype": "m.text", "body": body},
        )
        if isinstance(resp, RoomSendResponse):
            return {"ok": True, "room_id": room_id, "event_id": resp.event_id}
        return {"ok": False, "error": f"room_send failed: {resp}"}

    def send_to_room(self, room_id: str, body: str, timeout: float = 30.0) -> dict:
        """Send *body* to an existing Matrix room by its room_id.

        Unlike ``send_dm`` this does not create any room — the caller is
        responsible for supplying a valid ``room_id``. Safe to call from any
        thread. Returns ``{"ok": bool, ...}``.
        """
        if not self._running or self._loop is None:
            return {"ok": False, "error": "Matrix bot not running"}
        if not self._ready.wait(timeout=10):
            return {"ok": False, "error": "Matrix bot not logged in yet"}

        future = asyncio.run_coroutine_threadsafe(
            self._send_to_room_async(room_id, body), self._loop,
        )
        try:
            return future.result(timeout=timeout)
        except Exception as e:
            logger.exception("send_to_room failed")
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    async def _send_to_room_async(self, room_id: str, body: str) -> dict:
        client = self._client
        if client is None:
            return {"ok": False, "error": "Matrix client not initialised"}
        from nio import RoomSendResponse
        resp = await client.room_send(
            room_id=room_id,
            message_type="m.room.message",
            content={"msgtype": "m.text", "body": body},
        )
        if isinstance(resp, RoomSendResponse):
            return {"ok": True, "room_id": room_id, "event_id": resp.event_id}
        return {"ok": False, "error": f"room_send failed: {resp}"}

    def _find_dm_room(self, client, user_id: str) -> str | None:
        """Return an existing unencrypted DM room with ``user_id``.

        Checks Redis first (survives restarts), then scans in-memory rooms.
        Encrypted rooms are deliberately skipped — the bot cannot use them.
        """
        target = user_id.strip()

        # 1. Redis cache — verify the cached room is still valid and unencrypted
        cached = self._redis_get_dm_room(target)
        if cached and cached in client.rooms:
            cached_room = client.rooms[cached]
            if not getattr(cached_room, "encrypted", False):
                return cached
            # Cached room is encrypted — clear and fall through to scan
            logger.info("Cached DM room %s for %s is encrypted, discarding.", cached, target)
            self._redis_set_dm_room(target, "")  # clear

        # 2. In-memory scan — skip encrypted rooms
        for room_id, room in client.rooms.items():
            members = getattr(room, "users", {}) or {}
            if len(members) == 2 and target in members:
                if getattr(room, "encrypted", False):
                    continue  # skip encrypted rooms
                self._redis_set_dm_room(target, room_id)
                return room_id
        return None

    def _redis_get_sync_token(self) -> str | None:
        try:
            import redis as _redis
            r = _redis.Redis.from_url(
                self.app.config.get("REDIS_URL", "redis://localhost:6379/0"),
                socket_timeout=0.5, decode_responses=True,
            )
            return r.get("autobot:matrix:next_batch")
        except Exception:
            return None

    def _redis_set_sync_token(self, token: str) -> None:
        try:
            import redis as _redis
            r = _redis.Redis.from_url(
                self.app.config.get("REDIS_URL", "redis://localhost:6379/0"),
                socket_timeout=0.5, decode_responses=True,
            )
            r.set("autobot:matrix:next_batch", token)
        except Exception:
            pass

    def _redis_get_dm_room(self, user_id: str) -> str | None:
        try:
            import redis as _redis
            r = _redis.Redis.from_url(
                self.app.config.get("REDIS_URL", "redis://localhost:6379/0"),
                socket_timeout=0.5, decode_responses=True,
            )
            return r.get(f"autobot:matrix:dm:{user_id}")
        except Exception:
            return None

    def _redis_set_dm_room(self, user_id: str, room_id: str) -> None:
        try:
            import redis as _redis
            r = _redis.Redis.from_url(
                self.app.config.get("REDIS_URL", "redis://localhost:6379/0"),
                socket_timeout=0.5, decode_responses=True,
            )
            r.set(f"autobot:matrix:dm:{user_id}", room_id)
        except Exception:
            pass

    async def _create_dm_room(self, client, user_id: str) -> str | None:
        from nio import RoomCreateResponse, RoomPreset

        # Use private_chat preset instead of trusted_private_chat.
        # trusted_private_chat auto-enables E2E on most clients; private_chat
        # creates an invite-only room without it.
        resp = await client.room_create(
            is_direct=True,
            invite=[user_id],
            preset=RoomPreset.private_chat,
        )
        if isinstance(resp, RoomCreateResponse):
            self._redis_set_dm_room(user_id, resp.room_id)
            logger.info("Created unencrypted DM room %s for %s", resp.room_id, user_id)
            return resp.room_id
        logger.error(f"Failed to create DM room with {user_id}: {resp}")
        return None


def _sync_to_web_session(app, agent, matrix_room_id: str, user_msg: str, assistant_msg: str) -> None:
    """Append a Matrix exchange to today's web session if ``agent.sync_matrix_room`` matches.

    Runs inside the Matrix bot's async event loop thread — uses an app context
    to reach the DB. Non-fatal: any error is logged and swallowed.
    """
    sync_room = getattr(agent, "sync_matrix_room", None)
    if not sync_room or sync_room != matrix_room_id:
        return
    try:
        with app.app_context():
            from datetime import datetime, time, timezone
            from app.extensions import db
            from app.models.session import Session
            from app.services.session_service import add_message, get_or_create_session

            start_of_day = datetime.combine(
                datetime.now(timezone.utc).date(), time.min, tzinfo=timezone.utc
            )
            # Find or create today's web session for this agent.
            session = (
                Session.query.filter_by(agent_id=agent.id, channel_type="web")
                .filter(Session.updated_at >= start_of_day)
                .order_by(Session.updated_at.desc())
                .first()
            )
            if session is None:
                session = get_or_create_session(agent.id, channel_type="web")

            add_message(session.id, role="user",
                        content=f"[Matrix] {user_msg}",
                        metadata={"source": "matrix", "room_id": matrix_room_id})
            if assistant_msg:
                add_message(session.id, role="assistant", content=assistant_msg)
    except Exception:
        logger.exception(
            "Matrix->web sync failed for agent=%s room=%s", agent.id, matrix_room_id
        )
