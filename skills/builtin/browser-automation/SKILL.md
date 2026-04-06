---
name: "Browser Automation"
description: "Use this skill for web browser automation tasks such as opening pages, clicking elements, typing, filling forms, scraping content, uploading or downloading files, taking screenshots, and continuing after a manual login on Windows or macOS."
---

Use common recipes + optional helper tools for browser MCP on Windows or macOS: `open_page`, `open_and_type`, `click_followup`, `extract_or_verify`. Keep user-provided file paths unchanged.

Do not use this skill for desktop apps, Finder or Explorer, terminal commands, system settings, file manager workflows, or code editing.

Recipe Index

`open_page`
- Use when the user mainly wants a page opened or switched.
- Prefer a direct MCP call to `browser_navigate`.
- Minimum inputs: `url`.
- Fallback: wait briefly, verify URL or visible page anchor, retry once if navigation was interrupted.
- Stop and ask when the page needs login confirmation, a captcha, or a native permission prompt.

`open_and_type`
- Use when the task is "open page, then type query or form text".
- Prefer a direct MCP batch on the same tool family: `browser_navigate` then `browser_type`.
- Minimum inputs: `url`, `text`. Add `selector` only if the field is already known and stable.
- Fallback: verify the page first, then retry typing with a better selector or focused field.
- Stop and ask when the field is ambiguous, hidden behind login, or submits sensitive data.

`click_followup`
- Use when the page is already open and the next step is one stable click plus verification.
- Prefer a direct MCP call to `browser_click`.
- Minimum inputs: `selector`.
- Fallback: retry with a nearby stable selector, then stop instead of blind multi-click loops.
- Stop and ask before submits, confirms, deletes, purchases, payments, or account-changing clicks.

`extract_or_verify`
- Use when the goal is to confirm page state or compress long browser output into a short summary.
- Prefer direct MCP inspection if one clear read tool is already visible. Use the helper result compressor only when the browser output is long or noisy.
- Minimum inputs: a stable `selector` when checking a specific element.
- Fallback: verify the surrounding page anchor, then summarize the latest result instead of re-reading the whole page.
- Stop and ask when verification depends on hidden state, native dialogs, or uncertain account context.

Execution rules:
1. Use only allowlisted browser tools from `playwright.*` or `playwright_mcp.*`, plus the optional helper tools defined by this skill.
2. Follow this order: `navigate -> wait -> locate stable selector -> act -> verify -> summarize`.
3. Prefer selectors in this order: role, label, placeholder, data-testid, stable text anchor, then CSS.
4. Prefer DOM-visible controls over keyboard shortcuts, right-click menus, drag-and-drop, or OS-native dialogs.
5. If `playwright.*` and `playwright_mcp.*` are both visible, keep the same family for the whole step. Use `skill.browser-automation.resolve_mcp_recipe` only when the family choice is ambiguous or a standard recipe skeleton will save reasoning.
6. Prefer direct MCP calls for simple single-step actions. Do not call a local helper script before every navigate, click, or type action.
7. Use `skill.browser-automation.compact_browser_result` only when browser output is too long or noisy for the next reasoning step.
8. When you hit a captcha, permission prompt, native file picker, native download confirmation, or login uncertainty, stop in a recoverable state and ask for the next step.
9. High-risk actions still require approval before continuing: submit, confirm, delete, purchase, payment, bulk write, or account-changing actions.
10. After each major action, verify the page state before moving on.
11. If an action fails, retry only with a nearby selector or a short wait. Do not loop blindly.

Cross-platform guidance:
- This workflow is optimized for Windows and macOS browser sessions.
- Linux is best effort only.
- Keep file paths exactly as the user provided them. Do not rewrite Windows backslashes or macOS POSIX paths.
- Assume page layout or browser chrome may differ slightly across platforms and verify before acting.
- Avoid instructions that depend on Ctrl or Cmd differences unless the page itself clearly requires them.
