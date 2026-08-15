from __future__ import annotations

import json
from typing import Any, Iterable


GOAL_STATUSES = (
    "in_progress",
    "ready_for_review",
    "handoff_review",
    "done",
    "blocked",
    "need_user",
)
TERMINAL_GOAL_STATUSES = frozenset({"done", "blocked", "need_user"})
MEMORY_CATEGORIES = (
    "profile",
    "preference",
    "boundary",
    "person",
    "open_loop",
    "shared_moment",
    "general",
    "forget",
    "resolve",
    "snooze",
)


DECISION_SCHEMAS: dict[str, dict[str, Any]] = {
    "say": {
        "description": "回答、解释或给出终态说明",
        "required": ("text",),
        "example": {"kind": "say", "text": "给用户看的回复"},
    },
    "think": {
        "description": "记录无副作用的当前进展并选择下一步",
        "required": ("thought",),
        "example": {"kind": "think", "thought": "当前进展", "next_kind": "observe", "goal": {}},
    },
    "observe": {
        "description": "读取界面或系统状态，不产生副作用",
        "required": ("target",),
        "example": {
            "kind": "observe",
            "target": "screen",
            "target_app": "具体应用名",
            "question": "自然语言观察问题",
            "ax_query": "简短名称、AXRole、identifier 或动作能力",
            "require_coordinates": False,
            "goal": {},
        },
    },
    "propose_act": {
        "description": "向 Human Ops 提交一个待授权动作",
        "required": ("action_type", "arguments"),
        "example": {"kind": "propose_act", "action_type": "动作名", "arguments": {}, "goal": {}},
    },
    "propose_remember": {
        "description": "提交一条待审阅关系记忆",
        "required": ("category", "text"),
        "category_choices": MEMORY_CATEGORIES,
        "example": {"kind": "propose_remember", "category": "preference", "text": "要保存的内容"},
    },
    "stop": {
        "description": "无需继续或无法继续时结束当前生成",
        "required": ("summary",),
        "example": {"kind": "stop", "summary": "简短说明", "goal": {}},
    },
}


def _field(
    field_type: str,
    description: str,
    example: Any,
    *,
    choices: Iterable[Any] = (),
    allow_empty: bool = False,
) -> dict[str, Any]:
    return {
        "type": field_type,
        "description": description,
        "example": example,
        "choices": tuple(choices),
        "allow_empty": allow_empty,
    }


ACTION_SCHEMAS: dict[str, dict[str, Any]] = {
    "shell": {
        "scope": "shell",
        "required": ("command", "model_risk", "risk_reason"),
        "properties": {
            "command": _field("string", "要原样交给当前平台系统 Shell 的完整命令", "pwd"),
            "cwd": _field("string", "工作目录；相对路径按项目根目录解析", "."),
            "timeout_sec": _field("number", "执行超时秒数，范围 0 到 300", 30),
            "model_risk": _field(
                "string",
                "Brain 对完整命令副作用的判断；不确定必须标 uncertain",
                "safe",
                choices=("safe", "dangerous", "uncertain"),
            ),
            "risk_reason": _field("string", "Brain 判断风险级别的简短理由", "只读取当前目录名称"),
            "label": _field("string", "命令目的", "检查当前目录"),
        },
    },
    "launch_app": {
        "scope": "desktop",
        "required": ("app",),
        "properties": {
            "app": _field("string", "已确认的本地应用名称", "应用名称"),
            "label": _field("string", "动作目的", "打开应用"),
        },
    },
    "click": {
        "scope": "desktop",
        "required": ("target_app",),
        "any_of": (("ax_ref",), ("x", "y", "coordinate_space")),
        "properties": {
            "target_app": _field("string", "目标应用名称", "应用名称"),
            "ax_ref": _field("object", "从最新 affordance 完整复制的引用", {"app_id": "原样复制", "role": "AXButton", "path": [], "fingerprint": "原样复制"}),
            "x": _field("number", "无 AX 引用时的中心点横坐标", 0),
            "y": _field("number", "无 AX 引用时的中心点纵坐标", 0),
            "coordinate_space": _field("string", "坐标空间", "macos_screen_points", choices=("macos_screen_points", "image_pixels")),
            "label": _field("string", "目标说明", "目标"),
        },
    },
    "type_text": {
        "scope": "desktop",
        "required": ("target_app", "text"),
        "properties": {
            "target_app": _field("string", "目标应用名称", "应用名称"),
            "ax_ref": _field("object", "从最新输入 affordance 完整复制的引用", {"app_id": "原样复制", "role": "AXTextArea", "path": [], "fingerprint": "原样复制"}),
            "intended_chat": _field("string", "聊天应用中已观察确认的会话", "会话名称"),
            "text": _field("string", "要输入的完整文本；清空时为空字符串", "文字", allow_empty=True),
            "replace_existing": _field("boolean", "是否替换现有内容", False),
            "label": _field("string", "输入位置说明", "输入框"),
        },
    },
    "key_press": {
        "scope": "desktop",
        "required": ("target_app", "key"),
        "properties": {
            "target_app": _field("string", "目标应用名称", "应用名称"),
            "key": _field("string", "当前只支持 Enter/Return", "enter", choices=("enter", "return")),
            "label": _field("string", "按键目的", "确认"),
            "expected_text": _field("string", "聊天发送时的完整预期文本", "完整消息"),
            "intended_chat": _field("string", "聊天发送时已确认的会话", "会话名称"),
            "input_ax_ref": _field("object", "从输入阶段继承的输入框引用", {"app_id": "原样复制", "role": "AXTextArea", "path": [], "fingerprint": "原样复制"}),
        },
    },
    "playwright": {
        "scope": "browser",
        "required": ("profile", "operation"),
        "properties": {
            "profile": _field("string", "用户选择的 Chrome 资料名称", "Personal"),
            "operation": _field(
                "string",
                "Playwright 操作",
                "snapshot",
                choices=("attach", "open", "snapshot", "click", "fill", "type", "press", "go_back", "go_forward", "reload", "tab_list", "tab_new", "tab_select", "tab_close"),
            ),
            "url": _field("string", "open/tab_new 使用的绝对 http/https URL", "https://example.com"),
            "ref": _field("string", "同一会话最新 snapshot 的元素引用", "e12"),
            "text": _field("string", "fill/type 使用的文本", "文字"),
            "key": _field("string", "press 使用的按键", "Enter"),
            "index": _field("integer", "标签页索引", 0),
            "label": _field("string", "动作目标", "页面目标"),
        },
    },
    "file_list": {
        "scope": "file",
        "required": ("path",),
        "properties": {
            "path": _field("string", "允许根目录内的路径", "."),
            "max_entries": _field("integer", "正整数条目上限", 100),
        },
    },
    "file_read": {
        "scope": "file",
        "required": ("path",),
        "properties": {
            "path": _field("string", "允许根目录内的文件路径", "README.md"),
            "encoding": _field("string", "当前只支持 UTF-8", "utf-8", choices=("utf-8",)),
        },
    },
    "file_write": {
        "scope": "file",
        "required": ("path", "content"),
        "properties": {
            "path": _field("string", "允许根目录内的文件路径", "notes.txt"),
            "content": _field("string", "有大小上限的 UTF-8 文本", "内容"),
            "overwrite": _field("boolean", "覆盖已有文件时必须为 true", False),
        },
    },
    "file_mkdir": {
        "scope": "file",
        "required": ("path",),
        "properties": {"path": _field("string", "允许根目录内的新目录路径", "folder")},
    },
    "file_copy": {
        "scope": "file",
        "required": ("source", "destination"),
        "properties": {
            "source": _field("string", "允许根目录内的源文件", "source.txt"),
            "destination": _field("string", "不存在的目标文件", "copy.txt"),
        },
    },
    "file_move": {
        "scope": "file",
        "required": ("source", "destination"),
        "properties": {
            "source": _field("string", "允许根目录内的源文件", "source.txt"),
            "destination": _field("string", "不存在的目标文件", "moved.txt"),
        },
    },
    "file_delete": {
        "scope": "file",
        "required": ("path",),
        "properties": {"path": _field("string", "文件或空目录路径；不递归", "old.txt")},
    },
}


COMMON_ACTION_PROPERTIES = {
    "continue_after_approval": _field(
        "boolean",
        "动作验证后是否还需交回 Brain 选择下一步",
        True,
    ),
}


PROFILE_ACTION_SCOPES: dict[str, tuple[str, ...]] = {
    "proactive": (),
    "game": (),
    "chat": (),
    "agent": ("desktop", "browser", "file", "shell"),
    "desktop": ("desktop", "browser"),
    "file": ("file",),
}

PROFILE_DECISION_KINDS: dict[str, tuple[str, ...]] = {
    "proactive": ("say", "stop"),
    "game": ("say", "stop"),
    "chat": ("say", "propose_remember", "stop"),
    "agent": tuple(DECISION_SCHEMAS),
    "desktop": ("say", "observe", "propose_act", "stop"),
    "file": ("say", "observe", "propose_act", "stop"),
}


CAPABILITY_POLICIES: dict[str, str] = {
    "desktop": (
        "- 普通 macOS GUI 应用优先使用 Accessibility；QQ、微信、音乐和访达只有兼容别名，不再构成白名单。\n"
        "- observe.ax_query 是当前 target_app 内的窄搜索；question 不替代 ax_query。未返回的元素不能当作不存在。\n"
        "- ax_ref 必须从最新 affordance 完整原样复制；没有可靠 ax_ref 时才可使用观察明确给出的坐标和 coordinate_space。\n"
        "- click/type_text/key_press 必须有 target_app。QQ/微信消息输入和发送还必须继承已确认的 intended_chat、完整文本与输入框引用。"
    ),
    "browser": (
        "- 浏览器任务遵循用户明确选择的 Playwright 或人类操作方式；未选择时默认 Playwright。\n"
        "- Playwright 只使用同一会话最新 snapshot 的 ref，不得请求 eval、run-code 或原始 CLI 参数。\n"
        "- Chrome 资料优先使用当前任务中用户明确指定的名称，其次使用设置值；两者都没有时才 need_user。"
    ),
    "file": (
        "- 文件路径必须位于项目根目录或允许根目录，不能访问 .git、.venv、__pycache__、.env 或 pet_config.json。\n"
        "- 所有文件动作包括读取和列目录都需 Human Ops；写入只允许 UTF-8 文本，覆盖必须显式 overwrite=true，复制/移动不覆盖，删除不递归。"
    ),
    "shell": (
        "- shell.command 是提交给当前平台系统 Shell 的完整字符串，必须同时给出 model_risk 与 risk_reason。\n"
        "- 任何删除、覆盖、安装、联网、提权、进程/服务控制、远端写入、持久化或不确定命令都标 dangerous/uncertain。\n"
        "- 本地危险命令目录会独立复核且只能提升风险；不得拆分、编码或套解释器来规避审批。"
    ),
}


def normalize_prompt_profile(value: str) -> str:
    profile = str(value or "").strip().lower()
    return profile if profile in PROFILE_ACTION_SCOPES else "agent"


def decision_kinds(profile: str) -> tuple[str, ...]:
    normalized = normalize_prompt_profile(profile)
    return PROFILE_DECISION_KINDS[normalized]


def is_terminal_goal_status(status: object) -> bool:
    return str(status or "").strip() in TERMINAL_GOAL_STATUSES


def action_names(scopes: Iterable[str] | None = None) -> tuple[str, ...]:
    if scopes is None:
        return tuple(ACTION_SCHEMAS)
    selected = set(scopes)
    return tuple(
        name
        for name, schema in ACTION_SCHEMAS.items()
        if str(schema["scope"]) in selected
    )


def profile_action_names(profile: str) -> tuple[str, ...]:
    normalized = normalize_prompt_profile(profile)
    return action_names(PROFILE_ACTION_SCOPES[normalized])


def action_scope(action_type: str) -> str:
    schema = ACTION_SCHEMAS.get(str(action_type or "").strip())
    return str(schema.get("scope") or "") if isinstance(schema, dict) else ""


def action_choices(action_type: str, field_name: str) -> tuple[Any, ...]:
    schema = ACTION_SCHEMAS.get(str(action_type or "").strip(), {})
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    field = properties.get(field_name) if isinstance(properties.get(field_name), dict) else {}
    return tuple(field.get("choices") or ())


def _matches_type(value: Any, field_type: str) -> bool:
    if field_type == "string":
        return isinstance(value, str)
    if field_type == "boolean":
        return isinstance(value, bool)
    if field_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if field_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if field_type == "object":
        return isinstance(value, dict)
    return True


def validate_action_arguments(
    action_type: str,
    arguments: dict[str, Any] | None,
) -> tuple[bool, str]:
    action = str(action_type or "").strip()
    schema = ACTION_SCHEMAS.get(action)
    if schema is None:
        return False, action or "unknown"
    if not isinstance(arguments, dict):
        return False, f"{action} arguments must be an object"
    properties = {**COMMON_ACTION_PROPERTIES, **schema["properties"]}
    for name in schema.get("required", ()):
        field = properties[name]
        value = arguments.get(name)
        if (
            name not in arguments
            or value is None
            or (
                str(field["type"]) == "string"
                and not bool(str(value).strip())
                and not bool(field.get("allow_empty"))
            )
        ):
            return False, f"{action} missing {name}"
    for name, value in arguments.items():
        field = properties.get(name)
        if field is None:
            continue
        field_type = str(field["type"])
        if not _matches_type(value, field_type):
            return False, f"{action} {name} must be {field_type}"
        choices = tuple(field.get("choices") or ())
        comparable = value.lower() if isinstance(value, str) else value
        normalized_choices = tuple(item.lower() if isinstance(item, str) else item for item in choices)
        if choices and comparable not in normalized_choices:
            return False, f"{action} {name} must be one of {', '.join(map(str, choices))}"
    alternatives = tuple(schema.get("any_of") or ())
    if alternatives and not any(all(name in arguments for name in group) for group in alternatives):
        choices = " or ".join("+".join(group) for group in alternatives)
        return False, f"{action} requires {choices}"
    return True, ""


def validate_decision_payload(kind: str, payload: dict[str, Any] | None) -> tuple[bool, str]:
    decision_kind = str(kind or "").strip()
    schema = DECISION_SCHEMAS.get(decision_kind)
    if schema is None:
        return False, decision_kind or "unknown decision"
    data = payload if isinstance(payload, dict) else {}
    for name in schema.get("required", ()):
        if name not in data:
            return False, f"{decision_kind} missing {name}"
        if isinstance(data.get(name), str) and not str(data.get(name) or "").strip():
            return False, f"{decision_kind} missing {name}"
    goal = data.get("goal")
    if goal is not None:
        if not isinstance(goal, dict):
            return False, f"{decision_kind} goal must be an object"
        status = str(goal.get("status") or "").strip()
        if status and status not in GOAL_STATUSES:
            return False, f"{decision_kind} goal.status must be one of {', '.join(GOAL_STATUSES)}"
        for list_field in ("evidence", "missing"):
            if list_field in goal and not isinstance(goal.get(list_field), list):
                return False, f"{decision_kind} goal.{list_field} must be an array"
    if decision_kind == "propose_act":
        status = str(goal.get("status") or "").strip() if isinstance(goal, dict) else ""
        if status and status != "handoff_review":
            return False, "propose_act goal.status must be handoff_review"
        return validate_action_arguments(str(data.get("action_type") or ""), data.get("arguments"))
    if decision_kind == "propose_remember":
        category = str(data.get("category") or "").strip()
        if category not in MEMORY_CATEGORIES:
            return False, f"propose_remember category must be one of {', '.join(MEMORY_CATEGORIES)}"
    return True, ""


def render_decision_contract(profile: str) -> str:
    lines = ["当前可执行 kind（每次只返回一个 JSON 对象）："]
    for kind in decision_kinds(profile):
        schema = DECISION_SCHEMAS[kind]
        choices = schema.get("category_choices") or ()
        choices_text = f"；category: {', '.join(choices)}" if choices else ""
        lines.append(
            f"- {kind}: {schema['description']}；"
            f"{json.dumps(schema['example'], ensure_ascii=False, separators=(',', ':'))}"
            f"{choices_text}"
        )
    if normalize_prompt_profile(profile) not in {"proactive", "game"}:
        lines.append(
            '- 可选 goal: {"objective":"目标","status":"状态","evidence":["已知证据"],'
            '"missing":["缺失证据"],"next":"下一步"}；status: '
            f"{', '.join(GOAL_STATUSES)}；终态：{', '.join(sorted(TERMINAL_GOAL_STATUSES))}。"
        )
    return "\n".join(lines)


def render_action_contract(profile: str) -> str:
    normalized = normalize_prompt_profile(profile)
    names = profile_action_names(normalized)
    if not names:
        return "当前 profile 不提供 propose_act；不得提出外部动作。"
    lines = [
        "propose_act 的 action_type 与 arguments schema：",
        '- 所有动作可选：{"continue_after_approval":true}；仅在验证后仍需 Brain 选择下一步时设置。',
    ]
    for name in names:
        schema = ACTION_SCHEMAS[name]
        required_example = {
            field_name: field["example"]
            for field_name, field in schema["properties"].items()
            if field_name in schema.get("required", ())
        }
        alternatives = tuple(schema.get("any_of") or ())
        examples = []
        for group in alternatives or ((),):
            example = dict(required_example)
            for field_name in group:
                example[field_name] = schema["properties"][field_name]["example"]
            examples.append(json.dumps(example, ensure_ascii=False, separators=(",", ":")))
        represented = set(required_example)
        for group in alternatives:
            represented.update(group)
        optional = [
            field_name
            for field_name in schema["properties"]
            if field_name not in represented
        ]
        constraints = []
        for field_name, field in schema["properties"].items():
            choices = tuple(field.get("choices") or ())
            if choices:
                constraints.append(f"{field_name}={'|'.join(map(str, choices))}")
            elif str(field.get("type") or "") in {"boolean", "integer", "number"}:
                constraints.append(f"{field_name}:{field['type']}")
        suffix_parts = []
        if optional:
            suffix_parts.append(f"可选：{', '.join(optional)}")
        if constraints:
            suffix_parts.append(f"约束：{', '.join(constraints)}")
        suffix = f"；{'；'.join(suffix_parts)}" if suffix_parts else ""
        lines.append(
            f"- {name}: {' 或 '.join(examples)}{suffix}"
        )
    return "\n".join(lines)


def render_capability_policy(profile: str) -> str:
    scopes = PROFILE_ACTION_SCOPES[normalize_prompt_profile(profile)]
    blocks = [CAPABILITY_POLICIES[scope] for scope in scopes if scope in CAPABILITY_POLICIES]
    return "\n".join(blocks) if blocks else "当前是纯聊天 profile，不提供外部动作能力。"


def render_final_allowlist(profile: str) -> str:
    normalized = normalize_prompt_profile(profile)
    kinds = "、".join(decision_kinds(normalized))
    actions = profile_action_names(normalized)
    action_text = "、".join(actions) if actions else "无"
    return f"当前可执行决定只有：{kinds}。当前可执行动作只有：{action_text}。"


__all__ = [
    "ACTION_SCHEMAS",
    "CAPABILITY_POLICIES",
    "COMMON_ACTION_PROPERTIES",
    "DECISION_SCHEMAS",
    "GOAL_STATUSES",
    "MEMORY_CATEGORIES",
    "PROFILE_ACTION_SCOPES",
    "PROFILE_DECISION_KINDS",
    "TERMINAL_GOAL_STATUSES",
    "action_choices",
    "action_names",
    "action_scope",
    "decision_kinds",
    "is_terminal_goal_status",
    "normalize_prompt_profile",
    "profile_action_names",
    "render_action_contract",
    "render_capability_policy",
    "render_decision_contract",
    "render_final_allowlist",
    "validate_action_arguments",
    "validate_decision_payload",
]
