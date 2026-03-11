# AI Assistant 开发报告

## 1. 文档目的

本文档用于记录本项目的实现原理、功能清单、使用方法、配置结构和维护约定。

维护规则：

- 每次新增功能、删减功能、接口变更或配置变更时，必须同步更新本文档。
- 若改动影响 `README.md` 中的入口说明，也必须同步更新。

---

## 2. 项目概述

这是一个基于 `PySide6/PyQt6 + QtWebEngine + Live2D + FastAPI` 的本地桌宠项目。

当前项目目标：

- 在桌面显示可交互的 Live2D 桌宠
- 支持模型加载、动作、表情、缩放和位置编辑
- 支持聊天、TTS 播报、LipSync 口型同步
- 支持本地 `Ollama` 和远程 OpenAI 兼容模型
- 支持结构化表情输出驱动 Live2D 表情
- 支持本地工具调用
- 支持第三方 MCP 下载到本地、安装到本地、并在本地运行

---

## 3. 架构与实现原理

### 3.1 桌面宿主层

入口文件：`main.py`

职责：

- 创建透明桌宠窗口
- 创建控制面板
- 加载本地 `index.html`
- 通过 `QWebChannel` 和前端通信
- 自动拉起和关闭 FastAPI 后端
- 保存/恢复 `pet_config.json`

### 3.2 前端渲染层

入口文件：`index.html`

职责：

- 使用 `PIXI + Live2D` 渲染模型
- 聊天 UI、折叠面板、拖拽和缩放
- 流式显示对话文本
- 请求 `/api/chat/stream` 和 `/api/tts`
- 使用 `AudioContext + AnalyserNode` 驱动 `ParamMouthOpenY`
- 在结构化分段下先播表情，再播对应文本

### 3.3 后端服务层

入口文件：`backend/app.py`

职责：

- 提供聊天、TTS、音频、模型列表和 MCP 管理接口
- 对接 `Ollama` 或 OpenAI 兼容 API
- 在对话中注入工具描述和结构化表情协议
- 管理本地一期工具和第三方 MCP 运行时

### 3.4 一期本地工具

目录：`backend/tooling/` 和 `backend/mcp/local_server.py`

当前支持：

- `create_file`
- `read_file`
- `write_file`
- `move_file`
- `list_dir`

特点：

- 路径白名单限制
- 文件大小限制
- 当前不开放删除文件

### 3.5 二期第三方 MCP

核心模块：

- `backend/mcp/third_party_manager.py`
- `backend/mcp/stdio_client.py`
- `backend/mcp_bridge.py`

实现方式：

- 第三方 MCP 固定存放于 `third_party_mcp/<name>/`
- 通过 `manifest.json` 描述 runtime、entry 和 install 方式
- 若第三方项目只提供 `mcp-server.json`，后端会自动转换为本项目 `manifest.json`
- 当前只支持本地 `stdio` transport
- Python MCP 使用独立 `uv` 虚拟环境
- Node MCP 使用 `npm install` 或 `npm ci`
- 后端以标准 MCP `initialize / tools/list / tools/call` 进行通信
- 工具冲突时按配置顺序优先第三方，再回退到本地一期工具

---

## 4. 目录与模块职责

```text
AI_assistant/
├─ main.py
├─ index.html
├─ pet_config.json
├─ README.md
├─ DEVELOPMENT_REPORT.md
├─ backend/
│  ├─ app.py
│  ├─ models.py
│  ├─ ollama_client.py
│  ├─ tts.py
│  ├─ agent_orchestrator.py
│  ├─ mcp_bridge.py
│  ├─ tooling/
│  │  ├─ security.py
│  │  └─ file_tools.py
│  └─ mcp/
│     ├─ local_server.py
│     ├─ stdio_client.py
│     └─ third_party_manager.py
├─ third_party_mcp/
│  ├─ README.md
│  └─ example_mcp/
│     └─ manifest.json
└─ model/
```

---

## 5. 已实现功能

### 5.1 桌宠与模型

- 加载本地 Live2D 模型
- 自动补全部分缺失动作/表情引用
- 动作播放
- 表情播放
- 鼠标跟随
- 编辑模式拖拽和缩放
- 窗口位置和大小持久化

### 5.2 聊天与模型接入

- 折叠聊天面板
- 流式回复显示
- 最近 10 轮上下文记忆
- 自定义系统提示词
- 支持 `ollama`
- 支持 `openai_compat`
- 支持模型列表拉取和选择

### 5.3 语音与表情联动

- `edge-tts`
- 自定义 HTTP TTS
- 自定义语音和语速
- 停止语音按钮
- LipSync 口型同步
- NDJSON 表情驱动分段

### 5.4 工具调用

- 一期本地工具调用
- function call / JSON 回退协议兼容
- 第三方 MCP 本地安装、本地启动、本地管理
- 支持 Git 安装和本地目录注册
- Python 第三方 MCP 使用独立 `uv` 环境
- Node 第三方 MCP 使用 `npm`

### 5.5 第三方 MCP 控制面板

- 第三方 MCP 总开关
- 从 Git 安装
- 注册本地目录
- 重载第三方 MCP
- 启用/停用单个 MCP
- 显示 runtime、安装状态、健康状态和工具列表

---

## 6. 主要接口

### 6.1 聊天与语音

- `GET /api/health`
- `POST /api/chat/stream`
- `POST /api/models`
- `POST /api/tts`
- `GET /api/audio/{id}.mp3`

### 6.2 第三方 MCP 管理

- `GET /api/mcp/servers`
- `GET /api/mcp/health`
- `POST /api/mcp/install`
- `POST /api/mcp/register-local`
- `POST /api/mcp/toggle`
- `POST /api/mcp/reload`

---

## 7. 配置说明

配置文件：`pet_config.json`

关键字段：

```json
{
  "chat": {
    "backend_url": "http://127.0.0.1:8008",
    "llm_provider": "ollama",
    "api_base_url": "http://127.0.0.1:11434",
    "api_key": "",
    "model": "qwen3:8b",
    "voice": "zh-CN-XiaoxiaoNeural",
    "rate_pct": 0,
    "expression_mode": true,
    "tooling": {
      "enabled": true,
      "mode": "mcp_local_phase2",
      "file_allowlist": ["..."],
      "max_tool_calls_per_turn": 6,
      "tool_timeout_sec": 10,
      "third_party": {
        "enabled": true,
        "servers": [
          {
            "name": "example_mcp",
            "enabled": true,
            "source_type": "git",
            "source": "https://example.com/repo.git",
            "manifest_path": "third_party_mcp/example_mcp/manifest.json",
            "runtime": "python"
          }
        ]
      }
    }
  }
}
```

安全注意：

- `API Key` 不应提交到公共仓库
- `file_allowlist` 只应包含可信目录
- 第三方 MCP 仅应安装可信来源

---

## 8. 第三方 MCP manifest 规范

当前支持格式：

```json
{
  "name": "example_mcp",
  "version": "0.1.0",
  "enabled": true,
  "transport": "stdio",
  "runtime": "python",
  "entry": {
    "command": "python",
    "args": ["-m", "example_mcp.server"]
  },
  "install": {
    "type": "python_uv",
    "requirements": "requirements.txt"
  },
  "description": "Example third-party MCP manifest template."
}
```

约束：

- 只支持 `transport=stdio`
- Python 只支持 `install.type=python_uv|none`
- Node 只支持 `install.type=node_npm|none`
- Python MCP 一律运行在该 MCP 自己的 `.venv`
- 兼容导入：若目录中无 `manifest.json` 但有 `mcp-server.json`，会在注册或安装时自动生成 `manifest.json`

---

## 9. 使用方法

### 9.1 启动项目

```powershell
python main.py
```

### 9.2 使用本地或远程模型

在控制面板里设置：

- `模型源`
- `API Base`
- `API Key`
- `模型`

然后点击“测试后端”。

### 9.3 安装第三方 MCP

方式一：从 Git 安装

1. 打开控制面板
2. 在“第三方 MCP”区域输入 Git 地址
3. 点击“从 Git 安装”
4. 等待后端 clone、安装依赖、启动并列出工具

方式二：注册本地目录

1. 准备包含 `manifest.json` 或 `mcp-server.json` 的 MCP 目录
2. 点击“注册本地目录”
3. 选择目录
4. 等待后端复制到 `third_party_mcp/` 并执行安装

### 9.4 在聊天中使用工具

确保：

- 工具调用总开关开启
- 第三方 MCP 总开关开启
- 目标 MCP 状态为可用

然后直接在聊天中要求助手完成文件或工具任务。

---

## 10. 已知边界

- 第三方 MCP 首版只支持本地 `stdio`
- 不支持远程 HTTP MCP
- 不支持 Windows 服务化常驻
- Python MCP 依赖 `uv`
- Node MCP 依赖系统 `node/npm`
- 若远端大模型不支持工具调用，仍依赖当前 JSON 回退协议

---

## 11. 维护要求

以下改动必须同步更新本文档：

- 功能新增或删减
- MCP manifest 规范变化
- 后端管理接口变化
- 配置结构变化
- 第三方 MCP 安装策略变化
- TTS、模型接入或工具调用策略变化

---

## 12. 2026-03 MCP Timeout / jmmcp CPU Fix

- Default third-party MCP timeout is now `180` seconds.
- Runtime config migration treats legacy `tool_timeout_sec = 10` as an old default and upgrades it to `180`.
- If a third-party MCP request times out or hits a protocol/write failure, the stdio client now terminates the child process immediately instead of leaving it running.
- Third-party MCP failures are tracked as server health failures, and the next tool call recreates that MCP client instead of silently falling back to local tools.
- `third_party_mcp/jmmcp/main.py` no longer uses `after_album -> img2pdf` plugin conversion.
- `jmmcp` now downloads first and only generates PDF after a successful full download.
- `jmmcp` failure paths only return an error and clean temporary files; they do not continue PDF generation work after failure.
- `jmmcp` now forces conservative default concurrency:
- `JM_PHOTO_THREADS` default `1`
- `JM_IMAGE_THREADS` default `4`
- `JM_RETRY_TIMES` default `5`
