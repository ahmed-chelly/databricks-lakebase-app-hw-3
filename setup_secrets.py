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
import base64

from databricks.sdk import WorkspaceClient
from databricks.sdk.service import workspace
import getpass

w = WorkspaceClient()

w.secrets.create_scope(scope="database")
w.secrets.put_secret(
    scope="database",
    key="lakebase-url",
    # base64-encoded because lakebase.py base64-decodes every secret it reads
    string_value=base64.b64encode(
        getpass.getpass("Paste your lakebase url").encode("utf-8")
    ).decode("utf-8"),
)

w.secrets.put_acl(
    scope="database",
    principal="users",
    permission=workspace.AclPermission.READ,
)
