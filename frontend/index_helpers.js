(() => {
  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  function toRateString(ratePct) {
    let n = Number(ratePct);
    if (!Number.isFinite(n)) {
      n = 0;
    }
    n = Math.round(clamp(n, -50, 100));
    return `${n >= 0 ? "+" : ""}${n}%`;
  }

  function roundInt(value) {
    return Math.round(Number(value) || 0);
  }

  function backgroundOverlayValue(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) {
      return 0.42;
    }
    return clamp(n, 0, 0.9);
  }

  function parsePetNameFromPrompt(raw) {
    const text = String(raw || "").trim();
    if (!text) {
      return "";
    }
    try {
      const data = JSON.parse(text);
      const character = data && typeof data === "object" ? data.character : null;
      if (character && typeof character === "object") {
        if (character.name_cn) {
          return String(character.name_cn).trim();
        }
        if (character.name) {
          return String(character.name).trim();
        }
      }
      if (data && typeof data === "object" && data.name) {
        return String(data.name).trim();
      }
    } catch (_) {}
    return "";
  }

  function speakerNameForRole(role, petDisplayName = "\u684c\u5ba0") {
    return role === "user" ? "\u4f60" : String(petDisplayName || "\u684c\u5ba0");
  }

  function normalizeSkillId(value) {
    return String(value || "").trim();
  }

  function normalizeChatMode(value) {
    const mode = String(value || "").trim().toLowerCase();
    return mode === "chat" || mode === "skill" ? mode : "react";
  }

  function normalizeMemoryMode(value) {
    const mode = String(value || "").trim().toLowerCase();
    return mode === "temporary" ? "temporary" : "persistent";
  }

  function dedupeSkillIds(values, allowedIds = null) {
    const allowedSet = Array.isArray(allowedIds) ? new Set(allowedIds.map(normalizeSkillId).filter(Boolean)) : null;
    const seen = new Set();
    const result = [];
    for (const value of values || []) {
      const normalized = normalizeSkillId(value);
      if (!normalized || seen.has(normalized)) {
        continue;
      }
      if (allowedSet && !allowedSet.has(normalized)) {
        continue;
      }
      seen.add(normalized);
      result.push(normalized);
    }
    return result;
  }

  function normalizeAsrConfig(raw) {
    return {
      enabled: raw?.enabled !== false,
      provider: String(raw?.provider || "funasr").trim() || "funasr",
      api_base_url: String(raw?.api_base_url || "http://127.0.0.1:8012").trim() || "http://127.0.0.1:8012",
      push_to_talk_key: (() => {
        const key = String(raw?.push_to_talk_key || "Alt").trim();
        return ["Alt", "Ctrl", "Space"].includes(key) ? key : "Alt";
      })(),
      interim_results: raw?.interim_results !== false,
    };
  }

  function downsampleFloat32ToInt16Buffer(input, inputSampleRate, outputSampleRate = 16000) {
    const source = input instanceof Float32Array ? input : new Float32Array(input || []);
    if (!source.length) {
      return new ArrayBuffer(0);
    }
    if (inputSampleRate === outputSampleRate) {
      const direct = new Int16Array(source.length);
      for (let index = 0; index < source.length; index += 1) {
        const sample = Math.max(-1, Math.min(1, source[index] || 0));
        direct[index] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
      }
      return direct.buffer;
    }
    const ratio = inputSampleRate / outputSampleRate;
    const length = Math.max(1, Math.round(source.length / ratio));
    const result = new Int16Array(length);
    let offsetSource = 0;
    for (let offsetResult = 0; offsetResult < length; offsetResult += 1) {
      const nextOffsetSource = Math.min(source.length, Math.round((offsetResult + 1) * ratio));
      let total = 0;
      let count = 0;
      for (let index = offsetSource; index < nextOffsetSource; index += 1) {
        total += source[index];
        count += 1;
      }
      const sample = Math.max(-1, Math.min(1, count ? total / count : 0));
      result[offsetResult] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
      offsetSource = nextOffsetSource;
    }
    return result.buffer;
  }

  function normalizeTopicRecord(raw) {
    if (!raw || typeof raw !== "object") {
      return null;
    }
    const topic_id = String(raw.topic_id || raw.session_id || raw.id || "").trim();
    if (!topic_id) {
      return null;
    }
    return {
      topic_id,
      title: String(raw.title || "新话题").trim() || "新话题",
      preview: String(raw.preview || "").trim(),
      updated_at: String(raw.updated_at || raw.created_at || "").trim(),
      created_at: String(raw.created_at || "").trim(),
      persisted: raw.persisted !== false,
      assistant_turn_count: Number(raw.assistant_turn_count || 0) || 0,
      source: String(raw.source || "").trim(),
      supports_history_detail: raw.supports_history_detail !== false,
      supports_delete: raw.supports_delete !== false,
      history_unavailable: !!raw.history_unavailable,
    };
  }

  function normalizeSkillRecord(raw) {
    if (!raw || typeof raw !== "object") {
      return null;
    }
    const id = normalizeSkillId(raw.id || raw.skill_id || raw.slug || raw.name);
    if (!id) {
      return null;
    }
    const sourceValue = String(raw.source || raw.origin || raw.source_type || "").toLowerCase();
    const builtin = !!raw.builtin || sourceValue === "builtin";
    const imported = !!raw.imported || sourceValue === "imported" || (!builtin && sourceValue !== "builtin");
    return {
      id,
      name: String(raw.name || raw.title || id),
      description: String(raw.description || raw.summary || ""),
      source: builtin ? "builtin" : "imported",
      builtin,
      imported,
      scriptCount: Number(raw.script_count || 0) || 0,
      allowlistCount: Number(raw.allowlist_count || 0) || 0,
    };
  }

  function formatStepMeta(payload) {
    const stepIndex = Number(payload?.step_index || 0);
    const stepTitle = String(payload?.step_title || "").trim();
    const batchSize = Number(payload?.batch_size || 0);
    const batchId = String(payload?.batch_id || "").trim();
    const parts = [];
    if (stepIndex > 0) {
      parts.push(stepTitle ? `步骤 ${stepIndex}：${stepTitle}` : `步骤 ${stepIndex}`);
    }
    if (batchSize > 0) {
      parts.push(`批次：${batchSize} 次工具调用`);
    }
    if (batchId) {
      parts.push(`批次 ID：${batchId}`);
    }
    return parts.join("\n");
  }

  window.IpetIndexHelpers = {
    clamp,
    toRateString,
    roundInt,
    backgroundOverlayValue,
    parsePetNameFromPrompt,
    speakerNameForRole,
    normalizeSkillId,
    normalizeChatMode,
    normalizeMemoryMode,
    dedupeSkillIds,
    normalizeAsrConfig,
    downsampleFloat32ToInt16Buffer,
    normalizeTopicRecord,
    normalizeSkillRecord,
    formatStepMeta,
  };
})();
