from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import win32com.client


ALLOWED_TABLES = {"T001", "T001B", "MARV", "TABA"}


def _wait_until_idle(session: Any, timeout_seconds: float = 60.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while bool(session.Busy):
        if time.monotonic() >= deadline:
            raise TimeoutError("SAP GUI session did not become idle before timeout")
        time.sleep(0.2)


def _single_authenticated_session(expected_client: str) -> Any:
    sap_gui = win32com.client.GetObject("SAPGUI")
    application = sap_gui.GetScriptingEngine
    if application.Children.Count != 1:
        raise RuntimeError(
            f"expected exactly one SAP connection, found {application.Children.Count}"
        )
    connection = application.Children(0)
    if connection.Children.Count != 1:
        raise RuntimeError(
            f"expected exactly one SAP session, found {connection.Children.Count}"
        )
    session = connection.Children(0)
    _wait_until_idle(session)
    if str(session.Info.Client) != expected_client:
        raise RuntimeError(
            f"SAP client mismatch: expected {expected_client}, got {session.Info.Client}"
        )
    if not str(session.Info.User).strip():
        raise RuntimeError("SAP session is not authenticated")
    if session.Children.Count != 1:
        raise RuntimeError(
            "SAP session contains a modal or secondary window; refusing to continue"
        )
    return session


def _column_order(grid: Any) -> list[str]:
    raw = grid.ColumnOrder
    try:
        columns = [str(value) for value in raw]
    except TypeError:
        columns = [str(raw.ElementAt(index)) for index in range(raw.Count)]
    if not columns:
        raise RuntimeError("SE16N ALV returned no columns")
    return columns


def _display_title(grid: Any, column: str) -> str:
    for method_name in ("GetDisplayedColumnTitle", "GetColumnTitle"):
        try:
            value = getattr(grid, method_name)(column)
            if str(value).strip():
                return str(value).strip()
        except Exception:
            continue
    return column


def export_grid(
    table: str,
    max_hits: int,
    output: Path,
    expected_client: str,
) -> dict[str, Any]:
    table = table.upper()
    if table not in ALLOWED_TABLES:
        raise ValueError(f"table is not allowed: {table}")
    if output.exists():
        raise FileExistsError(f"refusing to overwrite existing output: {output}")

    session = _single_authenticated_session(expected_client)
    system_name = str(session.Info.SystemName)

    session.findById("wnd[0]/tbar[0]/okcd").text = "/nse16n"
    session.findById("wnd[0]").sendVKey(0)
    _wait_until_idle(session)
    session.findById("wnd[0]/usr/ctxtGD-TAB").text = table
    session.findById("wnd[0]/usr/ctxtGD-TAB").caretPosition = len(table)
    session.findById("wnd[0]").sendVKey(0)
    _wait_until_idle(session)
    session.findById("wnd[0]/usr/txtGD-MAX_LINES").text = str(max_hits)
    session.findById("wnd[0]/usr/txtGD-MAX_LINES").caretPosition = len(str(max_hits))
    session.findById("wnd[0]").sendVKey(0)
    session.findById("wnd[0]/tbar[1]/btn[8]").press()
    _wait_until_idle(session)

    grid = session.findById("wnd[0]/shellcont/shell")
    columns = _column_order(grid)
    row_count = min(int(grid.RowCount), max_hits)
    rows: list[dict[str, str]] = []
    for row_index in range(row_count):
        rows.append(
            {
                column: str(grid.GetCellValue(row_index, column))
                for column in columns
            }
        )

    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "source_type": "sap-gui-se16n-alv-grid",
        "read_only": True,
        "exported_at": datetime.now().astimezone().isoformat(),
        "sap_system": system_name,
        "sap_client": expected_client,
        "table": table,
        "max_hits": max_hits,
        "row_count": row_count,
        "columns": [
            {"technical_name": column, "display_title": _display_title(grid, column)}
            for column in columns
        ],
        "rows": rows,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(output)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Read a small SE16N ALV result without XXL/WPS")
    parser.add_argument("--table", required=True, choices=sorted(ALLOWED_TABLES))
    parser.add_argument("--max-hits", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--client", default="100")
    args = parser.parse_args()
    if not 1 <= args.max_hits <= 5000:
        parser.error("--max-hits must be between 1 and 5000")
    if len(args.client) != 3 or not args.client.isdigit():
        parser.error("--client must be a three-digit SAP client")
    payload = export_grid(args.table, args.max_hits, args.output, args.client)
    print(
        json.dumps(
            {
                "table": payload["table"],
                "row_count": payload["row_count"],
                "sap_system": payload["sap_system"],
                "sap_client": payload["sap_client"],
                "output": str(args.output.resolve()),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
