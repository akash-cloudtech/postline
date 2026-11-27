import os
from pathlib import Path

# Loads a .env file if python-dotenv is installed; otherwise falls back to
# whatever is already in the environment (e.g. set via launchd's EnvironmentVariables).
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")

# The exact CUPS queue name for your network printer.
# Find it with: lpstat -p
PRINTER_NAME = os.environ.get("PRINTER_NAME", "")

OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", str(Path.home() / "morning_pipeline_output")))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

LOG_DIR = Path(os.environ.get("LOG_DIR", str(Path.home() / "morning_pipeline_logs")))
LOG_DIR.mkdir(parents=True, exist_ok=True)
