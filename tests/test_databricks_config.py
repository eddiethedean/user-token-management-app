from __future__ import annotations

import pytest

from app.config import Settings


def _settings(**updates) -> Settings:
    return Settings(_env_file=None, **updates)  # pyright: ignore[reportCallIssue]


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        (
            {"pipeline_enable_databricks_writer": True},
            "requires PIPELINE_ENABLE_DATABRICKS",
        ),
        (
            {"pipeline_enable_databricks": True, "pipeline_enable_databricks_writer": True},
            "WRITER_LIVE_ACCEPTANCE_VERIFIED",
        ),
        (
            {
                "pipeline_enable_databricks": True,
                "pipeline_enable_databricks_writer": True,
                "pipeline_databricks_writer_live_acceptance_verified": True,
            },
            "verified Unity Catalog metadata visibility",
        ),
        (
            {
                "pipeline_enable_databricks": True,
                "pipeline_enable_databricks_writer": True,
                "pipeline_databricks_writer_live_acceptance_verified": True,
                "pipeline_databricks_metadata_visibility_verified": True,
            },
            "verified compute startup contract",
        ),
    ],
)
def test_writer_requires_every_independent_operator_attestation(updates, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _settings(**updates)


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        (
            {"pipeline_databricks_staging_schema": "private.staging.extra"},
            "catalog.schema pair",
        ),
        (
            {
                "pipeline_databricks_startup_poll_seconds": 10,
                "pipeline_databricks_startup_max_poll_seconds": 5,
            },
            "at least the startup poll interval",
        ),
        (
            {"pipeline_databricks_sensitive_tag_rules": "not-json"},
            "must be a JSON array",
        ),
        (
            {"pipeline_databricks_sensitive_tag_rules": "{}"},
            "must be a JSON array",
        ),
        (
            {"pipeline_databricks_sensitive_tag_rules": '[{"key":"x","marker":"Invalid"}]'},
            "exact key",
        ),
        ({"pipeline_databricks_metadata_policy_version": "bad version"}, "is invalid"),
    ],
)
def test_databricks_operator_settings_reject_unsafe_contracts(updates, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _settings(**updates)


def test_fully_attested_writer_configuration_is_accepted() -> None:
    settings = _settings(
        pipeline_enable_databricks=True,
        pipeline_enable_databricks_writer=True,
        pipeline_databricks_writer_live_acceptance_verified=True,
        pipeline_databricks_metadata_visibility_verified=True,
        pipeline_databricks_startup_contract_verified=True,
        pipeline_databricks_staging_schema="private.staging",
        pipeline_databricks_sensitive_tag_rules='[{"key":"classification","marker":"pii"}]',
    )

    assert settings.pipeline_enable_databricks_writer
    assert settings.pipeline_databricks_staging_schema == "private.staging"
