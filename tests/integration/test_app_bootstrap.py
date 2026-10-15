"""앱 시작 시 DB 부트스트랩: 신규 생성, 재실행 시 기존 데이터 유지."""

from rentdesk.app import bootstrap_database
from rentdesk.persistence.db import latest_schema_version


def test_bootstrap_creates_db_and_reopens_without_data_loss(tmp_path):
    db_path = tmp_path / "앱 데이터" / "data" / "rentdesk.db"
    db_path.parent.mkdir(parents=True)
    backup_dir = tmp_path / "backups"

    conn = bootstrap_database(db_path, backup_dir)
    conn.execute("INSERT INTO categories (name) VALUES ('카메라')")
    conn.close()

    again = bootstrap_database(db_path, backup_dir)
    try:
        assert again.execute("SELECT name FROM categories").fetchall()[0][0] == "카메라"
        version = again.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        assert version == latest_schema_version()
    finally:
        again.close()
    # 적용할 마이그레이션이 없으면 보호 백업도 만들지 않는다
    assert not backup_dir.exists() or not any(backup_dir.iterdir())
