"""Optional local-LLM phrasing layer (Ollama). OFF unless OLLAMA_URL is set. Never required.

The model only receives the already-retrieved facts and is told to use nothing else. Its output is shown
separately and labelled; the deterministic answer is always the source of truth.
STATUS: written against Ollama's documented /api/generate; NOT exercised against a real model here
(no Ollama installed in the build environment) — covered by a mocked-transport test only.
"""
import json

import httpx

from watchtower import config

SYSTEM = ("You are a shopping research writer. Use ONLY the JSON facts provided. If something is not in the "
          "facts, say it is not available. Never invent prices, specs, stock, or reviews. Be concise (<=120 words).")


def summarise(facts: dict, question: str, client: httpx.Client | None = None) -> str | None:
    if not config.OLLAMA_URL:
        return None
    try:
        c = client or httpx.Client(timeout=30)
        r = c.post(f"{config.OLLAMA_URL.rstrip('/')}/api/generate", json={
            "model": config.OLLAMA_MODEL, "stream": False, "system": SYSTEM,
            "prompt": f"Question: {question}\nFacts: {json.dumps(facts, default=str)}\nWrite the summary."})
        r.raise_for_status()
        text = (r.json().get("response") or "").strip()
        return text or None
    except Exception:  # any failure => silently fall back to deterministic answer
        return None
