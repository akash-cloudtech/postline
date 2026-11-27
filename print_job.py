"""
Sends a rendered PDF to the configured network printer via CUPS, and reports
whether the job was accepted.
"""
import subprocess
from pathlib import Path

from config import PRINTER_NAME


def print_pdf(pdf_path: Path) -> bool:
    if not PRINTER_NAME:
        print(f"[print_job] PRINTER_NAME not set - skipping print, file is at {pdf_path}")
        return False

    result = subprocess.run(
        ["lp", "-d", PRINTER_NAME, str(pdf_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"[print_job] Print failed for {pdf_path}: {result.stderr.strip()}")
        return False

    print(f"[print_job] Sent to printer '{PRINTER_NAME}': {result.stdout.strip()}")
    return True
