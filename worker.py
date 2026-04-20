"""Worker process: runs the scheduler and Matrix adapter alongside the Flask web service."""

import logging
import signal
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("worker")


def main():
    from app import create_app

    app = create_app()

    # Start scheduler
    from app.worker.scheduler import init_scheduler, shutdown_scheduler

    scheduler = init_scheduler(app)

    # Start Matrix bot
    from app.worker.matrix_adapter import MatrixBot

    matrix_bot = MatrixBot(app)
    matrix_bot.start()
    # Expose the bot on the app so in-process tools (matrix_send) can reuse
    # the already-authenticated client from the scheduler/runtime threads.
    app.matrix_bot = matrix_bot

    # Graceful shutdown
    def handle_signal(signum, frame):
        logger.info("Shutting down worker...")
        matrix_bot.stop()
        shutdown_scheduler()
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    logger.info("Worker running. Press Ctrl+C to stop.")

    # Keep main thread alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        handle_signal(None, None)


if __name__ == "__main__":
    main()
