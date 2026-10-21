"""Explicit TLS loopback-only server for the real-browser auth smoke. MOCK only."""
import argparse
import json
from pathlib import Path
import socket

import uvicorn

from tests.test_cloud_demo import setup_cloud


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--certificate", required=True)
    parser.add_argument("--key", required=True)
    args = parser.parse_args()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        origin = f"https://127.0.0.1:{sock.getsockname()[1]}"
        client, _, _, _ = setup_cloud(origin=origin, static_directory=Path(__file__).resolve().parents[1] / "frontend/dist")
        print(json.dumps({"origin": origin, "mode": "synthetic-test-only"}), flush=True)
        config = uvicorn.Config(client.app, access_log=False, log_level="error", proxy_headers=False,
                                ssl_certfile=args.certificate, ssl_keyfile=args.key)
        uvicorn.Server(config).run(sockets=[sock])


if __name__ == "__main__":
    main()
