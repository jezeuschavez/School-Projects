"""Optional Claude integration: a short, friendly explanation of a plan.

Claude never produces nutrition numbers here. It receives the already-computed
plan (FoodPro data × chosen quantities) and is told to only reference those values.
If ANTHROPIC_API_KEY isn't set, or the call fails, the app works without it.
"""
from __future__ import annotations

import json
import logging

from . import config

log = logging.getLogger(__name__)

SYSTEM = """You are a friendly, practical nutrition coach for a 19-year-old college student who eats at \
UC Santa Cruz dining halls. He's losing fat (~1.25 lb/week) while strength training for broader \
shoulders, a bigger chest, and a V-taper. You'll receive tomorrow's meal plan as JSON, with totals \
computed from the dining hall's own nutrition data.

Write 2-4 short sentences (under 90 words) that:
- say how the day lines up with the calorie and protein targets,
- point out one or two things that make the day enjoyable or easy to follow,
- give at most one practical tip (e.g. grab the snack at lunch, ask for an extra scoop).

Rules: only mention numbers that appear in the JSON. Never invent foods or nutrition values. \
No moralizing about food, no extreme advice. Plain text, no markdown."""


def summarize_plan(plan: dict, totals: dict, targets: dict) -> str | None:
    if not config.LLM_ENABLED:
        return None
    try:
        import anthropic
    except ImportError:
        return None
    payload = {
        "day_type": plan["day_type"],
        "targets": targets,
        "planned_totals": totals,
        "meals": [
            {"slot": i["slot"], "name": i["name"], "amount": i["amount_label"],
             "kcal": round((i["per"]["kcal"] or 0) * i["qty"]), "protein_g": round((i["per"]["protein"] or 0) * i["qty"], 1)}
            for i in plan["items"]
        ],
        "notes": plan.get("notes", []),
    }
    try:
        client = anthropic.Anthropic(timeout=60.0, max_retries=2)
        response = client.beta.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=2000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "low"},
            system=SYSTEM,
            messages=[{"role": "user", "content": json.dumps(payload)}],
        )
    except anthropic.APIConnectionError as exc:
        log.warning("Claude unreachable: %s", exc)
        return None
    except anthropic.RateLimitError:
        log.warning("Claude rate limited; skipping summary")
        return None
    except anthropic.APIStatusError as exc:
        log.warning("Claude API error %s: %s", exc.status_code, exc.message)
        return None
    if response.stop_reason == "refusal":
        return None
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    return text or None
