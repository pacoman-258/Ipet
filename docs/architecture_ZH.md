# Ipet Neo Aspect 架构说明

## 1. 项目定位

Ipet Neo Aspect 是一个本地桌宠产品。桌宠不是另一个应用的薄皮肤；它自己负责用户体验、本地上下文、审批边界和长期产品记忆。

当前架构采用 Body-first 设计：

```text
Body 负责感知和表达。
Brain 负责下一步决策。
Human Ops 负责危险影响审批。
Memory & Skills 负责记忆和学习。
```

## 2. 模块图

```mermaid
flowchart TD
    User[用户] --> Body[Body]
    Body --> Brain[Brain]
    Brain --> Body
    Brain --> HumanOps[Human Ops]
    Brain --> MemorySkills[Memory & Skills]
    HumanOps --> Body
    HumanOps --> MemorySkills
    MemorySkills --> Brain
    Body --> User
    QA[QA Reports] --> Docs[HTML 报告]
    Body --> QA
    Brain --> QA
    HumanOps --> QA
    MemorySkills --> QA
```

## 3. Body

Body 是产品表面，也是本地机器边界。它负责：

- 桌面宿主生命周期
- 宠物窗口行为
- 聊天气泡、表情、动作和 TTS 表现
- ASR 与语音输入状态
- 启用后的本地屏幕观察
- 审批后的受控本地动作执行
- 前端事件渲染

Body 不凭空生成长期事实。它观察、展示、执行已审批动作，并把结果交回 Brain。

## 4. Brain

Brain 负责单轮 LLM 决策。它接收紧凑的 turn packet：

- 当前用户请求
- 近期对话上下文
- 允许使用的记忆片段
- Body 观察摘要
- 可用技能
- 当前审批或执行状态

Brain 每次只返回一个结构化下一步：

- `say`
- `observe`
- `act`
- `remember`
- `learn_skill`
- `stop`

Brain 不直接改文件、改设置、写记忆、操作 UI 或管理进程。它只提出意图，由其他模块做边界清晰的执行。

LLM 输出会被归类成 `BrainDecision`。提示词要求 provider 返回类似 `{"kind":"say","text":"..."}` 的紧凑 JSON；如果 provider 返回普通文本，也会被当作 `say`，保证只聊天路径仍能正常使用。当前真正执行的路径只有 `say`；`observe`、`act`、`remember`、`learn_skill` 和 `stop` 先作为后续审批与执行循环的预留类型。

Brain 通过窄 API 边界调用用户选择的大模型服务。当前支持 OpenAI 兼容 chat completions、Ollama chat、Anthropic 兼容 messages 三种格式。Provider 端点、模型名、温度和可选 API Key 都在 Web 设置页配置，并只保存在本地配置里。

设置页可以通过 `/api/brain/models` 使用表单里当前尚未保存的 provider、endpoint 和可选 key 拉取模型列表。返回的模型 ID 可以一键填入模型名称字段，密钥不会回显到浏览器。

## 5. Human Ops

Human Ops 是安全与审批层。它负责：

- 风险分级
- 审批提示文案
- 拒绝与带约束批准流程
- 执行记录
- 本地动作审计文本
- 对用户解释副作用

任何会触及文件、进程、配置、网络访问、外部服务、破坏性操作或长期用户数据的动作，都必须经过 Human Ops。

## 6. Memory & Skills

Memory & Skills 负责长期学习表面：

- 对话记忆
- 摘要
- 用户偏好
- 技能清单
- 学到的流程
- 保留周期和隐私规则

记忆条目应包含来源、原因、范围和保留预期。学到的技能应包含触发条件、步骤、审批要求和验证方法。

## 7. 数据流

```text
用户输入
  -> Body observe/say
  -> Brain 单步决策
  -> 需要时进入 Human Ops 审批
  -> Body 执行或展示
  -> Body 观察结果
  -> 允许时 Memory & Skills 写入
  -> Brain 给出终态总结
  -> 文件改动轮次生成 HTML 报告
```

这个设计刻意避免隐藏式长链路自动执行。只要下一步改变风险或范围，Brain 就应停下，让 Human Ops 重新判断。

## 8. 当前实现备注

仓库正在迁移中：

- `main.py` 仍是桌面宿主入口。
- `backend/` 仍是当前 Python API 表面。
- `index.html` 仍是根目录桌宠 UI 入口。
- `settings.html`、`settings.css`、`settings.js` 仍是根目录设置页。
- 目标模块目录是 `body/`、`brain/`、`human_ops/`、`memory/`、`skills/`、`app/`、`frontend/`。

新代码应在实现 owner 确认迁移路径后向目标模块靠拢。文档应描述 Neo 模块模型，即使代码尚未全部搬完。

## 9. 开发规则

- 保持 Body-first 边界清楚。
- Brain 决策必须单步、可检查。
- 危险影响必须经过 Human Ops。
- Memory & Skills 是长期产品数据。
- 根目录 UI 文件在加载路径同步迁移前继续保留。
- 每轮文件改动后生成 HTML 报告。
- 不要回退同事在你负责切片之外的改动。

## 10. 一句话总结

Ipet Neo Aspect 是本地桌宠：Body 与世界交互，Brain 选择下一步，Human Ops 保护用户，Memory & Skills 让它逐渐变聪明。
