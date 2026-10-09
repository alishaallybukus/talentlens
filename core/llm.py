"""The LLM layer: one way to ask Gemini or Ollama for JSON and get back a checked Pydantic object.

Why this file exists (spec section 10):
- Every agent calls `generate_json(...)`. Nothing else in the app talks to an AI model.
- AI replies can be messy: wrapped in ```code fences```, with extra chatter, or
  with a missing field. This file pulls out the JSON, checks it with Pydantic,
  and if it's wrong, asks the model to fix it (up to 2 "repairs").
- The free Gemini tier has rate limits. This file spaces calls out, waits and
  retries on "too many requests", and falls back to the next Gemini model.
- Answers are cached in the database, so running the same CV again is instant
  and gives exactly the same result.
- Errors are turned into plain-English messages. The API key is never shown.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import asdict, dataclass, field
from typing import Callable, TypeVar

import requests
from pydantic import BaseModel, ValidationError

from core.config import Settings

SchemaType = TypeVar("SchemaType", bound=BaseModel)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_TIMEOUT_SECONDS = 120
OLLAMA_TIMEOUT_SECONDS = 600  # a laptop CPU can be slow
OLLAMA_CONTEXT_SIZE = 8192

MAX_REPAIRS = 2  # how many times we ask the model to fix invalid JSON
MAX_RETRIES = 5  # how many times we retry one model after "busy" or "too many requests"
FIRST_BACKOFF_SECONDS = 2.0  # waits grow 2, 4, 8, 16, 32 seconds
MAX_BACKOFF_SECONDS = 60.0


# ===========================================================================
# Errors
# ===========================================================================


class LLMError(Exception):
    """A problem talking to the AI. The message is written for the recruiter."""


class RetryableError(Exception):
    """The model is busy or rate-limited. Waiting and trying again may work."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ModelUnavailableError(Exception):
    """This model doesn't exist or can't be used. The next fallback model may work."""


# ===========================================================================
# Call metadata (shown in the trace and the run statistics)
# ===========================================================================


@dataclass
class CallMeta:
    """What happened during one generate_json call."""

    task: str
    provider: str
    model: str
    prompt_version: str | None = None
    duration_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cache_hit: bool = False
    repairs: int = 0
    retries: int = 0
    fallback_used: bool = False
    calls: int = 0  # real model calls made (1, plus one per repair)


@dataclass
class RawReply:
    """The text a model sent back, plus token counts."""

    text: str
    tokens_in: int
    tokens_out: int


def post_with_deadline(url: str, deadline_seconds: float, **request_options) -> requests.Response:
    """Send a POST request that gives up after `deadline_seconds` in TOTAL.

    Why: the `timeout` option in `requests` only limits each pause between pieces
    of data. A slow server can keep a call alive far longer (we once saw 49 minutes).
    Running the request in a helper thread lets us stop waiting at the deadline.
    If the deadline passes, requests.Timeout is raised, like a normal timeout.
    """
    request_options.setdefault("timeout", (15, deadline_seconds))  # connect, read
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(requests.post, url, **request_options)
    try:
        return future.result(timeout=deadline_seconds)
    except FutureTimeout as error:
        raise requests.Timeout(f"No complete reply within {deadline_seconds:.0f} seconds.") from error
    finally:
        # Don't wait for a stuck request: let its thread finish (or fail) on its own.
        executor.shutdown(wait=False)


def estimate_tokens(text: str) -> int:
    """A rough token count (characters ÷ 4) when the API doesn't tell us."""
    return max(1, len(text) // 4)


# A function that sends one request to a model and returns its raw reply.
# Tests replace it with a fake, so they never call a real API.
SendFunction = Callable[[str, str, str, str, dict, float], RawReply]


# ===========================================================================
# Talking to Gemini
# ===========================================================================


def hide_secret(text: str, secret: str) -> str:
    """Make sure a secret never appears in an error message."""
    return text.replace(secret, "***") if secret else text


def read_gemini_retry_delay(response: requests.Response) -> float | None:
    """Gemini sometimes says how long to wait ("retryDelay": "17s"). Read it if present."""
    header = response.headers.get("Retry-After")
    if header and header.replace(".", "", 1).isdigit():
        return float(header)
    try:
        details = response.json().get("error", {}).get("details", [])
    except ValueError:
        return None
    for detail in details:
        delay_text = str(detail.get("retryDelay", ""))
        match = re.fullmatch(r"(\d+(?:\.\d+)?)s", delay_text)
        if match:
            return float(match.group(1))
    return None


def read_gemini_error_message(response: requests.Response) -> str:
    try:
        return str(response.json().get("error", {}).get("message", ""))
    except ValueError:
        return ""


DAILY_QUOTA_MESSAGE = (
    "the free daily limit for {model} is used up (it resets at midnight Pacific time, about 11:00 in Mauritius)"
)


def is_daily_quota_error(response: requests.Response) -> bool:
    """True if a 429 means "daily quota used up", not just "too many requests this minute"."""
    try:
        error = response.json().get("error", {})
    except ValueError:
        return False
    for detail in error.get("details", []):
        for violation in detail.get("violations", []):
            if "PerDay" in str(violation.get("quotaId", "")):
                return True
    return False


def read_gemini_text(response_json: dict) -> str:
    """Join the answer parts of a Gemini reply, skipping any "thinking" parts."""
    candidates = response_json.get("candidates", [])
    if not candidates:
        return ""
    parts = candidates[0].get("content", {}).get("parts", [])
    texts = []
    for part in parts:
        if part.get("thought"):
            continue
        texts.append(part.get("text", ""))
    return "".join(texts)


def send_to_gemini(api_key: str, model: str, system: str, prompt: str, temperature: float) -> RawReply:
    """One request to Gemini in JSON response mode. Raises our own error types."""
    if not api_key:
        raise LLMError("No Gemini API key found. Add GEMINI_API_KEY to your .env file.")

    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": temperature},
    }
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    try:
        response = post_with_deadline(
            GEMINI_URL.format(model=model), GEMINI_TIMEOUT_SECONDS, headers=headers, json=body
        )
    except requests.Timeout as error:
        raise RetryableError("Gemini took too long to reply.") from error
    except requests.ConnectionError as error:
        raise RetryableError("Couldn't reach Gemini. Check your internet connection.") from error

    status = response.status_code
    if status == 200:
        data = response.json()
        text = read_gemini_text(data)
        usage = data.get("usageMetadata", {})
        tokens_in = int(usage.get("promptTokenCount") or estimate_tokens(system + prompt))
        tokens_out = int(usage.get("candidatesTokenCount") or estimate_tokens(text))
        return RawReply(text, tokens_in, tokens_out)

    server_message = hide_secret(read_gemini_error_message(response), api_key)
    if status == 429 and is_daily_quota_error(response):
        # Retrying can't help until tomorrow, and each retry would waste a request.
        # Move straight on to the next model, which has its own daily quota.
        raise ModelUnavailableError(DAILY_QUOTA_MESSAGE.format(model=model))
    if status in (429, 500, 503):
        raise RetryableError(f"Gemini is busy or rate-limited ({status}).", read_gemini_retry_delay(response))
    if status == 404:
        raise ModelUnavailableError(f"Gemini model '{model}' wasn't found.")
    if status in (401, 403) or "api key" in server_message.lower():
        raise LLMError("Gemini refused the API key. Check GEMINI_API_KEY in your .env file.")
    raise LLMError(f"Gemini couldn't handle the request ({status}). {server_message}".strip())


# ===========================================================================
# Talking to Ollama
# ===========================================================================


def send_to_ollama(base_url: str, model: str, system: str, prompt: str, schema: dict, temperature: float) -> RawReply:
    """One request to a local Ollama model, with the JSON schema as `format`."""
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        "format": schema,
        "stream": False,
        "options": {"temperature": temperature, "num_ctx": OLLAMA_CONTEXT_SIZE},
    }
    try:
        response = post_with_deadline(f"{base_url}/api/chat", OLLAMA_TIMEOUT_SECONDS, json=body)
    except requests.Timeout as error:
        raise LLMError("Ollama took more than 10 minutes to reply. Try a smaller model or fewer CVs.") from error
    except requests.ConnectionError as error:
        raise LLMError(f"Ollama isn't running at {base_url}. Start the Ollama app and try again.") from error

    if response.status_code == 404:
        raise LLMError(f"The Ollama model '{model}' isn't downloaded. Run:  ollama pull {model}")
    if response.status_code != 200:
        raise LLMError(f"Ollama returned an error ({response.status_code}).")

    data = response.json()
    text = data.get("message", {}).get("content", "")
    tokens_in = int(data.get("prompt_eval_count") or estimate_tokens(system + prompt))
    tokens_out = int(data.get("eval_count") or estimate_tokens(text))
    return RawReply(text, tokens_in, tokens_out)


# ===========================================================================
# Reading JSON out of a reply
# ===========================================================================


def extract_json_text(reply: str) -> str:
    """Find the JSON object inside a reply, even with code fences or chatter around it."""
    text = reply.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end < start:
        raise ValueError("The reply didn't contain a JSON object.")
    return text[start : end + 1]


def parse_reply(reply: str, schema: type[SchemaType]) -> SchemaType:
    """Extract and validate the JSON. Raises ValueError with a short reason if it's wrong."""
    json_text = extract_json_text(reply)
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as error:
        raise ValueError(f"The JSON couldn't be read: {error}") from error
    try:
        return schema.model_validate(data)
    except ValidationError as error:
        # Keep the message short: it is sent back to the model in a repair request.
        raise ValueError(f"The JSON didn't match the schema: {str(error)[:1500]}") from error


def build_repair_prompt(original_prompt: str, bad_reply: str, problem: str) -> str:
    """Ask the model to fix its previous answer."""
    return (
        original_prompt
        + "\n\n<previous_answer>\n"
        + bad_reply[:6000]
        + "\n</previous_answer>\n\n"
        + "Your previous answer was not valid. Problem: "
        + problem
        + "\nReturn only the corrected JSON object that matches the schema. No other text."
    )


def cache_key(provider: str, model: str, schema_name: str, system: str, prompt: str) -> str:
    """A fingerprint of the full request. The same request always gives the same key."""
    joined = "\n<<>>\n".join([provider, model, schema_name, system, prompt])
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


# ===========================================================================
# The client
# ===========================================================================


@dataclass
class LLMClient:
    """Sends prompts to the chosen provider and returns validated Pydantic objects.

    provider: "gemini" or "ollama". model: the model to try first.
    memory: the database (for the cache); None means no cache.
    send_function / sleep_function: replaced by fakes in tests.
    """

    settings: Settings
    provider: str
    model: str
    memory: object | None = None
    use_cache: bool = True
    send_function: SendFunction | None = None
    sleep_function: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    min_seconds_between_calls: float | None = None
    last_call_time: float | None = field(default=None, init=False)

    @classmethod
    def from_settings(cls, settings: Settings, memory: object | None = None, **overrides) -> "LLMClient":
        """Build a client with the provider and model from the settings (.env)."""
        provider = overrides.pop("provider", settings.llm_provider)
        default_model = settings.gemini_model if provider == "gemini" else settings.ollama_model
        model = overrides.pop("model", default_model)
        use_cache = overrides.pop("use_cache", settings.use_cache)
        return cls(settings=settings, provider=provider, model=model, memory=memory, use_cache=use_cache, **overrides)

    # --- model order and spacing -------------------------------------------

    def models_to_try(self) -> list[str]:
        """The chosen model first, then (for Gemini) the fallback models after it."""
        if self.provider != "gemini":
            return [self.model]
        ordered = [self.model]
        for fallback in self.settings.gemini_fallback_models:
            if fallback not in ordered:
                ordered.append(fallback)
        return ordered

    def spacing_seconds(self) -> float:
        if self.min_seconds_between_calls is not None:
            return self.min_seconds_between_calls
        if self.provider == "ollama":
            return 0.0  # a local model has no rate limit
        return self.settings.min_seconds_between_calls

    def wait_for_spacing(self) -> None:
        """Leave at least MIN_SECONDS_BETWEEN_CALLS between real calls (free-tier limits)."""
        if self.last_call_time is None:
            return
        elapsed = self.clock() - self.last_call_time
        remaining = self.spacing_seconds() - elapsed
        if remaining > 0:
            self.sleep_function(remaining)

    # --- sending -----------------------------------------------------------

    def send(self, model: str, system: str, prompt: str, schema: dict, temperature: float) -> RawReply:
        """Send one request, through the fake in tests or the real provider otherwise."""
        if self.send_function is not None:
            return self.send_function(self.provider, model, system, prompt, schema, temperature)
        if self.provider == "gemini":
            return send_to_gemini(self.settings.gemini_api_key, model, system, prompt, temperature)
        if self.provider == "ollama":
            return send_to_ollama(self.settings.ollama_url, model, system, prompt, schema, temperature)
        raise LLMError(f"Unknown AI provider '{self.provider}'. Choose gemini or ollama.")

    def send_with_retries(
        self, model: str, system: str, prompt: str, schema: dict, temperature: float, meta: CallMeta
    ) -> RawReply:
        """Send to one model, waiting and retrying when it's busy (up to MAX_RETRIES times)."""
        for attempt in range(MAX_RETRIES + 1):
            self.wait_for_spacing()
            try:
                return self.send(model, system, prompt, schema, temperature)
            except RetryableError as error:
                if attempt == MAX_RETRIES:
                    raise
                meta.retries += 1
                backoff = min(FIRST_BACKOFF_SECONDS * (2**attempt), MAX_BACKOFF_SECONDS)
                wait = error.retry_after if error.retry_after is not None else backoff
                self.sleep_function(min(wait, MAX_BACKOFF_SECONDS))
            finally:
                self.last_call_time = self.clock()
        raise LLMError("The AI model didn't answer.")  # not reached; keeps type checkers happy

    def send_with_fallback(
        self, models: list[str], system: str, prompt: str, schema: dict, temperature: float, meta: CallMeta
    ) -> tuple[RawReply, list[str]]:
        """Try each model in turn. Returns the reply and the models still worth trying."""
        problems: list[str] = []
        for position, model in enumerate(models):
            try:
                reply = self.send_with_retries(model, system, prompt, schema, temperature, meta)
                meta.model = model
                return reply, models[position:]
            except (RetryableError, ModelUnavailableError) as error:
                problems.append(str(error))
                if position + 1 < len(models):
                    meta.fallback_used = True  # move on to the next model

        if all("internet connection" in problem for problem in problems):
            raise LLMError(
                "Couldn't reach Gemini. Check your internet connection, or switch to Ollama "
                "(saved answers still work offline while the cache is on)."
            )
        if all("daily limit" in problem for problem in problems):
            raise LLMError(
                "Gemini's free daily limit is used up on every model. It resets at midnight "
                "Pacific time (about 11:00 in Mauritius). Switch to Ollama, or try again after that."
            )
        raise LLMError(
            f"The AI service is busy or rate-limited on every model ({problems[-1]}). "
            "Wait about a minute, then try again."
        )

    # --- Test connection (FR-L2) -------------------------------------------

    def test_connection(self) -> tuple[bool, str]:
        """Send one tiny request to the chosen model (no cache, no retries).

        Returns (worked, plain-English message), e.g. (True, "Gemini responded in 1.2 s").
        """
        provider_name = "Gemini" if self.provider == "gemini" else "Ollama"
        schema = {"type": "object", "properties": {"status": {"type": "string"}}, "required": ["status"]}
        started = time.perf_counter()
        try:
            reply = self.send(self.model, "Reply only with JSON.", 'Reply with {"status": "ok"}', schema, 0.0)
        except LLMError as error:
            return False, str(error)
        except ModelUnavailableError as error:
            return False, f"{provider_name}: {error}. Try another model."
        except RetryableError as error:
            return False, f"{error} Wait a minute and try again, or pick another model."
        seconds = time.perf_counter() - started
        try:
            extract_json_text(reply.text)
        except ValueError:
            return False, f"{provider_name} answered in {seconds:.1f} s, but not with JSON. Try another model."
        return True, f"{provider_name} ({self.model}) responded in {seconds:.1f} s"

    # --- the main function -------------------------------------------------

    def generate_json(
        self,
        task: str,
        system: str,
        prompt: str,
        schema: type[SchemaType],
        temperature: float,
        prompt_version: str | None = None,
    ) -> tuple[SchemaType, CallMeta]:
        """Ask the model for JSON matching `schema`. Returns (validated object, CallMeta).

        Order: cache → call (with retries and fallback) → extract and validate JSON →
        up to 2 repairs → save to cache. Raises LLMError with a friendly message.
        """
        started = time.perf_counter()
        meta = CallMeta(task=task, provider=self.provider, model=self.model, prompt_version=prompt_version)
        key = cache_key(self.provider, self.model, schema.__name__, system, prompt)

        cached = self.read_cache(key, schema, meta)
        if cached is not None:
            meta.duration_ms = int((time.perf_counter() - started) * 1000)
            return cached, meta

        json_schema = schema.model_json_schema()
        models = self.models_to_try()
        current_prompt = prompt
        result: SchemaType | None = None
        for repair_round in range(MAX_REPAIRS + 1):
            reply, models = self.send_with_fallback(models, system, current_prompt, json_schema, temperature, meta)
            meta.calls += 1
            meta.tokens_in += reply.tokens_in
            meta.tokens_out += reply.tokens_out
            try:
                result = parse_reply(reply.text, schema)
                break
            except ValueError as problem:
                if repair_round == MAX_REPAIRS:
                    raise LLMError(
                        f"The AI's answer for '{task}' still wasn't in the expected format "
                        f"after {MAX_REPAIRS} repair attempts. Try again, or switch model."
                    ) from problem
                meta.repairs += 1
                current_prompt = build_repair_prompt(prompt, reply.text, str(problem))

        meta.duration_ms = int((time.perf_counter() - started) * 1000)
        self.write_cache(key, task, result, meta)
        return result, meta

    # --- cache -------------------------------------------------------------

    def read_cache(self, key: str, schema: type[SchemaType], meta: CallMeta) -> SchemaType | None:
        if not self.use_cache or self.memory is None:
            return None
        row = self.memory.cache_get(key)
        if row is None:
            return None
        try:
            result = schema.model_validate_json(row["response_json"])
        except ValidationError:
            return None  # an old cached answer that no longer fits: ask again
        saved = json.loads(row.get("meta_json") or "{}")
        meta.cache_hit = True
        meta.model = saved.get("model", meta.model)
        meta.tokens_in = int(saved.get("tokens_in", 0))
        meta.tokens_out = int(saved.get("tokens_out", 0))
        return result

    def write_cache(self, key: str, task: str, result: BaseModel, meta: CallMeta) -> None:
        # The answer is always saved, so switching the cache on later can reuse it.
        if self.memory is None:
            return
        self.memory.cache_put(
            key, task, self.provider, meta.model, result.model_dump_json(), json.dumps(asdict(meta))
        )
