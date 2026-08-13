# Schema migrations

Run migrations **before** starting or deploying the application. Startup and `create-admin` refuse to
continue if the database is not at Alembic head.

## Commands

```bash
# Upgrade to head (normal path for new and existing Alembic-managed DBs)
python -m app migrate

# Show current revision vs expected head
python -m app schema-status

# Legacy databases created outside Alembic (verified stamp, then upgrade)
# Take a recoverable backup first.
python -m app migrate --adopt-existing
```

Equivalent: `access-registry migrate …` / `make migrate` / `make schema-status`.

## Adopt existing (`--adopt-existing`)

Use only when the database has legacy pre-Alembic Data Mover tables but **no** `alembic_version` row (for example
an old `create_all()` environment). The command:

1. Verifies known core tables and column shapes against the SQLAlchemy metadata.
2. Rejects partial or unknown intermediate schemas.
3. Stamps the matching historical revision.
4. Runs `upgrade` to head.

If the database is already Alembic-managed, omit `--adopt-existing` and run plain `migrate`.

## Operational rules

- **Backup** before adopt or major upgrades; rehearse restore on PostgreSQL.
- **No admin bootstrap in migrations** — create or promote administrators with
  `python -m app create-admin --email …` after the schema is current.
- **Downgrades** exist in revision scripts for operator recovery drills. They may remove schema and
  data. They are not an application feature for undoing user actions; prefer restore-from-backup for
  production incidents.
- Keep NIPR and SIPR (or other enclaves) on separate databases, credentials, and key material.

Recent data-movement revisions add owner-scoped saved pipelines (`0007`), connection catalog and
health/runtime metadata (`0008`), and owner-scoped CSV pipeline sources (`0009`). Do not remove or
rewrite these rows outside an approved retention/migration procedure: saved definitions may hold
foreign keys to CSV uploads, and credential ciphertext depends on the configured key ring.

## After migrate

```bash
python -m app schema-status
ADMIN_BOOTSTRAP_PASSWORD='…' python -m app create-admin \
  --email admin@example.gov --password-env ADMIN_BOOTSTRAP_PASSWORD
```

See [docs/deploy.md](../docs/deploy.md) and [docs/troubleshooting.md](../docs/troubleshooting.md).
