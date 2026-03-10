"""Async HTTP/OAuth клиенты для HH API.

Модуль сохраняет parity с sync-клиентом, но добавляет:
- кооперативный rate-limit для конкурентных coroutine;
- bounded retry/backoff для сетевых и временных ошибок;
- lifecycle-метод `aclose()` для корректного закрытия httpx клиента.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import time
from dataclasses import dataclass
from functools import cached_property
from typing import Any, Literal, TypeVar
from urllib.parse import urlencode, urljoin

import httpx
import requests

from hh_applicant_tool.api.client import (
    ANDROID_CLIENT_ID,
    ANDROID_CLIENT_SECRET,
    DEFAULT_DELAY,
    HH_API_URL,
    HH_OAUTH_URL,
)
from hh_applicant_tool.api.user_agent import generate_android_useragent

from . import errors
from .datatypes import AccessToken

AllowedMethods = Literal["GET", "POST", "PUT", "DELETE"]
T = TypeVar("T")

logger = logging.getLogger(__package__)


@dataclass
class AsyncBaseClient:
    """Базовый async HTTP-клиент с rate-limit и retry."""
    base_url: str
    _: dataclasses.KW_ONLY
    user_agent: str | None = None
    client: httpx.AsyncClient | None = None
    delay: float | None = None
    timeout: float = 15.0
    max_retries: int = 2
    backoff_base: float = 0.35
    _next_request_time: float = 0.0

    def __post_init__(self) -> None:
        assert self.base_url.endswith("/"), "base_url must ends with /"
        self.delay = self.delay or DEFAULT_DELAY
        self.user_agent = self.user_agent or generate_android_useragent()
        self._rate_lock = asyncio.Lock()
        self._closed = False
        if self.client is None:
            self.client = httpx.AsyncClient(
                follow_redirects=False,
                timeout=self.timeout,
            )

    async def aclose(self) -> None:
        """Закрывает внутренний `httpx.AsyncClient` и помечает клиент закрытым."""
        if self._closed:
            return
        if self.client is not None:
            await self.client.aclose()
        self._closed = True

    def _default_headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.user_agent or "",
            "X-HH-App-Active": "true",
        }

    async def _apply_rate_limit(self, delay: float | None = None) -> None:
        effective_delay = self.delay if delay is None else delay
        async with self._rate_lock:
            now = time.monotonic()
            # Резервируем слот атомарно под lock, чтобы конкурентные запросы
            # не "съедали" одну и ту же квоту.
            scheduled_at = max(now, self._next_request_time)
            self._next_request_time = scheduled_at + effective_delay

        wait_for = scheduled_at - now
        if wait_for > 0:
            logger.debug("wait %fs before request", wait_for)
            await asyncio.sleep(wait_for)

    @staticmethod
    def _to_requests_response(response: httpx.Response) -> requests.Response:
        req = requests.Request(
            method=response.request.method,
            url=str(response.request.url),
        ).prepare()
        rv = requests.Response()
        rv.status_code = response.status_code
        rv._content = response.content
        rv.headers = requests.structures.CaseInsensitiveDict(response.headers)
        rv.url = str(response.url)
        rv.request = req
        return rv

    async def request(
        self,
        method: AllowedMethods,
        endpoint: str,
        params: dict[str, Any] | None = None,
        delay: float | None = None,
        as_json: bool = False,
        **kwargs: Any,
    ) -> T:
        """Выполняет HTTP-запрос с retry/backoff и валидацией API-ошибок.

        Side effects:
        - обновляет внутренний график rate-limit (`_next_request_time`);
        - может ожидать перед запросом и между повторами.

        Raises:
            errors.BadResponse: если превышены ретраи или невалидный JSON.
            errors.ApiError: если HH API вернуло ошибочный статус.
        """
        assert method in AllowedMethods.__args__
        params = dict(params or {})
        params.update(kwargs)
        url = self.resolve_url(endpoint)
        has_body = method in {"POST", "PUT"}
        payload_key = "json" if as_json and has_body else ("data" if has_body else "params")
        payload = {payload_key: params}
        retries_left = self.max_retries

        while True:
            await self._apply_rate_limit(delay=delay)
            try:
                response = await self.client.request(
                    method=method,
                    url=url,
                    headers=self._default_headers(),
                    **payload,
                )
            except httpx.TimeoutException as ex:
                if retries_left <= 0:
                    raise errors.BadResponse(f"Request timeout: {method} {url}") from ex
                retries_left -= 1
                # Растягиваем интервалы повтора линейным backoff, чтобы
                # снизить burst-нагрузку после кратковременной деградации API.
                await asyncio.sleep(self.backoff_base * (self.max_retries - retries_left))
                continue
            except httpx.HTTPError as ex:
                if retries_left <= 0:
                    raise errors.BadResponse(f"Request error: {method} {url}: {ex}") from ex
                retries_left -= 1
                await asyncio.sleep(self.backoff_base * (self.max_retries - retries_left))
                continue
            if response.status_code in {429, 500, 502, 503, 504} and retries_left > 0:
                # 429/5xx считаем retriable: даем API время восстановиться.
                retries_left -= 1
                await asyncio.sleep(self.backoff_base * (self.max_retries - retries_left))
                continue

            try:
                data = response.json() if response.text else {}
            except json.JSONDecodeError as ex:
                raise errors.BadResponse(
                    f"Can't decode JSON: {method} {url} ({response.status_code})"
                ) from ex

            mapped_response = self._to_requests_response(response)
            errors.ApiError.raise_for_status(mapped_response, data)
            assert 300 > response.status_code >= 200, (
                f"Unexpected status code for {method} {url}: {response.status_code}"
            )
            return data

    async def get(self, *args: Any, **kwargs: Any) -> T:
        return await self.request("GET", *args, **kwargs)

    async def post(self, *args: Any, **kwargs: Any) -> T:
        return await self.request("POST", *args, **kwargs)

    async def put(self, *args: Any, **kwargs: Any) -> T:
        return await self.request("PUT", *args, **kwargs)

    async def delete(self, *args: Any, **kwargs: Any) -> T:
        return await self.request("DELETE", *args, **kwargs)

    def resolve_url(self, url: str) -> str:
        return urljoin(self.base_url, url.lstrip("/"))


@dataclass
class AsyncOAuthClient(AsyncBaseClient):
    """Async OAuth-клиент для получения и обновления access token."""
    client_id: str | None = None
    client_secret: str | None = None
    _: dataclasses.KW_ONLY
    base_url: str = HH_OAUTH_URL
    state: str = ""
    scope: str = ""
    redirect_uri: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        self.client_id = self.client_id or ANDROID_CLIENT_ID
        self.client_secret = self.client_secret or ANDROID_CLIENT_SECRET

    @property
    def authorize_url(self) -> str:
        params = dict(
            client_id=self.client_id,
            redirect_uri=self.redirect_uri,
            response_type="code",
            scope=self.scope,
            state=self.state,
        )
        params_qs = urlencode({k: v for k, v in params.items() if v})
        return self.resolve_url(f"/authorize?{params_qs}")

    async def request_access_token(
        self, endpoint: str, params: dict[str, Any] | None = None, **kw: Any
    ) -> AccessToken:
        """Запрашивает токен и нормализует ответ к формату `AccessToken`."""
        tok = await self.post(endpoint, params, **kw)
        return {
            "access_token": tok.get("access_token"),
            "refresh_token": tok.get("refresh_token"),
            "access_expires_at": int(time.time()) + tok.pop("expires_in", 0),
        }

    async def authenticate(self, code: str) -> AccessToken:
        """Обменивает authorization code на access/refresh токены."""
        params = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": code,
            "grant_type": "authorization_code",
        }
        return await self.request_access_token("/token", params)

    async def refresh_access_token(self, refresh_token: str) -> AccessToken:
        """Обновляет access token по refresh token."""
        return await self.request_access_token(
            "/token",
            grant_type="refresh_token",
            refresh_token=refresh_token,
        )


@dataclass
class AsyncApiClient(AsyncBaseClient):
    """Async API-клиент HH с автоматическим refresh токена."""
    access_token: str | None = None
    refresh_token: str | None = None
    access_expires_at: int = 0
    _: dataclasses.KW_ONLY
    client_id: str | None = None
    client_secret: str | None = None
    base_url: str = HH_API_URL

    @property
    def is_access_expired(self) -> bool:
        """Проверяет, истек ли access token по локальному timestamp."""
        return time.time() >= (self.access_expires_at or 0)

    @cached_property
    def oauth_client(self) -> AsyncOAuthClient:
        return AsyncOAuthClient(
            client_id=self.client_id,
            client_secret=self.client_secret,
            user_agent=self.user_agent,
        )

    def _default_headers(self) -> dict[str, str]:
        headers = super()._default_headers()
        if not self.access_token:
            return headers
        assert self.access_token.startswith("USER")
        return headers | {"authorization": f"Bearer {self.access_token}"}

    async def request(
        self,
        method: AllowedMethods,
        endpoint: str,
        params: dict[str, Any] | None = None,
        delay: float | None = None,
        as_json: bool = False,
        **kwargs: Any,
    ) -> T:
        """Выполняет API-запрос и один раз повторяет после refresh токена.

        Side effects:
        - при `403` и истекшем токене может обновить токены клиента.
        """
        async def do_request() -> T:
            return await AsyncBaseClient.request(
                self, method, endpoint, params, delay, as_json, **kwargs
            )

        try:
            return await do_request()
        except errors.Forbidden as ex:
            if not self.is_access_expired or not self.refresh_token:
                raise ex
            logger.info("try to refresh access_token")
            await self.refresh_access_token()
            return await do_request()

    def handle_access_token(self, token: AccessToken) -> None:
        """Применяет полученные токены к состоянию клиента."""
        for field in ("access_token", "refresh_token", "access_expires_at"):
            if field in token and hasattr(self, field):
                setattr(self, field, token[field])

    async def refresh_access_token(self) -> None:
        """Обновляет токены через OAuth и сохраняет их в клиенте."""
        if not self.refresh_token:
            raise ValueError("Refresh token required.")
        token = await self.oauth_client.refresh_access_token(self.refresh_token)
        self.handle_access_token(token)

    def get_access_token(self) -> AccessToken:
        """Возвращает текущий снимок токенов для внешнего сохранения."""
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "access_expires_at": self.access_expires_at,
        }
