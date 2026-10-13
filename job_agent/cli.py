import argparse
import json
import sys
from .store import COLLECTIONS, Store


def main():
    parser = argparse.ArgumentParser(description="AI-managed job search records; JSON in/out")
    parser.add_argument("--db", help="SQLite path; otherwise JOB_AGENT_DB or private user data directory")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    sub.add_parser("export")
    sub.add_parser("import")
    sub.add_parser("sync")
    for command in ("review-pending", "worker-context"):
        p = sub.add_parser(command)
        p.add_argument("--limit", type=int, default=50)
        p.add_argument("--offset", type=int, default=0)
    sub.add_parser("lookup-jobs")
    sub.add_parser("merge-jobs")
    for command in ("list", "get", "put", "patch", "history"):
        p = sub.add_parser(command)
        p.add_argument("collection", choices=COLLECTIONS)
        if command in ("get", "history"):
            p.add_argument("id")
        if command == "list":
            p.add_argument("--limit", type=int, default=50)
            p.add_argument("--offset", type=int, default=0)
        if command in ("put", "patch"):
            p.add_argument("--id")
            p.add_argument("--expected-version", type=int, default=0)
            p.add_argument("--actor", default="agent")
            p.add_argument("--reason", default="")
            p.add_argument("--quota-confirm", action="store_true")
            p.add_argument("--quota-reason", default="")
            p.add_argument("--human-confirm", action="store_true")
            p.add_argument("--confirmation-reason", default="")
    args = parser.parse_args()
    try:
        store = Store(args.db)
        if args.command == "init":
            result = {"database": str(store.path), "collections": COLLECTIONS}
        elif args.command == "export":
            result = store.export()
        elif args.command == "import":
            result = store.restore(json.load(sys.stdin))
        elif args.command == "sync":
            from .sync import Sync
            result = Sync(store).run()
        elif args.command in ("review-pending", "worker-context"):
            from .worker import context
            result = context(store, args.limit, args.offset)
        elif args.command == "lookup-jobs":
            from .worker import lookup
            result = lookup(store, json.load(sys.stdin))
        elif args.command == "merge-jobs":
            from .worker import merge_jobs
            result = merge_jobs(store, **json.load(sys.stdin))
        elif args.command == "list":
            result = store.list(args.collection, args.limit, args.offset)
        elif args.command in ("put", "patch"):
            from .worker import write
            result = write(store, args.collection, json.load(sys.stdin), args.id, args.expected_version, patch=args.command == "patch", actor=args.actor, reason=args.reason, quota_confirm=args.quota_confirm, quota_reason=args.quota_reason, human_confirm=args.human_confirm, confirmation_reason=args.confirmation_reason)
            if args.command == "put":
                result = result["record"]
        else:
            result = getattr(store, args.command)(args.collection, args.id)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    except (ValueError, KeyError, OSError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
