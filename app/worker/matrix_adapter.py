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

        # Login
        retry_delay = 1
        while self._running:
            try:
                response = await client.login(password)
                if isinstance(response, LoginResponse):
                    logger.info(f"Matrix logged in as {user_id}")
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
