"""
One-time setup script: creates the Databricks secret scope and stores the
Lakebase connection URL. Run this locally (with the Databricks CLI configured)
or from a notebook - never commit the resulting secret value anywhere.

The weather MCP server itself needs no API key (Open-Meteo is free and
unauthenticated) - this secret is only used by the dashboard app to log and
read recent agent queries/predictions from Lakebase.

Usage:
    python setup_secrets.py
"""
from databricks.sdk import WorkspaceClient
from databricks.sdk.service import workspace
import getpass

w = WorkspaceClient()

#w.secrets.create_scope(scope="database")
w.secrets.put_secret(
    scope="database",
    key="lakebase-url",
    # Store as plain text - WorkspaceClient().secrets.get_secret() already
    # base64-encodes the value on the way out (that's what lakebase.py's
    # base64.b64decode() undoes), so pre-encoding here would double-encode it.
    string_value=getpass.getpass("Paste your lakebase url"),
)

w.secrets.put_acl(
    scope="database",
    principal="users",
    permission=workspace.AclPermission.READ,
)
