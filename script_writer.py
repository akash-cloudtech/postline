"""
Turns a topic into a natural, spoken ~2-minute video script (~280-320 words).
"""
from google import genai

from config import GEMINI_API_KEY, GEMINI_MODEL

_client = genai.Client(api_key=GEMINI_API_KEY)

SCRIPT_PROMPT = """Write a spoken video script for a {kind} topic video, meant
to be read aloud on camera in about 2 minutes (roughly 280-320 words).

Topic: {topic}

Rules:
- Write it as spoken language, not an essay - short sentences, natural
  pauses, contractions. This will be read aloud, not shown as on-screen text.
- Open with a strong hook in the first line (avoid "Hey guys, welcome back").
- End with a clear, simple call to action.
- Do not include stage directions, camera notes, or brackets - just the
  words to be spoken.

Return ONLY the script text, nothing else.
"""


def write_script(topic: str, kind: str) -> str:
    prompt = SCRIPT_PROMPT.format(kind=kind, topic=topic)
    response = _client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
    )
    return response.text.strip()
