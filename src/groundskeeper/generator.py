"""The generator: an LLM that writes dbt SQL, with or without catalog grounding.

Two modes on purpose. `grounded=True` injects the real schemas DataHub holds;
`grounded=False` tells the model only the table names, which is what an agent
without a context platform actually knows. Running the same tasks both ways is
how we measure what the catalog is worth instead of asserting it.

Any OpenAI-compatible endpoint works, so judges can bring their own key.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

from .artifact import Artifact
from .context import CatalogContext

SYSTEM_PROMPT = """You write dbt models as a single SELECT statement.

Rules:
- Output ONLY SQL. No prose, no markdown fences, no CREATE TABLE, no semicolon.
- Reference only tables you were told about, by the exact name given.
- In multi-table queries, qualify every column (e.g. orders.order_id).
- Prefer explicit column lists over SELECT *."""


class GenerationError(RuntimeError):
    pass


def _endpoint() -> tuple[str, str, str]:
    key = os.environ.get("GROUNDSKEEPER_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not key:
        raise GenerationError(
            "No API key. Set GROUNDSKEEPER_API_KEY (any OpenAI-compatible provider)."
        )
    base = (
        os.environ.get("GROUNDSKEEPER_BASE_URL")
        or os.environ.get("OPENAI_BASE_URL")
        or "https://api.deepseek.com/v1"
    ).rstrip("/")
    model = os.environ.get("GROUNDSKEEPER_MODEL") or "deepseek-v4-pro"
    return key, base, model


def call_llm(messages: list[dict], temperature: float = 0.0, timeout: int = 90) -> str:
    key, base, model = _endpoint()
    payload = json.dumps(
        {"model": model, "messages": messages, "temperature": temperature}
    ).encode()
    request = urllib.request.Request(
        f"{base}/chat/completions",
        data=payload,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read())
    except urllib.error.HTTPError as e:
        raise GenerationError(f"LLM HTTP {e.code}: {e.read()[:200]!r}") from e
    except Exception as e:
        raise GenerationError(f"LLM call failed: {type(e).__name__}: {e}") from e

    try:
        return body["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise GenerationError(
            f"Unexpected LLM response shape: {str(body)[:200]}"
        ) from e


def strip_sql(text: str) -> str:
    """Pull SQL out of whatever wrapping the model used."""
    fenced = re.search(r"```(?:sql)?\s*(.+?)```", text, re.S | re.I)
    body = fenced.group(1) if fenced else text
    return body.strip().rstrip(";").strip()


def describe_schema(context: CatalogContext, name_to_urn: dict[str, str]) -> str:
    """The grounding: exact columns and types, straight from the catalog."""
    blocks = []
    for name, urn in name_to_urn.items():
        columns = context.schema_for(urn)
        if not columns:
            continue
        lines = [
            f"  {c} {context.type_of(urn, c) or ''}".rstrip() for c in sorted(columns)
        ]
        tags = {
            c: sorted(context.tags_for_column(urn, c) or ())
            for c in sorted(columns)
            if context.tags_for_column(urn, c)
        }
        block = f"Table {name}:\n" + "\n".join(lines)
        if tags:
            block += "\nGoverned columns: " + ", ".join(
                f"{c} [{', '.join(t)}]" for c, t in tags.items()
            )
        blocks.append(block)
    return "\n\n".join(blocks)


class LLMGenerator:
    """Callable that the pipeline drives: (task, repair_brief) -> Artifact."""

    def __init__(
        self,
        context: CatalogContext,
        name_to_urn: dict[str, str],
        output_name: str = "generated_model",
        grounded: bool = True,
    ):
        self.context = context
        self.name_to_urn = name_to_urn
        self.output_name = output_name
        self.grounded = grounded

    def _user_prompt(self, task: str, brief: str | None) -> str:
        if self.grounded:
            header = (
                "Write a dbt model for this request, using ONLY these tables and "
                "columns (from the data catalog):\n\n"
                + describe_schema(self.context, self.name_to_urn)
            )
        else:
            # What an ungrounded agent knows: the tables exist, nothing more.
            header = (
                "Write a dbt model for this request. Available tables: "
                + ", ".join(self.name_to_urn)
            )
        parts = [header, f"\nRequest: {task}"]
        if brief:
            parts.append("\n" + brief)
        return "\n".join(parts)

    def __call__(self, task: str, brief: str | None = None) -> Artifact:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": self._user_prompt(task, brief)},
        ]
        sql = strip_sql(call_llm(messages))
        return Artifact(sql=sql, output_name=self.output_name, description=task)
