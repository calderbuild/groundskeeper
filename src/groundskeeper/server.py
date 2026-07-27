"""Serves the verification console and streams a run over SSE.

Standard library only. A judge running this should not have to install a web
framework to watch the thing work.

Every run is also written to disk as it happens, so the console has history and
each run has a permalink worth pasting into a pull request.
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import store
from .catalog_loader import find_datasets, load_schemas, mcp_session
from .events import Event
from .gates.compilation import CompilationGate
from .gates.field_existence import FieldExistenceGate
from .gates.governance import GovernanceGate
from .generator import LLMGenerator
from .pipeline import VerifiedGenerator
from .warehouse import build_warehouse
from .writeback import emit, plan_writeback

ROOT = Path(__file__).resolve().parents[2]
CONSOLE = ROOT / "console" / "index.html"
BENCHMARK = ROOT / "examples" / "benchmark.json"
DEFAULT_TABLES = ["orders", "order_items", "customers", "products", "regions"]

_SENTINEL = object()
_catalog_cache: dict | None = None


async def _load_catalog(tables: list[str]):
    async with mcp_session() as session:
        available = await find_datasets(session)
        name_to_urn = {t: available[t] for t in tables if t in available}
        context = await load_schemas(session, list(name_to_urn.values()))
    return context, name_to_urn


def _absorb(record: store.RunRecord, event: Event) -> None:
    """Fold one streamed event into the durable record.

    The record is built from the same events the browser sees, so a permalink
    can never disagree with what was on screen during the run.
    """
    kind, data = event.kind, event.data
    if kind == "catalog":
        record.catalog = data.get("tables", [])
    elif kind == "attempt_start":
        attempt = record.attempt(data["attempt"])
        attempt.repairing = bool(data.get("repairing"))
    elif kind == "generated":
        record.attempt(data["attempt"]).sql = data.get("sql", "")
    elif kind == "gate":
        record.attempt(data["attempt"]).gates.append(
            store.GateResult(
                gate=data["gate"],
                verdict=data["verdict"],
                findings=data.get("findings", []),
            )
        )
    elif kind == "attempt_end":
        record.attempt(data["attempt"]).verdict = data["verdict"]
    elif kind == "written_back":
        record.writeback = {
            "urn": data.get("urn", ""),
            "upstreams": data.get("upstreams", 0),
            "tags": data.get("tags", []),
        }
    elif kind == "run_end":
        record.verdict = data.get("verdict", "unknown")
        record.shipped = bool(data.get("shipped"))
        record.final_sql = data.get("sql", "") or record.final_sql
    elif kind == "error":
        record.error = data.get("message", "")
        record.verdict = "error"


def _run_pipeline(
    task: str, grounded: bool, write_back: bool, out: queue.Queue
) -> None:
    """Execute a full run, pushing every event onto the queue as it happens."""
    record = store.start(task, grounded, write_back)
    started = time.monotonic()
    out.put(Event("run_id", {"id": record.id}))

    def report(event: Event) -> None:
        _absorb(record, event)
        out.put(event)

    try:
        context, name_to_urn = asyncio.run(_load_catalog(DEFAULT_TABLES))
        report(
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
        runner = VerifiedGenerator(generator, gates, context, reporter=report)
        report_out = runner.run(task, name_to_urn)

        if write_back and report_out.shipped and report_out.final:
            plan = plan_writeback(
                report_out.final.artifact, context, len(report_out.attempts)
            )
            emit(plan, os.environ.get("DATAHUB_GMS_URL", "http://localhost:8080"))
            report(
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
        report(
            Event(
                "error",
                {
                    "message": f"{type(e).__name__}: {e}",
                    "trace": traceback.format_exc()[-600:],
                },
            )
        )
    finally:
        record.duration_ms = int((time.monotonic() - started) * 1000)
        try:
            store.save(record)
        except OSError as e:
            out.put(Event("error", {"message": f"could not save the run: {e}"}))
        out.put(_SENTINEL)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep the console output clean
        pass

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/run":
            self._stream_run(parse_qs(parsed.query))
        elif path == "/api/runs":
            runs = store.recent()
            self._json({"runs": runs, "tally": store.tally(runs)})
        elif path.startswith("/api/runs/"):
            record = store.load(unquote(path[len("/api/runs/") :]))
            if record is None:
                self._json({"error": "no such run"}, code=404)
            else:
                self._json(record)
        elif path == "/api/catalog":
            self._catalog()
        elif path == "/api/benchmark":
            self._benchmark()
        elif path in ("/", "/benchmark") or path.startswith("/runs/"):
            # One document serves every view; the client routes on the path.
            self._serve_console()
        else:
            self.send_error(404)

    # -- responses ---------------------------------------------------------

    def _json(self, payload: dict, code: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _catalog(self) -> None:
        """What the console can be asked about, before anyone asks.

        Loaded once and cached: it answers a first-time visitor's actual first
        question, and an empty right-hand column answers nothing.
        """
        global _catalog_cache
        if _catalog_cache is None:
            try:
                context, name_to_urn = asyncio.run(_load_catalog(DEFAULT_TABLES))
                _catalog_cache = {
                    "tables": [
                        {"name": t, "columns": len(context.schema_for(u) or ())}
                        for t, u in name_to_urn.items()
                    ]
                }
            except Exception as e:
                # Not cached, so the console recovers as soon as DataHub is up.
                self._json(
                    {
                        "error": "Could not reach DataHub.",
                        "detail": f"{type(e).__name__}: {e}",
                    },
                    code=503,
                )
                return
        self._json(_catalog_cache)

    def _benchmark(self) -> None:
        try:
            payload = json.loads(BENCHMARK.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._json(
                {"error": "examples/benchmark.json is missing or unreadable"}, code=404
            )
            return
        self._json(payload)

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
        # Not keep-alive. BaseHTTPRequestHandler reads "Connection: keep-alive"
        # as an instruction to wait for another request once this response ends,
        # and an event stream has no Content-Length to tell a client the body is
        # over. A browser closes the EventSource itself and never notices, but
        # curl, a script, or CI hangs until it times out. This stream is used
        # once, so it says so.
        self.send_header("Connection", "close")
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
