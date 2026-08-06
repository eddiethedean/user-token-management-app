# Schema migrations

Run migrations explicitly before starting or deploying the application:

```bash
python -m app migrate
```

Account creation and role assignment are intentionally not performed by Alembic revisions. Use
`python -m app create-admin --email … --password-env ADMIN_BOOTSTRAP_PASSWORD` after the schema is
current (or the console script `access-registry create-admin …`).
