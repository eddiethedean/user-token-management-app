"""MongoDB contract tests against pytest-mongo (not a product connector)."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime

import polars as pl
import pytest
from pymongo import MongoClient

from tests.mongo_support import mongodb_available
from tests.simulators.links import FIXTURES

pytestmark = [
    pytest.mark.mongodb,
    pytest.mark.skipif(
        not mongodb_available(),
        reason="mongod is not on PATH; set PYTEST_MONGO_NOPROC=1 to use a running instance",
    ),
]


def _fixture_docs() -> tuple[str, str, list[dict]]:
    payload = json.loads((FIXTURES / "mongodb_documents.json").read_text(encoding="utf-8"))
    return payload["database"], payload["collection"], list(payload["documents"])


def test_mongodb_ping_and_lists_collections(mongodb: MongoClient) -> None:
    hello = mongodb.admin.command("ping")
    assert hello.get("ok") == 1
    database, collection, docs = _fixture_docs()
    mongodb[database][collection].insert_many(docs)
    names = mongodb[database].list_collection_names()
    assert collection in names


def test_mongodb_round_trips_sanitized_fixture_documents(mongodb: MongoClient) -> None:
    database, collection, docs = _fixture_docs()
    coll = mongodb[database][collection]
    coll.insert_many(deepcopy(docs))
    found = list(coll.find({}, {"_id": 0}).sort("event_id", 1))
    assert found == docs
    dumped = json.dumps(found)
    assert "mongodb.mil" not in dumped
    assert "password" not in dumped


def test_mongodb_extracts_mixed_types_to_polars(mongodb: MongoClient) -> None:
    database, collection, docs = _fixture_docs()
    coll = mongodb[database][collection]
    coll.insert_many(
        [
            *docs,
            {
                "event_id": 3,
                "unit_name": "Charlie",
                "ready": None,
                "score": None,
                "note": None,
                "tags": [],
                "occurred": datetime(2026, 1, 15, tzinfo=UTC),
            },
        ]
    )
    rows = list(coll.find({}, {"_id": 0}).sort("event_id", 1))
    frame = pl.DataFrame(rows)
    assert frame.height == 3
    assert frame["event_id"].to_list() == [1, 2, 3]
    assert frame["unit_name"].to_list() == ["Alpha", "Bravo", "Charlie"]
    assert frame["score"].null_count() == 1


def test_mongodb_replace_collection_drops_prior_docs(mongodb: MongoClient) -> None:
    database, collection, docs = _fixture_docs()
    coll = mongodb[database][collection]
    coll.insert_many(docs)
    coll.delete_many({})
    coll.insert_one({"event_id": 99, "unit_name": "Zulu"})
    remaining = list(coll.find({}, {"_id": 0}))
    assert remaining == [{"event_id": 99, "unit_name": "Zulu"}]
