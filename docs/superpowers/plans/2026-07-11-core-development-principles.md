# Ipet 开发核心原则分板块实施计划

> 面向不同 Codex 会话的实施路线。每个板块由一个会话独立负责；共享同一工作目录时必须串行执行，若要并行则使用独立 branch/worktree。每个会话开始前读取 `AGENTS.md`、`开发核心原则.txt`、本计划和本板块直接相关文件，结束前生成自己的 `docs/reports/*.html` 报告。

**目标：** 把工作过程透明化、人类思路与机器增强、探索/验证分离、严谨审批、本地停止、平台原生验证、显式失败和真实环境测试落入 Ipet 的运行时合同。

**非目标：** 本计划不要求一次重写全部桌面自动化，不引入通用工作流引擎，不为尚未支持的平台伪造实现，也不把原始模型思维链展示给用户。

## 总体验收原则

- 工作日志展示结构化事件与证据，不展示原始思维链、密钥、完整截图内容或未经脱敏的数据。
- 探索未知 GUI 可以主动截图；结果验证遵循“确定性机器信号 -> 用户确认 -> 最后才截图”。
- 任一状态更改在审批前不得执行，审批后也必须满足后置条件才能进入依赖步骤。
- 停止信号不等待 Brain 或网络，停止后不得产生新的副作用。
- `available`、`enabled`、`invoked`、`succeeded`、`verified` 必须在协议和 UI 中保持不同语义。
- `main.py`、`backend/app.py`、`index.html` 继续 composition-only，并且每个高冲突文件只有一个板块 owner。

## 会话依赖与落地顺序

| 顺序 | 板块 | 建议 owner | 依赖 | 高冲突文件所有权 |
| --- | --- | --- | --- | --- |
| 0 | 运行时合同与测试基线 | qa-reports | 无 | 无 |
| 1 | Brain 工作事件与搜索真实性 | brain | 板块 0 | `backend/chat_stream_flow.py` 第一阶段 owner |
| 2 | 本地停止后端与任务取消 | human-ops | 板块 0、1 | `backend/chat_stream_flow.py` 第二阶段 owner |
| 3 | Codex 风格工作日志与停止 UI | body/frontend | 板块 1、2 | `index.html` 唯一 owner |
| 4 | 探索截图策略 | body | 板块 0 | 无 |
| 5 | macOS 原生只读验证器 | desktop-shell/body | 板块 0 | `main.py` 仅允许薄 wiring |
| 6 | 证据绑定与失败关闭 | human-ops | 板块 4、5 | 无 |
| 7 | 后置条件与用户结果确认 | human-ops | 板块 2、5、6 | `human_ops/approval_flow.py` owner |
| 8 | 端到端验收与文档收口 | qa-reports | 板块 1-7 | 不实现新领域行为 |

板块 4 和 5 可以并行；其他共享文件板块按表中顺序落地。后续会话不得覆盖前序会话的未提交变更。

## 板块 0：运行时合同与测试基线

**目标：** 先固定跨模块语言，避免各会话分别发明“阶段”“停止”“验证”和“搜索成功”。

**主要范围：** `docs/WORKFLOW.md`、现有 Brain/Human Ops schema 测试、SSE 合同测试；优先扩展已有类型和帮助函数，无必要不新增模块。

- [ ] 定义工作事件：`planning`、`searching`、`observing`、`acting`、`waiting_approval`、`verifying`、`waiting_confirmation`、`stopping`、`stopped`、`blocked`、`completed`。
- [ ] 每个事件至少包含 `turn_id/task_id`、稳定 `status_id`、`source`、`status`、用户可见摘要以及是否瞬态；证据只传脱敏摘要。
- [ ] 定义任务生命周期和合法迁移，至少覆盖 `running -> stopping -> stopped`，以及 terminal 状态后拒绝新动作。
- [ ] 定义能力五态：available、enabled、invoked、succeeded、verified。
- [ ] 定义动作后置条件、验证结果和用户确认结果（yes/no/unknown）的最小数据形状。
- [ ] 先写合同测试，后续板块必须复用，不能各自复制字符串判断。

**验收：** 文档和测试能明确回答“某次搜索是否真实调用”“某个点击是否只发送了事件”“任务停止后哪些事件仍允许出现”。

## 板块 1：Brain 工作事件与搜索真实性

**目标：** 让 Brain、Codex app-server 和聊天 SSE 产生可审计工作事件，并解决“开启搜索但从未调用搜索仍被当成联网回答”。

**建议文件：** `brain/llm.py`、`backend/chat_stream_flow.py`、`frontend/chat_stream.js` 的协议测试对应项、`tests/test_brain_llm_providers.py`、`tests/test_chat_stream_flow.py`。本板块不实现最终 UI。

- [ ] 把 Brain -> Observe 的问题以脱敏、用户可读事件发送；不要传原始隐藏思维。
- [ ] 记录搜索能力五态，只有收到真实 `webSearch` item 才设置 `invoked=true`。
- [ ] 最新、当前、实时类请求在内置搜索 enabled 且 available 时优先使用原生搜索；若未调用或失败，明确降级，不得静默回答为“当前事实”。
- [ ] 搜索、打开页面、页内查找、普通推理和最终回答使用稳定 status id，支持前端原位更新而非重复刷屏。
- [ ] 保留 provider 原始失败事实并脱敏展示。

**验收场景：** 询问纳斯达克当前值时，原生搜索真实调用并显示搜索活动；若未调用，最终答复明确说明没有联网，且不会自动进入 Human Ops 浏览器路径，除非用户选择降级。

## 板块 2：本地停止后端与任务取消

**目标：** 建立不依赖 Brain 的本地任务取消通道。

**建议文件：** focused backend route/helper、`backend/chat_stream_flow.py`、`human_ops/approval_flow.py`、Codex app-server/HTTP 调用的取消清理、对应后端测试。`backend/app.py` 只做 router/wiring。

- [ ] 为活动 turn 建立最小取消状态和停止 API；终态后幂等返回。
- [ ] 停止时取消可取消的模型流、联网搜索、Observe 分析和等待任务；短生命周期子进程必须进入已有清理逻辑。
- [ ] 清空尚未执行的动作，令 pending approvals 失效；审批接口执行前再次检查任务是否已停止。
- [ ] 当前不可中断原子动作结束后禁止进入下一步，结果标记为“可能已发生”而不是自动回滚。
- [ ] SSE 发出 `stopping` 与 `stopped`，说明已完成、未执行和不确定项；停止后的 late event 不得触发副作用。

**验收场景：** 分别在模型流式输出、搜索、Observe、等待审批、审批后 continuation 五个阶段停止；所有场景都不再产生下一项状态更改。

## 板块 3：Codex 风格工作日志与停止 UI

**目标：** 实现用户给出的 Codex 风格效果：过程实时可见，结束可折叠，运行中始终能停止。

**建议文件：** `frontend/chat_worklog.js`、`frontend/chat_stream.js`、`frontend/chat_submit.js`、相关 controller wiring、`frontend/index.css`、前端 source/contract 测试；`index.html` 只允许增加必要 DOM 或加载 wiring。

- [ ] 将现有工作日志升级为按 status id 原位更新的阶段列表，支持运行耗时、来源、成功/失败/停止状态和展开/折叠。
- [ ] 最终回复与工作过程分离；完成、失败或停止后默认折叠过程，用户可以展开审计。
- [ ] Brain -> Observe 问题、搜索活动、审批等待、验证方式和失败原因有清晰但不过度暴露的呈现。
- [ ] 新增“停止任务”控件，不能复用当前 `#chat-stop`（它是停止语音）；运行、搜索、观察和等待审批时持续可见。
- [ ] 点击停止后在下一次 UI 更新立即进入 `stopping`，本地 abort 当前 fetch/stream，同时调用板块 2 的停止 API；重复点击幂等。
- [ ] 支持明确快捷键，并避免与输入框、ASR、系统快捷键冲突；具体键位需在实现时通过现有 UI 约定确认。

**验收：** UI 行为接近 Codex：进行中有连续工作记录和停止按钮，结束后只突出最终回复；停止后显示“用户已停止”，不会伪装成完成或继续出现审批。

## 板块 4：探索截图策略

**目标：** 允许未知 GUI 探索主动截图，同时阻止截图默认承担结果验证职责。

**建议文件：** `backend/human_ops_observe.py`、`backend/observe_context.py`、`body/active_vision.py`、`backend/vision_analyzer.py` 及 Body/vision 测试。

- [ ] 为 Observe 明确 `purpose=explore|verify`（名称可沿用现有 schema 最小扩展），默认不得从目标提示猜测用途。
- [ ] `explore` 在观察权限开启时可以主动截图、定位 surface/affordance/坐标，并记录事实、推断和 unknowns。
- [ ] `verify` 默认不启动截图，先交给板块 5/7 的验证阶梯；只有最后兜底条件满足时才调用视觉验证。
- [ ] 探索截图和坐标有时效性；动作、窗口切换、停止或矛盾证据出现后立即失效。
- [ ] 屏幕数据只保留任务所需摘要，不写入记忆、工作日志正文或持久报告。

**验收：** 探索陌生 GUI 能自动截图寻找入口；点击后的验证不会无条件再次截图。

## 板块 5：macOS 原生只读验证器

**目标：** 用 macOS 正式能力回答“动作后的真实状态是什么”，并保持验证只读。

**建议文件：** matching `app/desktop_*`、`body/` 模块及测试；`main.py` 仅做薄 composition/wiring。

- [ ] 提供进程存在、前台 bundle id、可见窗口、窗口标题、焦点元素/角色等最小验证能力。
- [ ] 每个验证结果包含 evidence、confidence/strength、unknowns 和采集方法；不得把“进程存在”提升为“应用已前台打开”。
- [ ] Safari 场景至少验证 `com.apple.Safari` 是否前台、窗口是否存在，以及输入前是否有可信焦点/地址栏证据。
- [ ] 验证命令和平台 API 保持只读，不因验证触发应用启动、切换焦点或输入。
- [ ] 非 macOS 平台明确返回 unsupported；后续由各平台独立会话实现正式适配器。

**验收：** Finder 前台、Safari 后台运行时，验证器必须判定“Safari 进程存在但未达成前台目标”。

## 板块 6：证据绑定与失败关闭

**目标：** 修复目标提示污染观察、错误坐标被重新标记以及错误界面上继续输入的问题。

**建议文件：** `backend/computer_use_context.py`、`backend/react_prompts.py`、Brain/Human Ops contract tests。

- [ ] affordance 的 label、坐标和证据必须来自同一目标候选；通用“Dock/图标”文字不得把 Finder 坐标赋给 Safari。
- [ ] observation、inference、expectation、unknown 分层传递；目标提示只能用于提问和筛选，不能覆盖观察事实。
- [ ] 矛盾证据出现时进入 blocked/replan，清除旧坐标、焦点和目标假设，禁止继续 type_text/key_press。
- [ ] `type_text` 不再因为参数齐全就自动可执行，必须有当前 surface 和焦点/输入入口证据。
- [ ] 重试必须改变观察问题、证据来源或路径；重复同一截图和假设消耗预算后停止。

**验收反例：** 输入“打开 Safari”，观察只返回 Finder 图标和 Finder 坐标时，不得生成 label=Safari 的 app_icon，不得申请点击或输入。

## 板块 7：动作后置条件与用户结果确认

**目标：** 把 Human Ops 从“动作已发送”升级为“结果经过分层验证”。

**建议文件：** `human_ops/proposals.py`、`human_ops/approval_flow.py`、`human_ops/approval_prompts.py`、focused backend route/helper、相关 Human Ops 测试。

- [ ] 每个状态更改 proposal 携带 expected outcome、验证策略和失败条件；旧 proposal 兼容路径必须保守。
- [ ] 动作返回只记录 executed，不直接产生 verified/completed；先调用平台原生/结构化验证。
- [ ] 自动验证不足时发出 `confirmation_required`，向用户询问“我是否已经达成 XX 效果？”，提供 yes/no/unknown。
- [ ] yes 才能进入依赖步骤；no 立即失败关闭并重新规划；unknown 才允许在屏幕权限开启时进入最后的截图验证。
- [ ] 验证发现目标、焦点、坐标或风险变化时，使旧批准失效；继续动作必须重新审批。
- [ ] 不自动回滚已发生副作用，回滚作为新动作单独审批。

**验收场景：** 点击事件成功发送但 Safari 未成为前台时，流程不得显示“Safari 已打开”，不得申请输入；自动状态不确定时先询问用户，只有 unknown 才进入截图兜底。

## 板块 8：端到端验收与文档收口

**目标：** 用真实路径证明原则已经成为行为，而不仅是提示词和模拟事件。

**主要范围：** `tests/`、`scripts/debug/` 中最小烟雾脚本、`docs/WORKFLOW.md`、`docs/architecture.md`、HTML 报告。发现领域缺陷时退回对应 owner 修复，不在 QA 会话里顺手重写业务模块。

- [ ] 覆盖纳斯达克当前值：原生搜索真实 invoked；工作日志展示搜索；无搜索时明确降级。
- [ ] 覆盖 Safari/Finder 反例：目标、label、坐标、前台状态必须一致，错误时 fail closed。
- [ ] 覆盖探索/验证分离：陌生 GUI 探索允许截图，动作后优先原生验证，用户确认在截图验证之前。
- [ ] 覆盖停止矩阵：模型流、搜索、Observe、等待审批、原子动作和 continuation 阶段。
- [ ] 覆盖隐私：工作日志和报告不包含 API key、完整截图数据、原始思维链或未脱敏本地状态。
- [ ] 先跑每个模块的最小回归，再跑全量回归；真实网络、GUI 和外部服务烟雾测试逐次说明风险并审批。
- [ ] 更新架构、工作流和最终 HTML 报告，列出仍未支持的平台与已知限制。

## 每个 Codex 会话的交付模板

1. 在开始前用 `git status --short` 确认并保护其他会话的改动。
2. 声明本板块 owner、允许修改的文件、依赖提交和明确不做的范围。
3. 先写一个能证明核心合同的失败测试，再做最小实现。
4. 不在 `main.py`、`backend/app.py`、`index.html` 添加领域逻辑；需要时只做薄 wiring，并由表中 owner 操作。
5. 运行本板块定向测试、`git diff --check`，必要时再跑全量回归。
6. 更新 `docs/WORKFLOW.md`（行为变化时）并创建独立 `docs/reports/YYYY-MM-DD-*.html`。
7. 交付说明必须列出：改变了什么、如何验证、仍有什么不确定、下一板块需要什么。
