"""Narrow SQLite adapter around project AI's query MCP; never starts its full tool surface."""
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, os.environ['SQL_READONLY_AI_ROOT'])
from mcp.server.fastmcp import FastMCP
from mcp_servers import nt_query_mcp as upstream

# No upstream default DSN: this pack supports explicitly provisioned local SQLite only.
dsn = os.environ['SQL_READONLY_DSN']
if not dsn.startswith('sqlite:///') or not Path(dsn.removeprefix('sqlite:///')).is_absolute():
    raise ValueError('SQL_READONLY_DSN must be sqlite:/// followed by an absolute file path')


class RestrictedSQLite(upstream.QueryDatabaseAdapter):
    def _get_connection(self):
        connection = super()._get_connection()  # upstream opens SQLite with mode=ro
        allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
        connection.set_authorizer(lambda action, arg1, arg2, db, trigger: sqlite3.SQLITE_OK if (
            action in allowed or (action == sqlite3.SQLITE_PRAGMA and arg1 == 'table_info')
        ) else sqlite3.SQLITE_DENY)
        return connection


upstream._db_adapter = RestrictedSQLite(upstream.DatabaseConfig('sqlite', dsn))
mcp = FastMCP('sql-readonly')


@mcp.tool()
def metadata(table: str = 'invoices') -> dict:
    """Return columns and a sample for an existing table in the configured source."""
    result = upstream.get_table_stats(table)
    if result.get('error'):
        raise ValueError('Unknown or inaccessible table')
    return result


@mcp.tool()
def validate(statement: str) -> dict:
    """Validate SQL using project AI's shared validation service; reject mutation."""
    result = upstream.validate_sql(statement)
    if not result['valid']:
        raise ValueError('SQL rejected: only validated SELECT/CTE queries are supported')
    return result


@mcp.tool()
def query(statement: str) -> dict:
    """Execute validated SQL with a 100-row cap; no validation bypass argument."""
    validate(statement)
    result = upstream.execute_query(statement, limit=100, validate_first=True)
    if not result['success']:
        raise ValueError('Query rejected or failed against the configured source')
    return {'statement': statement, 'columns': result['columns'], 'rows': result['data']}


if __name__ == '__main__':
    mcp.run(transport='stdio')
