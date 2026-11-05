"""The `logintel` command.

    logintel load                        # push the demo logs in demo_data/ into Loki
    logintel ask "question" [--use-case NAME]

Needs OPENAI_API_KEY for `ask`; Loki is read from LOKI_URL (default http://localhost:3100).
Set RCA_DEBUG=1 to see the catalog being built."""
import argparse
import dataclasses
import logging
import os
import sys

from . import loader
from .agent import Agent
from .config import Config
from .graph import LOG


def _ask(args) -> None:
    if os.environ.get("RCA_DEBUG"):
        LOG.setLevel(logging.DEBUG)
        logging.getLogger().addHandler(logging.StreamHandler())
    try:
        config = Config.from_env()
        if args.loki_url:
            config = dataclasses.replace(config, loki_url=args.loki_url)
        ans = Agent(config).ask(args.query, use_case=args.use_case)
    except (ConnectionError, ValueError, RuntimeError) as e:
        sys.exit(f"Error: {e}")

    print(f"\n=== Query: {ans.question}")
    print(f"path: {ans.path}")
    print(f"extracted: tenant={ans.tenant_id} start={ans.time_start} end={ans.time_end}")
    print(f"answer: {ans.answer}")
    print(f"confidence: {ans.confidence}")
    for line in ans.citations:
        print(f"  cited: {line}")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="logintel", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    ask = sub.add_parser("ask", help="ask a question about the logs in Loki")
    ask.add_argument("query")
    ask.add_argument("--use-case", help="search only this Loki use case (default: all)")
    ask.add_argument("--loki-url", help="Loki address (default: LOKI_URL or http://localhost:3100)")
    ask.set_defaults(run=_ask)

    load = sub.add_parser("load", help="push log files into Loki (default: the demo logs)")
    load.add_argument("pairs", nargs="*", metavar="NAME=FILE", default=loader.DEFAULT_PAIRS)
    load.set_defaults(run=lambda a: loader.run(a.pairs))

    args = parser.parse_args(argv)
    args.run(args)


if __name__ == "__main__":
    main()
