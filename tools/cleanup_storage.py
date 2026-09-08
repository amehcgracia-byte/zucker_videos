#!/usr/bin/env python3
"""Report and manually run recoverable ZuckerVideos retention."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from core.retention import build_storage_report, cleanup_plan, execute_cleanup


def gib(value: int) -> str:
    return f"{value / (1024 ** 3):.2f} GiB"


def print_report(report: dict) -> None:
    print(f"ZuckerVideos: {gib(report['root']['bytes'])}")
    for name, item in report["categories"].items():
        print(f"  {name}: {gib(item['bytes'])} ({item.get('recoverability', '')})")
        if name == "cache":
            for sub, detail in item["subfolders"].items(): print(f"    {sub}: {gib(detail['bytes'])}")
        if name == "app_backups":
            for copy in item["copies"]: print(f"    {copy['mtime']} {gib(copy['bytes'])} {copy['path']}")
        if name == "projects":
            for project in item["items"]:
                label = "verification" if project["automatic"] else "user/unknown"
                print(f"    {label}: {project['name']} {gib(project['bytes'])}")
                for export in project["exports"]["files"]:
                    state = "current" if export["current"] else "history" if export["retained_history"] else "candidate"
                    print(f"      {state}: {gib(export['bytes'])} {export['path']}")
        if name == "huggingface":
            for model in item["models"]: print(f"    {gib(model['bytes'])} {model['name']}")
        if name == "verification_temp":
            for temp in item["files"]: print(f"    candidate: {gib(temp['bytes'])} {temp['path']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("report", "clean"), nargs="?", default="report")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--yes", action="store_true", help="confirm cleanup without a prompt")
    args = parser.parse_args()
    report = build_storage_report()
    plan = cleanup_plan(report)
    if args.command == "report":
        if args.json:
            print(json.dumps(report, indent=2, ensure_ascii=False))
        else:
            print_report(report)
        return 0
    print_report(report)
    print(f"\nPlanned recoverable cleanup: {gib(sum(item['bytes'] for item in plan))} in {len(plan)} item(s).")
    if not args.yes and input("Type CLEANUP to move these items to the Trash: ").strip() != "CLEANUP":
        print("Cancelled; nothing was changed.")
        return 0
    result = execute_cleanup(report)
    print(f"Moved {len(result['moved'])} item(s) to Trash; freed {gib(result['freed_bytes'])} from the live tree.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
