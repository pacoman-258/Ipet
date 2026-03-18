from pydantic import BaseModel, Field


class ChatStreamRequest(BaseModel):
    session_id: str = Field(default="default")
    text: str
    model: str = Field(default="qwen3:8b")
    memory_window: int = Field(default=10, ge=1, le=50)
    system_prompt: str = Field(default="")
    expression_mode: bool = Field(default=True)
    expression_output_format: str = Field(default="ndjson_v1")
    available_expressions: list[str] = Field(default_factory=list)
    tools_enabled: bool = Field(default=True)
    tool_mode: str = Field(default="mcp_local_phase2")
    react_enabled: bool = Field(default=True)
    react_visibility: str = Field(default="inline")
    max_reasoning_steps: int = Field(default=10, ge=1, le=12)
    llm_provider: str = Field(default="ollama")
    api_base_url: str = Field(default="")
    api_key: str = Field(default="")


class ChatApprovalRequest(BaseModel):
    turn_id: str
    approved: bool = Field(default=True)


class TTSRequest(BaseModel):
    text: str
    voice: str = Field(default="zh-CN-XiaoxiaoNeural")
    rate: str = Field(default="+0%")
    volume: str = Field(default="+0%")
    provider: str = Field(default="edge_tts")
    provider_url: str = Field(default="")


class ModelListRequest(BaseModel):
    llm_provider: str = Field(default="ollama")
    api_base_url: str = Field(default="")
    api_key: str = Field(default="")


class MCPServerCreateRequest(BaseModel):
    config_json: str
    name: str = Field(default="")


class MCPToggleRequest(BaseModel):
    name: str
    enabled: bool


class MCPDeleteRequest(BaseModel):
    name: str
