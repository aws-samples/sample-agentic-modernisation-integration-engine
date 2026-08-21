"""Tests for authentication middleware and routes."""

from unittest.mock import patch

from fastapi.testclient import TestClient
from jose import jwt

from main import app

client = TestClient(app)


class TestAuthConfig:
    """Tests for GET /api/auth/config."""

    def test_auth_config_disabled_mode(self):
        """Returns disabled mode when AUTH_DISABLED=true (default)."""
        with patch("middleware.auth_routes.settings") as mock_settings:
            mock_settings.AUTH_DISABLED = True
            mock_settings.LOCAL_AUTH_SECRET = ""
            mock_settings.COGNITO_USER_POOL_ID = ""
            mock_settings.COGNITO_CLIENT_ID = ""
            response = client.get("/api/auth/config")
            assert response.status_code == 200
            data = response.json()
            assert data["mode"] == "disabled"

    def test_auth_config_local_mode(self):
        """Returns local mode when LOCAL_AUTH_SECRET is set."""
        with patch("middleware.auth_routes.settings") as mock_settings:
            mock_settings.AUTH_DISABLED = False
            mock_settings.LOCAL_AUTH_SECRET = "test-secret"
            mock_settings.COGNITO_USER_POOL_ID = ""
            mock_settings.COGNITO_CLIENT_ID = ""
            response = client.get("/api/auth/config")
            assert response.status_code == 200
            data = response.json()
            assert data["mode"] == "local"

    def test_auth_config_cognito_mode(self):
        """Returns cognito mode when pool+client are set."""
        with patch("middleware.auth_routes.settings") as mock_settings:
            mock_settings.AUTH_DISABLED = False
            mock_settings.LOCAL_AUTH_SECRET = ""
            mock_settings.COGNITO_USER_POOL_ID = "us-east-1_abc"
            mock_settings.COGNITO_CLIENT_ID = "client123"
            response = client.get("/api/auth/config")
            assert response.status_code == 200
            data = response.json()
            assert data["mode"] == "cognito"
            assert data["cognito_user_pool_id"] == "us-east-1_abc"
            assert data["cognito_client_id"] == "client123"


class TestLogin:
    """Tests for POST /api/auth/login."""

    def test_login_disabled_mode(self):
        """Login returns a token in disabled mode."""
        with (
            patch("middleware.auth_routes.settings") as mock_routes,
            patch("middleware.security.settings") as mock_sec,
        ):
            mock_routes.AUTH_DISABLED = True
            mock_routes.LOCAL_AUTH_SECRET = ""
            mock_routes.COGNITO_USER_POOL_ID = ""
            mock_routes.COGNITO_CLIENT_ID = ""
            mock_sec.AUTH_DISABLED = True
            mock_sec.LOCAL_AUTH_SECRET = ""
            mock_sec.COGNITO_USER_POOL_ID = ""
            mock_sec.COGNITO_CLIENT_ID = ""

            response = client.post(
                "/api/auth/login",
                json={"username": "testuser", "password": "pass"},
            )
            assert response.status_code == 200
            data = response.json()
            assert "access_token" in data
            assert data["token_type"] == "bearer"

    def test_login_local_mode_returns_valid_jwt(self):
        """Login in local mode returns a decodable JWT with correct claims."""
        secret = "my-local-secret"
        with (
            patch("middleware.auth_routes.settings") as mock_routes,
            patch("middleware.security.settings") as mock_sec,
        ):
            mock_routes.AUTH_DISABLED = False
            mock_routes.LOCAL_AUTH_SECRET = secret
            mock_routes.COGNITO_USER_POOL_ID = ""
            mock_routes.COGNITO_CLIENT_ID = ""
            mock_sec.AUTH_DISABLED = False
            mock_sec.LOCAL_AUTH_SECRET = secret
            mock_sec.COGNITO_USER_POOL_ID = ""
            mock_sec.COGNITO_CLIENT_ID = ""

            response = client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "admin123"},
            )
            assert response.status_code == 200
            token = response.json()["access_token"]

            # Decode and verify claims
            payload = jwt.decode(token, secret, algorithms=["HS256"])
            assert payload["sub"] == "admin"
            assert payload["role"] == "admin"
            assert "exp" in payload
            assert "iat" in payload

    def test_login_cognito_mode_returns_400(self):
        """Login in cognito mode returns 400 (must use hosted UI)."""
        with (
            patch("middleware.auth_routes.settings") as mock_routes,
            patch("middleware.security.settings") as mock_sec,
        ):
            mock_routes.AUTH_DISABLED = False
            mock_routes.LOCAL_AUTH_SECRET = ""
            mock_routes.COGNITO_USER_POOL_ID = "us-east-1_pool"
            mock_routes.COGNITO_CLIENT_ID = "client123"
            mock_sec.AUTH_DISABLED = False
            mock_sec.LOCAL_AUTH_SECRET = ""
            mock_sec.COGNITO_USER_POOL_ID = "us-east-1_pool"
            mock_sec.COGNITO_CLIENT_ID = "client123"

            response = client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "pass"},
            )
            assert response.status_code == 400
            assert "Cognito" in response.json()["detail"]


class TestAuthMiddleware:
    """Tests for AuthMiddleware behavior."""

    def test_disabled_mode_passes_all_requests(self):
        """In disabled mode, all endpoints are accessible without auth."""
        with patch("middleware.security.settings") as mock_sec:
            mock_sec.AUTH_DISABLED = True
            mock_sec.LOCAL_AUTH_SECRET = ""
            mock_sec.COGNITO_USER_POOL_ID = ""
            mock_sec.COGNITO_CLIENT_ID = ""
            response = client.get("/health")
            assert response.status_code == 200

    def test_local_mode_rejects_unauthenticated(self):
        """In local mode, non-public paths without token return 401."""
        with patch("middleware.security.settings") as mock_sec:
            mock_sec.AUTH_DISABLED = False
            mock_sec.LOCAL_AUTH_SECRET = "secret"
            mock_sec.COGNITO_USER_POOL_ID = ""
            mock_sec.COGNITO_CLIENT_ID = ""
            response = client.get("/api/auth/user")
            assert response.status_code == 401

    def test_local_mode_accepts_valid_token(self):
        """In local mode, valid token grants access."""
        secret = "test-secret-val"
        with (
            patch("middleware.auth_routes.settings") as mock_routes,
            patch("middleware.security.settings") as mock_sec,
        ):
            mock_routes.AUTH_DISABLED = False
            mock_routes.LOCAL_AUTH_SECRET = secret
            mock_routes.COGNITO_USER_POOL_ID = ""
            mock_routes.COGNITO_CLIENT_ID = ""
            mock_sec.AUTH_DISABLED = False
            mock_sec.LOCAL_AUTH_SECRET = secret
            mock_sec.COGNITO_USER_POOL_ID = ""
            mock_sec.COGNITO_CLIENT_ID = ""

            # Login to get a token
            login_resp = client.post(
                "/api/auth/login",
                json={"username": "devuser", "password": "pw"},
            )
            token = login_resp.json()["access_token"]

            # Use token to access protected endpoint
            response = client.get(
                "/api/auth/user",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["sub"] == "devuser"
            assert data["username"] == "devuser"

    def test_local_mode_rejects_invalid_token(self):
        """In local mode, invalid token returns 401."""
        with patch("middleware.security.settings") as mock_sec:
            mock_sec.AUTH_DISABLED = False
            mock_sec.LOCAL_AUTH_SECRET = "secret"
            mock_sec.COGNITO_USER_POOL_ID = ""
            mock_sec.COGNITO_CLIENT_ID = ""
            response = client.get(
                "/api/auth/user",
                headers={"Authorization": "Bearer invalid.token.here"},
            )
            assert response.status_code == 401

    def test_public_paths_bypass_auth(self):
        """Public paths are accessible without auth in local mode."""
        with patch("middleware.security.settings") as mock_sec:
            mock_sec.AUTH_DISABLED = False
            mock_sec.LOCAL_AUTH_SECRET = "secret"
            mock_sec.COGNITO_USER_POOL_ID = ""
            mock_sec.COGNITO_CLIENT_ID = ""
            # /api/auth/config is public
            response = client.get("/api/auth/config")
            assert response.status_code == 200
            # /health is public
            response = client.get("/health")
            assert response.status_code == 200
