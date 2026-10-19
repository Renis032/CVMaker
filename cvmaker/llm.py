import base64
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

from . import settings

T = TypeVar("T", bound=BaseModel)

FALLBACK_BETA = "server-side-fallback-2026-07-01"

_client: anthropic.Anthropic | None = None


class LLMError(RuntimeError):
    pass


def ask(system: str, text: str, schema: type[T], effort: str = "high", pdf: bytes | None = None) -> T:
    """One structured Claude call: returns a validated instance of `schema`."""
    if settings.BACKEND == "api":
        return _ask_api(system, text, schema, effort, pdf)
    return _ask_claude_code(system, text, schema, effort, pdf)


def _ask_claude_code(system: str, text: str, schema: type[T], effort: str, pdf: bytes | None) -> T:
    """Runs headless Claude Code, so calls use the Claude subscription it is logged in with."""
    env = {
        **os.environ,
        "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
        "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
    }
    env.pop("ANTHROPIC_API_KEY", None)
    with tempfile.TemporaryDirectory(prefix="cvmaker-") as workdir:
        tools = ""
        if pdf is not None:
            (Path(workdir) / "cv.pdf").write_bytes(pdf)
            text = f"The CV is the file cv.pdf in the current directory - read it with the Read tool.\n\n{text}"
            tools = "Read"
        cmd = [
            settings.CLAUDE_BIN,
            "-p",
            "--output-format", "json",
            "--json-schema", json.dumps(_strict_schema(schema.model_json_schema())),
            "--system-prompt", system,
            "--model", settings.MODEL,
            "--effort", effort,
            "--tools", tools,
            "--no-session-persistence",
        ]
        if tools:
            cmd += ["--allowedTools", tools]
        try:
            result = subprocess.run(
                cmd, input=text, capture_output=True, text=True, timeout=900, cwd=workdir, env=env
            )
        except FileNotFoundError as e:
            raise LLMError(f"Claude Code not found at '{settings.CLAUDE_BIN}' - set CLAUDE_BIN in .env") from e
        except subprocess.TimeoutExpired as e:
            raise LLMError("Claude Code took longer than 15 minutes - try again") from e

    try:
        out = json.loads(result.stdout)
    except json.JSONDecodeError:
        detail = (result.stderr or result.stdout).strip()[-500:]
        raise LLMError(f"Claude Code failed (exit {result.returncode}): {detail or 'no output'}")
    if out.get("is_error") or out.get("subtype") != "success":
        raise LLMError(f"Claude Code error: {out.get('result') or out.get('subtype')}")
    data = out.get("structured_output")
    if data is None:
        raise LLMError("Claude Code returned no structured output")
    try:
        return schema.model_validate(data)
    except ValidationError as e:
        raise LLMError(f"Claude Code returned data in the wrong shape: {e}") from e


def _strict_schema(node):
    """Close every object (no extra keys, all keys required) so the output matches the model exactly."""
    if isinstance(node, dict):
        node = {k: _strict_schema(v) for k, v in node.items()}
        if node.get("type") == "object" and "properties" in node:
            node["additionalProperties"] = False
            node["required"] = list(node["properties"])
        return node
    if isinstance(node, list):
        return [_strict_schema(v) for v in node]
    return node


def _client_api() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def _ask_api(system: str, text: str, schema: type[T], effort: str, pdf: bytes | None) -> T:
    content: list[dict] = [{"type": "text", "text": text}]
    if pdf is not None:
        content.insert(0, {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": base64.standard_b64encode(pdf).decode("ascii"),
            },
        })
    try:
        response = _client_api().beta.messages.parse(
            model=settings.MODEL,
            max_tokens=16000,
            system=system,
            messages=[{"role": "user", "content": content}],
            output_config={"effort": effort},
            output_format=schema,
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
    except (anthropic.AuthenticationError, TypeError) as e:
        if isinstance(e, TypeError) and "authentication" not in str(e):
            raise
        raise LLMError("Anthropic API key missing or invalid - set ANTHROPIC_API_KEY in .env") from e
    except anthropic.RateLimitError as e:
        raise LLMError("Rate limited by the Anthropic API, try again in a minute") from e
    except anthropic.APIStatusError as e:
        raise LLMError(f"Anthropic API error {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise LLMError("Could not reach the Anthropic API") from e

    if response.stop_reason == "refusal":
        raise LLMError("Claude declined this request")
    if response.stop_reason == "max_tokens":
        raise LLMError("Response was cut off (max_tokens) - try again")
    if response.parsed_output is None:
        raise LLMError("Claude returned no structured output")
    return response.parsed_output
