# jmmcp

一个基于 `jmcomic` 的 MCP（stdio）服务项目，核心能力是：

- 输入本子 `album_id`
- 自动下载并转换为 PDF
- 返回 PDF 的本地绝对路径

本仓库还包含一个 AstrBot 插件 `astrbot_plugin_jm_send`，可在 QQ 中通过指令触发下载并发送文件。

## 功能说明

### 1) MCP 工具

工具名：`download_jm_album_pdf`

输入参数：
- `album_id: str`（必须为纯数字字符串）
- `save_dir: str | null`（可选，默认为 `downloads/pdf`）

成功返回：
- `ok: true`
- `album_id`
- `pdf_path`（绝对路径）
- `file_size_bytes`
- `filename`

失败返回：
- `ok: false`
- `error`
- `suggestion`

### 2) 文件命名与存储

- 默认目录：`downloads/pdf`
- 命名规则：
  - 首次：`350234.pdf`
  - 重复下载：`350234 (1).pdf`、`350234 (2).pdf`...
- 每次任务会创建临时目录，任务结束后自动清理
- 仅保留 PDF（原图会删除）

## 环境要求

- Python `>=3.13`
- 建议使用 `uv`

## 安装与启动

```powershell
uv sync
uv run python main.py
```

服务启动后通过 `stdio` 等待 MCP 客户端调用。

## MCP Server 配置（JSON）

仓库已提供示例文件：
- [mcp-server.json](d:/LYJ/2. 02_学习提升/third_round/jmmcp/mcp-server.json)

你可以直接复制其中的 `mcpServers.jmmcp` 配置到支持 MCP 的客户端（如 Cherry Studio / Cline / Cursor 等）。

## 使用示例

自然语言示例：
- “帮我下载 id 为 350234 的本子”

模型应调用：
- `download_jm_album_pdf(album_id="350234")`

## 可选：JM 访问配置

默认匿名访问。若目标站点限制访问，可设置环境变量 `JM_OPTION_PATH` 指向你的 `jmcomic` 配置文件（包含 cookies / domain / proxy 等）。

## AstrBot 插件说明

目录：
- [astrbot_plugin_jm_send](d:/LYJ/2. 02_学习提升/third_round/jmmcp/astrbot_plugin_jm_send)

插件用途：
- QQ 指令触发下载
- 调用本 MCP 工具
- 将 PDF 发送到群或私聊

常用指令：
- `/jm 350234`
- `/jm_last`

## 发布到 GitHub（一步步）

下面以“新建仓库并推送”为例：

1. 初始化和检查（如果你还没初始化）
```powershell
git init
git status
```

2. 配置远端（把 `<your-repo-url>` 替换成你的仓库地址）
```powershell
git remote add origin <your-repo-url>
```

3. 提交代码
```powershell
git add .
git commit -m "feat: jmcomic mcp server with astrbot plugin"
```

4. 推送到 GitHub 主分支
```powershell
git branch -M main
git push -u origin main
```

5. 在 GitHub 页面补充仓库信息
- 设置仓库可见性（Public/Private）
- 添加 Topics（如 `mcp`, `python`, `astrbot`, `qq-bot`, `jmcomic`）
- 如需发版，创建 `Release` 并上传插件 zip

## 免责声明

请确保你的使用行为符合当地法律法规、平台条款以及版权要求。此项目仅用于技术学习与自动化开发实践。
