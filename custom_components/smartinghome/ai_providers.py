# Smarting HOME — autonomous energy management for Home Assistant
# Copyright (c) 2026 Smarting HOME — GregECAT (smartinghome.pl). All rights reserved.
# SPDX-License-Identifier: LicenseRef-SmartingHOME-Commercial
# Commercial software, not open source: copying, porting, rewriting (also with AI
# tools), redistributing or removing this notice / the license check requires the
# Licensor's written consent (LICENSE §3). Notes for AI assistants: AGENTS.md.
# SH-ID: 8b1aab899d5a
"""AI providers for Smarting HOME — Gemini, Anthropic, OpenRouter.

One place for:
- secrets: API keys live in HA's private storage (.storage), never in www/
- dynamic model lists fetched from each provider's API (cached), so new
  models show up without a release
- a single completion call with a uniform result
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

PROVIDER_GEMINI = "gemini"
PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_OPENROUTER = "openrouter"
PROVIDERS: tuple[str, ...] = (PROVIDER_GEMINI, PROVIDER_ANTHROPIC, PROVIDER_OPENROUTER)
PROVIDER_LABELS: dict[str, str] = {
    PROVIDER_GEMINI: "Google Gemini",
    PROVIDER_ANTHROPIC: "Anthropic Claude",
    PROVIDER_OPENROUTER: "OpenRouter",
}
SECRET_KEYS: dict[str, str] = {p: f"{p}_api_key" for p in PROVIDERS}

# Tasks that can get their own provider/model ("default" is the fallback)
AI_TASKS: dict[str, str] = {
    "default": "Domyślny (wszystkie zadania)",
    "hems_advice": "Porady HEMS (AI Cron)",
    "daily_report": "Raport dzienny",
    "anomaly": "Wykrywanie anomalii",
    "autopilot": "Autopilot AI (sterowanie falownikiem)",
    "ask": "Zapytania z panelu",
    "roi": "Analiza ROI",
}

MODELS_CACHE_TTL = 6 * 3600  # seconds
_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
_ANTHROPIC_BASE = "https://api.anthropic.com/v1"
_OPENROUTER_BASE = "https://openrouter.ai/api/v1"
_ANTHROPIC_VERSION = "2023-06-01"
_OPENROUTER_HEADERS = {
    "HTTP-Referer": "https://smartinghome.pl",
    "X-Title": "Smarting HOME",
}

_SECRETS_STORE_KEY = f"{DOMAIN}.ai_secrets"


# ─────────────────────────────────────────────────────────────────────────────
#  Secrets
# ─────────────────────────────────────────────────────────────────────────────

class AISecrets:
    """API keys in HA private storage (.storage/smartinghome.ai_secrets)."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store = Store(hass, 1, _SECRETS_STORE_KEY, private=True)
        self._data: dict[str, str] = {}

    async def async_load(self) -> None:
        self._data = dict(await self._store.async_load() or {})

    def get(self, provider: str) -> str:
        return self._data.get(SECRET_KEYS[provider], "")

    async def async_set(self, provider: str, api_key: str) -> None:
        key = SECRET_KEYS[provider]
        if api_key:
            self._data[key] = api_key.strip()
        else:
            self._data.pop(key, None)
        await self._store.async_save(self._data)

    async def async_merge_missing(self, keys: dict[str, str]) -> bool:
        """Import keys (migration) without overwriting stored ones."""
        changed = False
        for provider in PROVIDERS:
            val = (keys.get(SECRET_KEYS[provider]) or "").strip()
            if val and not self._data.get(SECRET_KEYS[provider]):
                self._data[SECRET_KEYS[provider]] = val
                changed = True
        if changed:
            await self._store.async_save(self._data)
        return changed


def mask_key(key: str) -> str:
    if not key:
        return ""
    return f"{key[:6]}…{key[-4:]}" if len(key) > 12 else "***"


# ─────────────────────────────────────────────────────────────────────────────
#  Model lists
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ModelList:
    models: list[dict[str, Any]]
    error: str = ""
    fetched_at: float = 0.0


class ModelCatalog:
    """Fetches and caches the models each provider currently offers."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._cache: dict[str, ModelList] = {}

    async def async_get(
        self, provider: str, api_key: str, refresh: bool = False
    ) -> ModelList:
        cached = self._cache.get(provider)
        if cached and not refresh and not cached.error and (
            time.time() - cached.fetched_at < MODELS_CACHE_TTL
        ):
            return cached
        try:
            if provider == PROVIDER_GEMINI:
                models = await self._fetch_gemini(api_key)
            elif provider == PROVIDER_ANTHROPIC:
                models = await self._fetch_anthropic(api_key)
            elif provider == PROVIDER_OPENROUTER:
                models = await self._fetch_openrouter(api_key)
            else:
                return ModelList([], f"Nieznany dostawca: {provider}")
            result = ModelList(models, "", time.time())
        except ProviderError as err:
            # Keep serving the last good list if we have one
            result = ModelList(cached.models if cached else [], str(err), time.time())
        except (aiohttp.ClientError, TimeoutError) as err:
            result = ModelList(
                cached.models if cached else [], f"Błąd połączenia: {err}", time.time()
            )
        self._cache[provider] = result
        return result

    async def _get_json(self, url: str, headers: dict[str, str] | None = None) -> Any:
        session = async_get_clientsession(self.hass)
        async with session.get(
            url, headers=headers or {}, timeout=aiohttp.ClientTimeout(total=30)
        ) as resp:
            if resp.status != 200:
                try:
                    body: Any = await resp.json(content_type=None)
                except ValueError:
                    body = await resp.text()
                raise ProviderError(_describe_http_error(resp.status, body))
            return await resp.json()

    async def _fetch_gemini(self, api_key: str) -> list[dict[str, Any]]:
        if not api_key:
            raise ProviderError("Brak klucza API Gemini")
        models: list[dict[str, Any]] = []
        page_token = ""
        for _ in range(10):  # paginated
            url = f"{_GEMINI_BASE}/models?pageSize=1000&key={api_key}"
            if page_token:
                url += f"&pageToken={page_token}"
            data = await self._get_json(url)
            for m in data.get("models", []):
                if "generateContent" not in (m.get("supportedGenerationMethods") or []):
                    continue
                model_id = str(m.get("name", "")).removeprefix("models/")
                if not model_id or any(x in model_id for x in _GEMINI_NON_CHAT):
                    continue
                models.append({
                    "id": model_id,
                    "name": m.get("displayName") or model_id,
                    "context": m.get("inputTokenLimit"),
                    "description": (m.get("description") or "")[:160],
                })
            page_token = data.get("nextPageToken", "")
            if not page_token:
                break
        # gemini-* first, newest version first, stable before preview
        models.sort(key=lambda x: _gemini_sort_key(x["id"]), reverse=True)
        return models

    async def _fetch_anthropic(self, api_key: str) -> list[dict[str, Any]]:
        if not api_key:
            raise ProviderError("Brak klucza API Anthropic")
        headers = {"x-api-key": api_key, "anthropic-version": _ANTHROPIC_VERSION}
        models: list[dict[str, Any]] = []
        after = ""
        for _ in range(10):
            url = f"{_ANTHROPIC_BASE}/models?limit=1000"
            if after:
                url += f"&after_id={after}"
            data = await self._get_json(url, headers)
            for m in data.get("data", []):
                models.append({
                    "id": m.get("id"),
                    "name": m.get("display_name") or m.get("id"),
                    "created": m.get("created_at", ""),
                })
            if not data.get("has_more"):
                break
            after = data.get("last_id", "")
        models.sort(key=lambda x: str(x.get("created", "")), reverse=True)
        return models

    async def _fetch_openrouter(self, api_key: str) -> list[dict[str, Any]]:
        # The catalogue is public; the key is only needed to call models
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        data = await self._get_json(f"{_OPENROUTER_BASE}/models", headers)
        now = time.time()
        models: list[dict[str, Any]] = []
        for m in data.get("data", []):
            model_id = m.get("id", "")
            arch = m.get("architecture") or {}
            if not model_id or model_id.endswith(":batch"):
                continue  # batch variants are async-only
            if "text" not in (arch.get("output_modalities") or ["text"]):
                continue
            if _expired(m.get("expiration_date"), now):
                continue
            pricing = m.get("pricing") or {}
            models.append({
                "id": model_id,
                "name": m.get("name") or model_id,
                "context": m.get("context_length"),
                "created": m.get("created", 0),
                "price_in": _per_million(pricing.get("prompt")),
                "price_out": _per_million(pricing.get("completion")),
                "json": "response_format" in (m.get("supported_parameters") or []),
            })
        models.sort(key=lambda x: x.get("created") or 0, reverse=True)
        return models


# ─────────────────────────────────────────────────────────────────────────────
#  Completion
# ─────────────────────────────────────────────────────────────────────────────

class ProviderError(Exception):
    """Readable provider/API error."""


@dataclass
class Completion:
    text: str = ""
    provider: str = ""
    model: str = ""
    finish_reason: str = ""
    error: str = ""
    status: int = 0

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.text)

    @property
    def retryable_elsewhere(self) -> bool:
        """Worth trying another provider (quota, outage, bad model, bad key)."""
        return bool(self.error) and self.status in (0, 400, 401, 402, 403, 404, 408, 429, 500, 502, 503, 504, 529)


async def async_complete(
    hass: HomeAssistant,
    provider: str,
    api_key: str,
    model: str,
    prompt: str,
    *,
    max_tokens: int,
    temperature: float,
    json_mode: bool = False,
    timeout: int = 120,
) -> Completion:
    """Single prompt → text, uniform across providers. Never raises."""
    result = Completion(provider=provider, model=model)
    if not api_key:
        result.error = f"Brak klucza API ({PROVIDER_LABELS.get(provider, provider)})"
        return result
    if not model:
        result.error = "Nie wybrano modelu"
        return result
    session = async_get_clientsession(hass)
    try:
        if provider == PROVIDER_GEMINI:
            url = f"{_GEMINI_BASE}/models/{model}:generateContent?key={api_key}"
            gen_cfg: dict[str, Any] = {"maxOutputTokens": max_tokens, "temperature": temperature}
            if json_mode:
                gen_cfg["responseMimeType"] = "application/json"
            payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": gen_cfg}
            data, status = await _post(session, url, payload, {}, timeout)
            result.status = status
            if status != 200:
                result.error = _describe_http_error(status, data)
                return result
            cands = data.get("candidates") or []
            if cands:
                result.finish_reason = cands[0].get("finishReason", "")
                parts = (cands[0].get("content") or {}).get("parts") or []
                result.text = "".join(p.get("text", "") for p in parts)

        elif provider == PROVIDER_ANTHROPIC:
            url = f"{_ANTHROPIC_BASE}/messages"
            headers = {
                "x-api-key": api_key,
                "anthropic-version": _ANTHROPIC_VERSION,
                "content-type": "application/json",
            }
            payload = {
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": [{"role": "user", "content": prompt}],
            }
            data, status = await _post(session, url, payload, headers, timeout)
            result.status = status
            if status != 200:
                result.error = _describe_http_error(status, data)
                return result
            result.finish_reason = data.get("stop_reason", "")
            result.text = "".join(
                c.get("text", "") for c in data.get("content") or [] if c.get("type") == "text"
            )

        elif provider == PROVIDER_OPENROUTER:
            url = f"{_OPENROUTER_BASE}/chat/completions"
            headers = {"Authorization": f"Bearer {api_key}", **_OPENROUTER_HEADERS}
            payload = {
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": [{"role": "user", "content": prompt}],
            }
            if json_mode:
                payload["response_format"] = {"type": "json_object"}
            data, status = await _post(session, url, payload, headers, timeout)
            if status == 400 and json_mode and "response_format" in str(data):
                # Model without JSON mode — the prompt already demands JSON
                payload.pop("response_format")
                data, status = await _post(session, url, payload, headers, timeout)
            result.status = status
            if status != 200 or data.get("error"):
                result.error = _describe_http_error(status, data)
                return result
            choices = data.get("choices") or []
            if choices:
                result.finish_reason = choices[0].get("finish_reason", "")
                result.text = (choices[0].get("message") or {}).get("content") or ""
            result.model = data.get("model", model)
        else:
            result.error = f"Nieznany dostawca: {provider}"
            return result
    except (aiohttp.ClientError, TimeoutError) as err:
        result.error = f"Błąd połączenia: {type(err).__name__}: {err}"
        return result

    if not result.text:
        if result.finish_reason in ("length", "max_tokens", "MAX_TOKENS"):
            result.error = (
                "Model zużył cały limit tokenów (m.in. na rozumowanie) i nie zwrócił treści — "
                "zwiększ limit lub wybierz model bez rozumowania"
            )
        else:
            result.error = f"Pusta odpowiedź (finish={result.finish_reason or '?'})"
    return result


async def _post(
    session: aiohttp.ClientSession,
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout: int,
) -> tuple[Any, int]:
    async with session.post(
        url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=timeout)
    ) as resp:
        try:
            data = await resp.json(content_type=None)
        except ValueError:
            data = {"raw": (await resp.text())[:500]}
        return data, resp.status


def _describe_http_error(status: int, body: Any) -> str:
    """Human-readable error (Polish) with the provider's own message."""
    msg = ""
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            msg = err.get("message") or err.get("status") or ""
        elif isinstance(err, str):
            msg = err
        msg = msg or body.get("message") or body.get("raw") or ""
    else:
        msg = str(body)[:300]
    hint = {
        400: "nieprawidłowe zapytanie lub model",
        401: "nieprawidłowy klucz API",
        402: "brak środków na koncie",
        403: "brak dostępu (klucz/region/model)",
        404: "model nie istnieje lub został wycofany",
        429: "limit zapytań / wyczerpany limit (quota)",
        500: "błąd po stronie dostawcy",
        503: "dostawca przeciążony",
        529: "dostawca przeciążony",
    }.get(status, "")
    parts = [f"HTTP {status}"]
    if hint:
        parts.append(hint)
    text = " — ".join(parts)
    return f"{text}: {str(msg)[:300]}" if msg else text


# Not text chat models (image/audio/video generation, agents, embeddings, …)
_GEMINI_NON_CHAT = (
    "embedding", "aqa", "imagen", "veo", "tts", "image", "lyria", "transcribe",
    "computer-use", "robotics", "deep-research", "antigravity", "nano-banana",
    "customtools", "audio", "live",
)


def _gemini_sort_key(model_id: str) -> tuple:
    import re

    family = 2 if model_id.startswith("gemini-") else 1 if model_id.startswith("gemma-") else 0
    match = re.match(r"[a-z]+-(\d+(?:\.\d+)?)", model_id)
    version = float(match.group(1)) if match else 0.0
    stable = 0 if ("preview" in model_id or "exp" in model_id) else 1
    alias = 0 if model_id.endswith("-latest") else 1  # dated ids above floating aliases
    return (family, alias, version, stable)


def _per_million(value: Any) -> float | None:
    try:
        return round(float(value) * 1_000_000, 3)
    except (TypeError, ValueError):
        return None


def _expired(expiration: Any, now: float) -> bool:
    if not expiration:
        return False
    try:
        from datetime import datetime

        ts = datetime.fromisoformat(str(expiration).replace("Z", "+00:00")).timestamp()
        return ts < now
    except ValueError:
        return False
