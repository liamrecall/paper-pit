"""Optional second opinion from Grok for the DEVIL and VETO seats.

Without XAI_API_KEY the desk runs on its own rules and this module stays asleep.
With a key, DEVIL and VETO get one extra reviewer that can only say "kill" or
"pass" and has to give a reason in one line.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

ENDPOINT = "https://api.x.ai/v1/chat/completions"
MODEL = os.environ.get("XAI_MODEL", "grok-4-latest")

DEVIL_PROMPT = (
    "You are DEVIL on a paper trading desk. Your only job is to argue against the setup. "
    "Reply with JSON: {\"kill\": true|false, \"reason\": \"one short line\"}. "
    "Kill it only if there is a concrete reason in the data you were given."
)
VETO_PROMPT = (
    "You are VETO on a paper trading desk. You can block a ticket, you can never approve one. "
    "Reply with JSON: {\"kill\": true|false, \"reason\": \"one short line\"}. "
    "Block only for risk reasons: exposure, correlation, stop distance, session timing."
)


def available() -> bool:
    return bool(os.environ.get("XAI_API_KEY"))


def reviewer(seat: str):
    """Return a callable(item) -> {'kill': bool, 'reason': str} or None when disabled."""
    if not available():
        return None
    system = DEVIL_PROMPT if seat == "devil" else VETO_PROMPT

    def ask(item: dict) -> dict | None:
        payload = {
            "model": MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(_slim(item))},
            ],
            "temperature": 0,
            "max_tokens": 120,
        }
        req = urllib.request.Request(
            ENDPOINT,
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {os.environ['XAI_API_KEY']}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = json.loads(resp.read().decode())
            text = body["choices"][0]["message"]["content"].strip()
            start, end = text.find("{"), text.rfind("}")
            return json.loads(text[start: end + 1]) if start >= 0 else None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError, IndexError):
            return None  # a dead API call must never move the desk

    return ask


def _slim(item: dict) -> dict:
    keep = ("symbol", "side", "kind", "price", "atr", "volume_ratio", "trend_up",
            "funding", "funding_tag", "qty", "notional", "stop", "target")
    return {k: item[k] for k in keep if k in item}
