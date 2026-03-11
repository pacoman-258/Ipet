from pydantic import BaseModel, Field


class ChatStreamRequest(BaseModel):
    session_id: str = Field(default="default")
    text: str
    model: str = Field(default="qwen3:8b")
    memory_window: int = Field(default=10, ge=1, le=50)
    system_prompt: str = Field(default="")
    expression_mode: bool = Field(default=True)
    expression_output_format: str = Field(default="ndjson_v1")
    tools_enabled: bool = Field(default=True)
    tool_mode: str = Field(default="mcp_local_phase2")
    llm_provider: str = Field(default="ollama")
    api_base_url: str = Field(default="")
    api_key: str = Field(default="")


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


class MCPInstallRequest(BaseModel):
    repo_url: str
    name: str = Field(default="")


class MCPRegisterLocalRequest(BaseModel):
    path: str
    name: str = Field(default="")


class MCPToggleRequest(BaseModel):
    name: str
    enabled: bool
