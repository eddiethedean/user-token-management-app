from __future__ import annotations

import pytest

from app.config import Settings


def _settings(**updates) -> Settings:
    return Settings(_env_file=None, **updates)  # pyright: ignore[reportCallIssue]


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        (
            {"pipeline_enable_databricks": False, "pipeline_enable_databricks_writer": True},
            "requires PIPELINE_ENABLE_DATABRICKS",
        ),
        (
            {"pipeline_databricks_writer_live_acceptance_verified": False},
            "WRITER_LIVE_ACCEPTANCE_VERIFIED",
        ),
        (
            {
                "pipeline_databricks_metadata_visibility_verified": False,
            },
            "verified Unity Catalog metadata visibility",
        ),
        (
            {
                "pipeline_databricks_startup_contract_verified": False,
            },
            "verified compute startup contract",
        ),
    ],
)
def test_writer_requires_every_independent_operator_attestation(updates, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _settings(
            **{
                "pipeline_enable_databricks_writer": True,
                "pipeline_databricks_metadata_visibility_verified": True,
                **updates,
            }
        )


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


def test_default_databricks_operations_require_metadata_verification(monkeypatch) -> None:
    for name in (
        "PIPELINE_ENABLE_DATABRICKS",
        "PIPELINE_ENABLE_DATABRICKS_WRITER",
        "PIPELINE_DATABRICKS_METADATA_VISIBILITY_VERIFIED",
        "PIPELINE_DATABRICKS_STARTUP_CONTRACT_VERIFIED",
        "PIPELINE_DATABRICKS_WRITER_LIVE_ACCEPTANCE_VERIFIED",
        "PIPELINE_DATABRICKS_STAGING_SCHEMA",
    ):
        monkeypatch.delenv(name, raising=False)
    settings = _settings()

    assert settings.pipeline_enable_databricks
    assert not settings.pipeline_enable_databricks_writer
    assert not settings.pipeline_databricks_metadata_visibility_verified
    assert settings.pipeline_databricks_startup_contract_verified
    assert settings.pipeline_databricks_writer_live_acceptance_verified
    assert settings.pipeline_databricks_staging_schema == "main.data_mover_staging"


@pytest.mark.parametrize(
    "prerequisite",
    (
        "pipeline_enable_databricks",
        "pipeline_databricks_metadata_visibility_verified",
        "pipeline_databricks_startup_contract_verified",
        "pipeline_databricks_writer_live_acceptance_verified",
    ),
)
@pytest.mark.parametrize("source", ("environment", "init"))
def test_disabled_prerequisite_disables_default_writer(monkeypatch, prerequisite, source) -> None:
    monkeypatch.setenv("PIPELINE_DATABRICKS_METADATA_VISIBILITY_VERIFIED", "true")
    monkeypatch.delenv("PIPELINE_ENABLE_DATABRICKS_WRITER", raising=False)
    if source == "environment":
        monkeypatch.setenv(prerequisite.upper(), "false")
        settings = _settings()
    else:
        settings = _settings(**{prerequisite: False})

    assert not getattr(settings, prerequisite)
    assert not settings.pipeline_enable_databricks_writer


def test_metadata_attestation_enables_default_writer(monkeypatch) -> None:
    monkeypatch.setenv("PIPELINE_DATABRICKS_METADATA_VISIBILITY_VERIFIED", "true")
    monkeypatch.delenv("PIPELINE_ENABLE_DATABRICKS_WRITER", raising=False)

    settings = _settings()

    assert settings.pipeline_enable_databricks_writer


def test_explicit_writer_opt_out_is_preserved(monkeypatch) -> None:
    monkeypatch.setenv("PIPELINE_DATABRICKS_METADATA_VISIBILITY_VERIFIED", "true")
    monkeypatch.setenv("PIPELINE_ENABLE_DATABRICKS_WRITER", "false")

    settings = _settings()

    assert not settings.pipeline_enable_databricks_writer
