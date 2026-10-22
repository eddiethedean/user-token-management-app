"""Provider credential catalog, independent of storage and runtime checks."""

from __future__ import annotations

from app.domain.credentials import CredentialField, SecretCatalog, SecretProvider

TOKEN_EXPIRY_FIELD = CredentialField(
    "token_expires_at",
    "Token expires at (UTC)",
    "Optional expiry from the provider's token page",
    input_type="datetime-local",
)

SECRET_PROVIDERS = (
    SecretProvider(
        "mss",
        "MSS",
        "MSS",
        "MSS_API_TOKEN",
        (
            CredentialField(
                "endpoint", "API endpoint", "https://mss.example", input_type="url", required=True
            ),
            CredentialField(
                "token",
                "API token",
                "Paste MSS API token",
                input_type="password",
                autocomplete="new-password",
                required=True,
            ),
            CredentialField("dataset_rid", "Default dataset RID", "ri.foundry.main.dataset..."),
            CredentialField("branch", "Default branch", "master", default="master"),
            CredentialField(
                "ca_profile",
                "TLS CA profile",
                "system",
                default="system",
                options=("system", "nipr"),
            ),
            TOKEN_EXPIRY_FIELD,
        ),
        setup_hint=(
            "Need help finding these values? Your platform administrator can provide the MSS "
            "API endpoint and token. In Foundry, the dataset page provides the dataset RID and "
            "available branches."
        ),
    ),
    SecretProvider(
        "mcscop",
        "MCS-COP",
        "MCS",
        "MCSCOP_API_TOKEN",
        (
            CredentialField(
                "endpoint",
                "API endpoint",
                "https://mcscop.example",
                input_type="url",
                required=True,
            ),
            CredentialField(
                "token",
                "API token",
                "Paste MCS-COP API token",
                input_type="password",
                autocomplete="new-password",
                required=True,
            ),
            CredentialField("dataset_rid", "Default dataset RID", "ri.foundry.main.dataset..."),
            CredentialField("branch", "Default branch", "master", default="master"),
            CredentialField(
                "ca_profile",
                "TLS CA profile",
                "system",
                default="system",
                options=("system", "nipr"),
            ),
            TOKEN_EXPIRY_FIELD,
        ),
        setup_hint=(
            "Need help finding these values? Your platform administrator can provide the "
            "MCS-COP API endpoint and token. In Foundry, the dataset page provides the dataset "
            "RID and available branches."
        ),
    ),
    SecretProvider(
        "postgres",
        "PostgreSQL",
        "PG",
        "DATABASE_URL",
        (
            CredentialField("host", "Host", "db.example.internal", required=True),
            CredentialField("port", "Port", "5432", required=True, default="5432"),
            CredentialField("database", "Database", "analytics", required=True),
            CredentialField(
                "username",
                "Username",
                "user.name.ctr",
                autocomplete="username",
                required=True,
            ),
            CredentialField(
                "password",
                "Password",
                "Enter database password",
                input_type="password",
                autocomplete="new-password",
                required=True,
            ),
            CredentialField(
                "sslmode",
                "SSL mode",
                "require",
                required=True,
                default="require",
                options=("require", "verify-ca", "verify-full"),
            ),
            CredentialField("connect_timeout", "Connect timeout (seconds)", "10", default="10"),
            CredentialField(
                "application_name", "Application name", "data-mover", default="data-mover"
            ),
        ),
        setup_hint=(
            "Find these values in pgAdmin: server Properties → Connection has the host and port; "
            "the Databases list has the database name; Login/Group Roles has the username. "
            "pgAdmin cannot reveal an existing role password, so use the password issued by your "
            "database administrator."
        ),
    ),
    SecretProvider(
        "databricks",
        "Databricks",
        "DBX",
        "DATABRICKS_API_TOKEN",
        (
            CredentialField(
                "workspace_url",
                "Workspace URL",
                "https://dbc-12345678.cloud.databricks.com",
                input_type="url",
                required=True,
            ),
            CredentialField(
                "token",
                "Personal access token",
                "Paste Databricks API token",
                input_type="password",
                autocomplete="new-password",
                required=True,
            ),
            CredentialField(
                "compute_kind",
                "Compute type",
                "Choose compute type",
                required=True,
                options=("warehouse", "classic_cluster"),
                option_labels=(
                    ("warehouse", "SQL warehouse"),
                    ("classic_cluster", "Classic cluster"),
                ),
            ),
            CredentialField(
                "compute_id",
                "Compute ID",
                "Existing warehouse or classic cluster ID",
                required=True,
            ),
            CredentialField(
                "http_path", "SQL HTTP path", "Connection details HTTP path", required=True
            ),
            CredentialField(
                "startup_policy",
                "Startup policy",
                "Manual startup",
                required=True,
                default="manual",
                options=("manual", "allow_start"),
                option_labels=(
                    ("manual", "Start manually"),
                    ("allow_start", "Allow automatic startup"),
                ),
            ),
            TOKEN_EXPIRY_FIELD,
        ),
        setup_hint=(
            "Use a workspace personal access token and an existing SQL warehouse or compatible "
            "all-purpose classic cluster. Confirm access, network and startup permissions with "
            "your platform administrator. Changing the token, workspace or compute resets startup consent."
        ),
        enabled_setting="pipeline_enable_databricks",
    ),
)

SECRET_CATALOG = SecretCatalog(SECRET_PROVIDERS)
SECRET_PROVIDER_MAP = {provider.name: provider for provider in SECRET_PROVIDERS}
