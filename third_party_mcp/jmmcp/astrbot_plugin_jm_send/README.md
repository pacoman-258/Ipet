# astrbot_plugin_jm_send

AstrBot plugin for JM download and QQ file sending.

## Features

- Command trigger: `/jm <album_id>`
- Natural-language trigger (optional): messages like `下载 id 为 350234 的本子`
- MCP call: `download_jm_album_pdf`
- File sending fallback chain:
  1. `event.send(File)`
  2. `upload_group_file` / `upload_private_file`
- Last download query: `/jm_last`

## Commands

- `/jm 350234`
- `/jm_last`

## Recommended Config

- `mcp_command`
  - `C:\\Users\\Administrator\\AppData\\Local\\Programs\\Python\\Python313\\Scripts\\uvx.exe`
- `mcp_args`
  - `["--directory","D:\\LYJ\\2. 02_学习提升\\third_round\\jmmcp","--from","mcp[cli]","--with","jmcomic>=2.6.15","--with","img2pdf>=0.5.1","mcp","run","main.py","--transport","stdio"]`
- `mcp_cwd`
  - `D:\\LYJ\\2. 02_学习提升\\third_round\\jmmcp`
- `mcp_tool_name`
  - `download_jm_album_pdf`
- `mcp_env`
  - `{"UV_CACHE_DIR":"D:\\LYJ\\2. 02_学习提升\\third_round\\jmmcp\\.uv-cache","UV_TOOL_DIR":"D:\\LYJ\\2. 02_学习提升\\third_round\\jmmcp\\.uv-tools"}`

## Important Options

- `report_local_path` (bool, default `true`)
  - Reply with exact local PDF path.
- `enable_nlp_trigger` (bool, default `true`)
  - Enable natural-language trigger.
- `prefer_chain_send` (bool, default `true`)
  - Try `event.send(File)` before API upload.
- `group_file_folder` (string)
  - Optional folder id for group file upload.
- `allow_group_ids` (list)
  - Group whitelist.

## Troubleshooting

- If file is downloaded but not sent:
- Use `/jm_last` to get local path.
  - Check platform adapter supports file segment or upload action.
- Check QQ file size limit and plugin `max_file_mb`.

- If you see `McpError: Connection closed`:
  - Most common cause is wrong process startup config (`mcp_cwd` empty or wrong `mcp_command` env).
  - Ensure MCP actually starts from jmmcp project path.
