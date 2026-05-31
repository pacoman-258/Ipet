from pydantic import BaseModel, Field


class TTSRequest(BaseModel):
    text: str
    voice: str = Field(default="zh-CN-XiaoxiaoNeural")
    rate: str = Field(default="+0%")
    volume: str = Field(default="+0%")
    provider: str = Field(default="edge_tts")
    provider_url: str = Field(default="")
