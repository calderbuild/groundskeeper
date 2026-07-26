"""Serves the verification console and streams a run over SSE.

Standard library only. A judge running this should not have to install a web
framework to watch the thing work.
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .catalog_loader import find_datasets, load_schemas, mcp_session
from .events import Event
from .gates.compilation import CompilationGate
from .gates.field_existence import FieldExistenceGate
from .gates.governance import GovernanceGate
from .generator import LLMGenerator
from .pipeline import VerifiedGenerator
from .warehouse import build_warehouse
from .writeback import emit, plan_writeback

CONSOLE = Path(__file__).resolve().parents[2] / "console" / "index.html"
DEFAULT_TABLES = ["orders", "order_items", "customers", "products", "regions"]

_SENTINEL = object()


async def _load_catalog(tables: list[str]):
    async with mcp_session() as session:
        available = await find_datasets(session)
        name_to_urn = {t: available[t] for t in tables if t in available}
        context = await load_schemas(session, list(name_to_urn.values()))
    return context, name_to_urn


def _run_pipeline(
    task: str, grounded: bool, write_back: bool, out: queue.Queue
) -> None:
    """Execute a full run, pushing every event onto the queue as it happens."""
    try:
        context, name_to_urn = asyncio.run(_load_catalog(DEFAULT_TABLES))
        out.put(
            Event(
                "catalog",
                {
                    "tables": [
                        {"name": t, "columns": len(context.schema_for(u) or ())}
                        for t, u in name_to_urn.items()
                    ]
                },
            )
        )

        warehouse = build_warehouse(context, {u: t for t, u in name_to_urn.items()})
        gates = [FieldExistenceGate(), GovernanceGate(), CompilationGate(warehouse)]
        generator = LLMGenerator(
            context, name_to_urn, output_name="mart_generated", grounded=grounded
        )
        runner = VerifiedGenerator(generator, gates, context, reporter=out.put)
        report = runner.run(task, name_to_urn)

        if write_back and report.shipped and report.final:
            plan = plan_writeback(report.final.artifact, context, len(report.attempts))
            emit(plan, os.environ.get("DATAHUB_GMS_URL", "http://localhost:8080"))
            out.put(
                Event(
                    "written_back",
                    {
                        "urn": plan.urn,
                        "upstreams": len(plan.upstreams),
                        "tags": plan.tags,
                    },
                )
            )
    except Exception as e:
        out.put(
            Event(
                "error",
                {
                    "message": f"{type(e).__name__}: {e}",
                    "trace": traceback.format_exc()[-600:],
                },
            )
        )
    finally:
        out.put(_SENTINEL)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep the console output clean
        pass

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._serve_console()
        elif parsed.path == "/api/run":
            self._stream_run(parse_qs(parsed.query))
        else:
            self.send_error(404)

    def _serve_console(self) -> None:
        try:
            body = CONSOLE.read_bytes()
        except FileNotFoundError:
            self.send_error(500, "console/index.html is missing")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _stream_run(self, params: dict) -> None:
        task = (params.get("task") or [""])[0].strip()
        if not task:
            self.send_error(400, "task is required")
            return
        grounded = (params.get("grounded") or ["1"])[0] != "0"
        write_back = (params.get("write_back") or ["0"])[0] == "1"

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        events: queue.Queue = queue.Queue()
        worker = threading.Thread(
            target=_run_pipeline, args=(task, grounded, write_back, events), daemon=True
        )
        worker.start()

        while True:
            item = events.get()
            if item is _SENTINEL:
                break
            try:
                self.wfile.write(item.to_sse().encode())
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return  # the browser navigated away
        try:
            self.wfile.write(
                b"data: " + json.dumps({"kind": "done"}).encode() + b"\n\n"
            )
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass


def serve(port: int = 8765) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Groundskeeper console on http://127.0.0.1:{port}")
    server.serve_forever()
