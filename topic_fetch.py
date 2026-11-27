"""
Fetches today's topic ideas: one general-interest topic, one tech topic.

TODO (next iteration): this currently relies on the model's own sense of
what's current. For genuinely fresh "trending today" topics, wire in a real
trend source - e.g. Google Trends via the pytrends library, a news API, or
Gemini's built-in Google Search grounding tool - and pass that context into
the prompt below instead of asking the model to guess.
"""
import json

from google import genai

from config import GEMINI_API_KEY, GEMINI_MODEL

_client = genai.Client(api_key=GEMINI_API_KEY)

TOPIC_PROMPT = """You are picking two video topics for today, {date}.

1. A GENERAL topic: something broadly interesting to a general audience
   (current events, culture, lifestyle, science curiosity) that people are
   actively discussing today.
2. A TECH topic: a notable, current development in technology, AI, or
   software.

Return ONLY valid JSON in exactly this shape, with no markdown fences and no
extra commentary:
{{
  "general_topic": "<one sentence topic>",
  "tech_topic": "<one sentence topic>"
}}
"""


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if "\n" in text:
            text = text.split("\n", 1)[1]
    return text.strip()


def fetch_topics(date_str: str) -> dict:
    prompt = TOPIC_PROMPT.format(date=date_str)
    response = _client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
    )
    text = _strip_code_fence(response.text)
    return json.loads(text)
