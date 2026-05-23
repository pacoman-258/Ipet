# Ipet 项目架构说明

## 1. 项目定位

Ipet 不是“给 AstrBot 套一个 Live2D 皮肤”，而是一个独立的桌面桌宠产品。当前实现是 Python 桌面宿主 + FastAPI 后端 + 根目录 HTML/JS 前端，而不是 TypeScript `src/` 应用。

项目分为两个边界：

- **Ipet 应用层**：用户真正看到、听到和交互的桌宠产品。
- **Agent Runtime 层**：后端智能体能力提供者。当前默认 Runtime 是 **AstrBot**；**Hermes** 是可手动选择的次选/高级 Runtime。

核心原则：

```text
Ipet 负责产品体验。
Agent Runtime 负责语义、文本和任务事件。
Runtime Adapter 负责连接二者。
```

Ipet 不把 AstrBot 或 Hermes 当成本体，也不依赖自动故障回退。active runtime 不可用时，应返回明确错误，由用户修正配置或手动切换 Runtime。

---

## 2. 当前真实架构

```mermaid
flowchart TD
    User[用户] --> Shell[main.py 桌面宿主]
    Shell --> UI[index.html 桌宠 UI]
    Shell --> Backend[backend/app.py FastAPI]
    UI --> Chat[/api/chat/stream]
    Chat --> Service[backend/runtime_service.py]
    Service --> Contract[backend/runtime_contracts.py]
    Service --> Adapters[backend/runtime_adapters.py]
    Adapters --> AstrBot[AstrBot 默认 Runtime]
    Adapters --> Hermes[Hermes 次选/高级 Runtime]
    Backend --> Vision[backend/vision*.py + active_vision.py]
    Backend --> ASR[backend/asr.py + asr_server.py]
    UI --> Presentation[Live2D/气泡/TTS/口型/表现层映射]
    Vision --> Service
    AstrBot --> Events[语义/文本/任务事件]
    Hermes --> Events
    Events --> UI
```

关键文件：

- `main.py`：桌面宿主、Qt/WebEngine 桥、后端和 runtime sidecar 生命周期。
- `backend/app.py`：FastAPI 路由、聊天流、设置、视觉、ASR/TTS 等 API 入口。
- `backend/runtime_config.py`：读取和规范化 `runtime.active` 与 `runtime.adapters.*`。
- `backend/runtime_contracts.py`：Ipet 与 Runtime Adapter 之间的共享请求、状态和事件契约。
- `backend/runtime_adapters.py`：AstrBot/Hermes 的具体适配器实现。
- `backend/runtime_service.py`：active runtime 解析、状态查询和流式请求调度。
- `index.html`：桌宠运行时 UI，当前仍在根目录，因为宿主按该路径加载。
- `settings.html`、`settings.css`、`settings.js`：设置页，当前仍在根目录。

---

## 3. 分层职责

### 3.1 Ipet 应用层

Ipet 负责所有直接面向用户的体验和本地设备能力：

- Live2D 风格渲染、模型资源、表情和动作映射
- 桌面窗口、托盘/右键入口、气泡 UI 和运行时前端状态
- ASR、TTS、音频播放和实时口型
- 设置页、本地配置读写和资源 allowlist
- 本地视觉证据：截图、被动时间线、主动观察、分析器元数据、证据注入策略
- 审批 UI、前端 SSE 渲染、分段输出和表现层映射

Ipet 不负责直接实现 Runtime 内部推理，也不直接管理 AstrBot/Hermes 的内部状态。

### 3.2 Agent Runtime 层

Agent Runtime 只负责智能体能力与语义结果：

- LLM 推理、上下文和会话
- 工具调用、插件、MCP、知识库、任务规划
- 返回文本、语义、任务状态、阶段事件或审批请求等 Runtime 事件

默认口径：

- **AstrBot 是默认 Runtime**。插件、MCP、知识库、Provider、QQ/NapCatQQ 等在 AstrBot WebUI 管理。
- **Hermes 是次选/高级 Runtime**。Hermes skills、MCP 和 approval flow 只在用户手动选择 Hermes 时作为高级路径使用。

Runtime 不负责 Live2D、ASR、TTS、口型、设置页、本地视觉证据或最终表现层映射。

### 3.3 Runtime Adapter 层

当前 Adapter 不是独立 TypeScript 包，而是 Python 后端的一组模块。Adapter 负责：

- 将 Ipet 的请求转换为 AstrBot 或 Hermes 的 API 请求。
- 暴露 `status`、`request_json`、`stream_sse` 三类能力。
- 以流式优先方式把 Runtime 原生响应转换为前端稳定 SSE 事件。
- 屏蔽 Runtime 的鉴权、错误、会话和事件格式差异。

当前稳定事件仍服务于前端兼容：

- `meta`
- `phase`
- `approval_required`
- `segment`
- `display_segment`
- `token`
- `done`
- `error`

后续可以把契约演进为更正式的 `AgentInputEvent` / `AgentRuntimeEvent`，但现阶段不得把文档写成已经存在 `src/runtime/*.ts`。

---

## 4. 核心边界规则

```text
1. Ipet 控制 Live2D、ASR、TTS、口型、设置页、本地视觉证据和表现层映射。
2. Runtime 只返回语义、文本、阶段、审批、任务等事件。
3. Runtime 不直接操作窗口、模型、音频播放器、截图、设置页或 UI DOM。
4. AstrBot 默认管理插件、MCP、知识库、Provider 和 QQ/NapCatQQ。
5. Hermes 的 skills/MCP/approval 是用户手动选择 Hermes 后的次选/高级路径。
6. Ipet 通过 `backend/runtime_*` 的契约和适配层访问 Runtime。
7. active runtime 不健康时返回明确错误；不描述、不依赖自动故障回退。
8. Ipet 将 Runtime 语义输出映射为 Live2D 表情、动作、TTS、口型和 UI 行为。
```

---

## 5. Runtime Contract

当前契约以流式优先：

```text
Ipet request
  -> backend/runtime_service.py
  -> active adapter.status()
  -> active adapter.stream_sse() 或 request_json()
  -> frontend-compatible SSE events
```

Adapter 能力：

- `status`：检查 active runtime 是否配置、启用、可连接。
- `request_json`：用于非流式或管理类请求。
- `stream_sse`：默认聊天路径，优先保持流式输出。

可演进方向：

- `AgentInputEvent`：统一描述来自桌面、语音、视觉、系统计时器或外部平台的输入。
- `AgentRuntimeEvent`：统一描述 token、segment、phase、approval、task、error、done 等输出。

这些是契约演进方向，不代表当前仓库已经有 TypeScript 类型文件。

---

## 6. 典型流程

### 6.1 默认 AstrBot 聊天流程

```text
index.html
  -> POST /api/chat/stream
  -> runtime_service 解析 runtime.active=astrbot
  -> Ipet 视觉证据门按需注入 bounded text evidence
  -> AstrBot adapter 调用 AstrBot HTTP API
  -> AstrBot 返回流式文本/语义事件
  -> Ipet 适配为前端 SSE
  -> index.html 渲染文本、TTS、表情和气泡
```

AstrBot 的插件、MCP、知识库、Provider、QQ/NapCatQQ 不在 Ipet 设置页内重做管理，用户应在 AstrBot WebUI 中配置。

### 6.2 Hermes 次选/高级流程

```text
用户手动选择 runtime.active=hermes
  -> Ipet 调用 Hermes adapter
  -> Hermes Dashboard / TUI gateway 处理会话、skills、MCP、approval
  -> Ipet 转换 token/phase/approval/done 等事件
  -> 前端继续使用相同 SSE 事件渲染
```

Hermes approval 的 pending turn 属于 Hermes；AstrBot v1 不支持 Ipet approval forwarding。

### 6.3 视觉证据流程

```text
/api/chat/stream 收到用户输入
  -> Ipet Vision Broker 判断是否需要 passive/active evidence
  -> main.py 按需截图、隐藏/恢复桌宠、执行轻交互
  -> backend/vision*.py 生成 bounded observations/unknowns
  -> 仅注入文本证据给 Runtime
  -> Runtime 基于证据回答，不直接接管截图或视觉状态
```

---

## 7. 配置

默认配置使用：

```json
{
  "runtime": {
    "active": "astrbot",
    "adapters": {
      "astrbot": {
        "base_url": "http://127.0.0.1:6185",
        "api_key_env": "ASTRBOT_API_KEY",
        "username": "ipet"
      },
      "hermes": {
        "base_url": "http://127.0.0.1:9119",
        "health_path": "/api/status"
      }
    }
  }
}
```

`runtime.active=astrbot` 是默认值。要使用 Hermes，用户需要手动把 `runtime.active` 改为 `hermes`，并配置 Hermes Dashboard 地址、健康检查路径和可选 sidecar 命令。

---

## 8. 开发约束

给 Codex 或其他编码代理的硬性要求：

```text
不要把 AstrBot 当成 Ipet 应用本体。
不要在 AstrBot 或 Hermes 中实现桌宠 UI、Live2D、ASR、TTS、口型或设置页。
不要把架构文档写成当前不存在的 src/*.ts 目录结构。
不要描述 AstrBot 与 Hermes 之间存在自动故障回退。
保留 Ipet-owned 视觉证据治理；Runtime 默认只消费文本证据。
默认 Runtime 口径是 AstrBot；Hermes 是手动选择的次选/高级 Runtime。
```

---

## 9. 一句话总结

```text
Ipet 是桌宠产品主体；AstrBot 是默认 Agent Runtime；Hermes 是可手动选择的高级 Runtime。
三者通过 Python/FastAPI 的 runtime contract 和 adapter 层解耦。
```
