"""QA DB는 MongoDB만 허용하고 이전 관계형 연결 주소를 거절한다."""
import pytest

from ticket_guard_qa.qa_database.database import QaDatabaseConfigError, connect_qa_database


@pytest.mark.parametrize("database_url", [
    "postgresql://localhost/ticketguard_qa",
    "postgresql+psycopg://localhost/ticketguard_qa",
])
def test_postgres_url_is_rejected_with_mongodb_setup_hint(database_url):
    with pytest.raises(QaDatabaseConfigError, match="compose.qa-db.yml"):
        connect_qa_database(database_url)
