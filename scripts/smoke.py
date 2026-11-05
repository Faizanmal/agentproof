"""HTTP-only checks against the local application. Does not launch any browser."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expect-not-ready", action="store_true")
    parser.add_argument("--record-evidence", action="store_true")
    parser.add_argument("--api-port", type=int, default=8000)
    parser.add_argument("--web-port", type=int, default=3000)
    args = parser.parse_args()
    if not all(1 <= port <= 65535 for port in (args.api_port, args.web_port)):
        parser.error("Ports must be between 1 and 65535")
    # Destinations remain loopback-only, including when alternate local ports are selected.
    with httpx.Client(
        base_url=f"http://127.0.0.1:{args.api_port}", timeout=8, trust_env=False
    ) as client:
        live = client.get("/api/v1/health/live")
        assert live.status_code == 200 and live.json() == {"status": "alive"}
        assert live.headers.get("x-request-id")
        ready = client.get("/api/v1/health/ready")
        if args.expect_not_ready:
            assert ready.status_code == 503, ready.text
            assert "down" in ready.json()["dependencies"].values()
        else:
            assert ready.status_code == 200, ready.text
            assert ready.json()["dependencies"] == {"postgres": "up", "redis": "up"}
        assert client.get("/api/v1/version").json()["api_version"] == "v1"
        assert (
            client.get("/api/v1/missing")
            .headers["content-type"]
            .startswith("application/problem+json")
        )
    with httpx.Client(
        base_url=f"http://127.0.0.1:{args.web_port}", timeout=30, trust_env=False
    ) as client:
        for path, text in (
            ("/", "AgentProof"),
            ("/auth", "not connected"),
            ("/projects", "Project workspace"),
        ):
            response = client.get(path)
            assert response.status_code == 200 and text in response.text, path
        assert client.get("/does-not-exist").status_code == 404
        response = client.get("/api/v1/health/live")
        assert response.status_code == 200 and response.json()["status"] == "alive"
        assert urlparse(str(response.url)).hostname == "127.0.0.1"
    if args.record_evidence:
        evidence = {
            "captured_at": datetime.now(UTC).isoformat(),
            "api_origin": f"http://127.0.0.1:{args.api_port}",
            "web_origin": f"http://127.0.0.1:{args.web_port}",
            "liveness": {
                "status_code": live.status_code,
                "body": live.json(),
                "request_id": live.headers["x-request-id"],
            },
            "readiness": {
                "status_code": ready.status_code,
                "body": ready.json(),
                "request_id": ready.headers["x-request-id"],
            },
            "web_paths_verified": ["/", "/auth", "/projects", "/does-not-exist"],
            "proxy_liveness": {"status_code": response.status_code, "body": response.json()},
            "verification": "HTTP assertions only; no browser execution",
        }
        folder = Path(__file__).resolve().parents[1] / "docs/evidence/phase01"
        folder.mkdir(parents=True, exist_ok=True)
        filename = "runtime-not-ready.json" if args.expect_not_ready else "runtime.json"
        (folder / filename).write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(
        "PASS: local HTTP routes, same-origin proxy, request IDs, errors and dependency readiness"
    )


if __name__ == "__main__":
    main()
