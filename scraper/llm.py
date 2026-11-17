"""Minimal JSON-mode LLM client for Google Gemini or OpenAI, using only `requests`.

Provider from environment variables (never from files written by this project):
  - GEMINI_API_KEY  -> Gemini (GEMINI_MODEL, default gemini-2.5-flash)
  - OPENAI_API_KEY  -> OpenAI Chat Completions (OPENAI_MODEL, default gpt-4o-mini)
Without a key `is_available()` is False and callers use the offline selector mode.
Keys are never logged; error messages contain only status codes.
"""

import os

import requests

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
TIMEOUT_SECONDS = 60


class LLMError(RuntimeError):
    """Any failure talking to the LLM; callers fall back to offline mode."""


def provider_name():
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    return None


def is_available():
    return provider_name() is not None


def generate_json(system_prompt, user_prompt):
    """Return the model's reply text, asking the provider for a JSON response."""
    provider = provider_name()
    try:
        if provider == "gemini":
            return _gemini(system_prompt, user_prompt)
        if provider == "openai":
            return _openai(system_prompt, user_prompt)
    except requests.RequestException as exc:
        raise LLMError(f"LLM request failed: {type(exc).__name__}") from exc
    raise LLMError("No LLM API key configured")


def _gemini(system_prompt, user_prompt):
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    body = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
    }
    headers = {"x-goog-api-key": os.environ["GEMINI_API_KEY"], "Content-Type": "application/json"}
    resp = requests.post(GEMINI_URL.format(model=model), json=body, headers=headers, timeout=TIMEOUT_SECONDS)
    if resp.status_code != 200:
        raise LLMError(f"Gemini API error {resp.status_code}")
    try:
        parts = resp.json()["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts).strip()
    except (KeyError, IndexError, ValueError) as exc:
        raise LLMError("Unexpected Gemini response format") from exc


def _openai(system_prompt, user_prompt):
    body = {
        "model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": system_prompt},
                     {"role": "user", "content": user_prompt}],
    }
    headers = {"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"}
    resp = requests.post(OPENAI_URL, json=body, headers=headers, timeout=TIMEOUT_SECONDS)
    if resp.status_code != 200:
        raise LLMError(f"OpenAI API error {resp.status_code}")
    try:
        return resp.json()["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, ValueError) as exc:
        raise LLMError("Unexpected OpenAI response format") from exc
