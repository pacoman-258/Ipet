from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import signal
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import httpx

from .decisions import BrainDecision


PROVIDER_OPENAI = "openai_compatible"
PROVIDER_OLLAMA = "ollama"
PROVIDER_ANTHROPIC = "anthropic_compatible"
PROVIDER_GOOGLE_AISTUDIO = "google_aistudio"
PROVIDER_CODEX = "codex"
GOOGLE_AISTUDIO_DEFAULT_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta"
GOOGLE_AISTUDIO_DEFAULT_MODEL = "gemini-3.5-flash"
GOOGLE_AISTUDIO_LIST_PAGE_SIZE = 1000
SUPPORTED_PROVIDERS = {
    PROVIDER_OPENAI,
    PROVIDER_OLLAMA,
    PROVIDER_ANTHROPIC,
    PROVIDER_GOOGLE_AISTUDIO,
    PROVIDER_CODEX,
}
ENDPOINTLESS_PROVIDERS = frozenset({PROVIDER_GOOGLE_AISTUDIO, PROVIDER_CODEX})
_GOOGLE_API_VERSION_RE = re.compile(r"^v\d+(?:alpha|beta)?(?:\d+)?$", re.IGNORECASE)
_CODEX_REASONING_EFFORT_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_BRAIN_IMAGE_DATA_URL_RE = re.compile(
    r"^data:(image/(?:png|jpeg|webp));base64,([A-Za-z0-9+/=\r\n]+)$",
    re.IGNORECASE,
)
_MAX_BRAIN_IMAGE_BYTES = 20 * 1024 * 1024
_CODEX_BASE_INSTRUCTIONS = (
    "You are a multimodal inference backend for Ipet. Follow the supplied messages and return one final response. "
    "Do not call tools, inspect the environment, or perform agent work; Ipet owns those responsibilities."
)
PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
IPET_SYSTEM_PROMPT_PATH = PROMPTS_DIR / "Ipet.md"
MAX_PROMPT_CHARS = 100_000
MAX_PERSONA_PROMPTS = 100
_IPET_PROMPT_FIELDS = (
    "USER_PERSONA_PROMPT",
    "IPET_SELF_STATE",
    "CONVERSATION_SUMMARIES",
)
_IPET_PROMPT_TOKEN_RE = re.compile(
    r"\{\{(" + "|".join(re.escape(field) for field in _IPET_PROMPT_FIELDS) + r")\}\}"
)


class BrainLLMError(RuntimeError):
    pass


def _read_prompt_file(path: Path, *, label: str) -> str:
    try:
        content = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise BrainLLMError(f"{label} could not be read: {exc}") from exc
    if not content:
        raise BrainLLMError(f"{label} is empty.")
    if len(content) > MAX_PROMPT_CHARS:
        raise BrainLLMError(f"{label} exceeds the {MAX_PROMPT_CHARS} character limit.")
    return content


def load_ipet_system_prompt() -> str:
    template = _read_prompt_file(IPET_SYSTEM_PROMPT_PATH, label="Ipet.md")
    invalid_fields = [field for field in _IPET_PROMPT_FIELDS if template.count(f"{{{{{field}}}}}") != 1]
    if invalid_fields:
        raise BrainLLMError(f"Ipet.md has invalid template fields: {', '.join(invalid_fields)}")
    return template


def _persona_prompt_path(file_name: str) -> Path:
    name = str(file_name or "").strip()
    if (
        not name
        or name != Path(name).name
        or "/" in name
        or "\\" in name
        or not name.casefold().endswith(".md")
        or name.casefold() == IPET_SYSTEM_PROMPT_PATH.name.casefold()
    ):
        raise BrainLLMError("Persona prompt must be a Markdown file inside prompts/ and cannot be Ipet.md.")
    path = (PROMPTS_DIR / name).resolve()
    if path.parent != PROMPTS_DIR.resolve():
        raise BrainLLMError("Persona prompt path escapes prompts/.")
    return path


def load_persona_prompt(file_name: str) -> str:
    name = str(file_name or "").strip()
    path = _persona_prompt_path(name)
    if not path.is_file():
        raise BrainLLMError(f"Selected persona prompt does not exist: {name}")
    return _read_prompt_file(path, label=f"Persona prompt {name}")


def list_persona_prompts() -> list[dict[str, Any]]:
    if not PROMPTS_DIR.is_dir():
        return []
    prompts: list[dict[str, Any]] = []
    for path in sorted(PROMPTS_DIR.glob("*.md"), key=lambda item: item.name.casefold()):
        if len(prompts) >= MAX_PERSONA_PROMPTS:
            break
        if path.name.casefold() == IPET_SYSTEM_PROMPT_PATH.name.casefold():
            continue
        try:
            content = load_persona_prompt(path.name)
        except BrainLLMError:
            continue
        prompts.append({"name": path.name, "content": content})
    return prompts


def _render_ipet_system_prompt(*, persona: str, self_state: str, conversation_summaries: str) -> str:
    values = {
        "USER_PERSONA_PROMPT": persona or "（未启用用户人格提示词）",
        "IPET_SELF_STATE": self_state or "（未提供当前自我状态）",
        "CONVERSATION_SUMMARIES": conversation_summaries or "（暂无会话摘要）",
    }
    template = load_ipet_system_prompt()
    return _IPET_PROMPT_TOKEN_RE.sub(lambda match: values[match.group(1)], template)


@dataclass(frozen=True)
class BrainMessage:
    role: str
    content: str
    image_data_url: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"role": str(self.role or "user"), "content": str(self.content or "")}


@dataclass(frozen=True)
class BrainProviderConfig:
    provider: str = PROVIDER_OPENAI
    endpoint: str = ""
    model: str = "gpt-5.4"
    api_key: str = ""
    temperature: float = 0.4
    max_tokens: int = 1024
    timeout_sec: float = 60.0
    reasoning_effort: str = ""
    streaming_enabled: bool = False
    web_search_enabled: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BrainProviderConfig":
        source = data if isinstance(data, dict) else {}
        provider = normalize_provider(source.get("provider"))
        endpoint = str(source.get("model_endpoint") or source.get("endpoint") or "").strip()
        model = _model_for_provider(
            provider,
            str(source.get("model_name") or source.get("model") or "").strip(),
        )
        try:
            temperature = float(source.get("decision_temperature", source.get("temperature", 0.4)))
        except Exception:
            temperature = 0.4
        try:
            max_tokens = int(source.get("max_output_tokens", source.get("max_tokens", 1024)))
        except Exception:
            max_tokens = 1024
        return cls(
            provider=provider,
            endpoint=endpoint,
            model=model,
            api_key=str(source.get("api_key") or "").strip(),
            temperature=max(0.0, min(2.0, temperature)),
            max_tokens=max(1, min(32000, max_tokens)),
            reasoning_effort=normalize_reasoning_effort(source.get("reasoning_effort")),
            streaming_enabled=source.get("streaming_enabled") is True,
            web_search_enabled=source.get("web_search_enabled") is True,
        )


@dataclass(frozen=True)
class BrainCompletion:
    text: str
    provider: str
    model: str
    decision: BrainDecision | None = None
    raw_text: str = ""
    usage: dict[str, int] | None = None
    web_search_unavailable: bool = False


@dataclass(frozen=True)
class BrainModel:
    id: str
    label: str = ""
    reasoning_efforts: tuple[tuple[str, str], ...] = ()
    default_reasoning_effort: str = ""
    is_default: bool = False
    input_modalities: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"id": self.id, "label": self.label or self.id}
        if self.reasoning_efforts:
            result["reasoning_efforts"] = [
                {"value": value, "description": description}
                for value, description in self.reasoning_efforts
            ]
        if self.default_reasoning_effort:
            result["default_reasoning_effort"] = self.default_reasoning_effort
        if self.is_default:
            result["is_default"] = True
        if self.input_modalities:
            result["input_modalities"] = list(self.input_modalities)
        return result


def google_aistudio_recommended_models() -> list[BrainModel]:
    return [
        BrainModel(id="gemini-3.5-flash", label="Gemini 3.5 Flash"),
        BrainModel(id="gemini-2.5-flash", label="Gemini 2.5 Flash"),
        BrainModel(id="gemini-2.5-flash-lite", label="Gemini 2.5 Flash-Lite"),
        BrainModel(id="gemini-2.5-pro", label="Gemini 2.5 Pro"),
    ]


def normalize_provider(value: Any) -> str:
    provider = str(value or "").strip().lower().replace("-", "_")
    provider = {
        "google_ai_studio": PROVIDER_GOOGLE_AISTUDIO,
        "google_aistudio": PROVIDER_GOOGLE_AISTUDIO,
        "gemini": PROVIDER_GOOGLE_AISTUDIO,
        "codex_cli": PROVIDER_CODEX,
    }.get(provider, provider)
    return provider if provider in SUPPORTED_PROVIDERS else PROVIDER_OPENAI


def normalize_reasoning_effort(value: Any) -> str:
    effort = str(value or "").strip().lower()
    return effort if _CODEX_REASONING_EFFORT_RE.fullmatch(effort) else ""


def _model_for_provider(provider: str, value: Any) -> str:
    model = str(value or "").strip()
    if provider == PROVIDER_CODEX and not model:
        return "default"
    if provider == PROVIDER_GOOGLE_AISTUDIO and (not model or model == "gpt-5.4"):
        return GOOGLE_AISTUDIO_DEFAULT_MODEL
    return model or "gpt-5.4"


def _provider_url(endpoint: str, path: str) -> str:
    base = str(endpoint or "").strip().rstrip("/")
    if not base:
        raise BrainLLMError("Brain model endpoint is required.")
    if path.startswith("/v1/") and base.endswith("/v1"):
        return f"{base}{path[3:]}"
    if path.startswith("/api/") and base.endswith("/api"):
        return f"{base}{path[4:]}"
    return f"{base}{path}"


def _json_headers(config: BrainProviderConfig) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    return headers


def _google_headers(config: BrainProviderConfig) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if config.api_key:
        headers["x-goog-api-key"] = config.api_key
    return headers


def _google_base_url(config: BrainProviderConfig) -> str:
    raw = str(config.endpoint or GOOGLE_AISTUDIO_DEFAULT_ENDPOINT).strip() or GOOGLE_AISTUDIO_DEFAULT_ENDPOINT
    parsed = urlsplit(raw)
    if not parsed.scheme or not parsed.netloc:
        return GOOGLE_AISTUDIO_DEFAULT_ENDPOINT
    segments = [segment for segment in parsed.path.split("/") if segment]
    version_index = next((index for index, segment in enumerate(segments) if _GOOGLE_API_VERSION_RE.fullmatch(segment)), None)
    if version_index is None:
        normalized_segments = [*segments, "v1beta"]
    else:
        normalized_segments = segments[: version_index + 1]
    path = "/" + "/".join(normalized_segments) if normalized_segments else "/v1beta"
    return urlunsplit((parsed.scheme, parsed.netloc, path.rstrip("/"), "", ""))


def _google_model_resource(model: Any) -> str:
    model_name = _model_for_provider(PROVIDER_GOOGLE_AISTUDIO, model)
    if model_name.startswith("models/"):
        return model_name
    return f"models/{model_name}"


def _google_url(endpoint: str, path: str, api_key: str, *, query: dict[str, Any] | None = None) -> str:
    url = _provider_url(endpoint, path)
    parsed = urlsplit(url)
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    if query:
        for key, value in query.items():
            if value is None or value == "":
                continue
            pairs.append((str(key), str(value)))
    key = str(api_key or "").strip()
    if key:
        pairs.append(("key", key))
    query_string = urlencode(pairs, doseq=True, quote_via=quote)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query_string, parsed.fragment))


def _brain_image_parts(data_url: Any) -> tuple[str, str, bytes] | None:
    raw = str(data_url or "").strip()
    if not raw:
        return None
    match = _BRAIN_IMAGE_DATA_URL_RE.fullmatch(raw)
    if match is None:
        raise BrainLLMError("Brain image input must be a base64 PNG, JPEG, or WebP data URL.")
    mime_type = match.group(1).lower()
    encoded = re.sub(r"\s+", "", match.group(2))
    try:
        image_bytes = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise BrainLLMError("Brain image input is not valid base64.") from exc
    if not image_bytes:
        raise BrainLLMError("Brain image input is empty.")
    if len(image_bytes) > _MAX_BRAIN_IMAGE_BYTES:
        raise BrainLLMError("Brain image input exceeds the 20 MB safety limit.")
    return mime_type, encoded, image_bytes


def _messages_payload(messages: list[BrainMessage]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for message in messages:
        text = str(message.content or "").strip()
        image = _brain_image_parts(message.image_data_url)
        if not text and image is None:
            continue
        if image is None:
            payload.append({"role": str(message.role or "user"), "content": text})
            continue
        payload.append(
            {
                "role": str(message.role or "user"),
                "content": [
                    {"type": "text", "text": text},
                    {
                        "type": "image_url",
                        "image_url": {"url": str(message.image_data_url), "detail": "low"},
                    },
                ],
            }
        )
    return payload


def _ollama_messages_payload(messages: list[BrainMessage]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for message in messages:
        text = str(message.content or "").strip()
        image = _brain_image_parts(message.image_data_url)
        if not text and image is None:
            continue
        item: dict[str, Any] = {"role": str(message.role or "user"), "content": text}
        if image is not None:
            item["images"] = [image[1]]
        payload.append(item)
    return payload


def _anthropic_messages_payload(messages: list[BrainMessage]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "system":
            continue
        text = str(message.content or "").strip()
        image = _brain_image_parts(message.image_data_url)
        if not text and image is None:
            continue
        if image is None:
            payload.append({"role": message.role, "content": text})
            continue
        payload.append(
            {
                "role": message.role,
                "content": [
                    {"type": "text", "text": text},
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": image[0], "data": image[1]},
                    },
                ],
            }
        )
    return payload


def _temporary_codex_image(messages: list[BrainMessage]) -> str:
    image = next(
        (_brain_image_parts(message.image_data_url) for message in reversed(messages) if message.image_data_url),
        None,
    )
    if image is None:
        return ""
    suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}[image[0]]
    with tempfile.NamedTemporaryFile(prefix="ipet-brain-vision-", suffix=suffix, delete=False) as image_file:
        image_file.write(image[2])
        return image_file.name


def _parse_openai_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], dict) else {}
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, list):
                parts = [str(item.get("text") or "") for item in content if isinstance(item, dict)]
                return "\n".join(part for part in parts if part).strip()
            return str(content or "").strip()
    return ""


def _parse_ollama_text(payload: dict[str, Any]) -> str:
    message = payload.get("message") if isinstance(payload, dict) else {}
    if isinstance(message, dict):
        return str(message.get("content") or "").strip()
    return str(payload.get("response") or "").strip() if isinstance(payload, dict) else ""


def _parse_anthropic_text(payload: dict[str, Any]) -> str:
    content = payload.get("content") if isinstance(payload, dict) else None
    if isinstance(content, list):
        parts = [str(item.get("text") or "") for item in content if isinstance(item, dict) and item.get("type") in {"text", None}]
        return "\n".join(part for part in parts if part).strip()
    return str(payload.get("text") or "").strip() if isinstance(payload, dict) else ""


def _parse_google_text(payload: dict[str, Any]) -> str:
    candidates = payload.get("candidates") if isinstance(payload, dict) else None
    if isinstance(candidates, list) and candidates:
        content = candidates[0].get("content") if isinstance(candidates[0], dict) else {}
        parts = content.get("parts") if isinstance(content, dict) else []
        if isinstance(parts, list):
            return "\n".join(str(item.get("text") or "") for item in parts if isinstance(item, dict)).strip()
    return str(payload.get("text") or "").strip() if isinstance(payload, dict) else ""


def _strip_json_fence(text: str) -> str:
    stripped = str(text or "").strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) >= 3 and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return stripped


def _json_object_from_text(text: str) -> dict[str, Any] | None:
    stripped = _strip_json_fence(text)
    try:
        parsed = json.loads(stripped)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _as_steps(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item or "").strip() for item in value if str(item or "").strip()]


def _goal_from_data(data: dict[str, Any], payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
    direct = data.get("goal") if isinstance(data.get("goal"), dict) else None
    nested = payload.get("goal") if isinstance(payload, dict) and isinstance(payload.get("goal"), dict) else None
    if direct is not None:
        return dict(direct)
    if nested is not None:
        return dict(nested)
    return None


def parse_brain_reply(raw_text: str) -> BrainDecision:
    text = str(raw_text or "").strip()
    data = _json_object_from_text(text)
    if data is None:
        return BrainDecision.say(text)

    kind = str(data.get("kind") or data.get("type") or "say").strip().lower()
    if kind == "say":
        payload = _as_dict(data.get("payload"))
        visible_text = str(data.get("text") or data.get("content") or data.get("summary") or "").strip()
        return BrainDecision.say(visible_text or text, goal=_goal_from_data(data, payload))
    if kind == "think":
        payload = _as_dict(data.get("payload"))
        thought = str(data.get("thought") or payload.get("thought") or data.get("summary") or data.get("text") or "").strip()
        next_kind = str(data.get("next_kind") or payload.get("next_kind") or data.get("next") or "").strip()
        return BrainDecision.think(thought or text, goal=_goal_from_data(data, payload), next_kind=next_kind)
    if kind == "observe":
        payload = _as_dict(data.get("payload"))
        observe_prompt = str(
            data.get("question")
            or payload.get("question")
            or data.get("observe_prompt")
            or payload.get("observe_prompt")
            or ""
        ).strip()
        return BrainDecision.observe(
            str(data.get("target") or payload.get("target") or "screen"),
            observe_prompt=observe_prompt,
            target_app=str(data.get("target_app") or payload.get("target_app") or ""),
            goal=_goal_from_data(data, payload),
        )
    if kind in {"act", "propose_act"}:
        payload = _as_dict(data.get("payload"))
        arguments = _as_dict(data.get("arguments") or payload.get("arguments"))
        action_type = str(data.get("action_type") or payload.get("action_type") or data.get("action") or "").strip()
        return BrainDecision.propose_act(action_type, arguments, goal=_goal_from_data(data, payload))
    if kind in {"remember", "propose_remember"}:
        payload = _as_dict(data.get("payload"))
        category = str(data.get("category") or payload.get("category") or "general").strip()
        memory_text = str(data.get("text") or payload.get("text") or data.get("summary") or "").strip()
        return BrainDecision.propose_remember(category, memory_text)
    if kind in {"learn_skill", "propose_learn_skill"}:
        payload = _as_dict(data.get("payload"))
        name = str(data.get("name") or payload.get("name") or "").strip()
        steps = _as_steps(data.get("steps") or payload.get("steps"))
        return BrainDecision.propose_learn_skill(name, steps)
    if kind == "stop":
        payload = _as_dict(data.get("payload"))
        return BrainDecision.stop(str(data.get("summary") or data.get("text") or "").strip(), goal=_goal_from_data(data, payload))
    return BrainDecision.say(text)


def _decision_text(decision: BrainDecision) -> str:
    if decision.kind.value == "say":
        return str(decision.payload.get("text") or decision.summary or "").strip()
    return decision.summary


async def complete_with_provider(
    config: BrainProviderConfig,
    messages: list[BrainMessage],
    *,
    client: Any | None = None,
    on_delta: Callable[[str], Awaitable[None]] | None = None,
    on_activity: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> BrainCompletion:
    if config.provider == PROVIDER_CODEX:
        if config.streaming_enabled and on_delta is not None:
            emitted = False

            async def track_delta(delta: str) -> None:
                nonlocal emitted
                emitted = emitted or bool(delta)
                await on_delta(delta)

            try:
                return await _complete_codex_stream(config, messages, track_delta, on_activity)
            except BrainLLMError:
                if emitted:
                    raise
                return await _complete_codex(config, messages)
        try:
            return await _complete_codex_stream(config, messages, None, on_activity)
        except BrainLLMError:
            return await _complete_codex(config, messages)
    if config.provider == PROVIDER_OLLAMA:
        return await _complete_ollama(config, messages, client=client)
    if config.provider == PROVIDER_ANTHROPIC:
        return await _complete_anthropic(config, messages, client=client)
    if config.provider == PROVIDER_GOOGLE_AISTUDIO:
        return await _complete_google_aistudio(config, messages, client=client)
    return await _complete_openai(config, messages, client=client)


async def _post_json(client: Any, url: str, *, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
    response = await client.post(url, headers=headers, json=payload)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise BrainLLMError("Brain provider returned a non-object JSON payload.")
    return data


async def _get_json(client: Any, url: str, *, headers: dict[str, str]) -> dict[str, Any]:
    response = await client.get(url, headers=headers)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise BrainLLMError("Brain provider returned a non-object JSON payload.")
    return data


def _should_trust_env(config: BrainProviderConfig) -> bool:
    return False


def _is_local_endpoint(endpoint: str) -> bool:
    parsed = urlsplit(str(endpoint or "").strip())
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return False
    if host in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}:
        return True
    return host.startswith("127.") or host.endswith(".local")


def _http_proxy_from_env() -> str | None:
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        value = str(os.environ.get(name) or "").strip()
        if value.lower().startswith(("http://", "https://")):
            return value
    return None


def _client_proxy(config: BrainProviderConfig) -> str | None:
    if config.provider == PROVIDER_OLLAMA:
        return None
    if config.provider == PROVIDER_GOOGLE_AISTUDIO:
        endpoint = _google_base_url(config)
    else:
        endpoint = config.endpoint
    if _is_local_endpoint(endpoint):
        return None
    return _http_proxy_from_env()


async def _with_client(config: BrainProviderConfig, callback):
    timeout = httpx.Timeout(connect=8.0, read=config.timeout_sec, write=20.0, pool=8.0)
    client_kwargs: dict[str, Any] = {"timeout": timeout, "trust_env": _should_trust_env(config)}
    proxy = _client_proxy(config)
    if proxy:
        client_kwargs["proxy"] = proxy
    async with httpx.AsyncClient(**client_kwargs) as client:
        return await callback(client)


def _model_from_value(value: Any) -> BrainModel | None:
    if isinstance(value, BrainModel):
        return value
    if isinstance(value, str):
        model_id = value.strip()
        return BrainModel(model_id) if model_id else None
    if not isinstance(value, dict):
        return None
    model_id = str(value.get("id") or value.get("name") or value.get("model") or "").strip()
    if not model_id:
        return None
    label = str(value.get("label") or value.get("display_name") or value.get("name") or model_id).strip()
    return BrainModel(id=model_id, label=label or model_id)


def _dedupe_models(values: list[Any]) -> list[BrainModel]:
    seen: set[str] = set()
    models: list[BrainModel] = []
    for value in values:
        model = _model_from_value(value)
        if model is None or model.id in seen:
            continue
        seen.add(model.id)
        models.append(model)
    return models


def _models_from_openai_payload(payload: dict[str, Any]) -> list[BrainModel]:
    values = payload.get("data") if isinstance(payload.get("data"), list) else payload.get("models")
    return _dedupe_models(values if isinstance(values, list) else [])


def _models_from_ollama_payload(payload: dict[str, Any]) -> list[BrainModel]:
    values = payload.get("models")
    return _dedupe_models(values if isinstance(values, list) else [])


def _models_from_google_payload(payload: dict[str, Any]) -> list[BrainModel]:
    values = payload.get("models") if isinstance(payload.get("models"), list) else []
    models: list[BrainModel] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, dict):
            continue
        methods = value.get("supportedGenerationMethods")
        if isinstance(methods, list) and "generateContent" not in methods:
            continue
        raw_id = str(value.get("baseModelId") or value.get("name") or value.get("id") or "").strip()
        model_id = raw_id.removeprefix("models/")
        if not model_id or model_id in seen:
            continue
        label = str(value.get("displayName") or value.get("display_name") or value.get("label") or model_id).strip()
        seen.add(model_id)
        models.append(BrainModel(id=model_id, label=label or model_id))
    return models


async def list_provider_models(
    config: BrainProviderConfig | dict[str, Any],
    *,
    client: Any | None = None,
) -> list[BrainModel]:
    provider_config = config if isinstance(config, BrainProviderConfig) else BrainProviderConfig.from_dict(config)
    if provider_config.provider == PROVIDER_CODEX:
        await _require_codex_login(provider_config.timeout_sec)
        return await _list_codex_models(provider_config.timeout_sec)

    async def run(active_client):
        if provider_config.provider == PROVIDER_OLLAMA:
            data = await _get_json(
                active_client,
                _provider_url(provider_config.endpoint, "/api/tags"),
                headers=_json_headers(provider_config),
            )
            return _models_from_ollama_payload(data)
        if provider_config.provider == PROVIDER_ANTHROPIC:
            headers = {"Content-Type": "application/json", "anthropic-version": "2023-06-01"}
            if provider_config.api_key:
                headers["x-api-key"] = provider_config.api_key
            data = await _get_json(active_client, _provider_url(provider_config.endpoint, "/v1/models"), headers=headers)
            return _models_from_openai_payload(data)
        if provider_config.provider == PROVIDER_GOOGLE_AISTUDIO:
            models: list[BrainModel] = []
            seen: set[str] = set()
            page_token = ""
            while True:
                data = await _get_json(
                    active_client,
                    _google_url(
                        _google_base_url(provider_config),
                        "/models",
                        provider_config.api_key,
                        query={
                            "pageSize": GOOGLE_AISTUDIO_LIST_PAGE_SIZE,
                            "pageToken": page_token or None,
                        },
                    ),
                    headers=_google_headers(provider_config),
                )
                for model in _models_from_google_payload(data):
                    if model.id in seen:
                        continue
                    seen.add(model.id)
                    models.append(model)
                page_token = str(data.get("nextPageToken") or "").strip()
                if not page_token:
                    break
            return models
        data = await _get_json(
            active_client,
            _provider_url(provider_config.endpoint, "/v1/models"),
            headers=_json_headers(provider_config),
        )
        return _models_from_openai_payload(data)

    return await run(client) if client is not None else await _with_client(provider_config, run)


def _codex_executable() -> str:
    discovered = shutil.which("codex")
    bundled = Path("/Applications/ChatGPT.app/Contents/Resources/codex")
    if discovered:
        return discovered
    if bundled.is_file():
        return str(bundled)
    raise BrainLLMError("Codex CLI was not found. Install Codex or the ChatGPT desktop app first.")


def _codex_process_env() -> dict[str, str]:
    env = dict(os.environ)
    home = str(Path.home())
    if not str(env.get("HOME") or "").strip():
        env["HOME"] = home
    codex_home = str(env.get("CODEX_HOME") or "").strip()
    if codex_home:
        env["CODEX_HOME"] = str(Path(codex_home).expanduser())
    else:
        env["CODEX_HOME"] = str(Path(home) / ".codex")
    return env


async def _run_codex_cli(
    args: list[str],
    *,
    input_text: str = "",
    timeout_sec: float,
) -> tuple[str, str]:
    try:
        process = await asyncio.create_subprocess_exec(
            _codex_executable(),
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=tempfile.gettempdir(),
            env=_codex_process_env(),
            start_new_session=os.name != "nt",
        )
    except OSError as exc:
        raise BrainLLMError(f"Codex CLI could not start: {exc}") from exc
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(input_text.encode("utf-8")),
            timeout=max(1.0, timeout_sec),
        )
    except TimeoutError as exc:
        await _stop_codex_process(process)
        raise BrainLLMError(f"Codex CLI timed out after {timeout_sec:g} seconds.") from exc
    except asyncio.CancelledError:
        await _stop_codex_process(process)
        raise
    except Exception:
        await _stop_codex_process(process)
        raise
    stdout_text = stdout.decode("utf-8", errors="replace").strip()
    stderr_text = stderr.decode("utf-8", errors="replace").strip()
    if process.returncode:
        detail = stderr_text or stdout_text or f"exit code {process.returncode}"
        raise BrainLLMError(f"Codex CLI failed: {detail[-1200:]}")
    return stdout_text, stderr_text


async def _require_codex_login(timeout_sec: float) -> None:
    # Codex versions disagree on whether status text goes to stdout or stderr.
    # A successful exit code is the stable contract; _run_codex_cli raises otherwise.
    await _run_codex_cli(["login", "status"], timeout_sec=min(timeout_sec, 10.0))


async def _codex_app_server_response(process: Any, request_id: int, timeout_sec: float) -> dict[str, Any]:
    while True:
        try:
            line = await asyncio.wait_for(process.stdout.readline(), timeout=max(1.0, timeout_sec))
        except TimeoutError as exc:
            raise BrainLLMError(f"Codex app-server timed out after {timeout_sec:g} seconds.") from exc
        if not line:
            detail = (await process.stderr.read()).decode("utf-8", errors="replace").strip()
            raise BrainLLMError(f"Codex app-server closed before replying: {detail[-1200:] or 'no detail'}")
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(message, dict) or message.get("id") != request_id:
            continue
        if message.get("error"):
            raise BrainLLMError(f"Codex app-server request failed: {message['error']}")
        result = message.get("result")
        if not isinstance(result, dict):
            raise BrainLLMError("Codex app-server returned an invalid response.")
        return result


async def _codex_app_server_request(
    process: Any,
    request_id: int,
    method: str,
    params: dict[str, Any],
    timeout_sec: float,
) -> dict[str, Any]:
    process.stdin.write(
        (json.dumps({"id": request_id, "method": method, "params": params}) + "\n").encode("utf-8")
    )
    await process.stdin.drain()
    return await _codex_app_server_response(process, request_id, timeout_sec)


def _signal_codex_process(process: Any, sig: int) -> None:
    if os.name != "nt":
        pid = getattr(process, "pid", None)
        if pid:
            try:
                os.killpg(os.getpgid(pid), sig)
                return
            except ProcessLookupError:
                return
            except OSError:
                pass
    if sig == signal.SIGKILL:
        process.kill()
    else:
        process.terminate()


async def _stop_codex_process(process: Any) -> None:
    if process.returncode is not None:
        return
    try:
        _signal_codex_process(process, signal.SIGTERM)
    except ProcessLookupError:
        return
    wait_task = asyncio.ensure_future(process.wait())
    try:
        await asyncio.wait_for(asyncio.shield(wait_task), timeout=1.0)
        return
    except TimeoutError:
        pass
    except Exception:
        if wait_task.done():
            return
    try:
        _signal_codex_process(process, signal.SIGKILL)
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(asyncio.shield(wait_task), timeout=1.0)
    except Exception:
        if not wait_task.done():
            wait_task.cancel()
            await asyncio.gather(wait_task, return_exceptions=True)


async def _stop_codex_app_server(process: Any) -> None:
    await _stop_codex_process(process)


def _codex_models_from_payload(values: Any) -> list[BrainModel]:
    models: list[BrainModel] = []
    seen: set[str] = set()
    for value in values if isinstance(values, list) else []:
        if not isinstance(value, dict) or value.get("hidden") is True:
            continue
        model_id = str(value.get("model") or value.get("id") or "").strip()
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        is_default = value.get("isDefault") is True
        display_name = str(value.get("displayName") or model_id).strip() or model_id
        efforts: list[tuple[str, str]] = []
        for option in value.get("supportedReasoningEfforts") or []:
            if not isinstance(option, dict):
                continue
            effort = normalize_reasoning_effort(option.get("reasoningEffort"))
            if effort:
                efforts.append((effort, str(option.get("description") or "").strip()))
        models.append(
            BrainModel(
                id=model_id,
                label=f"{display_name}{'（默认）' if is_default else ''}",
                reasoning_efforts=tuple(efforts),
                default_reasoning_effort=normalize_reasoning_effort(value.get("defaultReasoningEffort")),
                is_default=is_default,
                input_modalities=tuple(
                    modality
                    for modality in (
                        str(item or "").strip().lower()
                        for item in (
                            value.get("inputModalities")
                            if isinstance(value.get("inputModalities"), list)
                            else ["text", "image"]
                        )
                    )
                    if modality in {"text", "image"}
                ),
            )
        )
    return models


async def _list_codex_models(timeout_sec: float) -> list[BrainModel]:
    try:
        process = await asyncio.create_subprocess_exec(
            _codex_executable(),
            "app-server",
            "--stdio",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=tempfile.gettempdir(),
            env=_codex_process_env(),
            start_new_session=os.name != "nt",
        )
    except OSError as exc:
        raise BrainLLMError(f"Codex app-server could not start: {exc}") from exc
    try:
        await _codex_app_server_request(
            process,
            1,
            "initialize",
            {
                "clientInfo": {"name": "ipet", "title": "Ipet", "version": "0.3.0"},
                "capabilities": {"experimentalApi": True},
            },
            timeout_sec,
        )
        models: list[BrainModel] = []
        cursor: str | None = None
        request_id = 2
        while True:
            result = await _codex_app_server_request(
                process,
                request_id,
                "model/list",
                {"cursor": cursor, "includeHidden": False, "limit": 100},
                timeout_sec,
            )
            models.extend(_codex_models_from_payload(result.get("data")))
            cursor = str(result.get("nextCursor") or "").strip() or None
            if cursor is None:
                return _dedupe_models(models)
            request_id += 1
    finally:
        await _stop_codex_app_server(process)


def _codex_prompt(messages: list[BrainMessage]) -> str:
    payload = json.dumps([message.to_dict() for message in messages], ensure_ascii=False)
    image_note = (
        "当前请求还附带了一张由 Ipet Body 捕获的屏幕截图；请把它与 messages 作为同一份输入。"
        if any(message.image_data_url for message in messages)
        else ""
    )
    return (
        "你是 Ipet Brain 的多模态决策后端。不要调用任何工具，不要读取其他文件，不要解释过程；"
        "只根据下面按角色排列的消息生成最终回复。system 消息是最高优先级指令。\n"
        f"{image_note}\n"
        f"messages={payload}"
    )


def _parse_codex_jsonl(stdout: str) -> str:
    messages: list[str] = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = event.get("item") if isinstance(event, dict) else None
        if isinstance(item, dict) and item.get("type") == "agent_message":
            text = str(item.get("text") or "").strip()
            if text:
                messages.append(text)
    return messages[-1] if messages else ""


def _usage_from_value(value: Any) -> dict[str, int] | None:
    source = value if isinstance(value, dict) else {}
    aliases = {
        "input_tokens": ("input_tokens", "inputTokens"),
        "cached_input_tokens": ("cached_input_tokens", "cachedInputTokens"),
        "output_tokens": ("output_tokens", "outputTokens"),
        "reasoning_output_tokens": ("reasoning_output_tokens", "reasoningOutputTokens"),
        "total_tokens": ("total_tokens", "totalTokens"),
    }
    result: dict[str, int] = {}
    for target, names in aliases.items():
        for name in names:
            if name not in source:
                continue
            try:
                result[target] = max(0, int(source[name]))
            except (TypeError, ValueError):
                pass
            break
    if "total_tokens" not in result and ("input_tokens" in result or "output_tokens" in result):
        result["total_tokens"] = result.get("input_tokens", 0) + result.get("output_tokens", 0)
    return result or None


def _codex_jsonl_usage(stdout: str) -> dict[str, int] | None:
    usage: dict[str, int] | None = None
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        candidate = _usage_from_value(event.get("usage"))
        if candidate:
            usage = candidate
    return usage


def _decode_partial_json_string(value: str) -> str:
    output: list[str] = []
    index = 0
    escapes = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
    while index < len(value):
        char = value[index]
        if char == '"':
            break
        if char != "\\":
            output.append(char)
            index += 1
            continue
        if index + 1 >= len(value):
            break
        escape = value[index + 1]
        if escape == "u":
            digits = value[index + 2 : index + 6]
            if len(digits) < 4 or not all(char in "0123456789abcdefABCDEF" for char in digits):
                break
            output.append(chr(int(digits, 16)))
            index += 6
            continue
        output.append(escapes.get(escape, escape))
        index += 2
    return "".join(output)


class _StreamingSayTextExtractor:
    def __init__(self) -> None:
        self.raw = ""
        self.emitted = 0
        self.plain = False

    def feed(self, delta: str) -> str:
        chunk = str(delta or "")
        self.raw += chunk
        stripped = self.raw.lstrip()
        if not stripped:
            return ""
        if self.plain or not stripped.startswith(("{", "```")):
            self.plain = True
            return chunk
        if not re.search(r'"kind"\s*:\s*"say"', self.raw):
            return ""
        match = re.search(r'"text"\s*:\s*"', self.raw)
        if match is None:
            return ""
        decoded = _decode_partial_json_string(self.raw[match.end() :])
        fresh = decoded[self.emitted :]
        self.emitted = len(decoded)
        return fresh


async def _read_codex_app_server_message(process: Any, timeout_sec: float) -> dict[str, Any]:
    try:
        line = await asyncio.wait_for(process.stdout.readline(), timeout=max(1.0, timeout_sec))
    except TimeoutError as exc:
        raise BrainLLMError(f"Codex app-server timed out after {timeout_sec:g} seconds.") from exc
    if not line:
        detail = (await process.stderr.read()).decode("utf-8", errors="replace").strip()
        raise BrainLLMError(f"Codex app-server closed unexpectedly: {detail[-1200:] or 'no detail'}")
    try:
        message = json.loads(line)
    except json.JSONDecodeError:
        return {}
    return message if isinstance(message, dict) else {}


def _codex_web_search_activity(item: Any) -> dict[str, Any] | None:
    source = item if isinstance(item, dict) else {}
    if source.get("type") != "webSearch":
        return None
    action = source.get("action") if isinstance(source.get("action"), dict) else {}
    action_type = str(action.get("type") or "search").strip()
    query = str(action.get("query") or source.get("query") or "").strip()
    queries = [str(value).strip() for value in action.get("queries") or [] if str(value).strip()]
    url = str(action.get("url") or "").strip()
    pattern = str(action.get("pattern") or "").strip()
    if action_type == "openPage":
        text = f"正在浏览：{url or '网页'}"
    elif action_type == "findInPage":
        target = f"{pattern}（{url}）" if pattern and url else pattern or url or "网页内容"
        text = f"正在网页中查找：{target}"
    else:
        text = f"正在搜索：{query or '；'.join(queries) or '相关内容'}"
    return {
        "category": "searching",
        "phase": "search",
        "source": "brain",
        "status_id": f"codex-web-search:{str(source.get('id') or action_type)}",
        "action": action_type,
        "text": text,
        "transient": True,
    }


async def _complete_codex_stream(
    config: BrainProviderConfig,
    messages: list[BrainMessage],
    on_delta: Callable[[str], Awaitable[None]] | None,
    on_activity: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> BrainCompletion:
    image_path = ""
    try:
        process = await asyncio.create_subprocess_exec(
            _codex_executable(),
            "app-server",
            "--stdio",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=tempfile.gettempdir(),
            env=_codex_process_env(),
            start_new_session=os.name != "nt",
        )
    except OSError as exc:
        raise BrainLLMError(f"Codex app-server could not start: {exc}") from exc
    try:
        await _codex_app_server_request(
            process,
            1,
            "initialize",
            {
                "clientInfo": {"name": "ipet", "title": "Ipet", "version": "0.3.0"},
                "capabilities": {"experimentalApi": True},
            },
            config.timeout_sec,
        )
        request_id = 2
        web_search_available = False
        if config.web_search_enabled:
            try:
                capabilities = await _codex_app_server_request(
                    process,
                    request_id,
                    "modelProvider/capabilities/read",
                    {},
                    config.timeout_sec,
                )
                web_search_available = capabilities.get("webSearch") is True
            except BrainLLMError:
                web_search_available = False
            request_id += 1
        base_instructions = _CODEX_BASE_INSTRUCTIONS
        if web_search_available:
            base_instructions += (
                " The only available tool is built-in web search. Use it when the user requests online, current, "
                "or source-backed information, and otherwise answer directly."
            )
        thread_params: dict[str, Any] = {
            "approvalPolicy": "never",
            "baseInstructions": base_instructions,
            "developerInstructions": "",
            "config": {
                "web_search": "live" if web_search_available else "disabled",
                "features": {
                    "apps": False,
                    "browser_use": False,
                    "computer_use": False,
                    "goals": False,
                    "hooks": False,
                    "image_generation": False,
                    "in_app_browser": False,
                    "multi_agent": False,
                    "plugins": False,
                    "remote_plugin": False,
                    "shell_tool": False,
                    "tool_suggest": False,
                    "workspace_dependencies": False,
                },
                "tools": {"view_image": False, "web_search": web_search_available},
            },
            "cwd": tempfile.gettempdir(),
            "ephemeral": True,
            "sandbox": "read-only",
        }
        if config.model and config.model != "default":
            thread_params["model"] = config.model
        thread_result = await _codex_app_server_request(
            process,
            request_id,
            "thread/start",
            thread_params,
            config.timeout_sec,
        )
        thread = thread_result.get("thread") if isinstance(thread_result.get("thread"), dict) else {}
        thread_id = str(thread.get("id") or "").strip()
        if not thread_id:
            raise BrainLLMError("Codex app-server did not return a thread id.")
        request_id += 1
        image_path = _temporary_codex_image(messages)
        turn_input: list[dict[str, str]] = [{"type": "text", "text": _codex_prompt(messages)}]
        if image_path:
            turn_input.append({"type": "localImage", "path": image_path})
        turn_params: dict[str, Any] = {
            "threadId": thread_id,
            "input": turn_input,
        }
        if config.reasoning_effort:
            turn_params["effort"] = config.reasoning_effort
        process.stdin.write(
            (json.dumps({"id": request_id, "method": "turn/start", "params": turn_params}) + "\n").encode("utf-8")
        )
        await process.stdin.drain()

        final_text = ""
        usage: dict[str, int] | None = None
        web_search_updates: set[tuple[str, str, str]] = set()
        while True:
            message = await _read_codex_app_server_message(process, config.timeout_sec)
            if not message:
                continue
            if message.get("id") == request_id and message.get("error"):
                raise BrainLLMError(f"Codex app-server turn failed to start: {message['error']}")
            method = str(message.get("method") or "")
            params = message.get("params") if isinstance(message.get("params"), dict) else {}
            if method == "item/agentMessage/delta":
                delta = str(params.get("delta") or "")
                if delta:
                    final_text += delta
                    if on_delta is not None:
                        await on_delta(delta)
            elif method == "item/completed":
                item = params.get("item") if isinstance(params.get("item"), dict) else {}
                if item.get("type") == "agentMessage" and str(item.get("text") or "").strip():
                    final_text = str(item["text"])
            if method in {"item/started", "item/completed"}:
                item = params.get("item") if isinstance(params.get("item"), dict) else {}
                activity = _codex_web_search_activity(item)
                if activity is not None and on_activity is not None:
                    signature = (
                        str(activity.get("status_id") or ""),
                        str(activity.get("action") or ""),
                        str(activity.get("text") or ""),
                    )
                    if signature not in web_search_updates:
                        web_search_updates.add(signature)
                        await on_activity(activity)
            elif method == "thread/tokenUsage/updated":
                token_usage = params.get("tokenUsage") if isinstance(params.get("tokenUsage"), dict) else {}
                usage = _usage_from_value(token_usage.get("total")) or _usage_from_value(token_usage.get("last")) or usage
            elif method == "turn/completed":
                turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
                if str(turn.get("status") or "") == "failed":
                    error = turn.get("error") if isinstance(turn.get("error"), dict) else {}
                    raise BrainLLMError(str(error.get("message") or "Codex app-server turn failed."))
                for item in turn.get("items") or []:
                    if isinstance(item, dict) and item.get("type") == "agentMessage" and str(item.get("text") or "").strip():
                        final_text = str(item["text"])
                break
        if not final_text.strip():
            raise BrainLLMError("Codex app-server returned no final agent message.")
        return BrainCompletion(
            text=final_text.strip(),
            provider=PROVIDER_CODEX,
            model=config.model or "default",
            usage=usage,
            web_search_unavailable=config.web_search_enabled and not web_search_available,
        )
    finally:
        await _stop_codex_app_server(process)
        if image_path:
            try:
                os.unlink(image_path)
            except OSError:
                pass


async def _complete_codex(config: BrainProviderConfig, messages: list[BrainMessage]) -> BrainCompletion:
    args = [
        "-a",
        "never",
        "exec",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "--color",
        "never",
        "--json",
    ]
    image_path = ""
    try:
        image_path = _temporary_codex_image(messages)
        if image_path:
            args.extend(("--image", image_path))
        if config.model and config.model != "default":
            args.extend(("--model", config.model))
        if config.reasoning_effort:
            args.extend(("--config", f"model_reasoning_effort={json.dumps(config.reasoning_effort)}"))
        args.extend(("--config", 'web_search="disabled"', "--config", "tools.web_search=false"))
        args.append("-")
        stdout, _stderr = await _run_codex_cli(
            args,
            input_text=_codex_prompt(messages),
            timeout_sec=config.timeout_sec,
        )
    finally:
        if image_path:
            try:
                os.unlink(image_path)
            except OSError:
                pass
    text = _parse_codex_jsonl(stdout)
    if not text:
        raise BrainLLMError("Codex CLI returned no final agent message.")
    return BrainCompletion(
        text=text,
        provider=PROVIDER_CODEX,
        model=config.model or "default",
        usage=_codex_jsonl_usage(stdout),
        web_search_unavailable=config.web_search_enabled,
    )


def _google_generate_payload(config: BrainProviderConfig, messages: list[BrainMessage]) -> dict[str, Any]:
    system_parts = [message.content for message in messages if message.role == "system" and message.content.strip()]
    contents: list[dict[str, Any]] = []
    for message in messages:
        text = str(message.content or "").strip()
        if not text or message.role == "system":
            continue
        role = "model" if str(message.role or "").strip().lower() in {"assistant", "model"} else "user"
        image = _brain_image_parts(message.image_data_url)
        parts: list[dict[str, Any]] = [{"text": text}]
        if image is not None:
            parts.append({"inline_data": {"mime_type": image[0], "data": image[1]}})
        contents.append({"role": role, "parts": parts})
    payload: dict[str, Any] = {
        "contents": contents,
        "generationConfig": {
            "temperature": config.temperature,
            "maxOutputTokens": config.max_tokens,
        },
    }
    if system_parts:
        payload["system_instruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}
    return payload


async def _complete_google_aistudio(config: BrainProviderConfig, messages: list[BrainMessage], *, client: Any | None) -> BrainCompletion:
    async def run(active_client):
        data = await _post_json(
            active_client,
            _google_url(
                _google_base_url(config),
                f"/{_google_model_resource(config.model)}:generateContent",
                config.api_key,
            ),
            headers=_google_headers(config),
            payload=_google_generate_payload(config, messages),
        )
        text = _parse_google_text(data)
        if not text:
            raise BrainLLMError("Google AI Studio provider returned empty text.")
        return BrainCompletion(text=text, provider=config.provider, model=_model_for_provider(config.provider, config.model))

    return await run(client) if client is not None else await _with_client(config, run)


async def _complete_openai(config: BrainProviderConfig, messages: list[BrainMessage], *, client: Any | None) -> BrainCompletion:
    async def run(active_client):
        payload = {
            "model": config.model,
            "messages": _messages_payload(messages),
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
            "stream": False,
        }
        data = await _post_json(
            active_client,
            _provider_url(config.endpoint, "/v1/chat/completions"),
            headers=_json_headers(config),
            payload=payload,
        )
        text = _parse_openai_text(data)
        if not text:
            raise BrainLLMError("OpenAI-compatible provider returned empty text.")
        return BrainCompletion(text=text, provider=config.provider, model=config.model)

    return await run(client) if client is not None else await _with_client(config, run)


async def _complete_ollama(config: BrainProviderConfig, messages: list[BrainMessage], *, client: Any | None) -> BrainCompletion:
    async def run(active_client):
        payload = {
            "model": config.model,
            "messages": _ollama_messages_payload(messages),
            "options": {"temperature": config.temperature, "num_predict": config.max_tokens},
            "stream": False,
        }
        data = await _post_json(
            active_client,
            _provider_url(config.endpoint, "/api/chat"),
            headers=_json_headers(config),
            payload=payload,
        )
        text = _parse_ollama_text(data)
        if not text:
            raise BrainLLMError("Ollama provider returned empty text.")
        return BrainCompletion(text=text, provider=config.provider, model=config.model)

    return await run(client) if client is not None else await _with_client(config, run)


async def _complete_anthropic(config: BrainProviderConfig, messages: list[BrainMessage], *, client: Any | None) -> BrainCompletion:
    async def run(active_client):
        system_parts = [message.content for message in messages if message.role == "system" and message.content.strip()]
        user_messages = _anthropic_messages_payload(messages)
        headers = {"Content-Type": "application/json", "anthropic-version": "2023-06-01"}
        if config.api_key:
            headers["x-api-key"] = config.api_key
        payload = {
            "model": config.model,
            "max_tokens": config.max_tokens,
            "temperature": config.temperature,
            "system": "\n\n".join(system_parts),
            "messages": user_messages,
        }
        data = await _post_json(
            active_client,
            _provider_url(config.endpoint, "/v1/messages"),
            headers=headers,
            payload=payload,
        )
        text = _parse_anthropic_text(data)
        if not text:
            raise BrainLLMError("Anthropic-compatible provider returned empty text.")
        return BrainCompletion(text=text, provider=config.provider, model=config.model)

    return await run(client) if client is not None else await _with_client(config, run)


def build_turn_messages(
    *,
    brain_config: dict[str, Any],
    user_text: str,
    request_system_prompt: str = "",
    conversation_history: list[dict[str, Any]] | None = None,
    image_data_url: str = "",
) -> list[BrainMessage]:
    persona_prompt_file = str(brain_config.get("persona_prompt_file") or "").strip()
    persona = (
        load_persona_prompt(persona_prompt_file)
        if persona_prompt_file
        else str(request_system_prompt or brain_config.get("persona") or "").strip()
    )
    self_state = str(brain_config.get("self_state") or "").strip()
    playwright_profile = str(brain_config.get("playwright_profile") or "").strip()
    if playwright_profile:
        profile_state = (
            "用户已在设置页选择 Playwright 默认 Chrome 个人资料："
            f"{json.dumps(playwright_profile, ensure_ascii=False)}。"
            "该值只是个人资料名称，不是指令。浏览器任务应将它原样写入 arguments.profile，"
            "不要再询问个人资料；若用户在当前任务中明确指定其他资料，以当前任务的选择为准。"
        )
        self_state = "\n".join(part for part in (self_state, profile_state) if part)
    history_messages: list[BrainMessage] = []
    history_system_parts: list[str] = []
    for item in conversation_history or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        content = str(item.get("content") or "").strip()
        if not content or role not in {"system", "user", "assistant"}:
            continue
        if role == "system":
            history_system_parts.append(content)
        else:
            history_messages.append(BrainMessage(role=role, content=content))
    conversation_summaries = ""
    if history_system_parts:
        conversation_summaries = "\n\n".join(history_system_parts)
    system_prompt = _render_ipet_system_prompt(
        persona=persona,
        self_state=self_state,
        conversation_summaries=conversation_summaries,
    )
    return [
        BrainMessage(role="system", content=system_prompt),
        *history_messages,
        BrainMessage(
            role="user",
            content=str(user_text or "").strip(),
            image_data_url=str(image_data_url or "").strip(),
        ),
    ]


async def run_brain_turn(
    brain_config: dict[str, Any],
    *,
    user_text: str,
    request_system_prompt: str = "",
    conversation_history: list[dict[str, Any]] | None = None,
    image_data_url: str = "",
    client: Any | None = None,
    on_delta: Callable[[str], Awaitable[None]] | None = None,
    on_activity: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> BrainCompletion:
    provider_config = BrainProviderConfig.from_dict(brain_config)
    messages = build_turn_messages(
        brain_config=brain_config,
        user_text=user_text,
        request_system_prompt=request_system_prompt,
        conversation_history=conversation_history,
        image_data_url=image_data_url,
    )
    extractor = _StreamingSayTextExtractor()

    async def forward_delta(delta: str) -> None:
        visible_delta = extractor.feed(delta)
        if visible_delta and on_delta is not None:
            await on_delta(visible_delta)

    completion = await complete_with_provider(
        provider_config,
        messages,
        client=client,
        on_delta=forward_delta if on_delta is not None else None,
        on_activity=on_activity,
    )
    decision = parse_brain_reply(completion.text)
    web_search_unavailable = completion.web_search_unavailable or (
        provider_config.web_search_enabled and completion.provider != PROVIDER_CODEX
    )
    if web_search_unavailable and decision.kind.value == "say":
        answer = str(decision.payload.get("text") or decision.summary or "").strip()
        decision = BrainDecision.say(
            f"未进行联网搜索：当前模型不支持内置联网搜索，已自动回退为普通回答。\n\n{answer}"
        )
    return BrainCompletion(
        text=_decision_text(decision),
        provider=completion.provider,
        model=completion.model,
        decision=decision,
        raw_text=completion.text,
        usage=completion.usage,
        web_search_unavailable=web_search_unavailable,
    )
