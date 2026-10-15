"""통합 테스트 공통 픽스처: 임시 파일 DB + 주입 가능한 시계."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from rentdesk.persistence.db import open_database
from tests.support import FakeClock, Services, kst


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "데이터 폴더" / "rentdesk.db"  # 한글/공백 경로 포함


@pytest.fixture
def conn(db_path) -> Iterator[sqlite3.Connection]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = open_database(db_path)
    yield connection
    connection.close()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(kst(10, 19, 9, 0))


@pytest.fixture
def svc(conn, clock) -> Services:
    return Services(conn, clock)
