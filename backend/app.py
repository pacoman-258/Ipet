from __future__ import annotations

import asyncio
import json
import mimetypes
import re
import tempfile
import time
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import Body, FastAPI, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from brain.decisions import BrainDecision, DecisionKind
from brain.llm import BrainLLMError, list_provider_models, normalize_provider, run_brain_turn
from human_ops import ReviewableProposal, red_dot_click_preview

from .chat_topics import DEFAULT_TOPIC_TITLE, TopicStore, normalize_topic_id
from .models import TTSRequest
from .tts import (
    DEFAULT_PROVIDER as DEFAULT_TTS_PROVIDER,
    cleanup_old_audio,
    synthesize_to_audio,
    tts_available,
)
from .vision_analyzer import VisionAnalyzer


app = FastAPI(title="Ipet Neo Aspect Backend", version="0.3.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

ROOT_DIR = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT_DIR / "pet_config.json"
AUDIO_CACHE_DIR = ROOT_DIR / "backend" / "audio_cache"
CHAT_TOPICS_ROOT = ROOT_DIR / "data" / "chat_topics"
SETTINGS_HTML_PATH = ROOT_DIR / "settings.html"
SETTINGS_CSS_PATH = ROOT_DIR / "settings.css"
SETTINGS_JS_PATH = ROOT_DIR / "settings.js"
DESKTOP_COMMAND_PATH = ROOT_DIR / ".pet_desktop_command.json"

TOPIC_STORE = TopicStore(CHAT_TOPICS_ROOT)
HUMAN_OPS_PENDING_PROPOSALS: dict[str, dict[str, Any]] = {}

NEO_DEFAULTS: dict[str, Any] = {
    "model_path": "",
    "chat": {
        "backend_url": "http://127.0.0.1:8008",
        "model": "gpt-5.4",
        "session_id": "default",
        "voice": "zh-CN-XiaoxiaoNeural",
        "rate_pct": 0,
        "tts_provider": DEFAULT_TTS_PROVIDER,
        "tts_provider_url": "",
        "expression_mode": True,
        "expression_output_format": "ndjson_v1",
        "system_prompt": "",
        "asr": {
            "enabled": False,
            "provider": "disabled",
            "api_base_url": "",
            "push_to_talk_key": "Alt",
            "interim_results": False,
        },
    },
    "pet": {
        "scale": 0.3,
        "offset_x": 0,
        "offset_y": 40,
        "rotation": 0.0,
        "opacity": 1.0,
        "edit_mode": False,
        "follow_mouse": True,
        "background_enabled": False,
        "background_image": "",
        "background_overlay_opacity": 0.42,
    },
    "window": {
        "x": 120,
        "y": 80,
        "width": 420,
        "height": 640,
        "locked": False,
    },
    "brain": {
        "provider": "openai_compatible",
        "model_endpoint": "",
        "model_name": "gpt-5.4",
        "max_output_tokens": 1024,
        "persona": "你是 Ipet，一个有身体、有记忆、会一步一步操作电脑的陪伴助手。",
        "self_state": "等待用户目标，并在 act / remember / learn_skill 前请求批准。",
        "response_style": "lively",
        "decision_temperature": 0.4,
    },
    "human_ops": {
        "observe_screen": True,
        "accessibility": True,
        "require_act_review": True,
        "require_memory_review": True,
        "require_skill_review": True,
        "clipboard_write_review": True,
        "click_preview": {"x": 160, "y": 54, "label": "目标位置", "size": 16},
        "observe_model": {
            "enabled": False,
            "provider": "openai_compatible",
            "model_endpoint": "",
            "model_name": "",
            "max_output_tokens": 512,
        },
    },
    "memory": {
        "conversation_saving": True,
        "long_term_enabled": True,
        "preferences_enabled": True,
        "relationship_enabled": True,
        "retention_days": 365,
        "review_limit": 20,
        "review_queue": [],
    },
    "skills": {
        "recipes_enabled": True,
        "auto_propose": True,
        "review_required": True,
        "recipes": [],
        "proposal_queue": [],
    },
    "diagnostics": {
        "enabled": True,
        "log_level": "info",
        "last_error": "",
    },
}

ALLOWED_CONFIG_KEYS = set(NEO_DEFAULTS)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if key not in base:
            continue
        base_value = base.get(key)
        if isinstance(base_value, dict):
            if isinstance(value, dict):
                merged[key] = _deep_merge(base_value, value)
            continue
        if isinstance(value, dict):
            continue
        merged[key] = deepcopy(value)
    return merged


def _load_raw_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _normalize_private_config(raw: dict[str, Any] | None = None) -> dict[str, Any]:
    source = raw if isinstance(raw, dict) else _load_raw_config()
    selected = {key: source[key] for key in ALLOWED_CONFIG_KEYS if key in source}
    config = _deep_merge(NEO_DEFAULTS, selected)
    brain = config.setdefault("brain", {})
    raw_brain = source.get("brain") if isinstance(source.get("brain"), dict) else {}
    if raw_brain.get("api_key"):
        brain["api_key"] = str(raw_brain.get("api_key") or "")
    human_ops = config.setdefault("human_ops", {})
    raw_human_ops = source.get("human_ops") if isinstance(source.get("human_ops"), dict) else {}
    observe_model = human_ops.setdefault("observe_model", {})
    raw_observe_model = raw_human_ops.get("observe_model") if isinstance(raw_human_ops.get("observe_model"), dict) else {}
    if raw_observe_model.get("api_key"):
        observe_model["api_key"] = str(raw_observe_model.get("api_key") or "")
    return config


def _secret_preview(value: str) -> str:
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= 4:
        return "*" * len(text)
    return f"{text[:2]}***{text[-2:]}"


def _public_config(private_config: dict[str, Any]) -> dict[str, Any]:
    public = deepcopy(private_config)
    brain = public.get("brain")
    if not isinstance(brain, dict):
        brain = {}
        public["brain"] = brain
    secret = str(brain.pop("api_key", "") or "")
    brain.pop("api_key_clear", None)
    brain["api_key_set"] = bool(secret)
    brain["api_key_preview"] = _secret_preview(secret)
    human_ops = public.get("human_ops")
    if not isinstance(human_ops, dict):
        human_ops = {}
        public["human_ops"] = human_ops
    observe_model = human_ops.get("observe_model")
    if not isinstance(observe_model, dict):
        observe_model = {}
        human_ops["observe_model"] = observe_model
    observe_secret = str(observe_model.pop("api_key", "") or "")
    observe_model.pop("api_key_clear", None)
    observe_model["api_key_set"] = bool(observe_secret)
    observe_model["api_key_preview"] = _secret_preview(observe_secret)
    return public


def _settings_payload(private_config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = _normalize_private_config(private_config)
    return {
        "config": _public_config(config),
        "defaults": _public_config(_normalize_private_config(NEO_DEFAULTS)),
    }


def _save_config(private_config: dict[str, Any]) -> None:
    CONFIG_PATH.write_text(
        json.dumps(private_config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _sanitize_brain_error(error: Exception, brain_config: dict[str, Any]) -> str:
    text = str(error or "").strip() or "unknown provider error"
    secret = str(brain_config.get("api_key") or "")
    endpoint = str(brain_config.get("model_endpoint") or "")
    for sensitive in (secret, endpoint):
        if sensitive:
            text = text.replace(sensitive, "[redacted]")
    text = re.sub(r"(?i)(bearer\s+)[^\s,;]+", r"\1[redacted]", text)
    text = re.sub(r"(?i)((?:api[_-]?key|token|key)=)[^\s&]+", r"\1[redacted]", text)
    return text[:240] + "..." if len(text) > 240 else text


def _decision_from_completion(completion: Any) -> BrainDecision:
    decision = getattr(completion, "decision", None)
    if isinstance(decision, BrainDecision):
        return decision
    return BrainDecision.say(str(getattr(completion, "text", "") or ""))


_DESKTOP_OBSERVE_TERMS = (
    "看屏幕",
    "观察屏幕",
    "看看屏幕",
    "读屏幕",
    "当前屏幕",
    "屏幕上",
)
_DESKTOP_ACTION_TERMS = (
    "点击",
    "点一下",
    "点开",
    "打开",
    "启动",
    "切到",
    "选择",
    "按下",
)
_DESKTOP_TARGET_TERMS = (
    "dock",
    "程序坞",
    "app",
    "应用",
    "窗口",
    "按钮",
    "图标",
    "输入框",
    "设置",
)
_DESKTOP_EXPLANATION_TERMS = ("怎么", "如何", "为什么", "原理", "介绍", "解释")


def _looks_like_desktop_observe_request(user_text: str) -> bool:
    text = str(user_text or "").strip().lower()
    return bool(text) and any(term in text for term in _DESKTOP_OBSERVE_TERMS)


def _looks_like_desktop_action_request(user_text: str) -> bool:
    text = str(user_text or "").strip().lower()
    if not text:
        return False
    if any(term in text for term in _DESKTOP_EXPLANATION_TERMS) and not any(prefix in text for prefix in ("帮我", "请", "试试")):
        return False
    has_action = any(term in text for term in _DESKTOP_ACTION_TERMS)
    has_target = any(term in text for term in _DESKTOP_TARGET_TERMS)
    return has_action and has_target


def _looks_like_click_request(user_text: str) -> bool:
    text = str(user_text or "").strip().lower()
    return any(term in text for term in ("点击", "点一下", "点开", "click")) and any(
        target in text for target in _DESKTOP_TARGET_TERMS
    )


def _coerce_decision_for_human_ops(user_text: str, decision: BrainDecision) -> BrainDecision:
    if _decision_kind(decision) != DecisionKind.SAY:
        return decision
    if _looks_like_desktop_observe_request(user_text) or _looks_like_desktop_action_request(user_text):
        return BrainDecision.observe(str(user_text or "screen")[:120])
    return decision


def _decision_kind(decision: BrainDecision) -> DecisionKind:
    return decision.kind if isinstance(decision.kind, DecisionKind) else DecisionKind(str(decision.kind))


def _coerce_int(value: Any, fallback: int = 0) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return fallback


def _proposal_tool_label(proposal: ReviewableProposal) -> str:
    action_type = str(proposal.payload.get("action_type") or "").strip()
    args = proposal.payload.get("arguments") if isinstance(proposal.payload.get("arguments"), dict) else {}
    if action_type == "click":
        label = str(args.get("label") or args.get("target") or "目标位置").strip() or "目标位置"
        return f"点击 {label} ({_coerce_int(args.get('x'))}, {_coerce_int(args.get('y'))})"
    return proposal.summary or action_type


def _proposal_event_payload(proposal_id: str, proposal: ReviewableProposal) -> dict[str, Any]:
    action_type = str(proposal.payload.get("action_type") or "").strip()
    return {
        "turn_id": proposal_id,
        "proposal_id": proposal_id,
        "proposal_type": proposal.proposal_type,
        "action_type": action_type,
        "text": proposal.summary,
        "summary": proposal.summary,
        "tools": [{"name": action_type, "summary": _proposal_tool_label(proposal)}] if action_type else [],
        "preview": proposal.preview.to_dict() if proposal.preview else None,
        "requires_review": True,
    }


def _create_human_ops_act_proposal(decision: BrainDecision, *, session_id: str, user_text: str) -> tuple[str, ReviewableProposal]:
    payload = decision.payload if isinstance(decision.payload, dict) else {}
    action_type = str(payload.get("action_type") or "").strip()
    arguments = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
    preview = None
    if action_type == "click":
        label = str(arguments.get("label") or arguments.get("target") or "目标位置").strip() or "目标位置"
        preview = red_dot_click_preview(x=_coerce_int(arguments.get("x")), y=_coerce_int(arguments.get("y")), label=label)
        summary = f"Ipet 想点击：{label}"
    else:
        summary = decision.summary or f"Ipet 想执行：{action_type}"
    proposal = ReviewableProposal.act(
        action_type=action_type,
        summary=summary,
        payload=dict(arguments),
        preview=preview,
    )
    proposal_id = uuid4().hex
    HUMAN_OPS_PENDING_PROPOSALS[proposal_id] = {
        "proposal": proposal,
        "session_id": session_id,
        "user_text": user_text,
        "created_at": time.time(),
        "status": "pending",
    }
    return proposal_id, proposal


async def _send_desktop_command(command_type: str, payload: dict[str, Any] | None = None, *, timeout_sec: float = 8.0) -> dict[str, Any]:
    nonce = uuid4().hex
    response_path = Path(tempfile.gettempdir()) / f"ipet-{command_type}-{nonce}.response.json"
    command_payload = dict(payload or {})
    command_payload["response_path"] = str(response_path)
    command = {
        "nonce": nonce,
        "type": str(command_type or "").strip(),
        "payload": command_payload,
        "timestamp_ns": time.time_ns(),
    }
    try:
        if response_path.exists():
            response_path.unlink()
    except Exception:
        pass
    tmp_path = DESKTOP_COMMAND_PATH.with_name(f"{DESKTOP_COMMAND_PATH.name}.{nonce}.tmp")
    tmp_path.write_text(json.dumps(command, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(DESKTOP_COMMAND_PATH)

    deadline = time.monotonic() + max(0.2, float(timeout_sec))
    while time.monotonic() < deadline:
        if response_path.exists():
            try:
                response = json.loads(response_path.read_text(encoding="utf-8"))
            except Exception:
                await asyncio.sleep(0.05)
                continue
            try:
                response_path.unlink()
            except Exception:
                pass
            if str(response.get("nonce") or "") != nonce:
                await asyncio.sleep(0.05)
                continue
            result = response.get("result") if isinstance(response.get("result"), dict) else {}
            if response.get("ok") or response.get("status") == "success":
                return result
            detail = str(result.get("error") or response.get("status") or "desktop command failed")
            raise RuntimeError(detail)
        await asyncio.sleep(0.05)
    raise TimeoutError(f"desktop command timed out: {command_type}")


def _observation_text_from_result(result: dict[str, Any]) -> str:
    frame = result.get("frame") if isinstance(result.get("frame"), dict) else {}
    capture_backend = str(frame.get("capture_backend") or "").strip()
    has_screenshot = str(frame.get("data_url") or "").startswith("data:image/")
    analysis = frame.get("analysis") if isinstance(frame.get("analysis"), dict) else {}
    observations = frame.get("observations") if isinstance(frame.get("observations"), list) else []
    observe_answer = str(frame.get("observe_answer") or "").strip()
    if observe_answer:
        return observe_answer
    claims: list[str] = []
    for item in observations[:3]:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or item.get("claim") or item.get("summary") or "").strip()
        if text:
            claims.append(text)
    if claims:
        return "我看到：" + "；".join(claims)
    active = frame.get("active_observation") if isinstance(frame.get("active_observation"), dict) else {}
    foreground = frame.get("foreground_app") if isinstance(frame.get("foreground_app"), dict) else {}
    app_name = str(foreground.get("name") or active.get("app_name") or "").strip()
    window_title = str(foreground.get("window_title") or active.get("window_title") or "").strip()
    if app_name or window_title:
        parts = [part for part in (app_name, window_title) if part]
        return "我看了一下屏幕，前台大概是：" + " · ".join(parts)
    unknowns = []
    for source in (frame, active, result.get("trace") if isinstance(result.get("trace"), dict) else {}):
        values = source.get("unknowns") if isinstance(source.get("unknowns"), list) else []
        unknowns.extend(str(item or "").strip() for item in values if str(item or "").strip())
    analysis_error = str(analysis.get("last_error") or "").strip()
    if analysis_error:
        unknowns.append(analysis_error)
    unknowns = [
        item
        for item in unknowns
        if "无法读取 macOS 前台应用元数据" not in item
        and "active vision metadata observation failed" not in item
        and "osascript" not in item.lower()
    ]
    if unknowns:
        return "我试着观察了屏幕，但还不能确认内容：" + "；".join(unknowns[:2])
    if has_screenshot and bool(analysis.get("enabled")):
        provider = str(analysis.get("provider") or "observe 模型").strip()
        status = str(analysis.get("status") or "").strip()
        if status == "ok":
            return f"我已经截取了当前屏幕（{capture_backend or 'screen_capture'}），也调用了 observe 模型（{provider}），但模型没有返回可见内容。"
        if status:
            return f"我已经截取了当前屏幕（{capture_backend or 'screen_capture'}），但 observe 模型（{provider}）状态为 {status}，还没有可用分析结果。"
    if has_screenshot:
        method = f"（{capture_backend}）" if capture_backend else ""
        return f"我已经截取了当前屏幕{method}，但还没有提取到明确内容。"
    return "我看了一下屏幕，但还没有提取到明确内容。"


def _screen_resolution_from_frame(frame: dict[str, Any]) -> dict[str, int]:
    layout = frame.get("display_layout") if isinstance(frame.get("display_layout"), list) else []
    bounds: list[dict[str, Any]] = [item for item in layout if isinstance(item, dict)]
    if bounds:
        try:
            min_x = min(int(item.get("x") or 0) for item in bounds)
            min_y = min(int(item.get("y") or 0) for item in bounds)
            max_x = max(int(item.get("x") or 0) + int(item.get("width") or 0) for item in bounds)
            max_y = max(int(item.get("y") or 0) + int(item.get("height") or 0) for item in bounds)
            width = max(0, max_x - min_x)
            height = max(0, max_y - min_y)
            if width > 0 and height > 0:
                return {"width": width, "height": height}
        except Exception:
            pass
    width = _coerce_int(frame.get("width"))
    height = _coerce_int(frame.get("height"))
    return {"width": width, "height": height} if width > 0 and height > 0 else {}


def _default_observe_prompt_for_request(user_text: str, target: str) -> str:
    text = str(user_text or target or "").strip()
    if _looks_like_click_request(text):
        return f"我需要找到“{text or target}”对应的可点击目标。请观看屏幕截图，用自然语言告诉我它是否可见、可见依据，以及可点击中心点的绝对屏幕坐标 x 和 y。"
    if any(term in text for term in ("读", "文字", "内容", "写着", "显示")):
        return f"我需要读取当前屏幕中和“{text or target}”相关的可见文字和内容。请只根据截图用自然语言回答。"
    if any(term in text for term in ("是否", "有没有", "状态", "成功", "失败", "完成")):
        return f"我需要判断当前屏幕状态是否满足“{text or target}”。请根据可见界面给出结论和依据。"
    if any(term in text.lower() for term in ("dock", "程序坞", "app", "应用", "按钮", "图标", "窗口", "输入框")):
        return f"我需要找到屏幕上和“{text or target}”相关的界面目标。请描述它的位置、可见文字或图标依据；不需要输出 JSON。"
    return f"我需要了解当前屏幕和“{text or target or '当前任务'}”相关的主要可见内容。请概括窗口、文字和状态。"


def _observe_prompt_from_decision(decision: BrainDecision, user_text: str) -> str:
    payload = decision.payload if isinstance(decision.payload, dict) else {}
    target = str(payload.get("target") or user_text or "screen").strip() or "screen"
    observe_prompt = str(payload.get("observe_prompt") or payload.get("question") or "").strip()
    if not observe_prompt:
        observe_prompt = _default_observe_prompt_for_request(user_text, target)
    return observe_prompt[:700]


def _frame_with_observe_prompt(frame: dict[str, Any], decision: BrainDecision, user_text: str) -> dict[str, Any]:
    enriched = dict(frame or {})
    active = enriched.get("active_observation") if isinstance(enriched.get("active_observation"), dict) else {}
    active = dict(active)
    target = str(decision.payload.get("target") or user_text or "screen").strip() if isinstance(decision.payload, dict) else "screen"
    active["target_hint"] = str(active.get("target_hint") or target or "screen").strip()[:220]
    active["observe_prompt"] = _observe_prompt_from_decision(decision, user_text)
    resolution = _screen_resolution_from_frame(enriched)
    if resolution:
        active["screen_resolution"] = resolution
    enriched["active_observation"] = active
    return enriched


def _observe_model_analyzer_config(human_ops_config: dict[str, Any]) -> dict[str, Any]:
    observe_model = (
        human_ops_config.get("observe_model") if isinstance(human_ops_config.get("observe_model"), dict) else {}
    )
    if not bool(observe_model.get("enabled", False)):
        return {"enabled": False, "provider": "none"}
    provider = normalize_provider(observe_model.get("provider"))
    if provider == "ollama":
        analyzer_provider = "local_vlm"
    elif provider == "openai_compatible":
        analyzer_provider = "openai_compatible_vlm"
    else:
        return {"enabled": False, "provider": "none"}
    return {
        "enabled": True,
        "provider": analyzer_provider,
        "base_url": str(observe_model.get("model_endpoint") or observe_model.get("endpoint") or "").strip(),
        "model": str(observe_model.get("model_name") or observe_model.get("model") or "").strip(),
        "api_key": str(observe_model.get("api_key") or "").strip(),
        "timeout_sec": 12.0,
        "max_observations": 4,
        "image_detail": "low",
    }


def _enrich_observation_frame_with_model(frame: dict[str, Any], human_ops_config: dict[str, Any]) -> dict[str, Any]:
    analyzer_config = _observe_model_analyzer_config(human_ops_config)
    if not analyzer_config.get("enabled"):
        return frame
    result = VisionAnalyzer(analyzer_config).enrich_payload(frame)
    return result.payload if isinstance(result.payload, dict) else frame


async def _perform_human_ops_observe(decision: BrainDecision, human_ops_config: dict[str, Any]) -> dict[str, Any]:
    if not bool(human_ops_config.get("observe_screen", True)):
        return {
            "text": "看屏幕权限已关闭，请在设置页开启 Human Ops 的观察权限。",
            "observations": [],
            "unknowns": ["observe_screen disabled"],
        }
    target = str(decision.payload.get("target") or "screen").strip() or "screen"
    result = await _send_desktop_command(
        "active_vision_capture",
        {"mode": "desktop_survey", "target_hint": target, "target": target},
        timeout_sec=8,
    )
    frame = result.get("frame") if isinstance(result.get("frame"), dict) else {}
    frame = _frame_with_observe_prompt(frame, decision, target)
    analyzer_config = _observe_model_analyzer_config(human_ops_config)
    if not analyzer_config.get("enabled"):
        result = {**result, "frame": frame}
        capture_backend = str(frame.get("capture_backend") or "screen_capture").strip() or "screen_capture"
        if _looks_like_click_request(target):
            text = "我已经截取了当前屏幕，但这一步需要配置 Human Ops observe 模型来读取图像并返回可点击坐标。"
        else:
            text = f"截图成功（{capture_backend}），但 Human Ops observe 模型未启用，所以目前只能证明截图成功，不能读取截图内容。"
        return {
            "text": text,
            "frame": frame,
            "trace": result.get("trace") if isinstance(result.get("trace"), dict) else {},
            "observations": frame.get("observations") if isinstance(frame.get("observations"), list) else [],
            "unknowns": ["observe_model disabled"],
        }
    frame = _enrich_observation_frame_with_model(frame, human_ops_config)
    result = {**result, "frame": frame}
    return {
        "text": _observation_text_from_result(result),
        "frame": frame,
        "trace": result.get("trace") if isinstance(result.get("trace"), dict) else {},
        "observations": frame.get("observations") if isinstance(frame.get("observations"), list) else [],
        "unknowns": frame.get("unknowns") if isinstance(frame.get("unknowns"), list) else [],
    }


async def _perform_human_ops_click(proposal: ReviewableProposal) -> dict[str, Any]:
    if proposal.proposal_type != "act" or str(proposal.payload.get("action_type") or "") != "click":
        raise RuntimeError("unsupported human ops proposal")
    args = proposal.payload.get("arguments") if isinstance(proposal.payload.get("arguments"), dict) else {}
    payload = {
        "x": _coerce_int(args.get("x")),
        "y": _coerce_int(args.get("y")),
        "label": str(args.get("label") or args.get("target") or "目标位置").strip() or "目标位置",
    }
    result = await _send_desktop_command("human_ops_click", payload, timeout_sec=5)
    return {"clicked": True, **payload, **result}


def _brain_model_to_dict(model: Any) -> dict[str, str]:
    if hasattr(model, "to_dict"):
        data = model.to_dict()
        if isinstance(data, dict):
            model_id = str(data.get("id") or "").strip()
            label = str(data.get("label") or model_id).strip()
            return {"id": model_id, "label": label or model_id}
    model_id = str(getattr(model, "id", "") or "").strip()
    label = str(getattr(model, "label", "") or model_id).strip()
    return {"id": model_id, "label": label or model_id}


def _safe_audio_path(file_name: str) -> Path:
    candidate = Path(file_name)
    if candidate.name != file_name:
        raise HTTPException(status_code=404, detail="Audio file not found.")
    path = AUDIO_CACHE_DIR / candidate.name
    try:
        path.relative_to(AUDIO_CACHE_DIR)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Audio file not found.") from exc
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Audio file not found.")
    return path


@app.get("/api/health")
async def health() -> dict[str, Any]:
    config = _normalize_private_config()
    return {
        "status": "ok",
        "service": "ipet-neo-aspect-backend",
        "version": app.version,
        "neo_aspect": {
            "body": True,
            "brain": True,
            "human_ops": True,
            "memory": bool(config.get("memory", {}).get("conversation_saving", True)),
            "skills": bool(config.get("skills", {}).get("recipes_enabled", True)),
        },
        "asr": False,
        "tts": tts_available(
            config.get("chat", {}).get("tts_provider"),
            config.get("chat", {}).get("tts_provider_url"),
        ),
        "message": "Neo Aspect backend is running with the new Body, Brain, Human Ops, Memory & Skills contract.",
    }


@app.get("/settings")
async def settings_page() -> FileResponse:
    if not SETTINGS_HTML_PATH.exists():
        raise HTTPException(status_code=404, detail="settings.html not found.")
    return FileResponse(SETTINGS_HTML_PATH)


@app.get("/settings.css")
async def settings_css() -> FileResponse:
    if not SETTINGS_CSS_PATH.exists():
        raise HTTPException(status_code=404, detail="settings.css not found.")
    return FileResponse(SETTINGS_CSS_PATH, media_type="text/css")


@app.get("/settings.js")
async def settings_js() -> FileResponse:
    if not SETTINGS_JS_PATH.exists():
        raise HTTPException(status_code=404, detail="settings.js not found.")
    return FileResponse(SETTINGS_JS_PATH, media_type="application/javascript")


@app.get("/api/settings/config")
async def get_settings_config() -> dict[str, Any]:
    return _settings_payload()


@app.put("/api/settings/config")
async def put_settings_config(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    incoming = payload.get("config") if isinstance(payload, dict) else {}
    if not isinstance(incoming, dict):
        raise HTTPException(status_code=400, detail="config must be an object.")

    current = _normalize_private_config()
    next_config = _deep_merge(current, incoming)
    incoming_brain = incoming.get("brain") if isinstance(incoming.get("brain"), dict) else {}
    brain = next_config.setdefault("brain", {})
    if incoming_brain.get("api_key_clear"):
        brain.pop("api_key", None)
    elif "api_key" in incoming_brain:
        secret = str(incoming_brain.get("api_key") or "")
        if secret:
            brain["api_key"] = secret
    brain.pop("api_key_clear", None)
    incoming_human_ops = incoming.get("human_ops") if isinstance(incoming.get("human_ops"), dict) else {}
    incoming_observe_model = (
        incoming_human_ops.get("observe_model") if isinstance(incoming_human_ops.get("observe_model"), dict) else {}
    )
    human_ops = next_config.setdefault("human_ops", {})
    observe_model = human_ops.setdefault("observe_model", {})
    if incoming_observe_model.get("api_key_clear"):
        observe_model.pop("api_key", None)
    elif "api_key" in incoming_observe_model:
        observe_secret = str(incoming_observe_model.get("api_key") or "")
        if observe_secret:
            observe_model["api_key"] = observe_secret
    observe_model.pop("api_key_clear", None)

    sanitized = _normalize_private_config(next_config)
    _save_config(sanitized)
    return _settings_payload(sanitized)


@app.post("/api/brain/models")
async def get_brain_models(payload: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    body = payload if isinstance(payload, dict) else {}
    current = _normalize_private_config()
    saved_brain = current.get("brain", {}) if isinstance(current.get("brain"), dict) else {}
    saved_human_ops = current.get("human_ops", {}) if isinstance(current.get("human_ops"), dict) else {}
    saved_observe_model = (
        saved_human_ops.get("observe_model") if isinstance(saved_human_ops.get("observe_model"), dict) else {}
    )
    scope = str(body.get("scope") or "brain").strip()
    saved_model_config = saved_observe_model if scope in {"observe", "human_ops.observe_model"} else saved_brain
    provider = normalize_provider(body.get("provider", saved_model_config.get("provider")))
    endpoint = str(body.get("model_endpoint") or body.get("endpoint") or saved_model_config.get("model_endpoint") or "").strip()
    submitted_key = str(body.get("api_key") or "").strip()
    saved_provider = normalize_provider(saved_model_config.get("provider"))
    saved_key = str(saved_model_config.get("api_key") or "").strip()
    api_key = submitted_key or (saved_key if provider == saved_provider else "")
    if not endpoint:
        raise HTTPException(status_code=400, detail="Brain model endpoint is required.")

    request_config = {
        **saved_model_config,
        "provider": provider,
        "model_endpoint": endpoint,
        "api_key": api_key,
    }
    try:
        models = await list_provider_models(request_config)
    except BrainLLMError as exc:
        detail = _sanitize_brain_error(exc, request_config)
        raise HTTPException(status_code=502, detail=f"Brain model list failed: {detail}") from exc
    except Exception as exc:
        detail = _sanitize_brain_error(exc, request_config)
        raise HTTPException(status_code=502, detail=f"Brain model list failed: {detail}") from exc

    return {
        "ok": True,
        "provider": provider,
        "models": [item for item in (_brain_model_to_dict(model) for model in models) if item["id"]],
    }


@app.post("/api/chat/stream")
async def chat_stream(payload: dict[str, Any] | None = Body(default=None)) -> StreamingResponse:
    request_payload = payload if isinstance(payload, dict) else {}
    text = str(request_payload.get("text") or request_payload.get("message") or "").strip()
    session_id = normalize_topic_id(str(request_payload.get("session_id") or "default"))
    try:
        retry_from_assistant_turn = int(request_payload.get("retry_from_assistant_turn") or 0)
    except (TypeError, ValueError):
        retry_from_assistant_turn = 0
    turn_id = uuid4().hex
    private_config = _normalize_private_config()
    brain_config = private_config.get("brain", {}) if isinstance(private_config.get("brain"), dict) else {}
    human_ops_config = private_config.get("human_ops", {}) if isinstance(private_config.get("human_ops"), dict) else {}
    model = str(request_payload.get("model") or brain_config.get("model_name") or "gpt-5.4")
    endpoint_configured = bool(str(brain_config.get("model_endpoint") or "").strip())
    provider_hint = str(brain_config.get("provider") or "openai_compatible")
    initial_provider = provider_hint if endpoint_configured else "local_placeholder"

    async def resolve_reply() -> tuple[str, str, str, BrainDecision]:
        if not endpoint_configured:
            reply = (
                "Neo Brain placeholder: 我已经收到你的消息。当前还没有配置 Brain 模型端点；"
                "请在设置页选择 provider 并填写模型端点。"
            )
            return (
                reply,
                initial_provider,
                model,
                BrainDecision.say(reply),
            )
        try:
            completion = await run_brain_turn(
                brain_config,
                user_text=text,
                request_system_prompt=str(request_payload.get("system_prompt") or ""),
            )
            decision = _decision_from_completion(completion)
            return str(completion.text or decision.summary), completion.provider, completion.model, decision
        except BrainLLMError as exc:
            detail = _sanitize_brain_error(exc, brain_config)
            reply = f"Brain 调用失败：{detail}"
            return reply, provider_hint, model, BrainDecision.say(reply)

    async def resolve_after_observe(observation_text: str) -> tuple[str, str, str, BrainDecision]:
        if not endpoint_configured:
            return observation_text, initial_provider, model, BrainDecision.say(observation_text)
        followup_text = (
            f"用户原始请求：{text}\n\n"
            "你刚才让 observe 模型查看了屏幕。observe 用自然语言回答如下：\n"
            f"{observation_text}\n\n"
            "请基于这个观察结果决定下一步。不要因为格式问题要求 observe 输出 JSON。"
            "如果用户只是问屏幕内容，请用 say 直接回答。"
            "如果用户要求点击且观察结果里有可信绝对屏幕坐标，请使用 propose_act。"
            "如果观察结果说看不到或不确定，请用 say 如实告诉用户。"
        )
        try:
            completion = await run_brain_turn(
                brain_config,
                user_text=followup_text,
                request_system_prompt=str(request_payload.get("system_prompt") or ""),
            )
            followup_decision = _decision_from_completion(completion)
            return str(completion.text or followup_decision.summary), completion.provider, completion.model, followup_decision
        except BrainLLMError as exc:
            detail = _sanitize_brain_error(exc, brain_config)
            reply = f"Brain 读取 observe 结果失败：{detail}"
            return reply, provider_hint, model, BrainDecision.say(reply)
        except Exception as exc:
            detail = _sanitize_brain_error(exc, brain_config)
            reply = f"Brain 读取 observe 结果失败：{detail}"
            return reply, provider_hint, model, BrainDecision.say(reply)
        except Exception as exc:
            detail = _sanitize_brain_error(exc, brain_config)
            reply = f"Brain 调用失败：{detail}"
            return reply, provider_hint, model, BrainDecision.say(reply)

    async def event_stream():
        yield _sse(
            "meta",
            {
                "turn_id": turn_id,
                "session_id": session_id,
                "backend": "neo_aspect",
                "provider": initial_provider,
                "model": model,
            },
        )
        yield _sse("phase", {"name": "neo_brain", "status": "running", "text": f"Brain provider: {initial_provider}"})
        if retry_from_assistant_turn > 0:
            TOPIC_STORE.truncate_from_assistant_turn(session_id, retry_from_assistant_turn)
        reply, provider, used_model, decision = await resolve_reply()
        decision = _coerce_decision_for_human_ops(text, decision)
        decision_kind = _decision_kind(decision)
        if decision_kind == DecisionKind.OBSERVE:
            yield _sse("phase", {"name": "human_ops_observe", "status": "running", "text": "Human Ops: observe"})
            try:
                observation = await _perform_human_ops_observe(decision, human_ops_config)
            except Exception as exc:
                observation = {
                    "text": f"观察失败：{_sanitize_brain_error(exc, brain_config)}",
                    "observations": [],
                    "unknowns": [str(exc)],
                }
            observation_text = str(observation.get("text") or decision.summary or reply).strip() or "我看了一下屏幕。"
            followup_reply, provider, used_model, next_decision = await resolve_after_observe(observation_text)
            if _decision_kind(next_decision) == DecisionKind.OBSERVE:
                followup_reply = observation_text
                next_decision = BrainDecision.say(observation_text)
            if next_decision is not None:
                if _decision_kind(next_decision) == DecisionKind.PROPOSE_ACT:
                    yield _sse("display_segment", {"text": observation_text})
                    proposal_id, proposal = _create_human_ops_act_proposal(next_decision, session_id=session_id, user_text=text)
                    yield _sse(
                        "phase",
                        {"name": "human_ops_review", "status": "waiting", "text": "Human Ops: waiting for review"},
                    )
                    yield _sse("approval_required", _proposal_event_payload(proposal_id, proposal))
                    return
            await asyncio.sleep(0)
            final_text = str(followup_reply or observation_text).strip() or observation_text
            yield _sse("token", {"text": final_text})
            yield _sse("segment", {"text": final_text, "expression": "normal"})
            yield _sse("display_segment", {"text": final_text})
            TOPIC_STORE.append_exchange(session_id, user_text=text, assistant_text=final_text)
            yield _sse(
                "done",
                {
                    "turn_id": turn_id,
                    "session_id": session_id,
                    "text": final_text,
                    "provider": provider,
                    "model": used_model,
                    "decision": next_decision.to_dict(),
                    "observation": observation,
                    "retry_from_assistant_turn": retry_from_assistant_turn or None,
                },
            )
            return
        if decision_kind == DecisionKind.PROPOSE_ACT:
            proposal_id, proposal = _create_human_ops_act_proposal(decision, session_id=session_id, user_text=text)
            yield _sse("phase", {"name": "human_ops_review", "status": "waiting", "text": "Human Ops: waiting for review"})
            yield _sse("approval_required", _proposal_event_payload(proposal_id, proposal))
            return
        await asyncio.sleep(0)
        yield _sse("token", {"text": reply})
        yield _sse("segment", {"text": reply, "expression": "normal"})
        yield _sse("display_segment", {"text": reply})
        TOPIC_STORE.append_exchange(session_id, user_text=text, assistant_text=reply)
        yield _sse(
            "done",
            {
                "turn_id": turn_id,
                "session_id": session_id,
                "text": reply,
                "provider": provider,
                "model": used_model,
                "decision": decision.to_dict(),
                "retry_from_assistant_turn": retry_from_assistant_turn or None,
            },
        )

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/api/human-ops/proposals/{proposal_id}/decision")
async def decide_human_ops_proposal(
    proposal_id: str,
    payload: dict[str, Any] | None = Body(default=None),
) -> StreamingResponse:
    decision_payload = payload if isinstance(payload, dict) else {}
    approved = bool(decision_payload.get("approved"))
    proposal_key = str(proposal_id or "").strip()
    record = HUMAN_OPS_PENDING_PROPOSALS.get(proposal_key)
    if not record:
        raise HTTPException(status_code=404, detail="Human Ops proposal not found.")
    proposal = record.get("proposal")
    if not isinstance(proposal, ReviewableProposal):
        raise HTTPException(status_code=404, detail="Human Ops proposal not found.")
    session_id = str(record.get("session_id") or "default")
    label = _proposal_tool_label(proposal)

    async def event_stream():
        yield _sse(
            "meta",
            {
                "turn_id": proposal_key,
                "proposal_id": proposal_key,
                "session_id": session_id,
                "backend": "neo_aspect",
                "provider": "human_ops",
                "model": "desktop",
            },
        )
        if not approved:
            record["status"] = "rejected"
            user_text = str(decision_payload.get("user_text") or "").strip()
            final_text = "已拒绝这次点击。" + (f" 调整说明：{user_text}" if user_text else "")
            yield _sse("display_segment", {"text": final_text})
            yield _sse(
                "done",
                {
                    "turn_id": proposal_key,
                    "proposal_id": proposal_key,
                    "session_id": session_id,
                    "text": final_text,
                    "approved": False,
                    "execution": None,
                },
            )
            return

        record["status"] = "approved"
        yield _sse("phase", {"name": "human_ops_click", "status": "running", "text": label})
        try:
            execution = await _perform_human_ops_click(proposal.approve())
            final_text = f"已执行点击：{label}。"
            record["status"] = "executed"
        except Exception as exc:
            execution = {"ok": False, "error": str(exc)}
            final_text = f"点击执行失败：{str(exc)[:180]}"
            record["status"] = "failed"
        yield _sse("display_segment", {"text": final_text})
        yield _sse(
            "done",
            {
                "turn_id": proposal_key,
                "proposal_id": proposal_key,
                "session_id": session_id,
                "text": final_text,
                "approved": True,
                "execution": execution,
            },
        )

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/skills")
async def list_skills() -> dict[str, Any]:
    return {"skills": [], "recipes": [], "items": []}


@app.post("/api/chat/topics")
async def create_chat_topic(payload: dict[str, Any] | None = Body(default=None)) -> dict[str, Any]:
    body = payload if isinstance(payload, dict) else {}
    topic_id = body.get("topic_id") or body.get("session_id")
    title = str(body.get("title") or DEFAULT_TOPIC_TITLE)
    persisted = bool(body.get("persisted", True))
    topic = TOPIC_STORE.create_topic(topic_id=topic_id, title=title, persisted=persisted)
    return {"ok": True, "topic": topic, **topic}


@app.get("/api/chat/topics")
async def list_chat_topics() -> dict[str, Any]:
    return {"ok": True, "topics": TOPIC_STORE.list_topics()}


@app.get("/api/chat/topics/{topic_id}")
async def get_chat_topic(topic_id: str) -> dict[str, Any]:
    detail = TOPIC_STORE.get_topic_detail(topic_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Topic not found.")
    return {"ok": True, **detail}


@app.delete("/api/chat/topics/{topic_id}")
async def delete_chat_topic(topic_id: str) -> dict[str, Any]:
    deleted = TOPIC_STORE.delete_topic(topic_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Topic not found.")
    return {"ok": True, "deleted": True, "topics": TOPIC_STORE.list_topics()}


@app.post("/api/chat/topics/{topic_id}/delete")
async def delete_chat_topic_post(topic_id: str) -> dict[str, Any]:
    return await delete_chat_topic(topic_id)


@app.post("/api/asr/warmup")
async def asr_warmup() -> dict[str, Any]:
    return {
        "ok": False,
        "enabled": False,
        "available": False,
        "detail": "ASR is disabled in the Neo Aspect minimal backend.",
    }


@app.websocket("/api/asr/stream")
async def asr_stream(websocket: WebSocket) -> None:
    await websocket.accept()
    await websocket.send_json(
        {
            "type": "error",
            "detail": "ASR streaming is disabled in the Neo Aspect minimal backend.",
        }
    )
    await websocket.close(code=1000)


@app.post("/api/tts")
async def tts(request: TTSRequest) -> dict[str, Any]:
    if not request.text.strip():
        raise HTTPException(status_code=400, detail="text is required.")
    if not tts_available(request.provider, request.provider_url):
        raise HTTPException(status_code=503, detail=f"TTS provider is unavailable: {request.provider}")
    try:
        cleanup_old_audio(AUDIO_CACHE_DIR)
        result = await synthesize_to_audio(
            text=request.text,
            cache_dir=AUDIO_CACHE_DIR,
            voice=request.voice,
            rate=request.rate,
            volume=request.volume,
            provider=request.provider,
            provider_url=request.provider_url,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"TTS synthesis failed: {exc}") from exc
    return {
        "ok": True,
        "file_id": result.file_id,
        "url": f"/api/audio/{result.path.name}",
        "audio_url": f"/api/audio/{result.path.name}",
        "duration_ms": result.duration_ms,
        "media_type": result.media_type,
    }


@app.get("/api/audio/{file_name}")
async def audio(file_name: str) -> FileResponse:
    path = _safe_audio_path(file_name)
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type)
