"""Evaluate local, manually labelled screenshots without any network/write access."""

import argparse
import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.furniture_input.recognizer import Recognizer
from app.furniture_input import ENGINE_VERSION


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sample_directory", type=Path)
    parser.add_argument(
        "--labels", type=Path, default=Path("tests/fixtures/furniture_samples.json")
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--context",
        type=Path,
        help="Optional posted/manual category metadata keyed by furniture name",
    )
    args = parser.parse_args()
    cases = json.loads(args.labels.read_text(encoding="utf-8"))
    context = (
        json.loads(args.context.read_text(encoding="utf-8")) if args.context else {}
    )
    files = {
        p.stem: p
        for p in args.sample_directory.rglob("*")
        if p.suffix.lower() in {".jpg", ".png", ".webp"}
    }
    missing = [id for c in cases for id in c["images"] if id not in files]
    if missing:
        parser.error("Missing local screenshots: " + ", ".join(missing))
    peak = [0]
    stop = threading.Event()
    try:
        import psutil

        process = psutil.Process()

        def monitor():
            while not stop.wait(0.05):
                try:
                    rss = process.memory_info().rss
                    for child in process.children(recursive=True):
                        try:
                            rss += child.memory_info().rss
                        except psutil.Error:
                            pass
                    peak[0] = max(peak[0], rss)
                except psutil.Error:
                    pass

        thread = threading.Thread(target=monitor, daemon=True)
        thread.start()
    except ImportError:
        pass
    recognizer = Recognizer()
    records = []
    for case in cases:
        start = time.perf_counter()
        result = recognizer.analyze(
            (files[id].read_bytes() for id in case["images"]),
            category=context.get(case["name"], {}).get("category"),
        )
        accepted = {k: e.value for k, e in result.fields.items() if e.accepted}
        incorrect = {
            k: {"actual": v, "expected": case["expected"].get(k)}
            for k, v in accepted.items()
            if k not in case["expected"] or case["expected"][k] != v
        }
        records.append(
            {
                "name": case["name"],
                "split": case["split"],
                "seconds": round(time.perf_counter() - start, 3),
                "accepted": accepted,
                "incorrect": incorrect,
                "missing": [k for k in case["expected"] if k not in accepted],
                "expected_count": len(case["expected"]),
                "correct_count": sum(
                    k in accepted and accepted[k] == v
                    for k, v in case["expected"].items()
                ),
                "evidence": result.to_dict(),
            }
        )
        print(
            case["name"],
            len(accepted),
            "accepted",
            len(incorrect),
            "incorrect",
            flush=True,
        )
    stop.set()
    summary = {}
    for split in {c["split"] for c in cases}:
        subset = [r for r in records if r["split"] == split]
        summary[split] = {
            "furniture": len(subset),
            "accepted": sum(len(r["accepted"]) for r in subset),
            "expected": sum(r["expected_count"] for r in subset),
            "incorrect": sum(len(r["incorrect"]) for r in subset),
            "correct": sum(r["correct_count"] for r in subset),
        }
    output = {
        "engine_version": ENGINE_VERSION,
        "image_count": sum(len(c["images"]) for c in cases),
        "summary": summary,
        "peak_process_tree_rss_mb": round(peak[0] / 1024 / 1024, 1) or None,
        "records": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(summary, ensure_ascii=False),
        "peak MB",
        output["peak_process_tree_rss_mb"],
    )
    return 1 if any(r["incorrect"] for r in records) else 0


if __name__ == "__main__":
    raise SystemExit(main())
