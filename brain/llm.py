from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from .decisions import BrainDecision


PROVIDER_OPENAI = "openai_compatible"
PROVIDER_OLLAMA = "ollama"
PROVIDER_ANTHROPIC = "anthropic_compatible"
SUPPORTED_PROVIDERS = {PROVIDER_OPENAI, PROVIDER_OLLAMA, PROVIDER_ANTHROPIC}
STRUCTURED_REPLY_INSTRUCTIONS = """
你每次只能返回一个下一步决定。请优先返回一个 JSON 对象，不要使用 Markdown 代码块：
{"kind":"say","text":"给用户看的回复"}

当前可执行的 kind 只有 "say"。以下 kind 已预留给后续版本，但本版本不会直接执行：
- "observe": {"kind":"observe","target":"screen"}
- "act": {"kind":"act","action_type":"click","arguments":{"x":0,"y":0}}
- "remember": {"kind":"remember","category":"preference","text":"要保存的记忆"}
- "learn_skill": {"kind":"learn_skill","name":"技能名","steps":["步骤一"]}
- "stop": {"kind":"stop","summary":"本轮结束摘要"}

只聊天或回答问题时使用 "say"。不要把额外解释放在 JSON 外面。
""".strip()


class BrainLLMError(RuntimeError):
    pass


@dataclass(frozen=True)
class BrainMessage:
    role: str
    content: str

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

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BrainProviderConfig":
        source = data if isinstance(data, dict) else {}
        provider = normalize_provider(source.get("provider"))
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
            endpoint=str(source.get("model_endpoint") or source.get("endpoint") or "").strip(),
            model=str(source.get("model_name") or source.get("model") or "gpt-5.4").strip() or "gpt-5.4",
            api_key=str(source.get("api_key") or "").strip(),
            temperature=max(0.0, min(2.0, temperature)),
            max_tokens=max(1, min(32000, max_tokens)),
        )


@dataclass(frozen=True)
class BrainCompletion:
    text: str
    provider: str
    model: str
    decision: BrainDecision | None = None
    raw_text: str = ""


def normalize_provider(value: Any) -> str:
    provider = str(value or "").strip().lower()
    return provider if provider in SUPPORTED_PROVIDERS else PROVIDER_OPENAI


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


def _messages_payload(messages: list[BrainMessage]) -> list[dict[str, str]]:
    return [message.to_dict() for message in messages if str(message.content or "").strip()]


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


def parse_brain_reply(raw_text: str) -> BrainDecision:
    text = str(raw_text or "").strip()
    data = _json_object_from_text(text)
    if data is None:
        return BrainDecision.say(text)

    kind = str(data.get("kind") or data.get("type") or "say").strip().lower()
    if kind == "say":
        visible_text = str(data.get("text") or data.get("content") or data.get("summary") or "").strip()
        return BrainDecision.say(visible_text or text)
    if kind == "observe":
        return BrainDecision.observe(str(data.get("target") or "screen"))
    if kind in {"act", "propose_act"}:
        payload = _as_dict(data.get("payload"))
        arguments = _as_dict(data.get("arguments") or payload.get("arguments"))
        action_type = str(data.get("action_type") or payload.get("action_type") or data.get("action") or "").strip()
        return BrainDecision.propose_act(action_type, arguments)
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
        return BrainDecision.stop(str(data.get("summary") or data.get("text") or "").strip())
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
) -> BrainCompletion:
    if config.provider == PROVIDER_OLLAMA:
        return await _complete_ollama(config, messages, client=client)
    if config.provider == PROVIDER_ANTHROPIC:
        return await _complete_anthropic(config, messages, client=client)
    return await _complete_openai(config, messages, client=client)


async def _post_json(client: Any, url: str, *, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
    response = await client.post(url, headers=headers, json=payload)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise BrainLLMError("Brain provider returned a non-object JSON payload.")
    return data


async def _with_client(config: BrainProviderConfig, callback):
    timeout = httpx.Timeout(connect=8.0, read=config.timeout_sec, write=20.0, pool=8.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        return await callback(client)


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
            "messages": _messages_payload(messages),
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
        user_messages = [message.to_dict() for message in messages if message.role != "system" and message.content.strip()]
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
) -> list[BrainMessage]:
    persona = str(request_system_prompt or brain_config.get("persona") or "").strip()
    self_state = str(brain_config.get("self_state") or "").strip()
    response_style = str(brain_config.get("response_style") or "").strip()
    system_parts = [
        part
        for part in (
            persona,
            self_state,
            f"回复风格：{response_style}" if response_style else "",
            STRUCTURED_REPLY_INSTRUCTIONS,
        )
        if part
    ]
    return [
        BrainMessage(role="system", content="\n".join(system_parts)),
        BrainMessage(role="user", content=str(user_text or "").strip()),
    ]


async def run_brain_turn(
    brain_config: dict[str, Any],
    *,
    user_text: str,
    request_system_prompt: str = "",
    client: Any | None = None,
) -> BrainCompletion:
    provider_config = BrainProviderConfig.from_dict(brain_config)
    messages = build_turn_messages(
        brain_config=brain_config,
        user_text=user_text,
        request_system_prompt=request_system_prompt,
    )
    completion = await complete_with_provider(provider_config, messages, client=client)
    decision = parse_brain_reply(completion.text)
    return BrainCompletion(
        text=_decision_text(decision),
        provider=completion.provider,
        model=completion.model,
        decision=decision,
        raw_text=completion.text,
    )
