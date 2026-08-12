"""CLI: python -m app.audio generate --project-id RUN_ID [--force] [--merge]."""

import argparse
import json
import logging
from dataclasses import asdict

from app.audio.speech_generator import generate_project_audio


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate")
    generate.add_argument("--project-id", required=True)
    generate.add_argument("--force", action="store_true")
    generate.add_argument("--merge", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    try:
        print(json.dumps(asdict(generate_project_audio(args.project_id, force=args.force, merge=args.merge)), ensure_ascii=False, indent=2))
        return 0
    except Exception as error:
        logging.error("Audio generation failed (%s): %s", type(error).__name__, error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
