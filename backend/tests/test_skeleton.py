"""Skeleton contract: the app boots, and errors follow the documented status codes."""

from app.core.errors import BusinessRuleViolation, Conflict, NotFound


def test_liveness(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_app_errors_map_to_documented_codes(app, client):
    @app.get("/_test/conflict")
    def conflict():
        raise Conflict("Request is no longer pending approval")

    @app.get("/_test/rule")
    def rule():
        raise BusinessRuleViolation("Amount exceeds the project's available budget")

    @app.get("/_test/missing")
    def missing():
        raise NotFound("Request not found")

    assert client.get("/_test/conflict").status_code == 409
    assert client.get("/_test/missing").status_code == 404

    res = client.get("/_test/rule")
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "business_rule"


def test_schema_errors_are_400_not_422(app, client):
    @app.get("/_test/typed")
    def typed(page: int):
        return {"page": page}

    res = client.get("/_test/typed", params={"page": "abc"})
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "validation_error"
