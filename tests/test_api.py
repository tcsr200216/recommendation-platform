from fastapi.testclient import TestClient

import app.main as main
from app.repository import InMemoryInteractionRepository


class UnavailableRepository(InMemoryInteractionRepository):
    @property
    def backend(self) -> str:
        return "unavailable"

    def is_ready(self) -> bool:
        return False


def test_ready_reports_active_storage_backend(monkeypatch) -> None:
    monkeypatch.setattr(
        main,
        "interaction_repository",
        InMemoryInteractionRepository(),
    )

    response = TestClient(main.app).get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "storage": "memory"}


def test_ready_returns_503_when_repository_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(
        main,
        "interaction_repository",
        UnavailableRepository(),
    )

    response = TestClient(main.app).get("/ready")

    assert response.status_code == 503
    assert response.json()["detail"] == "Interaction repository is unavailable."
