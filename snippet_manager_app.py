"""Standalone entry point: run Snippet Manager as a native window.

Spins up the FastAPI server on a local port in a background thread, opens a
pywebview window pointing at it (no browser, no URL bar), and kills the
server the moment the window closes — no orphaned process left behind.
"""
import socket
import sys
import threading
import time

import uvicorn
import webview

from app import app

# If the server thread dies (bad port, startup exception, etc.) don't hang
# forever waiting for server.started — fail loudly instead of opening a
# window pointed at nothing.
STARTUP_TIMEOUT_SECONDS = 10


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for the server socket to actually accept connections before
    # pointing the window at it.
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            print("Snippet Manager server failed to start; exiting.", file=sys.stderr)
            sys.exit(1)
        time.sleep(0.02)

    window = webview.create_window(
        "Snippet Manager",
        f"http://127.0.0.1:{port}",
        width=1180,
        height=780,
        min_size=(820, 560),
    )

    def _on_closed():
        server.should_exit = True

    window.events.closed += _on_closed

    webview.start()

    # webview.start() blocks until the window is closed; make sure the
    # server thread has actually wound down before the process exits.
    server.should_exit = True
    thread.join(timeout=5)


if __name__ == "__main__":
    main()
