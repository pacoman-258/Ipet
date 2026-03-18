"use strict";

const fs = require("fs");
const path = require("path");
const readline = require("readline");
const { spawnSync } = require("child_process");

const SERVER_INFO = { name: "agent_reach_github", version: "0.1.0" };
const DEFAULT_BODY_LIMIT = 4000;
const DEFAULT_FILE_LIMIT = 12000;
const DEFAULT_SEARCH_LIMIT = 5;
const MAX_SEARCH_LIMIT = 20;
const TEST_MODE = String(process.env.AGENT_REACH_GITHUB_TEST_MODE || "").trim().toLowerCase();

function fileExists(filePath) {
  try {
    return fs.existsSync(filePath) && fs.statSync(filePath).isFile();
  } catch (_) {
    return false;
  }
}

function candidateCommandsFromPath(command) {
  const text = String(command || "").trim();
  if (!text) {
    return [];
  }
  const out = [text];
  if (process.platform === "win32") {
    const lower = text.toLowerCase();
    if (!/\.(exe|cmd|bat)$/.test(lower)) {
      out.push(`${text}.exe`, `${text}.cmd`, `${text}.bat`);
    }
  }
  return Array.from(new Set(out));
}

function pathSearchCandidates(baseName) {
  const pathValue = String(process.env.PATH || "");
  const dirs = pathValue.split(path.delimiter).map((item) => item.trim()).filter(Boolean);
  const out = [];
  for (const dir of dirs) {
    for (const candidate of candidateCommandsFromPath(baseName)) {
      out.push(path.join(dir, candidate));
    }
  }
  return out;
}

function commonWindowsGhLocations() {
  if (process.platform !== "win32") {
    return [];
  }
  const localAppData = String(process.env.LOCALAPPDATA || "").trim();
  const userProfile = String(process.env.USERPROFILE || "").trim();
  const programFiles = String(process.env.ProgramFiles || "C:\\Program Files").trim();
  const programFilesX86 = String(process.env["ProgramFiles(x86)"] || "C:\\Program Files (x86)").trim();
  const programData = String(process.env.ProgramData || "C:\\ProgramData").trim();
  return [
    path.join(programFiles, "GitHub CLI", "gh.exe"),
    path.join(programFilesX86, "GitHub CLI", "gh.exe"),
    localAppData ? path.join(localAppData, "Programs", "GitHub CLI", "gh.exe") : "",
    localAppData ? path.join(localAppData, "Microsoft", "WindowsApps", "gh.exe") : "",
    userProfile ? path.join(userProfile, "scoop", "shims", "gh.cmd") : "",
    programData ? path.join(programData, "chocolatey", "bin", "gh.exe") : "",
    programData ? path.join(programData, "chocolatey", "bin", "gh.cmd") : "",
  ].filter(Boolean);
}

function resolveInstalledCommand(command) {
  const directCandidates = [];
  for (const candidate of candidateCommandsFromPath(command)) {
    if (path.isAbsolute(candidate) || candidate.includes(path.sep) || candidate.includes("/")) {
      directCandidates.push(candidate);
    }
  }
  for (const candidate of directCandidates) {
    if (fileExists(candidate)) {
      return candidate;
    }
  }

  const baseName = path.basename(String(command || "").trim() || "gh");
  const searchCandidates = [
    ...pathSearchCandidates(baseName),
    ...commonWindowsGhLocations(),
  ];
  for (const candidate of searchCandidates) {
    if (fileExists(candidate)) {
      return candidate;
    }
  }
  return String(command || "").trim() || "gh";
}

function parseJsonEnv(name, fallback) {
  const raw = String(process.env[name] || "").trim();
  if (!raw) {
    return fallback;
  }
  try {
    const parsed = JSON.parse(raw);
    return parsed;
  } catch (_) {
    return fallback;
  }
}

function getGhCommandParts() {
  const configured = String(process.env.AGENT_REACH_GITHUB_GH_COMMAND || "gh").trim() || "gh";
  const command = TEST_MODE ? configured : resolveInstalledCommand(configured);
  const extraArgs = parseJsonEnv("AGENT_REACH_GITHUB_GH_ARGS", []);
  return {
    command,
    extraArgs: Array.isArray(extraArgs) ? extraArgs.map((item) => String(item)) : [],
  };
}

function writeMessage(payload) {
  process.stdout.write(`${JSON.stringify(payload)}\n`);
}

function response(id, result) {
  writeMessage({ jsonrpc: "2.0", id, result });
}

function errorResponse(id, message, code = -32000) {
  writeMessage({
    jsonrpc: "2.0",
    id,
    error: { code, message: String(message || "unknown error") },
  });
}

function clampLimit(value, fallback = DEFAULT_SEARCH_LIMIT) {
  const num = Number(value);
  if (!Number.isFinite(num)) {
    return fallback;
  }
  return Math.max(1, Math.min(MAX_SEARCH_LIMIT, Math.trunc(num)));
}

function trimText(value, limit) {
  const text = String(value || "");
  if (text.length <= limit) {
    return text;
  }
  return `${text.slice(0, Math.max(0, limit - 3)).trimEnd()}...`;
}

function repoNameFromUrl(url) {
  const text = String(url || "");
  const match = text.match(/repos\/([^/]+\/[^/]+)$/);
  if (match) {
    return match[1];
  }
  return "";
}

function ensureRepoName(repo) {
  const text = String(repo || "").trim();
  if (!/^[^/\s]+\/[^/\s]+$/.test(text)) {
    throw new Error("repo must be in owner/name format");
  }
  return text;
}

function ensurePathValue(filePath) {
  const text = String(filePath || "").trim().replace(/^\/+/, "");
  if (!text) {
    throw new Error("path cannot be empty");
  }
  return text;
}

function buildQuery(baseQuery, qualifiers) {
  const parts = [String(baseQuery || "").trim()];
  for (const qualifier of qualifiers) {
    const text = String(qualifier || "").trim();
    if (text) {
      parts.push(text);
    }
  }
  return parts.filter(Boolean).join(" ").trim();
}

function runCommand(command, args) {
  const proc = spawnSync(command, args, {
    encoding: "utf-8",
    cwd: process.cwd(),
    env: process.env,
    maxBuffer: 16 * 1024 * 1024,
  });
  if (proc.error) {
    if (proc.error.code === "ENOENT") {
      throw new Error(`required command not found: ${command}`);
    }
    throw proc.error;
  }
  return proc;
}

function runGh(args, { parseJson = true } = {}) {
  const { command, extraArgs } = getGhCommandParts();
  const proc = runCommand(command, extraArgs.concat(args.map((item) => String(item))));
  if (proc.status !== 0) {
    const detail = trimText(proc.stderr || proc.stdout || `gh exited with code ${proc.status}`, 800);
    throw new Error(detail || `gh exited with code ${proc.status}`);
  }
  const stdout = String(proc.stdout || "").trim();
  if (!parseJson) {
    return stdout;
  }
  if (!stdout) {
    return {};
  }
  try {
    return JSON.parse(stdout);
  } catch (error) {
    throw new Error(`gh returned invalid JSON: ${error.message || String(error)}`);
  }
}

function mockGhApi(endpoint, params = {}) {
  if (endpoint === "search/repositories") {
    return {
      total_count: 1,
      items: [
        {
          full_name: "openai/openai-python",
          description: "Python client",
          html_url: "https://github.com/openai/openai-python",
          language: "Python",
          stargazers_count: 123,
          forks_count: 12,
          open_issues_count: 9,
          updated_at: "2026-03-15T00:00:00Z",
          default_branch: "main",
          private: false,
        },
      ],
    };
  }
  if (endpoint === "search/issues") {
    const payload = {
      total_count: 1,
      items: [
        {
          number: 42,
          title: "Fix issue",
          state: "open",
          repository_url: "https://api.github.com/repos/openai/openai-python",
          html_url: "https://github.com/openai/openai-python/issues/42",
          created_at: "2026-03-10T00:00:00Z",
          updated_at: "2026-03-15T00:00:00Z",
          body: "issue body",
          user: { login: "alice" },
        },
      ],
    };
    if (String(params.q || "").includes("is:pr")) {
      payload.items[0].pull_request = { url: "https://api.github.com/repos/openai/openai-python/pulls/42" };
    }
    return payload;
  }
  if (endpoint === "repos/openai/openai-python") {
    return {
      full_name: "openai/openai-python",
      description: "Python client",
      html_url: "https://github.com/openai/openai-python",
      language: "Python",
      stargazers_count: 123,
      forks_count: 12,
      open_issues_count: 9,
      updated_at: "2026-03-15T00:00:00Z",
      default_branch: "main",
      private: false,
    };
  }
  if (endpoint === "repos/openai/openai-python/issues/42") {
    return {
      number: 42,
      title: "Fix issue",
      state: "open",
      repository_url: "https://api.github.com/repos/openai/openai-python",
      html_url: "https://github.com/openai/openai-python/issues/42",
      created_at: "2026-03-10T00:00:00Z",
      updated_at: "2026-03-15T00:00:00Z",
      body: "issue body",
      user: { login: "alice" },
    };
  }
  if (endpoint === "repos/openai/openai-python/contents/README.md") {
    return {
      sha: "abc123",
      size: 24,
      html_url: "https://github.com/openai/openai-python/blob/main/README.md",
      encoding: "base64",
      content: Buffer.from("# Demo\\nHello from README", "utf-8").toString("base64"),
    };
  }
  throw new Error(`unexpected mock endpoint: ${endpoint}`);
}

function ensureGhReady() {
  if (TEST_MODE === "missing") {
    throw new Error(
      "GitHub CLI (gh) is not installed or not reachable from this process. " +
      "Resolved command: gh. Install gh or set AGENT_REACH_GITHUB_GH_COMMAND to the full gh.exe path, then run `gh auth login`."
    );
  }
  if (TEST_MODE === "unauth") {
    throw new Error("GitHub CLI is not authenticated. Run `gh auth login` first. Details: mock auth failure");
  }
  if (TEST_MODE) {
    return;
  }
  const { command } = getGhCommandParts();
  try {
    runGh(["auth", "status"], { parseJson: false });
  } catch (error) {
    const message = String(error && error.message ? error.message : error);
    if (message.includes("required command not found") || message.includes(" EPERM")) {
      throw new Error(
        `GitHub CLI (gh) is not installed or not reachable from this process. ` +
        `Resolved command: ${command}. Install gh or set AGENT_REACH_GITHUB_GH_COMMAND to the full gh.exe path, then run \`gh auth login\`.`
      );
    }
    throw new Error(`GitHub CLI is not authenticated. Run \`gh auth login\` first. Details: ${message}`);
  }
  if (!command) {
    throw new Error("GitHub CLI command is not configured.");
  }
}

function ghApi(endpoint, params = {}) {
  if (TEST_MODE) {
    return mockGhApi(endpoint, params);
  }
  const args = ["api", endpoint];
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") {
      continue;
    }
    args.push("-F", `${key}=${value}`);
  }
  return runGh(args);
}

function mapRepository(item) {
  return {
    full_name: String(item.full_name || item.name || ""),
    description: trimText(item.description || "", 500),
    html_url: String(item.html_url || ""),
    language: item.language || "",
    stargazers_count: Number(item.stargazers_count || 0),
    forks_count: Number(item.forks_count || 0),
    open_issues_count: Number(item.open_issues_count || 0),
    updated_at: String(item.updated_at || ""),
    default_branch: String(item.default_branch || ""),
    visibility: item.private ? "private" : "public",
  };
}

function mapIssueLike(item) {
  return {
    number: Number(item.number || 0),
    title: String(item.title || ""),
    state: String(item.state || ""),
    kind: item.pull_request ? "pull_request" : "issue",
    repo: repoNameFromUrl(item.repository_url) || "",
    author: item.user && item.user.login ? String(item.user.login) : "",
    html_url: String(item.html_url || ""),
    created_at: String(item.created_at || ""),
    updated_at: String(item.updated_at || ""),
    body: trimText(item.body || "", DEFAULT_BODY_LIMIT),
  };
}

function decodeBase64Content(text) {
  return Buffer.from(String(text || "").replace(/\s+/g, ""), "base64").toString("utf-8");
}

function searchRepositories(args) {
  const query = String(args.query || "").trim();
  if (!query) {
    throw new Error("query is required");
  }
  const owner = String(args.owner || "").trim();
  const language = String(args.language || "").trim();
  const q = buildQuery(query, [owner ? `user:${owner}` : "", language ? `language:${language}` : ""]);
  const data = ghApi("search/repositories", { q, per_page: clampLimit(args.limit) });
  const items = Array.isArray(data.items) ? data.items : [];
  return {
    total_count: Number(data.total_count || items.length),
    items: items.slice(0, clampLimit(args.limit)).map(mapRepository),
  };
}

function searchIssues(kind, args) {
  const query = String(args.query || "").trim();
  if (!query) {
    throw new Error("query is required");
  }
  const repo = String(args.repo || "").trim();
  const state = String(args.state || "").trim().toLowerCase();
  const qualifiers = [kind === "pr" ? "is:pr" : "is:issue"];
  if (repo) {
    qualifiers.push(`repo:${ensureRepoName(repo)}`);
  }
  if (state) {
    qualifiers.push(`state:${state}`);
  }
  const q = buildQuery(query, qualifiers);
  const data = ghApi("search/issues", { q, per_page: clampLimit(args.limit) });
  const items = Array.isArray(data.items) ? data.items : [];
  return {
    total_count: Number(data.total_count || items.length),
    items: items.slice(0, clampLimit(args.limit)).map(mapIssueLike),
  };
}

function getRepository(args) {
  const repo = ensureRepoName(args.repo);
  const data = ghApi(`repos/${repo}`);
  return mapRepository(data);
}

function getIssueOrPr(args) {
  const repo = ensureRepoName(args.repo);
  const number = Number(args.number);
  if (!Number.isFinite(number) || number <= 0) {
    throw new Error("number must be a positive integer");
  }
  const data = ghApi(`repos/${repo}/issues/${Math.trunc(number)}`);
  return mapIssueLike(data);
}

function getFile(args) {
  const repo = ensureRepoName(args.repo);
  const filePath = ensurePathValue(args.path);
  const ref = String(args.ref || "").trim();
  const data = ghApi(`repos/${repo}/contents/${filePath}`, ref ? { ref } : {});
  if (Array.isArray(data)) {
    throw new Error("path points to a directory, not a file");
  }
  const content = data.encoding === "base64" ? decodeBase64Content(data.content || "") : String(data.content || "");
  return {
    repo,
    path: filePath,
    ref: ref || "",
    size: Number(data.size || Buffer.byteLength(content, "utf-8")),
    sha: String(data.sha || ""),
    html_url: String(data.html_url || ""),
    truncated: content.length > DEFAULT_FILE_LIMIT,
    content: trimText(content, DEFAULT_FILE_LIMIT),
  };
}

const TOOLS = [
  {
    name: "github_search_repositories",
    description: "Search public GitHub repositories with optional owner and language filters.",
    inputSchema: {
      type: "object",
      properties: {
        query: { type: "string" },
        limit: { type: "integer", minimum: 1, maximum: MAX_SEARCH_LIMIT },
        language: { type: "string" },
        owner: { type: "string" },
      },
      required: ["query"],
    },
  },
  {
    name: "github_search_issues",
    description: "Search GitHub issues in public repositories or within a specific repo.",
    inputSchema: {
      type: "object",
      properties: {
        query: { type: "string" },
        repo: { type: "string" },
        state: { type: "string", enum: ["open", "closed"] },
        limit: { type: "integer", minimum: 1, maximum: MAX_SEARCH_LIMIT },
      },
      required: ["query"],
    },
  },
  {
    name: "github_search_pull_requests",
    description: "Search GitHub pull requests in public repositories or within a specific repo.",
    inputSchema: {
      type: "object",
      properties: {
        query: { type: "string" },
        repo: { type: "string" },
        state: { type: "string", enum: ["open", "closed"] },
        limit: { type: "integer", minimum: 1, maximum: MAX_SEARCH_LIMIT },
      },
      required: ["query"],
    },
  },
  {
    name: "github_get_repository",
    description: "Get summary metadata for a GitHub repository using owner/name.",
    inputSchema: {
      type: "object",
      properties: {
        repo: { type: "string" },
      },
      required: ["repo"],
    },
  },
  {
    name: "github_get_issue_or_pr",
    description: "Get a GitHub issue or pull request by repository and number.",
    inputSchema: {
      type: "object",
      properties: {
        repo: { type: "string" },
        number: { type: "integer", minimum: 1 },
      },
      required: ["repo", "number"],
    },
  },
  {
    name: "github_get_file",
    description: "Read a text file from a GitHub repository at an optional ref.",
    inputSchema: {
      type: "object",
      properties: {
        repo: { type: "string" },
        path: { type: "string" },
        ref: { type: "string" },
      },
      required: ["repo", "path"],
    },
  },
];

const TOOL_HANDLERS = {
  github_search_repositories: searchRepositories,
  github_search_issues: (args) => searchIssues("issue", args || {}),
  github_search_pull_requests: (args) => searchIssues("pr", args || {}),
  github_get_repository: getRepository,
  github_get_issue_or_pr: getIssueOrPr,
  github_get_file: getFile,
};

function handleInitialize() {
  ensureGhReady();
  return {
    protocolVersion: "2024-11-05",
    capabilities: {
      tools: { listChanged: false },
    },
    serverInfo: SERVER_INFO,
  };
}

function handleToolsList() {
  return { tools: TOOLS };
}

function handleToolsCall(params) {
  const name = String((params && params.name) || "").trim();
  const handler = TOOL_HANDLERS[name];
  if (!handler) {
    throw new Error(`unknown tool: ${name}`);
  }
  const result = handler((params && params.arguments) || {});
  return {
    content: [
      {
        type: "text",
        text: JSON.stringify(result, null, 2),
      },
    ],
        structuredContent: result,
  };
}

async function handleMessage(message) {
  const method = String(message.method || "");
  if (!Object.prototype.hasOwnProperty.call(message, "id")) {
    return;
  }
  try {
    if (method === "initialize") {
      response(message.id, handleInitialize());
      return;
    }
    if (method === "tools/list") {
      response(message.id, handleToolsList());
      return;
    }
    if (method === "tools/call") {
      response(message.id, handleToolsCall(message.params || {}));
      return;
    }
    errorResponse(message.id, `unsupported method: ${method}`, -32601);
  } catch (error) {
    errorResponse(message.id, error && error.message ? error.message : String(error));
  }
}

const rl = readline.createInterface({
  input: process.stdin,
  crlfDelay: Infinity,
});

rl.on("line", async (line) => {
  const raw = String(line || "").trim();
  if (!raw) {
    return;
  }
  try {
    const message = JSON.parse(raw);
    await handleMessage(message);
  } catch (error) {
    const message = error && error.message ? error.message : String(error);
    writeMessage({ jsonrpc: "2.0", error: { code: -32700, message } });
  }
});

rl.on("close", () => {
  process.exit(0);
});
