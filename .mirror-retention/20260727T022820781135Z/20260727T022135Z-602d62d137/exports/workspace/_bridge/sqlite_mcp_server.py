#!/usr/bin/env python3
"""Small stdio MCP server for SQLite.

This server exists to keep the local SQLite capability lightweight and
predictable. It uses Python's stdlib sqlite3, supports a small set of
high-leverage tools, and enforces profile permissions at the server boundary.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MCP_PROTOCOL_VERSION = "2025-11-25"
SERVER_NAME = "local-sqlite"
SERVER_VERSION = "0.1.0"
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_permissions(value: str) -> set[str]:
    return {item.strip().lower() for item in value.split(",") if item.strip()}


def statement_kind(sql: str) -> str:
    text = sql.strip().lstrip("\ufeff")
    if not text:
        return ""
    return text.split(None, 1)[0].lower()


def permission_for_sql(sql: str) -> str:
    kind = statement_kind(sql)
    if kind in {"select", "with", "explain"}:
        return "read"
    if kind == "insert":
        return "create"
    if kind == "update":
        return "update"
    if kind == "delete":
        return "delete"
    if kind in {"create", "alter", "drop"}:
        return "ddl"
    if kind in {"pragma", "vacuum", "analyze", "reindex"}:
        return "utility"
    if kind in {"begin", "commit", "rollback"}:
        return "transaction"
    return "execute"


def reject_multi_statement(sql: str) -> str:
    stripped = sql.strip()
    if not stripped:
        return "sql is required"
    tail = stripped[:-1] if stripped.endswith(";") else stripped
    if ";" in tail:
        return "multiple SQL statements are not allowed"
    return ""


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


class SqliteMcpService:
    def __init__(self, db_path: Path, permissions: set[str], readonly: bool):
        self.db_path = db_path
        self.permissions = permissions
        self.readonly = readonly

    def connect(self) -> sqlite3.Connection:
        if self.readonly:
            uri = self.db_path.resolve().as_uri() + "?mode=ro"
            db = sqlite3.connect(uri, uri=True, timeout=5)
        else:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(str(self.db_path), timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=3000")
        return db

    def has_permission(self, permission: str) -> bool:
        return permission in self.permissions

    def instructions(self) -> str:
        mode = "read-only" if self.readonly else "read-write"
        if self.readonly:
            return (
                f"Local SQLite MCP for {self.db_path} in {mode} mode. "
                "Use sqlite_query for SELECT-style reads and sqlite_tables/schema for inspection. "
                "This profile does not allow writes."
            )
        return (
            f"Local SQLite MCP for {self.db_path} in {mode} mode. "
            "Use sqlite_query for SELECT-style reads, sqlite_tables/schema for inspection, "
            "sqlite_insert_record/sqlite_upsert_record for structured writes, and sqlite_execute "
            "only for short bounded SQL when the profile permissions allow the requested write."
        )

    def tool_specs(self) -> list[dict[str, Any]]:
        tools = [
            {
                "name": "sqlite_health",
                "description": "Return SQLite MCP connection, mode, and permission health.",
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            },
            {
                "name": "sqlite_tables",
                "description": "List user tables and views in the configured SQLite database.",
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            },
            {
                "name": "sqlite_schema",
                "description": "Return schema SQL and PRAGMA table_info for one table, or all user tables when table is omitted.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"table": {"type": "string"}},
                    "additionalProperties": False,
                },
            },
            {
                "name": "sqlite_query",
                "description": "Run a bounded SELECT/ WITH query with optional positional parameters.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "sql": {"type": "string"},
                        "params": {"type": "array"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                    },
                    "required": ["sql"],
                    "additionalProperties": False,
                },
            },
        ]
        if not self.readonly:
            tools.extend(
                [
                    {
                        "name": "sqlite_execute",
                        "description": "Run one short non-read SQL statement if this profile grants the required permission. Prefer structured write tools for complex records.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "sql": {"type": "string"},
                                "params": {"type": "array"},
                            },
                            "required": ["sql"],
                            "additionalProperties": False,
                        },
                    },
                    {
                        "name": "sqlite_insert_record",
                        "description": "Insert one JSON object record into one table. The server builds SQL internally to avoid fragile long raw SQL tool calls.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "table": {"type": "string"},
                                "record": {"type": "object"},
                            },
                            "required": ["table", "record"],
                            "additionalProperties": False,
                        },
                    },
                    {
                        "name": "sqlite_upsert_record",
                        "description": "Insert or update one JSON object record using explicit key columns. The server builds the UPSERT SQL internally.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "table": {"type": "string"},
                                "key_columns": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                                "record": {"type": "object"},
                            },
                            "required": ["table", "key_columns", "record"],
                            "additionalProperties": False,
                        },
                    },
                ]
            )
        return tools

    def initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        _ = params
        return {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "capabilities": {"tools": {"listChanged": False}},
            "instructions": self.instructions(),
        }

    def tools_list(self, params: dict[str, Any]) -> dict[str, Any]:
        _ = params
        return {"tools": self.tool_specs()}

    def tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
        name = str(params.get("name") or "")
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        result = self.dispatch_tool(name, arguments)
        return {
            "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}],
            "isError": not bool(result.get("ok", True)),
        }

    def dispatch_tool(self, name: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            if name == "sqlite_health":
                exists = self.db_path.exists()
                return {
                    "ok": True,
                    "generated_at": now_iso(),
                    "db_path": str(self.db_path),
                    "exists": exists,
                    "readonly": self.readonly,
                    "permissions": sorted(self.permissions),
                }
            if name == "sqlite_tables":
                return self.sqlite_tables()
            if name == "sqlite_schema":
                return self.sqlite_schema(str(params.get("table") or "").strip())
            if name == "sqlite_query":
                return self.sqlite_query(
                    str(params.get("sql") or ""),
                    params.get("params") if isinstance(params.get("params"), list) else [],
                    int(params.get("limit") or 100),
                )
            if name == "sqlite_execute":
                return self.sqlite_execute(
                    str(params.get("sql") or ""),
                    params.get("params") if isinstance(params.get("params"), list) else [],
                )
            if name == "sqlite_insert_record":
                record = params.get("record") if isinstance(params.get("record"), dict) else {}
                return self.sqlite_insert_record(str(params.get("table") or ""), record)
            if name == "sqlite_upsert_record":
                record = params.get("record") if isinstance(params.get("record"), dict) else {}
                key_columns = params.get("key_columns") if isinstance(params.get("key_columns"), list) else []
                return self.sqlite_upsert_record(str(params.get("table") or ""), key_columns, record)
            return {"ok": False, "reason": "unknown_tool", "tool": name}
        except Exception as exc:
            return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}

    def sqlite_tables(self) -> dict[str, Any]:
        if not self.has_permission("list"):
            return {"ok": False, "reason": "missing_permission:list"}
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT name, type
                FROM sqlite_master
                WHERE type IN ('table','view')
                  AND name NOT LIKE 'sqlite_%'
                ORDER BY type, name
                """
            ).fetchall()
        return {"ok": True, "tables": [row_to_dict(row) for row in rows]}

    def sqlite_schema(self, table: str) -> dict[str, Any]:
        if not self.has_permission("list"):
            return {"ok": False, "reason": "missing_permission:list"}
        with self.connect() as db:
            if table:
                row = db.execute(
                    "SELECT name, type, sql FROM sqlite_master WHERE name=? AND type IN ('table','view')",
                    (table,),
                ).fetchone()
                if not row:
                    return {"ok": False, "reason": "table_or_view_not_found", "table": table}
                columns = [row_to_dict(item) for item in db.execute(f"PRAGMA table_info({quote_identifier(table)})").fetchall()]
                return {"ok": True, "schema": row_to_dict(row), "columns": columns}
            rows = db.execute(
                """
                SELECT name, type, sql
                FROM sqlite_master
                WHERE type IN ('table','view')
                  AND name NOT LIKE 'sqlite_%'
                ORDER BY type, name
                """
            ).fetchall()
        return {"ok": True, "schemas": [row_to_dict(row) for row in rows]}

    def sqlite_query(self, sql: str, params: list[Any], limit: int) -> dict[str, Any]:
        if not self.has_permission("read"):
            return {"ok": False, "reason": "missing_permission:read"}
        reject = reject_multi_statement(sql)
        if reject:
            return {"ok": False, "reason": reject}
        if permission_for_sql(sql) != "read":
            return {"ok": False, "reason": "sqlite_query only allows read statements"}
        limit = max(1, min(int(limit or 100), 500))
        query = f"SELECT * FROM ({sql.rstrip().rstrip(';')}) LIMIT ?"
        with self.connect() as db:
            rows = db.execute(query, [*params, limit]).fetchall()
        return {"ok": True, "row_count": len(rows), "rows": [row_to_dict(row) for row in rows]}

    def sqlite_execute(self, sql: str, params: list[Any]) -> dict[str, Any]:
        reject = reject_multi_statement(sql)
        if reject:
            return {"ok": False, "reason": reject}
        required = permission_for_sql(sql)
        if required == "read":
            return {"ok": False, "reason": "use sqlite_query for read statements"}
        if self.readonly:
            return {"ok": False, "reason": "profile_is_readonly"}
        if not self.has_permission(required):
            return {"ok": False, "reason": f"missing_permission:{required}", "required_permission": required}
        with self.connect() as db:
            cursor = db.execute(sql, params)
            db.commit()
            lastrowid = cursor.lastrowid
            rowcount = cursor.rowcount
        return {"ok": True, "required_permission": required, "rowcount": rowcount, "lastrowid": lastrowid}

    def sqlite_insert_record(self, table: str, record: dict[str, Any]) -> dict[str, Any]:
        allowed = self._structured_write_allowed(required=("create",))
        if allowed:
            return allowed
        valid = self._validate_table_record(table, record)
        if valid:
            return valid
        columns = list(record.keys())
        sql = (
            f"INSERT INTO {quote_identifier(table)} "
            f"({', '.join(quote_identifier(column) for column in columns)}) "
            f"VALUES ({', '.join('?' for _ in columns)})"
        )
        with self.connect() as db:
            cursor = db.execute(sql, [record[column] for column in columns])
            db.commit()
            lastrowid = cursor.lastrowid
            rowcount = cursor.rowcount
        return {
            "ok": True,
            "tool": "sqlite_insert_record",
            "table": table,
            "column_count": len(columns),
            "rowcount": rowcount,
            "lastrowid": lastrowid,
        }

    def sqlite_upsert_record(self, table: str, key_columns: list[Any], record: dict[str, Any]) -> dict[str, Any]:
        allowed = self._structured_write_allowed(required=("create", "update"))
        if allowed:
            return allowed
        valid = self._validate_table_record(table, record)
        if valid:
            return valid
        keys = [str(column) for column in key_columns]
        invalid_keys = [column for column in keys if not IDENTIFIER_RE.match(column)]
        if not keys:
            return {"ok": False, "reason": "key_columns_required"}
        if invalid_keys:
            return {"ok": False, "reason": "invalid_key_column", "columns": invalid_keys}
        missing_keys = [column for column in keys if column not in record]
        if missing_keys:
            return {"ok": False, "reason": "key_column_missing_from_record", "columns": missing_keys}
        columns = list(record.keys())
        insert_sql = (
            f"INSERT INTO {quote_identifier(table)} "
            f"({', '.join(quote_identifier(column) for column in columns)}) "
            f"VALUES ({', '.join('?' for _ in columns)})"
        )
        update_columns = [column for column in columns if column not in set(keys)]
        if update_columns:
            update_sql = ", ".join(
                f"{quote_identifier(column)}=excluded.{quote_identifier(column)}" for column in update_columns
            )
            conflict_sql = (
                f" ON CONFLICT({', '.join(quote_identifier(column) for column in keys)}) "
                f"DO UPDATE SET {update_sql}"
            )
        else:
            conflict_sql = f" ON CONFLICT({', '.join(quote_identifier(column) for column in keys)}) DO NOTHING"
        with self.connect() as db:
            cursor = db.execute(insert_sql + conflict_sql, [record[column] for column in columns])
            db.commit()
            lastrowid = cursor.lastrowid
            rowcount = cursor.rowcount
        return {
            "ok": True,
            "tool": "sqlite_upsert_record",
            "table": table,
            "key_columns": keys,
            "column_count": len(columns),
            "rowcount": rowcount,
            "lastrowid": lastrowid,
        }

    def _structured_write_allowed(self, required: tuple[str, ...]) -> dict[str, Any]:
        if self.readonly:
            return {"ok": False, "reason": "profile_is_readonly"}
        missing = [permission for permission in required if not self.has_permission(permission)]
        if missing:
            return {"ok": False, "reason": "missing_permission", "missing_permissions": missing}
        return {}

    def _validate_table_record(self, table: str, record: dict[str, Any]) -> dict[str, Any]:
        table = table.strip()
        if not IDENTIFIER_RE.match(table):
            return {"ok": False, "reason": "invalid_table_identifier", "table": table}
        if not record:
            return {"ok": False, "reason": "record_required"}
        invalid_columns = [str(column) for column in record if not IDENTIFIER_RE.match(str(column))]
        if invalid_columns:
            return {"ok": False, "reason": "invalid_record_column", "columns": invalid_columns}
        return {}


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def serve(service: SqliteMcpService) -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            method = str(request.get("method") or "")
            params = request.get("params") if isinstance(request.get("params"), dict) else {}
            if method == "initialize":
                result = service.initialize(params)
            elif method == "tools/list":
                result = service.tools_list(params)
            elif method == "tools/call":
                result = service.tools_call(params)
            elif method == "notifications/initialized":
                continue
            else:
                result = {"error": {"code": -32601, "message": f"Unknown method: {method}"}}
            if "error" in result:
                response = {"jsonrpc": "2.0", "id": request.get("id"), **result}
            else:
                response = {"jsonrpc": "2.0", "id": request.get("id"), "result": result}
        except Exception as exc:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32603, "message": f"{type(exc).__name__}: {exc}"},
            }
        sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
        sys.stdout.flush()
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local SQLite MCP server")
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--permissions", default="list,read")
    parser.add_argument("--readonly", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    service = SqliteMcpService(args.db, normalize_permissions(args.permissions), bool(args.readonly))
    return serve(service)


if __name__ == "__main__":
    raise SystemExit(main())
