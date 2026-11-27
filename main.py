"""
Morning pipeline entry point - meant to be triggered by launchd at ~6:55 AM.

Run manually to test:
    python main.py
"""
import sys
from datetime import date

from topic_fetch import fetch_topics
from script_writer import write_script
from render_pdf import render_script_pdf
from print_job import print_pdf


def run():
    today = date.today()
    print(f"[main] Starting morning pipeline for {today.isoformat()}")

    try:
        topics = fetch_topics(today.isoformat())
    except Exception as e:
        print(f"[main] FATAL: topic fetch failed: {e}")
        sys.exit(1)

    jobs = [
        ("general", topics["general_topic"]),
        ("tech", topics["tech_topic"]),
    ]

    for kind, topic in jobs:
        print(f"[main] {kind} topic: {topic}")
        try:
            script = write_script(topic, kind)
        except Exception as e:
            print(f"[main] Script generation failed for {kind}: {e}")
            continue

        pdf_path = render_script_pdf(kind, topic, script, today)
        print(f"[main] Rendered PDF: {pdf_path}")

        print_pdf(pdf_path)

    print("[main] Done.")


if __name__ == "__main__":
    run()
