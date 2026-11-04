"""HTTP client of the registry API used by the sync engine."""

from typing import Any

import httpx

from vigiles_common.auth.core import TokenProvider
from vigiles_common.correlation import HEADER, current_correlation_id, new_correlation_id


class RegistryError(Exception):
    def __init__(self, status: int | None, message: str, retry_after: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class RegistryClient:
    def __init__(self, base_url: str, tokens: TokenProvider, timeout_s: float = 5.0,
                 http: httpx.Client | None = None) -> None:
        self.http = http or httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout_s)
        self.tokens = tokens

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        headers = {
            "Authorization": f"Bearer {self.tokens.token()}",
            HEADER: current_correlation_id() or new_correlation_id(),
        }
        response = self.http.request(method, path, headers=headers, **kwargs)
        if response.status_code == 401:
            self.tokens.invalidate()
        if not response.is_success:
            raise RegistryError(response.status_code, f"HTTP {response.status_code}: {response.text[:300]}",
                                response.headers.get("retry-after"))
        return response.json()

    def changes(self, since: int, limit: int = 500) -> dict[str, Any]:
        return self._request("GET", "/api/v1/sync/changes", params={"since": since, "limit": limit})

    def push(self, changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self._request("POST", "/api/v1/sync/push", json={"changes": changes})["results"]
