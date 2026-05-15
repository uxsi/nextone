"""CLI entry point: next-edit-server --stdio"""

from __future__ import annotations

import argparse
import logging
import sys

from .server import NextEditServer


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="next-edit-server",
        description="NextOne: local next edit prediction server",
    )
    parser.add_argument(
        "--stdio",
        action="store_true",
        default=True,
        help="Use stdio for JSON-RPC communication (default)",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level (default: INFO)",
    )
    parser.add_argument(
        "--log-file",
        type=str,
        default=None,
        help="Log to file instead of stderr",
    )
    parser.add_argument(
        "--model-path",
        type=str,
        default=None,
        help="Path to GGUF model file. If not provided, uses a dummy backend for testing.",
    )
    args = parser.parse_args()

    # Configure logging (to file or stderr, never stdout — stdout is the RPC channel)
    log_handlers: list[logging.Handler] = []
    if args.log_file:
        log_handlers.append(logging.FileHandler(args.log_file))
    else:
        log_handlers.append(logging.StreamHandler(sys.stderr))

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=log_handlers,
    )

    server = NextEditServer(model_path=args.model_path)
    server.run()


if __name__ == "__main__":
    main()
