"""정답표 저장 · 읽기 · QA DB 전용 · Run 하나 = answer_keys 문서 하나"""
import re

from pymongo.database import Database

from ticket_guard_qa.domain.enums import ActorType
from ticket_guard_qa.domain.models import AnswerKey, SessionKey
from ticket_guard_qa.qa_database.database import ANSWER_KEYS

SUITE_ANSWER_KEY_SEPARATOR = "/"  # run-…/detection_regression


class AnswerKeyRepository:
    def __init__(self, qa_database: Database) -> None:
        self.answer_keys = qa_database[ANSWER_KEYS]

    def save(self, run_id: str, answer_key: AnswerKey) -> None:
        entries = [{"session_key": key, "actor_type": actor_type.value} for key, actor_type in answer_key.items()]
        self.answer_keys.replace_one({"_id": run_id}, {"_id": run_id, "entries": entries}, upsert=True)

    def save_for_suite(self, run_id: str, suite_name: str, answer_key: AnswerKey) -> None:
        """판정 정확도 밖 Suite(탐지 회귀 · 장애 중 탐지) 정답표 · 같은 Run 안에서 따로 · B4 · 05는 Run 정답표만 봄"""
        self.save(f"{run_id}{SUITE_ANSWER_KEY_SEPARATOR}{suite_name}", answer_key)

    def load_for_suite(self, run_id: str, suite_name: str) -> AnswerKey:
        return self.load(f"{run_id}{SUITE_ANSWER_KEY_SEPARATOR}{suite_name}")

    def delete(self, run_id: str) -> None:
        self.answer_keys.delete_many({"_id": {"$regex": f"^{re.escape(run_id)}({re.escape(SUITE_ANSWER_KEY_SEPARATOR)}.*)?$"}})

    def load(self, run_id: str) -> AnswerKey:
        document = self.answer_keys.find_one({"_id": run_id}) or {}
        return {SessionKey(entry["session_key"]): ActorType(entry["actor_type"]) for entry in document.get("entries", [])}
