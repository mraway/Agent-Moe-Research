#!/usr/bin/env python3
"""Render a repository Markdown report as HTML, DOCX, or CJK-capable PDF."""

from __future__ import annotations

import argparse
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="source Markdown file")
    parser.add_argument("output", type=Path, help=".html, .docx, or .pdf output")
    parser.add_argument(
        "--css",
        type=Path,
        default=ROOT / "docs" / "report.css",
        help="stylesheet used for PDF output",
    )
    parser.add_argument(
        "--no-toc",
        action="store_true",
        help="omit the generated table of contents",
    )
    return parser.parse_args()


def _pandoc_args(include_toc: bool) -> list[str]:
    arguments = ["--standalone"]
    if include_toc:
        arguments.append("--toc")
    return arguments


def render(input_path: Path, output_path: Path, css_path: Path, include_toc: bool) -> None:
    try:
        import pypandoc
    except ImportError as error:
        raise RuntimeError(
            "report dependencies are missing; install requirements-report.txt"
        ) from error

    input_path = input_path.resolve()
    output_path = output_path.resolve()
    css_path = css_path.resolve()
    if not input_path.is_file():
        raise ValueError(f"input Markdown does not exist: {input_path}")
    if output_path.suffix.lower() not in {".html", ".docx", ".pdf"}:
        raise ValueError("output suffix must be .html, .docx, or .pdf")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if output_path.suffix.lower() == ".docx":
        pypandoc.convert_file(
            str(input_path),
            "docx",
            outputfile=str(output_path),
            extra_args=_pandoc_args(include_toc),
        )
        return

    html = pypandoc.convert_file(
        str(input_path),
        "html5",
        extra_args=_pandoc_args(include_toc),
    )
    if output_path.suffix.lower() == ".html":
        output_path.write_text(html, encoding="utf-8")
        return

    try:
        from weasyprint import CSS, HTML
    except ImportError as error:
        raise RuntimeError(
            "PDF dependencies are missing; install requirements-report.txt"
        ) from error
    if not css_path.is_file():
        raise ValueError(f"report stylesheet does not exist: {css_path}")
    HTML(string=html, base_url=str(input_path.parent)).write_pdf(
        str(output_path),
        stylesheets=[CSS(filename=str(css_path))],
    )


def main() -> None:
    args = _args()
    render(args.input, args.output, args.css, not args.no_toc)
    print(args.output.resolve())


if __name__ == "__main__":
    main()
