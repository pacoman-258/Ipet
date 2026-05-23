# Ipet 桌面宠物 Agent 外壳

Ipet 是一个本地优先的桌宠式 Agent 外壳。它把 Python 桌面宿主、Qt WebEngine 前端、FastAPI 后端、Live2D 风格角色资源、聊天流式输出、TTS/ASR、设置页、审批 UI 和可选屏幕视觉 grounding 组合在一起。AstrBot 是默认 Runtime，负责 Agent 决策、会话、插件、MCP、知识库、Provider 与 QQ/NapCatQQ 集成；Hermes 是可手动选择的次选/高级 Runtime。Ipet 负责桌面体验、运行时适配和本地证据治理。

编码代理请先读 `AGENTS.md`。

## 核心能力

- Qt / WebEngine 悬浮桌宠窗口与浏览器设置页。
- FastAPI 后端，把 AstrBot 或 Hermes 的原生流式响应适配成前端 SSE 事件。
- 默认 `runtime.active=astrbot`；Hermes 作为手动选择的次选/高级 Runtime，并可由桌面宿主启动本地 sidecar。
- 支持 TTS、表情提示、分段输出、审批气泡和 ReAct/阶段轨迹展示。
- 支持 push-to-talk ASR；启用后通过本地 ASR 服务把语音写入输入框。
- Ipet 自有自动视觉链路：被动屏幕时间线、主动观察、分析器元数据、受控文本证据注入。
- 内置本地技能；默认 AstrBot 模式把插件、MCP、知识库、Provider 与 QQ/NapCatQQ 配置留在 AstrBot WebUI；Hermes 模式代理 skills/MCP/approval 作为次选路径。

## 项目结构

```text
Ipet/
|-- main.py                    # 桌面宿主、Qt 桥、后端/运行时 sidecar 启停
|-- backend/                   # FastAPI、运行时适配、ASR/TTS、技能、MCP、视觉
|-- index.html                 # 桌宠运行时 UI，因宿主加载路径保留在根目录
|-- settings.html              # 设置页入口，因后端加载路径保留在根目录
|-- settings.css
|-- settings.js
|-- docs/                      # 结构说明、工作流、报告、subagent 文档
|-- tests/                     # 单元测试与回归测试
|-- scripts/                   # 手动工具和 debug 辅助脚本
|-- skills/                    # Ipet 内置技能
|-- integrations/              # 可选桥接/参考集成，例如 AstrBot helper
|-- prompts/                   # 可复用提示词或人设 JSON
|-- model/                     # 已跟踪的示例/参考 Live2D 资源与本地忽略覆盖
|-- third_party_mcp/           # 第三方 MCP manifest 与本地安装区域
|-- third_party_skills/        # 旧版/导入技能存储，默认忽略
|-- pet_config.example.json    # 安全示例配置
|-- README.md                  # English README
|-- README.html                # 中文 HTML 阅读版
```

根目录只保留启动入口、核心 UI、配置示例和 README。一次性输出、热搜报告、缓存、运行状态文件和个人配置不要放进仓库；手动诊断脚本放在 `scripts/debug/`，运行缓存走 `.gitignore` 中的忽略路径。

## 环境要求

- Python 3.12+
- 当前验证过的开发运行时是项目 `.venv`，Python 3.12。
- Qt 绑定顺序固定：优先 `PySide6`，失败后回退 `PyQt6`。
- Windows 是原始主要目标环境。
- macOS v1 目标是 macOS 13+ Apple Silicon，重点保障桌面启动、设置页、聊天和屏幕视觉可用。

可选组件：

- AstrBot v4.18+：启用 HTTP API，外部托管或由 Ipet sidecar 启动。
- Hermes Agent：手动选择为次选/高级 Runtime 时，外部托管或由 Ipet sidecar 启动。
- NapCatQQ：通过 AstrBot 的 OneBot v11 反向 WebSocket 接入 QQ。
- Edge TTS 或自定义 HTTP TTS。
- 放在 `model/` 下的 Live2D 兼容模型资源。

## 快速开始

1. 确认项目 `.venv` 使用 Python 3.12。

```powershell
.\.venv\Scripts\python.exe -V
```

2. 如果还没有本地配置，从示例复制一份。

```powershell
Copy-Item pet_config.example.json pet_config.json
```

3. 按本地环境修改 `pet_config.json`：模型路径、AstrBot 地址、API Key、sidecar 命令，以及可选 TTS/ASR/视觉设置。默认使用 `runtime.active=astrbot`；如需 Hermes，手动改为 `runtime.active=hermes` 并配置 `runtime.adapters.hermes`。

4. 启动桌宠；推荐不要让 `uv` 重写当前环境。

```powershell
uv run --no-sync python main.py
```

5. 启动后从托盘或右键菜单打开设置页，也可以访问后端地址加 `/settings`。

## 运行时工作流

`main.py` 启动桌面宿主，拉起 `backend.app:app`，并按配置启动 AstrBot 或 Hermes sidecar。前端把聊天请求发到 `/api/chat/stream`；后端解析 active runtime，必要时先执行 Ipet 自有视觉证据门，默认调用 AstrBot 适配器，或在手动选择 Hermes 时调用 Hermes 适配器，并把运行时事件转换成前端稳定事件。

稳定前端事件包括：

- `meta`
- `phase`
- `approval_required`
- `segment`
- `display_segment`
- `token`
- `done`
- `error`

当 active runtime 未启用、缺失或不健康时，聊天流返回明确错误；后端不会悄悄回退到旧的本地 Agent runtime。

## 自动视觉

自动视觉默认关闭，由 `vision.*` 配置控制。Ipet 负责截图、状态、分析器路由与证据治理；原始截图不落盘，设置/状态接口不暴露图片数据，本地帧与上下文接口需要桌面宿主生成的 token。

视觉链路分两条：

- 被动链路：只在内存里保留短屏幕变化时间线，作为背景上下文。
- 主动链路：当用户问当前屏幕/窗口/图片，或开启强制 grounding 时，执行一次受控实时观察。桌宠会临时隐藏/恢复，只允许轻交互，例如聚焦、切换一次窗口/标签、小幅滚动。

Vision Analyzer v2 可在显式启用后使用本地 OCR 或 OpenAI-compatible/local VLM 端点补充结构化 observations。分析失败只会形成 bounded unknowns，不会伪造观察。没有足够新鲜证据时，运行时必须回答“我无法从当前截图确认”。

## 配置与仓库卫生

- `pet_config.json` 已忽略，只保留在本地。
- 运行时选择在 `runtime.active` 与 `runtime.adapters.*`；默认是 `runtime.active=astrbot`。
- Hermes 是手动选择的次选/高级 Runtime，不是自动故障回退路径。
- 兼容期仍读取旧顶层 `hermes` 配置。
- `chat.tooling.file_allowlist` 控制本地文件工具可访问目录。
- 当前仓库的 `model/` 包含已跟踪的示例/参考 Live2D 资源；私人、大体积、下载或生成模型资源在提交前应保持本地忽略。
- 第三方 MCP 安装目录、`node_modules`、运行状态、topic history、热搜输出、下载文件和音频缓存都不应进入版本库。

## 测试

完整测试：

```powershell
python -m unittest discover -s tests -p "test*.py" -v
```

更小的回归切片见 `AGENTS.md` 的 Regression Matrix。

## 文档入口

- 项目结构：`docs/PROJECT_STRUCTURE.md`
- 运行时工作流：`docs/WORKFLOW.md`
- English README：`README.md`
- 中文 HTML 阅读版：`README.html`
- Subagent 快速说明：`docs/SUBAGENTS.md`
- Subagent 角色提示词库：`docs/subagents/`
