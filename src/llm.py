"""LLM client: Ollama-only via OpenAI SDK with configurable base_url."""

import json
import logging
import os
import time
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import openai

logger = logging.getLogger("pageindex-rag")

ROOT = Path(__file__).resolve().parent.parent
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
    return cfg.get("ollama_base_url", "http://localhost:11434/v1")


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

def _get_summary_backend():
    """Backend for summary generation. Returns 'ollama' by default."""
    cfg = _load_config()
    return cfg.get("summary_backend", "ollama")



@asynccontextmanager
async def _maybe_semaphore(sem):
    """Async context manager that acquires sem if provided, otherwise is a no-op."""
    if sem is not None:
        async with sem:
            yield
    else:
        yield


def _resolve_model_and_client():
    """Return (sync_client, model) for Ollama."""
    base = _get_ollama_base_url()
    model = _get_ollama_model()
    client = openai.OpenAI(base_url=base, api_key="ollama")
    return client, model


def _resolve_model_and_client_async():
    """Return (async_client, model) for Ollama."""
    base = _get_ollama_base_url()
    model = _get_ollama_model()
    return openai.AsyncOpenAI(base_url=base, api_key="ollama"), model


def _get_max_tokens():
    """Max tokens per completion. 16384 gives headroom for large PDF TOC JSON extraction."""
    cfg = _load_config()
    return int(cfg.get("max_tokens", 16384))


def llm_call(model=None, prompt="", api_key=None, chat_history=None):
    """Synchronous LLM call. Drop-in replacement for ChatGPT_API."""
    max_retries = 10
    client, resolved_model = _resolve_model_and_client()
    if model is not None:
        resolved_model = model

    for i in range(max_retries):
        try:
            if chat_history:
                messages = list(chat_history)
                messages.append({"role": "user", "content": prompt})
            else:
                messages = [{"role": "user", "content": prompt}]

            response = client.chat.completions.create(
                model=resolved_model,
                messages=messages,
                temperature=0,
                max_tokens=_get_max_tokens(),
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"LLM call error (attempt {i+1}): {e}")
            if i < max_retries - 1:
                time.sleep(1)
            else:
                logger.error(f"Max retries reached for prompt: {prompt[:100]}...")
                return "Error"


def llm_call_with_finish_reason(model=None, prompt="", api_key=None, chat_history=None):
    """Synchronous LLM call returning (content, finish_status). Drop-in replacement for ChatGPT_API_with_finish_reason."""
    max_retries = 10
    client, resolved_model = _resolve_model_and_client()
    if model is not None:
        resolved_model = model

    for i in range(max_retries):
        try:
            if chat_history:
                messages = list(chat_history)
                messages.append({"role": "user", "content": prompt})
            else:
                messages = [{"role": "user", "content": prompt}]

            response = client.chat.completions.create(
                model=resolved_model,
                messages=messages,
                temperature=0,
                max_tokens=_get_max_tokens(),
            )
            finish_reason = response.choices[0].finish_reason
            if finish_reason in ("length", "max_tokens"):
                return response.choices[0].message.content, "max_output_reached"
            else:
                return response.choices[0].message.content, "finished"
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
    async_client, resolved_model = _resolve_model_and_client_async()
    if model is not None:
        resolved_model = model
    messages = [{"role": "user", "content": prompt}]

    async with _maybe_semaphore(semaphore):
        for i in range(max_retries):
            try:
                response = await async_client.chat.completions.create(
                    model=resolved_model,
                    messages=messages,
                    temperature=0,
                    max_tokens=_get_max_tokens(),
                )
                return response.choices[0].message.content
            except Exception as e:
                logger.error(f"Async LLM call error (attempt {i+1}): {e}")
                if i < max_retries - 1:
                    await asyncio.sleep(1)
                else:
                    logger.error(f"Max retries reached for prompt: {prompt[:100]}...")
                    return "Error"
