#!/usr/bin/env python3
"""Read-only routing decisions for resource acquisition and inspection.

This layer decides which tool family should handle a resource request. It does
not fetch, convert, open browsers, call MCP tools, install packages, or write
files. Materialization remains owned by resource_cli/resource_fetcher.
"""

from __future__ import annotations

import json
from typing import Any

from resource_fetcher import ResourceIntent, ResourceStage
from resource_route_rules import ResourceRoute, build_resource_route


def route_resource(
    *,
    url: str = "",
    path: str = "",
    target: str = "",
    intent: str = ResourceIntent.UNKNOWN,
    need_materialization: bool = False,
    task: str = "",
    name: str = "",
    resource_kind_hint: str = "",
    source_kind_hint: str = "",
    site_or_domain: str = "",
) -> ResourceRoute:
    """Return a read-only route plan for a resource task."""
    return build_resource_route(
        url=url,
        path=path,
        target=target,
        intent=intent,
        need_materialization=need_materialization,
        task=task,
        name=name,
        resource_kind_hint=resource_kind_hint,
        source_kind_hint=source_kind_hint,
        site_or_domain=site_or_domain,
    )


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Plan resource acquisition routing without side effects.")
    parser.add_argument("--target", default="", help="Generic resource target for owner-tool routing.")
    parser.add_argument("--url", default="", help="URL resource to route.")
    parser.add_argument("--path", default="", help="Local file resource to route.")
    parser.add_argument("--intent", default=ResourceIntent.UNKNOWN, help="Declared resource intent.")
    parser.add_argument("--need-materialization", action="store_true", help="Whether a durable local artifact is required.")
    parser.add_argument("--task", default="", help="Short task description for route hints.")
    parser.add_argument("--name", default="", help="Optional output/display name.")
    parser.add_argument("--resource-kind", default="", help="Structured resource-kind hint from delegation.")
    parser.add_argument("--source-kind", default="", help="Structured source-kind hint from delegation.")
    parser.add_argument("--site-or-domain", default="", help="Structured site/domain constraint from delegation.")
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    args = parser.parse_args()

    route = route_resource(
        url=args.url,
        path=args.path,
        target=args.target,
        intent=args.intent,
        need_materialization=args.need_materialization,
        task=args.task,
        name=args.name,
        resource_kind_hint=args.resource_kind,
        source_kind_hint=args.source_kind,
        site_or_domain=args.site_or_domain,
    )
    payload = route.to_dict()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(f"primary_tool={route.primary_tool}")
        print(f"recommended_stage={route.recommended_stage}")
        print(f"intent={route.intent}")
        print(f"need_materialization={route.need_materialization}")
        if route.secondary_tools:
            print(f"secondary_tools={', '.join(route.secondary_tools)}")
        if route.resource_cli_command:
            print(f"resource_cli_command={route.resource_cli_command}")
        if route.reasons:
            print(f"reasons={'; '.join(route.reasons)}")
    return 0 if route.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
