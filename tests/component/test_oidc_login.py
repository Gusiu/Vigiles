"""UI login through the identity provider (Authorization Code + PKCE),
against a minimal stand-in of the Keycloak token endpoint."""

import base64
import hashlib
import json
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
from pytest_httpserver import HTTPServer
from werkzeug import Request, Response

from harness import tokens


def test_authorization_code_with_pkce(seeded_db, httpserver: HTTPServer) -> None:
    from harness.registry import registry_settings
    from vigiles_registry.app import create_app

    seen: dict = {}

    def token_endpoint(request: Request) -> Response:
        seen.update(request.form)
        seen["auth"] = request.headers.get("Authorization")
        access = tokens._issuer.user_token("anna.kierownik", ["kierownik"], "registry-api")
        return Response(json.dumps({"access_token": access, "token_type": "Bearer", "expires_in": 300}),
                        content_type="application/json")

    httpserver.expect_request("/realm/protocol/openid-connect/token", method="POST").respond_with_handler(token_endpoint)
    settings = registry_settings(seeded_db.owner_url, auth_mode="oidc", oidc_issuer=tokens.ISSUER, oidc_jwks_url="http://unused.invalid/certs",
                                 oidc_public_url="http://idp.example/realm",
                                 oidc_token_url=httpserver.url_for("/realm/protocol/openid-connect/token"),
                                 oidc_ui_secret_registry="ui-secret")
    app = create_app(settings)
    app.state.verifier = tokens.verifier("registry-api")
    with TestClient(app) as client:
        login = client.get("/login?next=/ui/conflicts", follow_redirects=False)
        assert login.status_code == 302
        location = urlparse(login.headers["location"])
        assert location.netloc == "idp.example" and location.path == "/realm/protocol/openid-connect/auth"
        query = parse_qs(location.query)
        assert query["client_id"] == ["registry-ui"] and query["code_challenge_method"] == ["S256"]

        callback = client.get(f"/auth/callback?code=abc&state={query['state'][0]}", follow_redirects=False)
        assert callback.status_code == 303 and callback.headers["location"] == "/ui/conflicts"
        verifier = seen["code_verifier"]
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        assert challenge == query["code_challenge"][0]
        assert seen["code"] == "abc" and seen["auth"].startswith("Basic ")

        page = client.get("/ui/conflicts")
        assert page.status_code == 200 and "anna.kierownik" in page.text and "demo mode" not in page.text
        # The demo picker is not available with a real identity provider.
        assert client.post("/login", data={"user": "admin"}).status_code == 405
