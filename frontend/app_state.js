(() => {
  function createDefaultAppState() {
    return {
      model_url: "",
      scale: 0.3,
      offset_x: 0,
      offset_y: 40,
      rotation: 0,
      opacity: 1,
      edit_mode: false,
      follow_mouse: true,
      background_enabled: false,
      background_image: "",
      background_image_url: "",
      background_overlay_opacity: 0.42,
      chat: {
        backend_url: "http://127.0.0.1:8008",
        model: "gpt-5.4",
        session_id: "default",
        voice: "zh-CN-XiaoxiaoNeural",
        rate_pct: 0,
        tts_provider: "edge_tts",
        tts_provider_url: "",
        expression_mode: true,
        expression_output_format: "ndjson_v1",
        react_enabled: true,
        react_visibility: "inline",
        max_reasoning_steps: 10,
        available_expressions: [],
        lip_sync_gain: 1,
        mouth_parameter_ids: [],
        mouth_form_parameter_ids: [],
        skills: {
          enabled: true,
          default_active_ids: [],
        },
        asr: {
          enabled: true,
          provider: "funasr",
          api_base_url: "http://127.0.0.1:8012",
          provider_url: "https://api.groq.com/openai/v1/audio/transcriptions",
          model: "whisper-large-v3-turbo",
          push_to_talk_key: "Alt",
          interim_results: true,
        },
        system_prompt: "",
        pet_display_name: "机魂",
      },
    };
  }

  window.IpetAppState = Object.freeze({
    createDefaultAppState,
  });
})();
