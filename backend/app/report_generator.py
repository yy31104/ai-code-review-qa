from __future__ import annotations

from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from schemas import ReviewResult


def generate_report(review: ReviewResult, output_path: str | Path) -> Path:
    output = Path(output_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    template_dir = Path(__file__).resolve().parents[1] / "templates"
    env = Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template("report.html")

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    html = template.render(review=_to_dict(review), generated_at=generated_at)
    html = html.replace("\r\n", "\n").replace("\r", "\n")
    with output.open("w", encoding="utf-8", newline="\n") as file:
        file.write(html)
    return output


def _to_dict(review: ReviewResult) -> dict:
    if hasattr(review, "model_dump"):
        return review.model_dump()
    return review.dict()
