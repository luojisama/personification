<template>
  <FormField :label="label" :control-id="id" :description="description" :error="error" group>
    <template #default="{ controlId, labelledBy, describedBy, invalid }">
      <section :id="controlId" class="purpose-bindings" role="group" :aria-labelledby="labelledBy" :aria-describedby="describedBy || undefined" :aria-invalid="invalid ? 'true' : undefined">
        <p class="muted-copy">每个用途选择“供应商／模型”；留空则使用已有 Provider 默认路由。保存后下一轮生效，正在进行的回复仍使用其开始时的配置快照。</p>
        <p v-if="loading" class="muted-copy" role="status">正在读取已保存供应商模型…</p>
        <p v-else-if="loadError" class="form-error" role="alert">{{ loadError }}</p>
        <template v-else>
          <div class="binding-grid">
            <SearchableSelect v-for="purpose in purposes" :key="purpose.key" :id="`${id}-${purpose.key}`" :model-value="bindingValue(purpose.key)" :label="purpose.label" :options="options" placeholder="搜索供应商或模型；留空继承" @update:model-value="setBinding(purpose.key, $event)" />
          </div>
          <ul class="effective-list" aria-label="各用途的有效模型说明">
            <li v-for="purpose in purposes" :key="`${purpose.key}-effective`">
              <strong>{{ purpose.label }}：</strong><span>{{ effectiveSummary(purpose.key) }}</span>
              <span v-if="capabilityWarning(purpose.key)" class="capability-warning">{{ capabilityWarning(purpose.key) }}</span>
            </li>
          </ul>
          <p v-if="!options.length" class="muted-copy">尚无可选模型。请先在“模型 Provider 池”添加供应商和模型并保存；现有绑定不会被清空。</p>
        </template>
      </section>
    </template>
  </FormField>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { resources } from "@/api/resources";
import FormField from "@vue-app/components/forms/FormField.vue";
import SearchableSelect from "@vue-app/components/forms/SearchableSelect.vue";

type Row = Record<string, unknown>;
type Purpose = "main" | "lite" | "persona" | "compress" | "vision" | "labeler";
const props = withDefaults(defineProps<{ modelValue: unknown; label: string; id: string; description?: string; error?: string }>(), { description: "", error: "" });
const emit = defineEmits<{ "update:modelValue": [value: Record<string, { provider_id: string; model_id: string }>]; "update:error": [value: string] }>();
const loading = ref(true); const loadError = ref(""); const pools = ref<Row[]>([]); const savedSettings = ref<Record<string, unknown>>({});
const purposes: Array<{ key: Purpose; label: string }> = [
  { key: "main", label: "主回复" }, { key: "lite", label: "轻量判断／审核" }, { key: "persona", label: "画像" },
  { key: "compress", label: "摘要" }, { key: "vision", label: "视觉／表情识别" }, { key: "labeler", label: "表情打标" },
];
const effectiveSettingFields = [
  "personification_api_pools", "personification_strict_main_model", "personification_model_overrides",
  "personification_model", "personification_lite_model", "personification_persona_model",
  "personification_compress_model", "personification_vision_fallback_model", "personification_labeler_model",
] as const;
const bindings = computed<Record<string, { provider_id: string; model_id: string }>>(() => record(props.modelValue) ? normalizeBindings(props.modelValue) : {});
const strictMainEnabled = computed(() => savedSettings.value.personification_strict_main_model === true);
const options = computed(() => pools.value.flatMap((provider) => {
  const providerId = text(provider.provider_id); const providerName = text(provider.name) || providerId;
  const models = Array.isArray(provider.models) ? provider.models.filter(record) : [];
  return models.filter(model => model.enabled !== false && text(model.model_id)).map(model => ({ value: `${providerId}\u001f${text(model.model_id)}`, label: `${providerName}／${text(model.display_name) || text(model.model_id)}`, description: text(model.model_id) }));
}));
function record(value: unknown): value is Row { return Boolean(value) && typeof value === "object" && !Array.isArray(value); }
function text(value: unknown): string { return typeof value === "string" ? value.trim() : ""; }
function normalizeBindings(value: Row): Record<string, { provider_id: string; model_id: string }> { const result: Record<string, { provider_id: string; model_id: string }> = {}; for (const purpose of purposes) { const binding = value[purpose.key]; if (!record(binding)) continue; const providerId = text(binding.provider_id); const modelId = text(binding.model_id); if (providerId && modelId) result[purpose.key] = { provider_id: providerId, model_id: modelId }; } return result; }
function bindingValue(purpose: Purpose): string { const binding = bindings.value[purpose]; return binding ? `${binding.provider_id}\u001f${binding.model_id}` : ""; }
function setBinding(purpose: Purpose, value: string): void { const next = { ...bindings.value }; if (!value) delete next[purpose]; else { const [providerId, modelId, ...rest] = value.split("\u001f"); if (!providerId || !modelId || rest.length) return; next[purpose] = { provider_id: providerId, model_id: modelId }; } emit("update:error", ""); emit("update:modelValue", next); }
function configuredBinding(purpose: Purpose): { provider_id: string; model_id: string } | null { return bindings.value[purpose] ?? null; }
function effectiveBinding(purpose: Purpose): { provider_id: string; model_id: string } | null { return purpose === "lite" && strictMainEnabled.value ? configuredBinding("main") : configuredBinding(purpose); }
function findModel(binding: { provider_id: string; model_id: string } | null): { provider: Row; model: Row } | null {
  if (!binding) return null;
  const provider = pools.value.find(candidate => text(candidate.provider_id) === binding.provider_id);
  const model = Array.isArray(provider?.models) ? provider.models.filter(record).find(candidate => text(candidate.model_id) === binding.model_id) : undefined;
  return provider && model ? { provider, model } : null;
}
function legacyOverride(purpose: Purpose): string {
  const rawOverrides = savedSettings.value.personification_model_overrides;
  let overrides: Row = record(rawOverrides) ? rawOverrides : {};
  if (typeof rawOverrides === "string") try { const parsed: unknown = JSON.parse(rawOverrides); overrides = record(parsed) ? parsed : {}; } catch { overrides = {}; }
  const roleByPurpose: Partial<Record<Purpose, string>> = { main: "agent", lite: "intent", labeler: "sticker" };
  return roleByPurpose[purpose] ? text(overrides[roleByPurpose[purpose] as string]) : "";
}
function legacyValue(purpose: Purpose): string {
  const fieldByPurpose: Record<Purpose, string> = { main: "personification_model", lite: "personification_lite_model", persona: "personification_persona_model", compress: "personification_compress_model", vision: "personification_vision_fallback_model", labeler: "personification_labeler_model" };
  return text(savedSettings.value[fieldByPurpose[purpose]]);
}
function providerDefaultCandidate(): { provider: Row; model: Row } | null {
  for (const provider of pools.value) {
    const models = Array.isArray(provider.models) ? provider.models.filter(record).filter(model => model.enabled !== false && text(model.model_id)) : [];
    const chosen = models.find(model => text(model.model_id) === text(provider.default_model_id)) ?? models[0];
    if (chosen) return { provider, model: chosen };
  }
  return null;
}
function effectiveSummary(purpose: Purpose): string {
  if (purpose === "lite" && strictMainEnabled.value) {
    const main = configuredBinding("main");
    return main ? `严格主模型模式已开启；实际继承主回复的明确绑定 ${main.provider_id}／${main.model_id}。` : "严格主模型模式已开启；实际继承主回复路由。";
  }
  const binding = configuredBinding(purpose);
  if (binding) return `明确绑定：${binding.provider_id}／${binding.model_id}。`;
  const override = legacyOverride(purpose);
  if (override) return `沿用遗留阶段覆盖 “${override}”。`;
  const legacy = legacyValue(purpose);
  if (legacy && ["persona", "compress"].includes(purpose)) return `已配置旧项 “${legacy}”；实际仍继承已有路由／回退设置。`;
  if (legacy) return `未绑定 Provider；沿用遗留模型覆盖 “${legacy}”。`;
  const candidate = providerDefaultCandidate();
  return candidate ? `未显式绑定；默认候选为 ${text(candidate.provider.provider_id)}／${text(candidate.model.model_id)}，实际仍受已有路由策略影响。` : "未显式绑定；继承已有 Provider 默认路由。";
}
function capabilityWarning(purpose: Purpose): string {
  const binding = effectiveBinding(purpose); if (!binding) return "";
  const resolved = findModel(binding); if (!resolved) return "当前目录找不到该绑定目标；保存后服务端会拒绝使用，需重新选择。";
  const caps = record(resolved.model.capabilities) ? resolved.model.capabilities : {};
  if (["vision", "labeler"].includes(purpose) && caps.image_input === false) return "该模型已声明不支持图片输入，视觉／打标路由不可用。";
  if (["vision", "labeler"].includes(purpose) && caps.image_input !== true) return "该模型的图片输入能力尚未经验证。";
  if (!["vision", "labeler"].includes(purpose) && caps.function_call === false) return "该模型已声明不支持工具调用；需要工具的该用途路由可能不可用。";
  if (!["vision", "labeler"].includes(purpose) && caps.function_call !== true) return "该模型的工具调用能力尚未经验证。";
  return "";
}
onMounted(async () => {
  try {
    // The registry spans several pages. Exact existing searches avoid silently
    // treating settings outside the first page as inherited defaults.
    const pages = await Promise.all(effectiveSettingFields.map(fieldName => resources.config(1, 10, { search: fieldName })));
    const entries = pages.flatMap(page => page.items).filter(candidate => effectiveSettingFields.includes(candidate.field_name as typeof effectiveSettingFields[number]));
    const item = entries.find(candidate => candidate.field_name === "personification_api_pools");
    const value = item?.value;
    pools.value = Array.isArray(value) ? value.filter(record) : [];
    savedSettings.value = Object.fromEntries(entries.map(candidate => [candidate.field_name, candidate.value]));
  } catch { loadError.value = "无法读取供应商模型目录；当前绑定已保留，请稍后重试。"; } finally { loading.value = false; }
});
</script>

<style scoped>
.purpose-bindings,.binding-grid,.effective-list{display:grid;gap:.7rem}.binding-grid{grid-template-columns:repeat(auto-fit,minmax(13rem,1fr))}.effective-list{list-style:none;margin:0;padding:0}.effective-list li{display:flex;gap:.35rem;align-items:baseline;flex-wrap:wrap}.capability-warning{color:var(--danger,#b42318)}.form-error{color:var(--danger,#b42318);margin:0}
</style>
