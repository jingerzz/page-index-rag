"""
A/B test: Mistral 7B (Ollama) vs Gemini 2.5 Flash (OpenRouter) for summarization.

Measures:
  1. Latency (seconds per call) — multiple runs each, report mean ± std.
  2. Quality — side-by-side output of section summary and doc description.

Requires: config.json with openrouter_api_key for Gemini; for Mistral: Ollama running and `ollama pull mistral:7b`.
Run: uv run python scripts/ab_test_mistral_vs_gemini.py
"""

import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

# Project root
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import openai

# Same prompt as pageindex utils.generate_node_summary
NODE_SUMMARY_PROMPT_TEMPLATE = """You are given a part of a document, your task is to generate a description of the partial document about what are main points covered in the partial document.

Partial Document Text: {text}

Directly return the description, do not include any other text.
"""

# Same prompt as pageindex utils.generate_doc_description
DOC_DESCRIPTION_PROMPT_TEMPLATE = """Your are an expert in generating descriptions for a document.
You are given a structure of a document. Your task is to generate a one-sentence description for the document, which makes it easy to distinguish the document from other documents.

Document Structure: {structure}

Directly return the description, do not include any other text.
"""

# Realistic SEC-style section (would trigger summary: >200 tokens)
SAMPLE_SECTION_TEXT = """
Item 1A. Risk Factors

Our business is subject to a number of risks and uncertainties that could adversely affect our financial condition, results of operations, cash flows, and the price of our common stock. The following is a summary of the principal risks that could have such an effect. We operate in a highly competitive global industry. Our competitors include other large, diversified manufacturers as well as companies that specialize in particular product lines or geographic regions. Competition is based on product performance, quality, reliability, availability, price, and customer support. We may not be able to maintain or increase our market share in the face of such competition. Our business is cyclical and is significantly affected by general economic conditions. Demand for our products is dependent on capital spending by our customers, which tends to decline during economic downturns. In addition, our operations are subject to the effects of changes in interest rates, currency exchange rates, commodity prices, and trade policy. We have significant international operations and are subject to political, economic, and other risks associated with doing business in foreign countries, including changes in government policies, tariffs, and trade barriers. Our ability to meet our growth objectives depends in part on our ability to develop and introduce new products and services that meet customer needs. We may not be successful in these efforts. We are also subject to extensive environmental, health, and safety laws and regulations that could require substantial expenditures.
""".strip()

# Minimal structure for doc-description test (titles + one summary)
SAMPLE_STRUCTURE = {
    "title": "Caterpillar Inc. 10-K",
    "node_id": "0001",
    "summary": "Annual report covering business, risk factors, and financials.",
    "nodes": [
        {"title": "Part I - Business", "node_id": "0002", "summary": "Overview of business segments and operations."},
        {"title": "Part II - Risk Factors", "node_id": "0003", "summary": "Principal risks and uncertainties."},
    ],
}


def get_gemini_client():
    import json
    config_path = ROOT / "config.json"
    if not config_path.exists():
        raise SystemExit("config.json not found; need openrouter_api_key for Gemini.")
    cfg = json.loads(config_path.read_text())
    model = cfg.get("model", "google/gemini-2.5-flash")
    api_key = cfg.get("openrouter_api_key") or ""
    if not api_key:
        raise SystemExit("config.json must set openrouter_api_key for Gemini.")
    return openai.OpenAI(api_key=api_key, base_url="https://openrouter.ai/api/v1"), model


def get_ollama_client():
    return openai.OpenAI(base_url="http://localhost:11434/v1", api_key="ollama"), "mistral:7b"


async def call_async(client, model, prompt):
    """Single async chat completion (for node summary)."""
    response = await client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        max_tokens=1024,
    )
    return (response.choices[0].message.content or "").strip()


def call_sync(client, model, prompt):
    """Single sync chat completion (for doc description)."""
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        max_tokens=512,
    )
    return (response.choices[0].message.content or "").strip()


async def run_node_summary_async(client, model, n_runs=3):
    prompt = NODE_SUMMARY_PROMPT_TEMPLATE.format(text=SAMPLE_SECTION_TEXT)
    times = []
    results = []
    for _ in range(n_runs):
        t0 = time.perf_counter()
        out = await call_async(client, model, prompt)
        times.append(time.perf_counter() - t0)
        results.append(out)
    return times, results


def run_doc_description_sync(client, model, n_runs=2):
    prompt = DOC_DESCRIPTION_PROMPT_TEMPLATE.format(structure=json.dumps(SAMPLE_STRUCTURE, indent=2))
    times = []
    results = []
    for _ in range(n_runs):
        t0 = time.perf_counter()
        out = call_sync(client, model, prompt)
        times.append(time.perf_counter() - t0)
        results.append(out)
    return times, results


def report(name, times, results):
    if not times:
        print(f"  {name}: no runs")
        return
    mean_t = statistics.mean(times)
    std_t = statistics.stdev(times) if len(times) > 1 else 0
    print(f"  {name}: {mean_t:.2f}s +/- {std_t:.2f}s  (runs: {len(times)})")
    print(f"  First summary:\n    {results[0][:300]}{'...' if len(results[0]) > 300 else ''}\n")


async def main():
    print("A/B test: Mistral 7B (Ollama) vs Gemini 2.5 Flash (OpenRouter)\n")
    print("Sample: SEC-style 'Item 1A Risk Factors' section (~320 words)\n")

    # Load config once
    config_path = ROOT / "config.json"
    try:
        cfg = json.loads(config_path.read_text()) if config_path.exists() else {}
    except Exception:
        cfg = {}

    # Gemini (OpenRouter)
    gemini_client = gemini_async = gemini_model = None
    if cfg.get("openrouter_api_key"):
        gemini_model = cfg.get("model", "google/gemini-2.5-flash")
        gemini_client = openai.OpenAI(api_key=cfg["openrouter_api_key"], base_url="https://openrouter.ai/api/v1")
        gemini_async = openai.AsyncOpenAI(api_key=cfg["openrouter_api_key"], base_url="https://openrouter.ai/api/v1")
        print(f"Gemini: model={gemini_model}")
    else:
        print("Gemini: skipped (no openrouter_api_key in config.json)")

    # Ollama (Mistral)
    ollama_client = ollama_async = ollama_model = None
    try:
        _oc = openai.OpenAI(base_url="http://localhost:11434/v1", api_key="ollama")
        _oa = openai.AsyncOpenAI(base_url="http://localhost:11434/v1", api_key="ollama")
        call_sync(_oc, "mistral:7b", "Reply with only: OK")
        ollama_client, ollama_async, ollama_model = _oc, _oa, "mistral:7b"
        print(f"Ollama:  model={ollama_model}\n")
    except Exception as e:
        print(f"Ollama:  skipped (is Ollama running? Run: ollama pull mistral:7b). Error: {e}\n")

    if not gemini_client and not ollama_client:
        print("Need at least one backend (Gemini config and/or Ollama).")
        return

    n_runs = 3

    # --- 1) Node summary (async) ---
    print("1) Node summary (section -> short description)")
    print("-" * 60)

    if gemini_async:
        times_g, results_g = await run_node_summary_async(gemini_async, gemini_model, n_runs)
        report("Gemini 2.5 Flash", times_g, results_g)

    if ollama_async:
        times_o, results_o = await run_node_summary_async(ollama_async, ollama_model, n_runs)
        report("Mistral 7B (Ollama)", times_o, results_o)

    # --- 2) Doc description (sync) ---
    print("2) Doc description (structure -> one sentence)")
    print("-" * 60)

    if gemini_client:
        times_g, results_g = run_doc_description_sync(gemini_client, gemini_model, n_runs=2)
        report("Gemini 2.5 Flash", times_g, results_g)

    if ollama_client:
        times_o, results_o = run_doc_description_sync(ollama_client, ollama_model, n_runs=2)
        report("Mistral 7B (Ollama)", times_o, results_o)

    # --- 3) Side-by-side: full first section summary ---
    print("3) Side-by-side: full first section summary (quality comparison)")
    print("-" * 60)
    if gemini_async and ollama_async:
        _, rg = await run_node_summary_async(gemini_async, gemini_model, 1)
        _, ro = await run_node_summary_async(ollama_async, ollama_model, 1)
        print("Gemini 2.5 Flash:\n", rg[0], "\n")
        print("Mistral 7B (Ollama):\n", ro[0])
    elif gemini_async:
        _, rg = await run_node_summary_async(gemini_async, gemini_model, 1)
        print("Gemini 2.5 Flash:\n", rg[0])
    elif ollama_async:
        _, ro = await run_node_summary_async(ollama_async, ollama_model, 1)
        print("Mistral 7B (Ollama):\n", ro[0])


if __name__ == "__main__":
    asyncio.run(main())
