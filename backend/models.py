from pydantic import BaseModel, Field


class ChatStreamRequest(BaseModel):
    session_id: str = Field(default="default")
    text: str
    model: str = Field(default="gpt-5.4")
    system_prompt: str = Field(default="")
    expression_mode: bool = Field(default=True)
    expression_output_format: str = Field(default="ndjson_v1")
    available_expressions: list[str] = Field(default_factory=list)
    tools_enabled: bool = Field(default=True)
    tool_mode: str = Field(default="mcp_local_phase2")
    react_enabled: bool = Field(default=True)
    react_visibility: str = Field(default="inline")
    max_reasoning_steps: int = Field(default=10, ge=1)
    chat_mode: str = Field(default="react")
    skill_ids: list[str] = Field(default_factory=list)
    memory_mode: str = Field(default="persistent")


class ChatApprovalRequest(BaseModel):
    turn_id: str
    approved: bool = Field(default=True)
    user_text: str = Field(default="")


class ChatMemoryCandidate(BaseModel):
    marker: str
    title: str
    content: str
    source_kind: str = Field(default="")
    start_turn: int = Field(default=0)
    end_turn: int = Field(default=0)


class ChatMemoryDecisionRequest(BaseModel):
    topic_id: str
    action: str
    candidate: ChatMemoryCandidate


class TTSRequest(BaseModel):
    text: str
    voice: str = Field(default="zh-CN-XiaoxiaoNeural")
    rate: str = Field(default="+0%")
    volume: str = Field(default="+0%")
    provider: str = Field(default="edge_tts")
    provider_url: str = Field(default="")


class MCPServerCreateRequest(BaseModel):
    config_json: str
    name: str = Field(default="")


class MCPToggleRequest(BaseModel):
    name: str
    enabled: bool


class MCPDeleteRequest(BaseModel):
    name: str


class SkillImportLocalRequest(BaseModel):
    path: str = Field(default="")
    directory: str = Field(default="")
    name: str = Field(default="")


class SkillImportGitRequest(BaseModel):
    url: str = Field(default="")
    repo_url: str = Field(default="")
    name: str = Field(default="")
    ref: str = Field(default="")
    branch: str = Field(default="")
    subdir: str = Field(default="")


class SkillDeleteRequest(BaseModel):
    skill_id: str = Field(default="")
    id: str = Field(default="")
    name: str = Field(default="")
