"""Checks that each AI model TalentLens might use is reachable and replies with valid JSON.

Run it with:  python scripts/check_models.py

It sends one tiny request to each Gemini model (main and fallbacks) and to the local
Ollama model, then prints OK or FAIL, how long it took, whether the reply was valid
JSON, and, on failure, a plain-English reason.

Why: Phase 0 of the plan. We confirm the models work *before* building anything on top.
The API key is never printed.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import requests

# Let this script import from `core/` even though it lives in `scripts/`.
# (Python only looks in the script's own folder by default.)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.config import Settings, get_settings  # noqa: E402  (import after the path fix)
from core.llm import post_with_deadline  # noqa: E402

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_TIMEOUT_SECONDS = 120
OLLAMA_TIMEOUT_SECONDS = 600  # a local CPU can be slow, especially on the first call

TEST_PROMPT = (
    'Reply with only this JSON object and nothing else: {"status": "ok", "number": 42}'
)

# The JSON schema we give Ollama, so it must answer in this shape.
TEST_SCHEMA = {
    "type": "object",
    "properties": {"status": {"type": "string"}, "number": {"type": "integer"}},
    "required": ["status", "number"],
}


@dataclass
class CheckResult:
    """The outcome of checking one model."""

    label: str
    ok: bool
    seconds: float
    valid_json: bool
    message: str


def is_valid_test_reply(reply_text: str) -> bool:
    """True if the reply is JSON with "status": "ok" (what we asked for)."""
    try:
        data = json.loads(reply_text)
    except (json.JSONDecodeError, TypeError):
        return False
    return isinstance(data, dict) and data.get("status") == "ok"


def hide_secret(text: str, secret: str) -> str:
    """Replace the secret with *** in case an error message ever repeats it."""
    if secret:
        return text.replace(secret, "***")
    return text


def explain_gemini_error(status_code: int, server_message: str) -> str:
    """Turn a Gemini HTTP error code into a plain-English explanation."""
    explanations = {
        400: "Gemini rejected the request (400). If it mentions JSON, the way JSON mode is requested needs changing.",
        401: "The API key was refused (401). Check GEMINI_API_KEY in .env.",
        403: "The API key isn't allowed to use this model (403). Check the key in AI Studio.",
        404: "Model not found (404). The model name is probably wrong or not available to your key.",
        429: "Rate limit or quota reached (429). Wait a minute and try again.",
        500: "Gemini had an internal error (500). Try again in a moment.",
        503: "Gemini is overloaded right now (503). Try again in a moment.",
    }
    explanation = explanations.get(status_code, f"Gemini returned HTTP {status_code}.")
    if server_message:
        explanation += f" Server said: {server_message}"
    return explanation


def read_gemini_text(response_json: dict) -> str:
    """Pull the answer text out of a Gemini reply, skipping any 'thinking' parts."""
    candidates = response_json.get("candidates", [])
    if not candidates:
        return ""
    parts = candidates[0].get("content", {}).get("parts", [])
    texts: list[str] = []
    for part in parts:
        if part.get("thought"):
            continue  # internal reasoning, not the answer
        texts.append(part.get("text", ""))
    return "".join(texts)


def check_gemini_model(model: str, api_key: str) -> CheckResult:
    """Send the test prompt to one Gemini model in JSON response mode."""
    label = f"Gemini {model}"
    if not api_key:
        return CheckResult(label, False, 0.0, False, "No GEMINI_API_KEY in .env.")

    body = {
        "contents": [{"role": "user", "parts": [{"text": TEST_PROMPT}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0},
    }
    # The key goes in a header (not the URL), so it can't end up in logs of URLs.
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}

    started = time.perf_counter()
    try:
        response = post_with_deadline(
            GEMINI_URL.format(model=model), GEMINI_TIMEOUT_SECONDS, headers=headers, json=body
        )
    except requests.Timeout:
        seconds = time.perf_counter() - started
        return CheckResult(label, False, seconds, False, "No reply in time (timeout).")
    except requests.ConnectionError:
        seconds = time.perf_counter() - started
        return CheckResult(label, False, seconds, False, "Couldn't reach Google. Check your internet connection.")
    seconds = time.perf_counter() - started

    if response.status_code != 200:
        server_message = ""
        try:
            server_message = response.json().get("error", {}).get("message", "")
        except ValueError:
            server_message = ""
        message = explain_gemini_error(response.status_code, server_message)
        return CheckResult(label, False, seconds, False, hide_secret(message, api_key))

    reply_text = read_gemini_text(response.json())
    valid = is_valid_test_reply(reply_text)
    message = "Replied with valid JSON." if valid else f"Reply wasn't the expected JSON: {reply_text[:120]!r}"
    return CheckResult(label, valid, seconds, valid, message)


def check_ollama_model(base_url: str, model: str) -> CheckResult:
    """Send the test prompt to the local Ollama model, with the JSON schema as `format`."""
    label = f"Ollama {model}"
    body = {
        "model": model,
        "messages": [{"role": "user", "content": TEST_PROMPT}],
        "format": TEST_SCHEMA,
        "stream": False,
        "options": {"temperature": 0, "num_ctx": 8192},
    }

    started = time.perf_counter()
    try:
        response = post_with_deadline(f"{base_url}/api/chat", OLLAMA_TIMEOUT_SECONDS, json=body)
    except requests.Timeout:
        seconds = time.perf_counter() - started
        return CheckResult(label, False, seconds, False, "Ollama didn't reply in time (timeout).")
    except requests.ConnectionError:
        seconds = time.perf_counter() - started
        return CheckResult(label, False, seconds, False, f"Ollama isn't running at {base_url}. Start the Ollama app and try again.")
    seconds = time.perf_counter() - started

    if response.status_code == 404:
        return CheckResult(label, False, seconds, False, f"Model not downloaded. Run:  ollama pull {model}")
    if response.status_code != 200:
        return CheckResult(label, False, seconds, False, f"Ollama returned HTTP {response.status_code}.")

    reply_text = response.json().get("message", {}).get("content", "")
    valid = is_valid_test_reply(reply_text)
    message = "Replied with valid JSON." if valid else f"Reply wasn't the expected JSON: {reply_text[:120]!r}"
    return CheckResult(label, valid, seconds, valid, message)


def print_result(result: CheckResult) -> None:
    """Print one result as a readable line."""
    status = "OK  " if result.ok else "FAIL"
    json_text = "yes" if result.valid_json else "no"
    print(f"[{status}] {result.label:<28} {result.seconds:6.1f}s  valid JSON: {json_text:<3}  {result.message}")


def run_all_checks(settings: Settings) -> list[CheckResult]:
    """Check every Gemini model in order, then Ollama. Prints as it goes."""
    results: list[CheckResult] = []
    for model in settings.gemini_models_in_order:
        result = check_gemini_model(model, settings.gemini_api_key)
        print_result(result)
        results.append(result)
    ollama_result = check_ollama_model(settings.ollama_url, settings.ollama_model)
    print_result(ollama_result)
    results.append(ollama_result)
    return results


def main() -> int:
    """Run the checks and return 0 if at least one model works, otherwise 1."""
    print("TalentLens model check (one tiny JSON request per model)\n")
    settings = get_settings()
    results = run_all_checks(settings)
    working = sum(1 for result in results if result.ok)
    print(f"\n{working} of {len(results)} models OK.")
    return 0 if working > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
