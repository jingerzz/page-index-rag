"""LLM client: Ollama-only via native HTTP API with thinking-mode control.

Uses Ollama's native /api/chat endpoint (not OpenAI-compatible) so that the
``think`` parameter is honoured.  Thinking is **disabled by default** because:
  - PageIndex tasks (summarisation, JSON extraction, reasoning search) need
    deterministic, structured output in the *content* field.
  - Qwen 3.5 models with thinking enabled put answers in the thinking trace
    and return **empty** content, breaking all downstream consumers.
  - Disabling thinking gives 5-25x speed improvement with identical quality
    on all benchmarked PageIndex tasks.
"""

import json
import logging
import time
import asyncio
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path

try:
    import aiohttp
    _HAS_AIOHTTP = True
except ImportError:
    _HAS_AIOHTTP = False

logger = logging.getLogger("pageindex-rag")

ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = ROOT / "config.json"


def _load_config():
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except Exception:
            return {}
    return {}


def _get_ollama_base_url():
    cfg = _load_config()
    url = cfg.get("ollama_base_url", "http://localhost:11434/v1")
    # Strip /v1 suffix if present — we call native /api/chat directly
    return url.removesuffix("/v1").removesuffix("/v1/")


def _get_ollama_model():
    cfg = _load_config()
    return cfg.get("ollama_model", "qwen3-coder:30b")


def _get_summary_model():
    """Model for summary generation."""
    cfg = _load_config()
    return cfg.get("summary_model") or _get_ollama_model()


def _get_summary_concurrency():
    """Max concurrent async summary LLM calls. Returns None if not configured (unlimited)."""
    cfg = _load_config()
    val = cfg.get("summary_concurrency")
    return int(val) if val else None


def _get_summary_token_threshold():
    """Token count below which nodes skip LLM summarization. Defaults to 200."""
    cfg = _load_config()
    val = cfg.get("summary_token_threshold")
    return int(val) if val else 200


def _get_thinning_threshold():
    """Min token count for tree thinning. Nodes below this merge into parent. 0 disables."""
    cfg = _load_config()
    val = cfg.get("thinning_threshold")
    return int(val) if val else 0


def _get_max_tokens():
    """Max tokens per completion. 16384 gives headroom for large PDF TOC JSON extraction."""
    cfg = _load_config()
    return int(cfg.get("max_tokens", 16384))


@asynccontextmanager
async def _maybe_semaphore(sem):
    """Async context manager that acquires sem if provided, otherwise is a no-op."""
    if sem is not None:
        async with sem:
            yield
    else:
        yield


# ── Native Ollama HTTP helpers ──────────────────────────────────────────────

def _ollama_chat_sync(model: str, messages: list[dict], max_tokens: int,
                      think: bool = False) -> dict:
    """Synchronous call to Ollama native /api/chat. Returns parsed JSON response."""
    base = _get_ollama_base_url()
    url = f"{base}/api/chat"
    body = {
        "model": model,
        "messages": messages,
        "stream": False,
        "think": think,
        "options": {
            "temperature": 0,
            "num_predict": max_tokens,
        },
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as resp:
        return json.loads(resp.read())


async def _ollama_chat_async(model: str, messages: list[dict], max_tokens: int,
                             think: bool = False) -> dict:
    """Async call to Ollama native /api/chat."""
    base = _get_ollama_base_url()
    url = f"{base}/api/chat"
    body = {
        "model": model,
        "messages": messages,
        "stream": False,
        "think": think,
        "options": {
            "temperature": 0,
            "num_predict": max_tokens,
        },
    }

    if _HAS_AIOHTTP:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=body, timeout=aiohttp.ClientTimeout(total=600)) as resp:
                return await resp.json()
    else:
        # Fallback: run sync call in thread pool
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, _ollama_chat_sync, model, messages, max_tokens, think
        )


# ── Public API (drop-in replacements for ChatGPT_API functions) ─────────────

def llm_call(model=None, prompt="", api_key=None, chat_history=None):
    """Synchronous LLM call. Drop-in replacement for ChatGPT_API."""
    max_retries = 10
    resolved_model = model or _get_ollama_model()
    max_tokens = _get_max_tokens()

    for i in range(max_retries):
        try:
            if chat_history:
                messages = list(chat_history)
                messages.append({"role": "user", "content": prompt})
            else:
                messages = [{"role": "user", "content": prompt}]

            result = _ollama_chat_sync(resolved_model, messages, max_tokens)
            return result.get("message", {}).get("content", "")
        except Exception as e:
            logger.error(f"LLM call error (attempt {i+1}): {e}")
            if i < max_retries - 1:
                time.sleep(1)
            else:
                logger.error(f"Max retries reached for prompt: {prompt[:100]}...")
                return "Error"


def llm_call_with_finish_reason(model=None, prompt="", api_key=None, chat_history=None):
    """Synchronous LLM call returning (content, finish_status).

    Drop-in replacement for ChatGPT_API_with_finish_reason.
    """
    max_retries = 10
    resolved_model = model or _get_ollama_model()
    max_tokens = _get_max_tokens()

    for i in range(max_retries):
        try:
            if chat_history:
                messages = list(chat_history)
                messages.append({"role": "user", "content": prompt})
            else:
                messages = [{"role": "user", "content": prompt}]

            result = _ollama_chat_sync(resolved_model, messages, max_tokens)
            content = result.get("message", {}).get("content", "")

            # Ollama native API uses "stop" for normal completion and
            # "length" when num_predict is hit
            done_reason = result.get("done_reason", "stop")
            if done_reason == "length":
                return content, "max_output_reached"
            else:
                return content, "finished"
        except Exception as e:
            logger.error(f"LLM call error (attempt {i+1}): {e}")
            if i < max_retries - 1:
                time.sleep(1)
            else:
                logger.error(f"Max retries reached for prompt: {prompt[:100]}...")
                return "Error", "error"


async def llm_call_async(model=None, prompt="", api_key=None, semaphore=None):
    """Async LLM call. Drop-in replacement for ChatGPT_API_async.

    semaphore: optional asyncio.Semaphore to limit concurrency. The semaphore
    is held for the entire duration of the call (including retries) so that at
    most N calls are in-flight at once.
    """
    max_retries = 10
    resolved_model = model or _get_ollama_model()
    max_tokens = _get_max_tokens()
    messages = [{"role": "user", "content": prompt}]

    async with _maybe_semaphore(semaphore):
        for i in range(max_retries):
            try:
                result = await _ollama_chat_async(resolved_model, messages, max_tokens)
                return result.get("message", {}).get("content", "")
            except Exception as e:
                logger.error(f"Async LLM call error (attempt {i+1}): {e}")
                if i < max_retries - 1:
                    await asyncio.sleep(1)
                else:
                    logger.error(f"Max retries reached for prompt: {prompt[:100]}...")
                    return "Error"
