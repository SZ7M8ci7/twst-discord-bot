"""Offline analysis: does not connect to Discord or Google Sheets."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.furniture_input.recognizer import Recognizer
from app.furniture_input.post_parser import parse_post
from app.furniture_input.sheet_writer import build_plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", nargs="+")
    parser.add_argument("--post-file", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    post = parse_post(args.post_file.read_text(encoding="utf-8-sig"))
    if post is None:
        parser.error("Furniture name and category are required")
    result = Recognizer().analyze(
        (Path(p).read_bytes() for p in args.images), category=post["category"]
    )
    output = json.dumps(
        {
            "post": post,
            "recognition": result.to_dict(),
            "new_row_plan": vars(build_plan([], post, result)),
        },
        ensure_ascii=False,
        indent=2,
    )
    if args.output:
        args.output.write_text(output, encoding="utf-8")
    else:
        print(output)


if __name__ == "__main__":
    main()
