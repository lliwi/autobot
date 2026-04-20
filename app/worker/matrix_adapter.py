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
        from nio import AsyncClient, InviteMemberEvent, LoginResponse, RoomMessageText

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

        # Initial sync to skip old messages
        await client.sync(timeout=10000, full_state=True)

        # Sync loop
        retry_delay = 1
        while self._running:
            try:
                await client.sync(timeout=30000)
                retry_delay = 1  # Reset on success
            except Exception as e:
                logger.error(f"Matrix sync error: {e}")
                await asyncio.sleep(min(retry_delay, 60))
                retry_delay *= 2

        await client.close()

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
                return
            await asyncio.sleep(2)
        logger.error(f"Failed to join {room.room_id} after retries")

    async def _handle_message(self, client, room, event):
        """Handle an incoming Matrix message."""
        from nio import RoomSendResponse

        # Ignore own messages
        if event.sender == client.user_id:
            return

        with self.app.app_context():
            from app.services.matrix_service import (
                get_agent_for_room,
                is_dm_user_allowed,
                is_room_allowed,
                is_user_allowed,
                should_respond,
            )

            is_dm = room.member_count <= 2

            # Check allowlists
            if not is_room_allowed(room.room_id):
                return
            if is_dm:
                if not is_dm_user_allowed(event.sender):
                    return
            elif not is_user_allowed(event.sender):
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
                logger.warning(f"No active agent for room {room.room_id}")
                return

            # Check group response policy
            if not should_respond(room.member_count, event.body, client.user_id, agent):
                return

            logger.info(f"Matrix message from {event.sender} in {room.room_id}: {event.body[:80]}")

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

    def _find_dm_room(self, client, user_id: str) -> str | None:
        """Return an existing 2-person room we share with ``user_id``, or None."""
        target = user_id.strip()
        for room_id, room in client.rooms.items():
            members = getattr(room, "users", {}) or {}
            if len(members) == 2 and target in members:
                return room_id
        return None

    async def _create_dm_room(self, client, user_id: str) -> str | None:
        from nio import RoomCreateResponse

        resp = await client.room_create(
            is_direct=True,
            invite=[user_id],
            preset="trusted_private_chat",
        )
        if isinstance(resp, RoomCreateResponse):
            return resp.room_id
        logger.error(f"Failed to create DM room with {user_id}: {resp}")
        return None
