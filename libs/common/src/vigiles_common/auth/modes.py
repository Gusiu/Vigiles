"""Selection of the token verifier from ``AUTH_MODE``."""

from vigiles_common.auth.core import StaticTokenVerifier, TokenVerifier, parse_service_tokens


def build_verifier(mode: str, service_tokens: str = "") -> TokenVerifier:
    if mode == "static":
        tokens = parse_service_tokens(service_tokens)
        if not tokens:
            raise ValueError("AUTH_MODE=static requires SERVICE_TOKENS")
        return StaticTokenVerifier(tokens)
    raise ValueError(f"AUTH_MODE={mode} is not supported yet")
