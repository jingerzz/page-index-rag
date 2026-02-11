"""LLM client: Ollama (default, Mistral) or OpenRouter (Gemini). Uses OpenAI SDK with configurable base_url."""

import json
import logging
import os
import time
import asyncio
from pathlib import Path

import openai

logger = logging.getLogger("pageindex-rag")

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"

_config_cache = None

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def _load_config():
    global _config_cache
    if _config_cache is None:
        if CONFIG_PATH.exists():
            _config_cache = json.loads(CONFIG_PATH.read_text())
        else:
            _config_cache = {}
    return _config_cache


def _get_backend():
    """Return 'ollama' or 'openrouter'. Default is ollama (Mistral)."""
    cfg = _load_config()
    return (cfg.get("llm_backend") or "ollama").lower().strip()


def _get_api_key():
    cfg = _load_config()
    return cfg.get("openrouter_api_key") or os.getenv("OPENROUTER_API_KEY") or ""


def _get_model():
    """Model name for the active backend. Ignored when backend is ollama (uses ollama_model)."""
    cfg = _load_config()
    return cfg.get("model", "google/gemini-2.5-flash")


def _get_ollama_base_url():
    cfg = _load_config()
    return cfg.get("ollama_base_url", "http://localhost:11434/v1")


def _get_ollama_model():
    cfg = _load_config()
    return cfg.get("ollama_model", "mistral:7b")


def _resolve_model_and_client():
    """Return (sync_client, model) for the configured backend."""
    backend = _get_backend()
    if backend == "ollama":
        base = _get_ollama_base_url()
        model = _get_ollama_model()
        client = openai.OpenAI(base_url=base, api_key="ollama")
        return client, model
    # openrouter
    api_key = _get_api_key()
    model = _get_model()
    client = openai.OpenAI(api_key=api_key, base_url=OPENROUTER_BASE_URL)
    return client, model


def _resolve_model_and_client_async():
    """Return (async_client, model) for the configured backend."""
    backend = _get_backend()
    if backend == "ollama":
        base = _get_ollama_base_url()
        model = _get_ollama_model()
        return openai.AsyncOpenAI(base_url=base, api_key="ollama"), model
    api_key = _get_api_key()
    model = _get_model()
    return openai.AsyncOpenAI(api_key=api_key, base_url=OPENROUTER_BASE_URL), model


def _get_max_tokens():
    """Max tokens per completion. 16384 gives headroom for large PDF TOC JSON extraction.

    OpenRouter pre-checks affordability against this ceiling but only charges actual
    tokens generated, so a higher default costs nothing extra for short responses
    (summaries, yes/no checks, etc.).
    """
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
            # Normalize: OpenRouter/Gemini may return "length" or "max_tokens"
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


async def llm_call_async(model=None, prompt="", api_key=None):
    """Async LLM call. Drop-in replacement for ChatGPT_API_async."""
    max_retries = 10
    async_client, resolved_model = _resolve_model_and_client_async()
    if model is not None:
        resolved_model = model
    messages = [{"role": "user", "content": prompt}]

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
