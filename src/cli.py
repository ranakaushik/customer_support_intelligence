"""Command-line runner: shows every step's output, so each decision can be inspected.

Usage:
    python -m src.cli "My order #45821 never arrived"
    python -m src.cli --file message.txt
    echo "message" | python -m src.cli
    python -m src.cli --json "message"       # machine-readable output
"""

import argparse
import json
import sys

from src import config
from src.pipeline import run_pipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="Customer Support Intelligence pipeline")
    parser.add_argument("message", nargs="?", help="customer message (or use --file / stdin)")
    parser.add_argument("--file", help="read the message from a file")
    parser.add_argument("--json", action="store_true", help="print the full result as JSON")
    args = parser.parse_args()

    if args.file:
        message = open(args.file, encoding="utf-8").read()
    elif args.message is not None:
        message = args.message
    else:
        message = sys.stdin.read()

    result = run_pipeline(message)

    if args.json:
        print(result.model_dump_json(indent=2))
        return

    print(f"Model: {config.GEMINI_MODEL}\n")
    for t in result.traces:
        header = f"== {t.name} ({t.kind}"
        if t.prompt_version:
            header += f", {t.prompt_version}"
        header += f") {t.latency_ms} ms"
        if t.kind == "llm":
            header += f", tokens in/out {t.input_tokens}/{t.output_tokens}, attempts {t.attempts}"
        print(header + " ==")
        if t.error:
            print(f"ERROR: {t.error}")
        if t.output is not None:
            print(json.dumps(t.output, indent=2, ensure_ascii=False))
        print()

    if result.draft:
        print("== Draft reply ==")
        print(result.draft.response)
        print()
    tokens_in, tokens_out = result.total_tokens
    print(f"Total: {result.wall_ms} ms end to end, tokens in/out {tokens_in}/{tokens_out}")


if __name__ == "__main__":
    main()
