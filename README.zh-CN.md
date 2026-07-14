# Ipet Neo Aspect

Ipet Neo Aspect 是一个本地桌宠产品，后端重构采用 Body-first 模型。当前产品只按四个模块理解：

- **Body**：桌面宿主、宠物窗口、前端表现、语音输入输出、屏幕观察和本地设备控制。
- **Brain**：每轮用户输入只做一步 LLM 决策。
- **Human Ops**：审批、拒绝、补充指令和危险动作执行安全。
- **Memory & Skills**：长期记忆、用户偏好、对话摘要、已学习流程和本地技能。

编码代理请先读 `AGENTS.md`。

## 能力概览

- 通过 Python 桌面宿主和根目录 HTML UI 运行本地桌宠。
- 支持文字输入、push-to-talk 语音输入、聊天气泡和 TTS 输出。
- Brain 每次只选择下一步动作，避免隐藏式长链路自动执行。
- 可能影响本机、文件或用户数据的动作必须先经过 Human Ops。
- Memory & Skills 是 Ipet 自己的数据层，不交给外部黑盒状态。
- 启用视觉时，只使用受控屏幕观察和边界清晰的证据文本。

## 仓库结构

```text
Ipet/
|-- main.py                    # 桌面组合、Qt 生命周期与兼容薄包装
|-- backend/                   # API 组合、路由、辅助函数与适配器
|-- body/                      # Body 宿主、观察、语音与表现行为
|-- brain/                     # Brain LLM 单步决策
|-- human_ops/                 # 审批、动作复核和执行记录
|-- memory/                    # 长期记忆与摘要
|-- skills/                    # 内置与学习得到的本地技能
|-- app/                       # 桌面组合与宿主支持模块
|-- frontend/                  # 桌宠 UI 控制器与行为模块
|-- index.html                 # 根桌宠 UI 文档与脚本装载入口
|-- settings.html              # 根设置页入口
|-- settings.css
|-- settings.js
|-- docs/                      # 架构、工作流、所有权和按需报告
|-- tests/                     # 单元测试与回归测试
|-- scripts/                   # 诊断与开发工具
|-- model/                     # 已跟踪示例/参考宠物资源与本地忽略资源
|-- pet_config.example.json    # 安全示例配置
|-- README.md                  # 英文 README
|-- README.html                # 中文 HTML 阅读版
```

根入口路径会为桌面加载兼容保持稳定。`main.py`、`backend/app.py` 和 `index.html` 只负责组合与装载；新增领域行为应放入对应模块目录。

## 本地启动

优先使用项目 `.venv` 与 Python 3.12。

```powershell
uv run --no-sync python main.py
```

如需本地配置，先从示例复制：

```powershell
Copy-Item pet_config.example.json pet_config.json
```

`pet_config.json` 只属于本机。不要提交 API Key、本地绝对路径、截图、生成音频或本地应用状态。

### 在线 ASR

设置页的 **Body → 语音识别提供方** 可以选择本地 FunASR 或 `Groq Whisper（在线）`。选择 Groq 后填写 API Key；也可以不保存密钥，改用 `GROQ_API_KEY` 环境变量。在线模式会在松开 push-to-talk 热键后上传这一段音频并返回最终文字，暂不提供边说边出的中间结果。

## 测试

完整回归：

```powershell
python -m unittest discover -s tests -p "test*.py" -v
```

编码代理应选择最小相关测试。纯文档任务通常只需 `git diff --check` 和针对所改契约的搜索检查。

## 核心文档

- `docs/architecture.md`
- `docs/architecture_ZH.md`
- `docs/WORKFLOW.md`
- `docs/PROJECT_STRUCTURE.md`
- `docs/SUBAGENTS.md`
- `AGENTS.md`
