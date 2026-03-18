# AI Assistant Desktop Pet

这是一个以本地运行优先的桌宠助手项目，使用 Python、Qt WebEngine、FastAPI 以及 Live2D 风格角色资源构建。项目把桌面宠物外壳、聊天、TTS、表情控制和基于 MCP 的工具系统整合在一起，适合做可控的 Agent 桌面助手。

## 主要特性

- 基于 Qt / WebEngine 的桌宠宿主与悬浮窗口
- 基于 FastAPI 的聊天后端，支持 `ollama` 与 OpenAI 兼容接口
- 基于 LangGraph 的 Agent 内核，支持工具审批、暂停恢复和状态持久化
- 支持 `edge_tts` 与自定义 HTTP TTS
- 支持本地 MCP 与第三方 MCP 服务接入
- 前端支持 ReAct 轨迹展示和分段流式输出

## 目录说明

- `main.py`：桌宠宿主程序
- `backend/`：后端接口、Agent 编排、MCP 桥接、TTS 和模型相关逻辑
- `index.html`、`settings.html`、`settings.css`、`settings.js`：前端页面
- `third_party_mcp/`：第三方 MCP manifest 与示例目录
- `test_*.py`：回归测试与集成风格单元测试

## 环境要求

- Python 3.13 及以上
- 当前代码主要面向 Windows 环境
- 可选依赖：
  - Ollama，本地推理时使用
  - OpenAI 兼容接口服务
  - Edge TTS 或自定义 HTTP TTS 服务
  - 放在 `model/` 目录下的本地模型资源

## 快速开始

1. 安装依赖。

```powershell
uv sync
```

2. 从示例配置复制一份本地配置。

```powershell
Copy-Item pet_config.example.json pet_config.json
```

3. 按你的本地环境修改 `pet_config.json`，包括模型路径、接口地址、API key 和可选的 MCP 服务。

4. 启动桌宠程序。

```powershell
python main.py
```

5. 程序启动后可通过托盘 / 右键菜单打开设置页，或直接访问 `http://127.0.0.1:8008/settings`。

## 配置说明

- `pet_config.json` 已加入忽略列表，只保留在本地，不应提交。
- `model/` 下的本地模型资源不会随仓库发布。
- 第三方 MCP 的安装目录、`node_modules`、缓存、下载文件和音频缓存都不会被跟踪。
- 安全示例配置文件为 `pet_config.example.json`。

## 测试

运行测试：

```powershell
python -m unittest discover -p "test*.py" -v
```

## 公开仓库清理原则

公开仓库默认不包含以下内容：

- 私有 API key 和个人本地路径
- 运行时状态文件与音频缓存
- 下载得到的第三方 MCP 运行目录
- 本地模型资源和体积较大的生成产物

## 相关文档

- 开发记录：`DEVELOPMENT_REPORT.md`
- English README：`README.md`

