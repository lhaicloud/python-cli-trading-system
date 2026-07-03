from __future__ import annotations

import os

from app.live.live_notifications import live_notify_error


def main() -> None:
    result = os.environ.get("SERVICE_RESULT", "unknown")
    if result != "success":
        live_notify_error(
            "lqmtf-live.service stopped unexpectedly",
            f"result={result} exit_code={os.environ.get('EXIT_CODE', '?')} "
            f"exit_status={os.environ.get('EXIT_STATUS', '?')} — "
            f"systemd will retry (Restart=always, 60s)",
        )


if __name__ == "__main__":
    main()
