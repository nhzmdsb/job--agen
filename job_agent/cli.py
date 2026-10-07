import argparse
import json
import sys
from .store import COLLECTIONS, Store


def main():
    parser = argparse.ArgumentParser(description="AI-managed job search records; JSON in/out")
    parser.add_argument("--db", help="SQLite path; otherwise JOB_AGENT_DB or data/workspace.sqlite3")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    sub.add_parser("export")
    for command in ("list", "get", "put", "history"):
        p = sub.add_parser(command)
        p.add_argument("collection", choices=COLLECTIONS)
        if command in ("get", "history"):
            p.add_argument("id")
        if command == "list":
            p.add_argument("--limit", type=int, default=50)
            p.add_argument("--offset", type=int, default=0)
        if command == "put":
            p.add_argument("--id")
            p.add_argument("--expected-version", type=int, default=0)
            p.add_argument("--actor", default="agent")
            p.add_argument("--reason", default="")
    args = parser.parse_args()
    try:
        store = Store(args.db)
        if args.command == "init":
            result = {"database": str(store.path), "collections": COLLECTIONS}
        elif args.command == "export":
            result = store.export()
        elif args.command == "list":
            result = store.list(args.collection, args.limit, args.offset)
        elif args.command == "put":
            result = store.put(args.collection, json.load(sys.stdin), args.id, args.expected_version, args.actor, args.reason)
        else:
            result = getattr(store, args.command)(args.collection, args.id)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    except (ValueError, KeyError, OSError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
