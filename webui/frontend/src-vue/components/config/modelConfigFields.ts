export const POOL_FIELD = "personification_api_pools";
export const BINDINGS_FIELD = "personification_model_purpose_bindings";

export const ROUTE_PREVIEW_FIELDS = [
  POOL_FIELD,
  "personification_strict_main_model",
  "personification_model_overrides",
  "personification_model",
  "personification_lite_model",
  "personification_persona_model",
  "personification_compress_model",
  "personification_vision_fallback_model",
  "personification_labeler_model",
];

export const MODEL_CONFIG_FIELDS = [
  BINDINGS_FIELD,
  ...ROUTE_PREVIEW_FIELDS,
  "personification_fallback_model",
  "personification_video_fallback_model",
  "personification_fullmodal_provider_model",
  "personification_audio_transcription_model",
  "personification_state_model",
  "personification_style_api_model",
  "personification_splitter_model",
  "personification_embedding_model",
  "personification_favorability_observer_model",
  "personification_response_review_model_role",
  "personification_tts_llm_decision_model_role",
  "personification_tts_model",
  "personification_image_gen_model",
  "personification_image_gen_nanobanan_model",
];
