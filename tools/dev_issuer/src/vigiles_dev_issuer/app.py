"""HTTP endpoints of the dev issuer: discovery, JWKS and the client
credentials grant with the client secrets from the environment."""

import os
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import JSONResponse

from vigiles_dev_issuer import SERVICE_CLIENTS, Issuer

TOKEN_LIFETIME_S = int(os.environ.get("DEV_ISSUER_TOKEN_LIFETIME_S", "300"))


def create_app() -> FastAPI:
    issuer = Issuer.load_or_create(os.environ["OIDC_ISSUER"], Path(os.environ.get("DEV_ISSUER_KEY", ".local/dev_issuer.pem")))
    app = FastAPI(title="Vigiles dev issuer")
    app.state.issuer = issuer

    @app.get("/.well-known/openid-configuration")
    def discovery() -> dict:
        return {"issuer": issuer.issuer, "jwks_uri": f"{issuer.issuer}/protocol/openid-connect/certs",
                "token_endpoint": f"{issuer.issuer}/protocol/openid-connect/token",
                "id_token_signing_alg_values_supported": ["RS256"]}

    @app.get("/protocol/openid-connect/certs")
    def certs() -> dict:
        return issuer.jwks()

    @app.post("/protocol/openid-connect/token")
    def token(request: Request, grant_type: str = Form(...)) -> JSONResponse:
        import base64

        if grant_type != "client_credentials":
            return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)
        scheme, _, value = request.headers.get("authorization", "").partition(" ")
        try:
            client_id, _, secret = base64.b64decode(value).decode().partition(":")
        except ValueError:
            client_id, secret = "", ""
        expected = os.environ.get(f"OIDC_SECRET_{client_id.upper().replace('-', '_')}")
        if scheme.lower() != "basic" or client_id not in SERVICE_CLIENTS or not expected or secret != expected:
            return JSONResponse({"error": "invalid_client"}, status_code=401)
        return JSONResponse({"access_token": issuer.service_token(client_id, TOKEN_LIFETIME_S), "token_type": "Bearer",
                             "expires_in": TOKEN_LIFETIME_S})

    @app.get("/health/live")
    def live() -> dict:
        return {"status": "ok"}

    return app


def main() -> None:
    import uvicorn
    from dotenv import load_dotenv

    load_dotenv(".env")  # same settings source as the services; the environment wins

    uvicorn.run(create_app(), host="127.0.0.1", port=int(os.environ.get("DEV_ISSUER_PORT", "8006")), log_config=None)
