"""API-level auth tests.

Each test receives:
  - `client`    : TestClient wired to the test DB session (no dev-DB side effects).
  - `make_user` : factory for creating in-transaction users.
  - `db_session`: direct DB access for setup/assertions.
"""

from fastapi.testclient import TestClient

from app.core.enums import Role
from app.core.security import create_access_token
from app.modules.accounts.permissions import FINANCE_OPS

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _login(client: TestClient, email: str, password: str = "Test@1234") -> dict:
    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    return resp


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

class TestLogin:
    def test_correct_credentials_returns_200(self, client, make_user):
        make_user(Role.REQUESTOR, email="good@test.com")
        resp = _login(client, "good@test.com")
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"
        assert data["user"]["email"] == "good@test.com"

    def test_wrong_password_gives_401(self, client, make_user):
        make_user(Role.REQUESTOR, email="pw@test.com")
        resp = _login(client, "pw@test.com", password="WrongPass1!")
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "not_authenticated"

    def test_unknown_email_gives_401_same_message(self, client):
        resp = _login(client, "nobody@test.com")
        assert resp.status_code == 401
        assert resp.json()["error"]["message"] == "Invalid email or password"

    def test_inactive_user_gives_401(self, client, make_user):
        make_user(Role.REQUESTOR, email="inactive@test.com", is_active=False)
        resp = _login(client, "inactive@test.com")
        assert resp.status_code == 401

    def test_password_over_72_bytes_gives_400(self, client, make_user):
        make_user(Role.REQUESTOR, email="longpw@test.com")
        resp = _login(client, "longpw@test.com", password="A" * 73)
        assert resp.status_code == 400

    def test_wrong_password_and_unknown_email_same_message(self, client, make_user):
        make_user(Role.REQUESTOR, email="match@test.com")
        r1 = _login(client, "match@test.com", password="BadPass1!")
        r2 = _login(client, "nobody@test.com")
        assert r1.json()["error"]["message"] == r2.json()["error"]["message"]


# ---------------------------------------------------------------------------
# /me
# ---------------------------------------------------------------------------

class TestMe:
    def _get_me(self, client, user_id: int) -> dict:
        token = create_access_token(user_id)
        return client.get("/api/me", headers={"Authorization": f"Bearer {token}"})

    def test_no_token_gives_401(self, client):
        resp = client.get("/api/me")
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "not_authenticated"

    def test_garbage_token_gives_401(self, client):
        resp = client.get("/api/me", headers={"Authorization": "Bearer notavalidtoken"})
        assert resp.status_code == 401

    def test_requestor_permissions(self, client, make_user):
        user = make_user(Role.REQUESTOR, email="me_req@test.com")
        resp = self._get_me(client, user.id)
        assert resp.status_code == 200
        perms = set(resp.json()["permissions"])
        assert "request.create" in perms
        assert "request.approve" not in perms
        assert "request.disburse" not in perms

    def test_approver_permissions(self, client, make_user):
        user = make_user(Role.APPROVER, email="me_app@test.com")
        resp = self._get_me(client, user.id)
        perms = set(resp.json()["permissions"])
        assert "request.approve" in perms
        assert "expense.review" in perms
        assert "request.disburse" not in perms

    def test_finance_always_has_money_actions(self, client, make_user):
        user = make_user(Role.FINANCE, email="me_fin@test.com", can_finance_ops=False)
        resp = self._get_me(client, user.id)
        perms = set(resp.json()["permissions"])
        for p in FINANCE_OPS:
            assert p in perms, f"Finance should always have {p}"

    def test_super_admin_without_flag_has_no_money_actions(self, client, make_user):
        user = make_user(Role.SUPER_ADMIN, email="me_sa_noops@test.com", can_finance_ops=False)
        resp = self._get_me(client, user.id)
        perms = set(resp.json()["permissions"])
        assert "user.manage" in perms
        for p in FINANCE_OPS:
            assert p not in perms, f"Super Admin without flag should NOT have {p}"

    def test_super_admin_with_flag_has_money_actions(self, client, make_user):
        user = make_user(Role.SUPER_ADMIN, email="me_sa_ops@test.com", can_finance_ops=True)
        resp = self._get_me(client, user.id)
        perms = set(resp.json()["permissions"])
        assert "user.manage" in perms
        for p in FINANCE_OPS:
            assert p in perms, f"Super Admin WITH flag should have {p}"

    def test_deactivated_user_old_token_gives_401(self, client, make_user, db_session):
        user = make_user(Role.REQUESTOR, email="deact@test.com")
        # Issue token while active, then deactivate
        token = create_access_token(user.id)
        user.is_active = False
        db_session.flush()
        resp = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------

class TestRefresh:
    def test_refresh_returns_new_access_token(self, client, make_user):
        make_user(Role.REQUESTOR, email="ref@test.com")
        login_resp = _login(client, "ref@test.com")
        refresh_token = login_resp.json()["refresh_token"]

        resp = client.post("/api/auth/refresh", json={"refresh_token": refresh_token})
        assert resp.status_code == 200
        assert "access_token" in resp.json()

    def test_refresh_with_access_token_gives_401(self, client, make_user):
        make_user(Role.REQUESTOR, email="refbad@test.com")
        login_resp = _login(client, "refbad@test.com")
        access_token = login_resp.json()["access_token"]  # wrong type

        resp = client.post("/api/auth/refresh", json={"refresh_token": access_token})
        assert resp.status_code == 401

    def test_refresh_deactivated_user_gives_401(self, client, make_user, db_session):
        user = make_user(Role.REQUESTOR, email="refdeact@test.com")
        login_resp = _login(client, "refdeact@test.com")
        refresh_token = login_resp.json()["refresh_token"]

        user.is_active = False
        db_session.flush()

        resp = client.post("/api/auth/refresh", json={"refresh_token": refresh_token})
        assert resp.status_code == 401
