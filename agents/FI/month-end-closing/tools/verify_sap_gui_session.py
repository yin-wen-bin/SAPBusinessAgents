"""Verify that exactly one idle authenticated SAP GUI session targets a client."""

from __future__ import annotations

import argparse
import sys

import win32com.client


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--client", required=True)
    args = parser.parse_args()

    try:
        application = win32com.client.GetObject("SAPGUI").GetScriptingEngine
    except Exception as exc:
        print(f"SAP GUI Scripting is unavailable: {type(exc).__name__}.", file=sys.stderr)
        return 1
    matches = []
    total = 0
    for connection_index in range(application.Children.Count):
        connection = application.Children(connection_index)
        for session_index in range(connection.Children.Count):
            total += 1
            session = connection.Children(session_index)
            try:
                info = session.Info
                is_match = (
                    str(info.Client) == args.client
                    and bool(info.User)
                    and not bool(session.Busy)
                    and session.Children.Count == 1
                )
            except Exception:
                print(
                    "SAP GUI session metadata is temporarily unavailable; "
                    "resolve any modal or external Save As dialog before export.",
                    file=sys.stderr,
                )
                return 1
            if is_match:
                matches.append(session)

    if len(matches) != 1 or total != 1:
        print(
            f"Expected exactly one idle authenticated SAP GUI session for client "
            f"{args.client}; matching={len(matches)}, total={total}.",
            file=sys.stderr,
        )
        return 1
    info = matches[0].Info
    print(
        f"Verified one idle authenticated SAP GUI session: "
        f"system={info.SystemName}, client={info.Client}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
