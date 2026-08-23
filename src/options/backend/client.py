"""TSETMC REST API client with authentication and retry logic."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

import requests

from options.backend.api_errors import get_data, is_success, parse_api_error
from options.backend.config import (
    TSETMC_BASE_URL,
    TSETMC_LOGIN_TIMEOUT,
    TSETMC_PASSWORD,
    TSETMC_REQUEST_TIMEOUT,
    TSETMC_TRUST_ENV_PROXY,
    TSETMC_USERNAME,
)
from options.backend.schema import ENDPOINTS, ERROR_BAD_CREDENTIALS, ERROR_CHANGE_PASSWORD, ERROR_RELOGIN

logger = logging.getLogger(__name__)


class TsetmcAPIError(Exception):
    """Raised when the API returns an error response."""

    def __init__(self, message: str, code: Optional[int] = None):
        super().__init__(message)
        self.code = code


class TsetmcClient:
    """Client for api.tsetmc.com REST API."""

    def __init__(
        self,
        username: Optional[str] = None,
        password: Optional[str] = None,
        base_url: Optional[str] = None,
        max_retries: int = 3,
        retry_delay: float = 2.0,
    ):
        self.username = username or TSETMC_USERNAME
        self.password = password or TSETMC_PASSWORD
        self.base_url = (base_url or TSETMC_BASE_URL).rstrip("/")
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self._token: Optional[str] = None
        self._token_expires: Optional[datetime] = None
        self._session = requests.Session()
        self._session.trust_env = TSETMC_TRUST_ENV_PROXY
        self._session.headers.update(
            {
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/json",
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                ),
            }
        )

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _parse_expiry(self, expire_str: str) -> datetime:
        try:
            dt = datetime.fromisoformat(expire_str.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return datetime.now(timezone.utc)

    def _token_valid(self) -> bool:
        if not self._token:
            return False
        if self._token_expires is None:
            return True
        return datetime.now(timezone.utc) < self._token_expires

    def login(self, force: bool = False) -> str:
        if not force and self._token_valid():
            return self._token

        payloads = [
            {"Username": self.username, "Password": self.password},
            {"UserName": self.username, "Password": self.password},
        ]

        last_error: Optional[TsetmcAPIError] = None
        for payload in payloads:
            try:
                response = self._session.post(
                    self._url(ENDPOINTS["login"]),
                    json=payload,
                    timeout=TSETMC_LOGIN_TIMEOUT,
                )
                body = self._parse_json(response)
                if is_success(body):
                    data = get_data(body) or {}
                    token = data.get("token") or data.get("Token")
                    if not token:
                        raise TsetmcAPIError("Login succeeded but no token in response")
                    self._token = token
                    expire = data.get("expireDate") or data.get("ExpireDate")
                    self._token_expires = self._parse_expiry(expire) if expire else None
                    logger.info("Logged in successfully")
                    return self._token
                code, msg = parse_api_error(body)
                if code in ERROR_CHANGE_PASSWORD:
                    raise TsetmcAPIError(
                        f"رمز عبور باید تغییر کند (کد {code}). "
                        "از scripts/change_password.py استفاده کنید.",
                        code=code,
                    )
                raise TsetmcAPIError(f"ورود ناموفق: {msg}", code=code)
            except requests.Timeout as exc:
                last_error = TsetmcAPIError(
                    f"اتصال به TSETMC در {TSETMC_LOGIN_TIMEOUT:.0f} ثانیه برقرار نشد. "
                    "اتصال اینترنت، VPN/Proxy یا دسترسی به api.tsetmc.com را بررسی کنید."
                )
                logger.warning("Login timed out: %s", exc)
                continue
            except requests.RequestException as exc:
                last_error = TsetmcAPIError(f"خطای ارتباط با TSETMC: {exc}")
                continue
            except TsetmcAPIError as exc:
                if exc.code in ERROR_BAD_CREDENTIALS or exc.code in ERROR_CHANGE_PASSWORD:
                    raise
                last_error = exc
                continue

        raise last_error or TsetmcAPIError("ورود ناموفق")

    def change_password(self, new_password: str) -> bool:
        response = self._session.post(
            self._url(ENDPOINTS["change_password"]),
            json={
                "Username": self.username,
                "Password": self.password,
                "NewPassword": new_password,
            },
            timeout=60,
        )
        body = self._parse_json(response)
        if not is_success(body):
            code, msg = parse_api_error(body)
            raise TsetmcAPIError(f"تغییر رمز ناموفق: {msg}", code=code)
        self.password = new_password
        self._token = None
        return True

    def _auth_headers(self) -> dict[str, str]:
        token = self.login()
        return {"Authorization": f"Bearer {token}"}

    def _parse_json(self, response: requests.Response) -> dict[str, Any]:
        if response.status_code == 429:
            raise TsetmcAPIError(
                "محدودیت درخواست (429). چند دقیقه صبر کنید و دوباره تلاش کنید.",
                code=429,
            )
        try:
            return response.json()
        except ValueError:
            raise TsetmcAPIError(
                f"پاسخ نامعتبر از سرور (وضعیت {response.status_code}): {response.text[:200]}"
            )

    def _extract_data(self, body: dict[str, Any]) -> Any:
        if not is_success(body):
            code, msg = parse_api_error(body)
            if code in ERROR_RELOGIN:
                self._token = None
            raise TsetmcAPIError(msg, code=code)
        return get_data(body)

    def post(
        self,
        path: str,
        json_body: Optional[dict[str, Any]] = None,
        authenticated: bool = True,
    ) -> Any:
        headers = self._auth_headers() if authenticated else {}
        url = self._url(path)

        for attempt in range(self.max_retries):
            try:
                response = self._session.post(
                    url,
                    json=json_body or {},
                    headers=headers,
                    timeout=TSETMC_REQUEST_TIMEOUT,
                )

                if response.status_code in (401, 403):
                    self._token = None
                    headers = self._auth_headers()
                    if attempt < self.max_retries - 1:
                        time.sleep(self.retry_delay)
                        continue

                if response.status_code >= 500:
                    if attempt < self.max_retries - 1:
                        time.sleep(self.retry_delay * (attempt + 1))
                        continue
                    raise TsetmcAPIError(f"Server error {response.status_code}")

                body = self._parse_json(response)
                return self._extract_data(body)

            except requests.Timeout as exc:
                if attempt < self.max_retries - 1:
                    logger.warning("Request timed out (attempt %s): %s", attempt + 1, exc)
                    time.sleep(self.retry_delay * (attempt + 1))
                    continue
                raise TsetmcAPIError(
                    f"اتصال به TSETMC در {TSETMC_REQUEST_TIMEOUT:.0f} ثانیه پاسخ نداد. "
                    "بعداً دوباره تلاش کنید یا وضعیت شبکه/VPN را بررسی کنید."
                ) from exc
            except requests.RequestException as exc:
                if attempt < self.max_retries - 1:
                    logger.warning("Request failed (attempt %s): %s", attempt + 1, exc)
                    time.sleep(self.retry_delay * (attempt + 1))
                    continue
                raise TsetmcAPIError(f"خطای ارتباط با TSETMC: {exc}") from exc
            except TsetmcAPIError as exc:
                if exc.code in ERROR_RELOGIN and attempt < self.max_retries - 1:
                    headers = self._auth_headers()
                    time.sleep(self.retry_delay)
                    continue
                raise

        raise TsetmcAPIError("Max retries exceeded")

    def call(self, endpoint_key: str, json_body: Optional[dict[str, Any]] = None) -> Any:
        path = ENDPOINTS.get(endpoint_key)
        if not path:
            raise ValueError(f"Unknown endpoint key: {endpoint_key}")
        return self.post(path, json_body=json_body)
