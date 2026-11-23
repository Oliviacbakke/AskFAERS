"""Small pure-Python helpers for the Streamlit app (kept separate so they can be unit-tested without Streamlit)."""
from __future__ import annotations

import csv
import io

MAX_QUESTIONS_PER_SESSION = 25   # protects your API budget if the app is ever shared
MODEL_CHOICES = {"Claude Sonnet 5.5 (recommended)": "claude-sonnet-5-5", "Claude Haiku 5.5 (faster, cheaper)": "claude-haiku-5-5"}

DISCLAIMER = ("Educational project using public FDA adverse event data (FAERS). Not medical advice, and not affiliated "
              "with or endorsed by the FDA or Anthropic. Talk to a pharmacist or doctor about any medicine.")

EXAMPLE_QUESTIONS = [
    "What are the most reported reactions when amlodipine is the primary suspect?",
    "How many reports mention metformin?",
    "What percent of reports that mention ibuprofen have a hospitalization outcome?",
    "Which three ingredients are most often the primary suspect in reports with a death outcome?",
    "How many reports per receive year mention adalimumab?",
]


def unique_columns(columns) -> list:
    """Make column names unique and non-empty (SQL results can repeat names, which table widgets dislike)."""
    seen, out = {}, []
    for i, name in enumerate(columns):
        base = str(name) if str(name).strip() else f"column_{i + 1}"
        seen[base] = seen.get(base, 0) + 1
        out.append(base if seen[base] == 1 else f"{base}_{seen[base]}")
    return out


def rows_to_csv(columns, rows) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(unique_columns(columns))
    for r in rows:
        writer.writerow(["" if v is None else v for v in r])
    return buf.getvalue()


def questions_left(asked: int, limit: int = MAX_QUESTIONS_PER_SESSION) -> int:
    return max(0, limit - asked)


def friendly_api_error(exc: Exception) -> str:
    """Turn an API exception into a message that is safe to show. Never includes keys or raw response bodies."""
    name = type(exc).__name__
    status = getattr(exc, "status_code", None)
    if name == "AuthenticationError" or status in (401, 403):
        return "The API key was rejected. Check ANTHROPIC_API_KEY and restart the app."
    if name == "NotFoundError" or status == 404:
        return "That model isn't available for this account. Try the other model in the sidebar."
    if name == "RateLimitError" or status == 429:
        return "The API is busy or the rate limit was reached. Wait a moment and try again."
    if name == "APIConnectionError":
        return "Could not reach the API. Check your internet connection."
    return "Something went wrong while contacting the API. Please try again."
