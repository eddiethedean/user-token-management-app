# Schema migrations

Run migrations explicitly before starting or deploying the application (from `hedron-app/`):

```bash
python -m access_registry migrate
```

Account creation and role assignment are intentionally not performed by Alembic revisions. Use
`python -m access_registry.cli create-admin --email ...` after the schema is current.
