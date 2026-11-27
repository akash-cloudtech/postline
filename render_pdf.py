"""
Renders a topic + script into a simple, readable PDF for printing.
"""
from datetime import date
from pathlib import Path

from fpdf import FPDF

from config import OUTPUT_DIR


def render_script_pdf(kind: str, topic: str, script: str, run_date: date) -> Path:
    pdf = FPDF(format="Letter")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, f"{kind.upper()} SCRIPT - {run_date.isoformat()}", ln=True)

    pdf.set_font("Helvetica", "I", 11)
    pdf.multi_cell(0, 7, f"Topic: {topic}")
    pdf.ln(4)

    pdf.set_font("Helvetica", "", 13)
    pdf.multi_cell(0, 8, script)

    filename = f"{run_date.isoformat()}_{kind}_script.pdf"
    out_path = OUTPUT_DIR / filename
    pdf.output(str(out_path))
    return out_path
