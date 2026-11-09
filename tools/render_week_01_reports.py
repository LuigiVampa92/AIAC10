"""Render brief Markdown reports from existing raw.json files, without network calls."""

import argparse
import json
from pathlib import Path

from aiac.config import ROOT
from aiac.reports.markdown import render


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", nargs="+", type=Path)
    args = parser.parse_args()
    for source in args.sources:
        source = source.resolve()
        if (
            not source.is_relative_to(ROOT)
            or source.name != "raw.json"
            or source.parent.parent.name != "runs"
        ):
            raise ValueError("Source must be a project-local day/runs/RUN_ID/raw.json")
        output = source.parent.parent.parent / "README.md"
        if output.exists():
            raise ValueError("Refusing to overwrite an existing report")
        data = json.loads(source.read_text(encoding="utf-8"))
        text = render(data["day"], data)
        relative_source = source.relative_to(output.parent).as_posix()
        text += f"\n## Исходные данные\n\n[Запросы, ответы и метрики — JSON]({relative_source}).\n"
        with output.open("x", encoding="utf-8") as stream:
            stream.write(text)
        print(output.relative_to(ROOT))


if __name__ == "__main__":
    main()
