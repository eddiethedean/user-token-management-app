# Schema migrations

Run migrations explicitly before starting or deploying the application:

```bash
python -m app migrate
```

Account creation and role assignment are intentionally not performed by Alembic revisions. Use
`python -m app create-admin --email ...` after the schema is current.
