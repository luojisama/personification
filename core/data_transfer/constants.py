FORMAT = "personification-data-package"
VERSION = 1

DATASETS = (
    "group_messages",
    "session_messages",
    "conversation_threads",
    "group_relation_edges",
    "group_style_snapshots",
    "group_state",
    "local_user_profiles",
    "group_memories",
)

DEFAULT_DATASETS = (
    "conversation_threads",
    "group_relation_edges",
    "group_style_snapshots",
    "group_state",
    "local_user_profiles",
    "group_memories",
)

TABLE_FIELDS = {
    "group_messages": ("id", "group_id", "user_id", "nickname", "content", "image_count", "visual_summary", "is_bot", "reply_to_msg_id", "reply_to_user_id", "mentioned_ids", "is_at_bot", "message_id", "thread_id", "source_kind", "sender_role", "timestamp"),
    "session_messages": ("id", "session_id", "role", "content", "is_summary", "timestamp", "metadata"),
    "conversation_threads": ("thread_id", "group_id", "topic_summary", "participants", "created_at", "last_active_at"),
    "group_relation_edges": ("group_id", "src_user_id", "dst_user_id", "edge_kind", "weight", "last_seen_at", "sample_msg_id"),
    "group_style_snapshots": ("id", "group_id", "style_text", "style_json", "created_at"),
}

PRIMARY_KEYS = {
    "group_messages": ("id",),
    "session_messages": ("id",),
    "conversation_threads": ("thread_id",),
    "group_relation_edges": ("group_id", "src_user_id", "dst_user_id", "edge_kind"),
    "group_style_snapshots": ("id",),
}

GROUP_CONFIG_FIELDS = frozenset({
    "enabled", "sticker_enabled", "tts_enabled", "schedule_enabled",
    "schedule_prompt", "custom_prompt", "allow_group_admin_config",
})

GROUP_KV_NAMESPACES = frozenset({
    "group_member_aliases", "group_mute_state", "group_style_last_run",
    "group_style_daily_count", "group_knowledge_last_run",
    "group_knowledge_daily_count",
})

EXCLUDED_CATEGORIES = (
    "credentials", "auth", "log", "audit", "trace", "token",
    "provider_health", "tasks", "proactive", "qzone",
)

MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_ENTRY_BYTES = 64 * 1024 * 1024
MAX_ENTRIES = 32
MAX_COMPRESSION_RATIO = 100

PLAN_TOKEN_TTL_SECONDS = 15 * 60
ARTIFACT_RETENTION_SECONDS = 7 * 24 * 60 * 60
BACKUP_RETENTION_SECONDS = 30 * 24 * 60 * 60

# Only stable, scope-local memory fields cross the package boundary. Derived
# indexes and fields that can point at another memory are rebuilt locally.
MEMORY_PAYLOAD_FIELDS = frozenset({
    "memory_id", "memory_type", "palace_zone", "summary", "aliases",
    "topic_tags", "entity_tags", "snippets", "user_id", "group_id",
    "thread_id", "time_created", "last_accessed_at", "access_count",
    "confidence", "salience", "stability", "emotional_weight",
    "privacy_level", "permission_type", "expires_at", "supports_recall",
    "supports_autofill", "revision", "tone_risk", "irony_risk",
    "time_sensitivity", "reinforcement_count", "tier",
})
