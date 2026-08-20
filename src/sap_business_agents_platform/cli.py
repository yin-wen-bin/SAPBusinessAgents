from __future__ import annotations

import argparse
import os

import uvicorn


def configure_internal_api_url(host: str, port: int) -> str:
    """Bind Harness callbacks to the exact local API instance being started."""
    callback_host = "127.0.0.1" if host == "localhost" else host
    value = f"http://{callback_host}:{port}"
    os.environ["SAPBA_INTERNAL_API_URL"] = value
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the SAPBusinessAgents local prototype API.")
    parser.add_argument("--host", default="127.0.0.1", choices=["127.0.0.1", "localhost"])
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    configure_internal_api_url(args.host, args.port)
    uvicorn.run(
        "sap_business_agents_platform.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
