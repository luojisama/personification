<template>
  <FormField :label="label" :control-id="id" :description="description" :error="error" group>
    <template #default="{ controlId, labelledBy, describedBy, invalid }">
      <section :id="controlId" class="provider-pool" role="group" :aria-labelledby="labelledBy" :aria-describedby="describedBy || undefined" :aria-invalid="invalid ? 'true' : undefined">
        <p class="muted-copy">连接信息按供应商保存一次；模型、容量和用途在同一张卡片中配置。模型列表探测失败不会清空已添加模型。</p>
        <ol v-if="providers.length" class="provider-list">
          <li v-for="(provider, index) in providers" :key="providerKey(provider, index)" class="provider-card">
            <header class="provider-card-head">
              <strong>{{ providerName(provider, index) }}</strong>
              <button class="button button-secondary" type="button" :aria-label="`删除供应商 ${providerName(provider, index)}`" @click="removeProvider(index)">删除供应商</button>
            </header>
            <div class="provider-grid">
              <TextField :id="fieldId(index, 'provider-id')" :model-value="text(provider.provider_id)" label="供应商 ID" description="稳定 ID；用于模型用途绑定，创建后请勿随意改动。" required @update:model-value="updateProvider(index, 'provider_id', $event)" />
              <TextField :id="fieldId(index, 'name')" :model-value="text(provider.name)" label="显示名称" required @update:model-value="updateProvider(index, 'name', $event)" />
              <SelectField :id="fieldId(index, 'api-type')" :model-value="text(provider.api_type, 'openai')" label="API 类型" :options="apiTypeOptions" @update:model-value="updateProvider(index, 'api_type', $event)" />
              <TextField :id="fieldId(index, 'api-url')" :model-value="text(provider.api_url)" label="API 地址" placeholder="https://…/v1" @update:model-value="updateProvider(index, 'api_url', $event)" />
              <TextField :id="fieldId(index, 'api-key')" :model-value="text(provider.api_key)" label="API Key" type="password" autocomplete="new-password" placeholder="保留掩码沿用原凭据；输入新密钥替换" @update:model-value="updateProvider(index, 'api_key', $event)" />
              <TextField v-if="proxySupported(provider)" :id="fieldId(index, 'proxy')" :model-value="text(provider.proxy)" label="代理（可选）" placeholder="http://127.0.0.1:7890" @update:model-value="updateProvider(index, 'proxy', $event)" />
              <p v-else-if="text(provider.proxy)" class="muted-copy provider-grid-note">当前 {{ text(provider.api_type) || 'Provider' }} 路由不会读取此连接代理；已保留原值但未将它当作有效配置。</p>
              <SelectField v-if="isGemini(provider)" :id="fieldId(index, 'gemini-auth')" :model-value="text(provider.gemini_auth_mode, 'auto')" label="Gemini 认证" :options="geminiAuthOptions" @update:model-value="updateProvider(index, 'gemini_auth_mode', $event)" />
              <TextField v-if="usesAuthPath(provider)" :id="fieldId(index, 'auth-path')" :model-value="text(provider.auth_path)" label="认证文件路径" type="password" autocomplete="new-password" placeholder="留空保留服务端已保存路径" @update:model-value="updateProvider(index, 'auth_path', $event)" />
              <TextField v-if="usesProject(provider)" :id="fieldId(index, 'project')" :model-value="text(provider.project)" label="项目 ID（可选）" @update:model-value="updateProvider(index, 'project', $event)" />
            </div>
            <div class="provider-model-toolbar">
              <h4>已添加模型</h4>
              <button class="button button-secondary" type="button" :disabled="probing[providerKey(provider, index)]" @click="discoverModels(index)">{{ probing[providerKey(provider, index)] ? '正在获取…' : '获取可用模型' }}</button>
              <button class="button button-secondary" type="button" @click="addModel(index)">手动添加模型</button>
              <button class="button button-secondary" type="button" @click="previewBudget(index)">核对生效容量</button>
            </div>
            <p v-if="probeNotice[providerKey(provider, index)]" class="muted-copy" role="status">{{ probeNotice[providerKey(provider, index)] }}</p>
            <p v-if="probeError[providerKey(provider, index)]" class="form-error" role="alert">{{ probeError[providerKey(provider, index)] }}</p>
            <section v-if="candidates(provider, index).length" class="candidate-panel" :aria-label="`${providerName(provider, index)} 的待添加模型`">
              <div class="candidate-toolbar">
                <h5>探测到的候选模型</h5>
                <input v-model="candidateSearch[providerKey(provider, index)]" class="form-control" type="search" placeholder="搜索模型 ID 或名称" :aria-label="`${providerName(provider, index)} 候选模型搜索`" />
                <button class="button button-secondary" type="button" :disabled="!selectedCandidates(provider, index).length" @click="addSelectedCandidates(index)">添加选中的 {{ selectedCandidates(provider, index).length }} 个</button>
              </div>
              <ul class="candidate-list">
                <li v-for="candidate in filteredCandidates(provider, index)" :key="text(candidate.model_id)">
                  <label><input type="checkbox" :checked="isCandidateSelected(provider, index, text(candidate.model_id))" @change="toggleCandidate(provider, index, text(candidate.model_id), ($event.target as HTMLInputElement).checked)" /> <span>{{ text(candidate.display_name) || text(candidate.model_id) }}</span> <code>{{ text(candidate.model_id) }}</code><span v-if="number(candidate.reported_input_token_limit)">输入能力 {{ candidate.reported_input_token_limit }} / 输出能力 {{ candidate.reported_output_token_limit || '未知' }}</span></label>
                </li>
              </ul>
            </section>
            <ol v-if="models(provider).length" class="model-list">
              <li v-for="(model, modelIndex) in models(provider)" :key="modelKey(model, modelIndex)" class="model-card">
                <div class="model-grid">
                  <TextField :id="fieldId(index, `model-${modelIndex}-id`)" :model-value="text(model.model_id)" label="模型 ID" required placeholder="例如 gemini-2.5-flash" @update:model-value="updateModel(index, modelIndex, 'model_id', $event)" />
                  <TextField :id="fieldId(index, `model-${modelIndex}-display`)" :model-value="text(model.display_name)" label="显示名称" placeholder="留空使用模型 ID" @update:model-value="updateModel(index, modelIndex, 'display_name', $event)" />
                  <NumberField :id="fieldId(index, `model-${modelIndex}-context`)" :model-value="number(model.context_window_tokens)" label="上下文 Token" :min="1" :step="1" placeholder="未配置时保守窗口 32,768" @update:model-value="updateModel(index, modelIndex, 'context_window_tokens', $event)" />
                  <NumberField :id="fieldId(index, `model-${modelIndex}-input`)" :model-value="number(model.max_input_tokens)" label="最大输入 Token" :min="1" :step="1" placeholder="可选" @update:model-value="updateModel(index, modelIndex, 'max_input_tokens', $event)" />
                  <NumberField :id="fieldId(index, `model-${modelIndex}-output`)" :model-value="number(model.max_output_tokens)" label="每次输出预留 Token" :min="1" :step="1" placeholder="可选" @update:model-value="updateModel(index, modelIndex, 'max_output_tokens', $event)" />
                  <NumberField :id="fieldId(index, `model-${modelIndex}-limit`)" :model-value="number(model.input_token_limit)" label="输入 Token 硬上限" :min="1" :step="1" placeholder="可选" @update:model-value="updateModel(index, modelIndex, 'input_token_limit', $event)" />
                  <SwitchField :id="fieldId(index, `model-${modelIndex}-enabled`)" :model-value="model.enabled !== false" label="启用模型" @update:model-value="updateModel(index, modelIndex, 'enabled', $event)" />
                  <SwitchField :id="fieldId(index, `model-${modelIndex}-tools`)" :model-value="capability(model, 'function_call')" label="工具调用（声明）" @update:model-value="setCapability(index, modelIndex, 'function_call', $event)" />
                  <SwitchField :id="fieldId(index, `model-${modelIndex}-vision`)" :model-value="capability(model, 'image_input')" label="图片输入（声明）" @update:model-value="setCapability(index, modelIndex, 'image_input', $event)" />
                </div>
                <p class="muted-copy">{{ capacityText(provider, index, model) }}</p>
                <p v-if="number(reportedModel(provider, index, model).reported_input_token_limit) || number(reportedModel(provider, index, model).reported_output_token_limit)" class="muted-copy">服务元数据：输入能力 {{ number(reportedModel(provider, index, model).reported_input_token_limit) || '未知' }}；输出能力 {{ number(reportedModel(provider, index, model).reported_output_token_limit) || '未知' }}。采纳会将输入能力作为未配置模型的保守窗口和输入容量；生效输入仍受分配比例与预留约束。输出能力上限不作为每次输出预留。</p>
                <button v-if="number(reportedModel(provider, index, model).reported_input_token_limit)" class="button button-secondary" type="button" @click="adoptCapacity(index, modelIndex)">采纳为保守窗口和输入容量</button>
                <p class="muted-copy model-capability-note">能力声明会随模型保存，用于路由能力展示；未经过实际探测前仍是“未知”，不会把列表发现当成已验证能力。</p>
                <div class="model-actions">
                  <button class="button button-secondary" type="button" :disabled="text(provider.default_model_id) === text(model.model_id)" @click="setDefaultModel(index, text(model.model_id))">设为默认</button>
                  <button class="button button-secondary" type="button" @click="removeModel(index, modelIndex)">删除模型</button>
                </div>
              </li>
            </ol>
            <p v-else class="structured-list-editor-empty">尚未添加模型。可以获取列表，或直接手动填写模型 ID。</p>
            <div class="purpose-panel">
              <h4>按用途快捷选择</h4>
              <p class="muted-copy">留空即继承该供应商默认模型；严格主模型模式仍由服务端优先处理。</p>
              <div class="purpose-grid">
                <SearchableSelect v-for="purpose in purposes" :key="purpose.key" :id="fieldId(index, `purpose-${purpose.key}`)" :model-value="text(purposeModels(provider)[purpose.key])" :label="purpose.label" :options="modelOptions(provider)" placeholder="继承默认模型" @update:model-value="setPurpose(index, purpose.key, $event)" />
              </div>
              <p v-if="text(provider.default_model_id)" class="muted-copy">默认模型：<code>{{ text(provider.default_model_id) }}</code></p>
            </div>
          </li>
        </ol>
        <p v-else class="structured-list-editor-empty">暂未添加供应商。</p>
        <button class="button button-secondary" type="button" @click="addProvider">添加供应商</button>
      </section>
    </template>
  </FormField>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { api } from "@/api/client";
import { resources } from "@/api/resources";
import FormField from "@vue-app/components/forms/FormField.vue";
import NumberField from "@vue-app/components/forms/NumberField.vue";
import SearchableSelect from "@vue-app/components/forms/SearchableSelect.vue";
import SelectField from "@vue-app/components/forms/SelectField.vue";
import SwitchField from "@vue-app/components/forms/SwitchField.vue";
import TextField from "@vue-app/components/forms/TextField.vue";

type Row = Record<string, unknown>;
type Purpose = "main" | "lite" | "persona" | "compress" | "vision" | "labeler";
const props = withDefaults(defineProps<{ modelValue: unknown; label: string; id: string; description?: string; error?: string }>(), { description: "", error: "" });
const emit = defineEmits<{ "update:modelValue": [value: Row[]]; "update:error": [value: string] }>();
const probing = ref<Record<string, boolean>>({});
const budgetPreviews = ref<Record<string, { snapshot: string; models: Row[] }>>({});
const probeNotice = ref<Record<string, string>>({});
const probeError = ref<Record<string, string>>({});
const discoveredCandidates = ref<Record<string, Row[]>>({});
const discoveredMetadata = ref<Record<string, Row[]>>({});
const candidateSearch = ref<Record<string, string>>({});
const selectedCandidateIds = ref<Record<string, string[]>>({});
const purposes: Array<{ key: Purpose; label: string }> = [
  { key: "main", label: "主回复" }, { key: "lite", label: "轻量判断／审核" }, { key: "persona", label: "画像" },
  { key: "compress", label: "摘要" }, { key: "vision", label: "视觉／表情识别" }, { key: "labeler", label: "表情打标" },
];
const apiTypeOptions: Array<{ value: string; label: string }> = [
  { value: "openai", label: "OpenAI 兼容" }, { value: "anthropic", label: "Anthropic" }, { value: "gemini", label: "Gemini" },
  { value: "openai_codex", label: "OpenAI Codex" }, { value: "gemini_cli", label: "Gemini CLI" }, { value: "antigravity_cli", label: "Antigravity CLI" },
];
const geminiAuthOptions = [
  { value: "auto", label: "自动（x-goog 优先）" }, { value: "x-goog-api-key", label: "x-goog-api-key" },
  { value: "bearer", label: "Authorization Bearer" }, { value: "query_legacy", label: "Query key（旧兼容）" },
];
const providers = computed<Row[]>(() => Array.isArray(props.modelValue) ? props.modelValue.filter(record).map(clone) : []);
function record(value: unknown): value is Row { return Boolean(value) && typeof value === "object" && !Array.isArray(value); }
function clone<T>(value: T): T { return JSON.parse(JSON.stringify(value)) as T; }
function text(value: unknown, fallback = ""): string { return typeof value === "string" ? value : fallback; }
function number(value: unknown): number | null { return typeof value === "number" && Number.isFinite(value) ? value : null; }
function providerKey(provider: Row, index: number): string { return text(provider.provider_id) || `new-${index}`; }
function providerName(provider: Row, index: number): string { return text(provider.name) || `供应商 ${index + 1}`; }
function modelKey(model: Row, index: number): string { return text(model.model_id) || `new-model-${index}`; }
function models(provider: Row): Row[] { return Array.isArray(provider.models) ? provider.models.filter(record).map(clone) : []; }
function purposeModels(provider: Row): Row { return record(provider.purpose_models) ? clone(provider.purpose_models) : {}; }
function normalizedType(provider: Row): string { return text(provider.api_type).toLowerCase().replaceAll("-", "_"); }
function proxySupported(provider: Row): boolean { return ["openai", "openai_codex", "gemini_cli", "antigravity_cli"].includes(normalizedType(provider)); }
function isGemini(provider: Row): boolean { return normalizedType(provider) === "gemini"; }
function usesAuthPath(provider: Row): boolean { return ["openai_codex", "gemini_cli", "antigravity_cli"].includes(normalizedType(provider)); }
function usesProject(provider: Row): boolean { return ["gemini_cli", "antigravity_cli"].includes(normalizedType(provider)); }
function modelOptions(provider: Row) { return models(provider).filter(model => model.enabled !== false && text(model.model_id)).map(model => ({ value: text(model.model_id), label: text(model.display_name) || text(model.model_id) })); }
function capability(model: Row, key: string): boolean { return record(model.capabilities) && model.capabilities[key] === true; }
function fieldId(providerIndex: number, field: string): string { return `${props.id}-provider-${providerIndex}-${field}`; }
function commit(next: Row[]): void { emit("update:error", validate(next)); emit("update:modelValue", next); }
function mutateProvider(index: number, updater: (provider: Row) => void): void { const next = providers.value.map(clone); const provider = next[index]; if (!provider) return; updater(provider); commit(next); }
function updateProvider(index: number, key: string, value: unknown): void { mutateProvider(index, provider => setOrDelete(provider, key, value)); }
function updateModel(index: number, modelIndex: number, key: string, value: unknown): void { mutateProvider(index, provider => { const nextModels = models(provider); const model = nextModels[modelIndex]; if (!model) return; setOrDelete(model, key, value); provider.models = nextModels; }); }
function setOrDelete(target: Row, key: string, value: unknown): void { if (value === "" || value === null || value === undefined) delete target[key]; else target[key] = value; }
function newProviderId(): string { const random = globalThis.crypto?.randomUUID?.().replaceAll("-", "") ?? `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`; return `provider_${random.slice(0, 24)}`; }
function addProvider(): void { const next = providers.value.map(clone); const suffix = next.length + 1; next.push({ provider_id: newProviderId(), name: `Provider ${suffix}`, api_type: "openai", models: [], purpose_models: {} }); commit(next); }
function removeProvider(index: number): void { commit(providers.value.filter((_, current) => current !== index).map(clone)); }
function addModel(index: number, model: Row = {}): void { mutateProvider(index, provider => { const nextModels = models(provider); nextModels.push({ model_id: "", display_name: "", enabled: true, ...model }); provider.models = nextModels; }); }
function setDefaultModel(index: number, modelId: string): void { if (modelId) mutateProvider(index, provider => { provider.default_model_id = modelId; }); }
function removeModel(index: number, modelIndex: number): void {
  const provider = providers.value[index]; const model = provider ? models(provider)[modelIndex] : undefined; const modelId = text(model?.model_id);
  if (!provider || !model) return;
  const used = text(provider.default_model_id) === modelId || Object.values(purposeModels(provider)).some(value => value === modelId);
  if (used) { emit("update:error", "该模型仍被默认模型或用途选择器引用；请先改为其他模型或恢复继承。"); return; }
  mutateProvider(index, current => { current.models = models(current).filter((_, currentIndex) => currentIndex !== modelIndex); });
}
function setPurpose(index: number, purpose: Purpose, modelId: string): void { mutateProvider(index, provider => { const next = purposeModels(provider); if (modelId) next[purpose] = modelId; else delete next[purpose]; provider.purpose_models = next; }); }
function setCapability(index: number, modelIndex: number, key: "function_call" | "image_input", enabled: boolean): void { mutateProvider(index, provider => { const nextModels = models(provider); const model = nextModels[modelIndex]; if (!model) return; const caps = record(model.capabilities) ? clone(model.capabilities) : {}; caps[key] = enabled; model.capabilities = caps; provider.models = nextModels; }); }
function candidates(provider: Row, index: number): Row[] { return discoveredCandidates.value[providerKey(provider, index)] ?? []; }
function filteredCandidates(provider: Row, index: number): Row[] { const query = text(candidateSearch.value[providerKey(provider, index)]).toLocaleLowerCase("zh-CN"); return candidates(provider, index).filter(candidate => !query || [text(candidate.model_id), text(candidate.display_name)].join(" ").toLocaleLowerCase("zh-CN").includes(query)); }
function selectedCandidates(provider: Row, index: number): Row[] { const ids = new Set(selectedCandidateIds.value[providerKey(provider, index)] ?? []); return candidates(provider, index).filter(candidate => ids.has(text(candidate.model_id))); }
function isCandidateSelected(provider: Row, index: number, modelId: string): boolean { return (selectedCandidateIds.value[providerKey(provider, index)] ?? []).includes(modelId); }
function toggleCandidate(provider: Row, index: number, modelId: string, selected: boolean): void { const key = providerKey(provider, index); const ids = new Set(selectedCandidateIds.value[key] ?? []); if (selected) ids.add(modelId); else ids.delete(modelId); selectedCandidateIds.value = { ...selectedCandidateIds.value, [key]: [...ids] }; }
function addSelectedCandidates(index: number): void { const provider = providers.value[index]; if (!provider) return; const key = providerKey(provider, index); const adding = selectedCandidates(provider, index); if (!adding.length) return; mutateProvider(index, current => { const existing = models(current); const known = new Set(existing.map(model => text(model.model_id))); current.models = [...existing, ...adding.filter(model => !known.has(text(model.model_id))).map(clone)]; }); discoveredCandidates.value = { ...discoveredCandidates.value, [key]: candidates(provider, index).filter(candidate => !adding.some(chosen => text(chosen.model_id) === text(candidate.model_id))) }; selectedCandidateIds.value = { ...selectedCandidateIds.value, [key]: [] }; }
async function discoverModels(index: number): Promise<void> {
  const provider = providers.value[index]; if (!provider) return; const key = providerKey(provider, index); probing.value = { ...probing.value, [key]: true }; probeError.value = { ...probeError.value, [key]: "" };
  try {
    const result = await resources.providerModels(clone(provider));
    const discovered = Array.isArray(result.models) ? result.models.filter(record) : [];
    discoveredMetadata.value = { ...discoveredMetadata.value, [key]: discovered };
    const existingIds = new Set(models(provider).map(model => text(model.model_id)).filter(Boolean));
    const normalized = discovered.filter(model => text(model.model_id || model.id)).map(model => ({ ...model, model_id: text(model.model_id || model.id), display_name: text(model.display_name || model.label || model.name || model.model_id || model.id) })).filter(model => !existingIds.has(text(model.model_id)));
    discoveredCandidates.value = { ...discoveredCandidates.value, [key]: normalized };
    selectedCandidateIds.value = { ...selectedCandidateIds.value, [key]: [] };
    probeNotice.value = { ...probeNotice.value, [key]: normalized.length ? `已读取 ${normalized.length} 个候选模型；请筛选并勾选后再添加，现有模型未被覆盖。` : "服务端未返回新的可选模型；仍可手动填写模型 ID。" };
  } catch {
    probeError.value = { ...probeError.value, [key]: "获取模型列表未完成。已添加模型和凭据草稿均已保留；可稍后重试或手动填写模型 ID。" };
  } finally { probing.value = { ...probing.value, [key]: false }; }
}
function reportedModel(provider: Row, index: number, model: Row): Row {
  const metadata = discoveredMetadata.value[providerKey(provider, index)]?.find(item => text(item.model_id || item.id) === text(model.model_id));
  return metadata ? { ...model, ...metadata } : model;
}
function capacityText(provider: Row, index: number, model: Row): string {
  const preview = budgetPreviews.value[providerKey(provider, index)];
  const current = preview?.snapshot === JSON.stringify(provider) ? preview.models.find(item => item.model_id === model.model_id) : undefined;
  if (!current) return "未配置窗口时使用保守 32,768；默认输入分配为窗口的一半，并受输出、思考、安全预留和配置上限约束。点击“核对生效容量”读取服务端预算。";
  if (current.error) return "容量无效：输出、思考和安全预留超过上下文窗口。";
  return `生效窗口 ${current.context_window_tokens}；输入上限 ${current.effective_input_limit}；来源 ${current.source === 'configured' ? '显式配置' : '保守默认值'}。`;
}
async function previewBudget(index: number): Promise<void> {
  const provider = providers.value[index]; if (!provider) return;
  // Capacity checks need no credentials, connection address or private metadata.
  const draft = { model: provider.model, legacy_capacity_model_id: provider.legacy_capacity_model_id, models: provider.models,
    default_model_id: provider.default_model_id, context_window_tokens: provider.context_window_tokens,
    max_input_tokens: provider.max_input_tokens, max_output_tokens: provider.max_output_tokens, input_token_limit: provider.input_token_limit };
  try {
    const result = await api.post<{ models: Row[] }>("/config/provider-budget", { provider: draft });
    budgetPreviews.value = { ...budgetPreviews.value, [providerKey(provider, index)]: { snapshot: JSON.stringify(provider), models: result.models } };
  } catch { probeError.value = { ...probeError.value, [providerKey(provider, index)]: "容量核对未完成，请稍后重试。" }; }
}
function adoptCapacity(index: number, modelIndex: number): void {
  mutateProvider(index, provider => {
    const entries = models(provider); const model = entries[modelIndex]; if (!model) return;
    const metadata = reportedModel(provider, index, model);
    const input = number(metadata.reported_input_token_limit);
    if (metadata.reported_input_token_limit) model.reported_input_token_limit = metadata.reported_input_token_limit;
    if (metadata.reported_output_token_limit) model.reported_output_token_limit = metadata.reported_output_token_limit;
    if (!input || input <= 0) return;
    if (!(Number(model.context_window_tokens) > 0)) model.context_window_tokens = input;
    if (!(Number(model.max_input_tokens) > 0)) model.max_input_tokens = input;
    provider.models = entries;
  });
}
function validate(rows: Row[]): string {
  const ids = new Set<string>();
  for (const [index, provider] of rows.entries()) { const id = text(provider.provider_id).trim(); if (!id) return `第 ${index + 1} 个供应商缺少稳定 ID。`; if (ids.has(id)) return `供应商 ID “${id}”重复。`; ids.add(id); const modelIds = new Set<string>(); for (const model of models(provider)) { const modelId = text(model.model_id).trim(); if (!modelId) return `供应商 “${id}”存在未填写模型 ID 的条目。`; if (modelIds.has(modelId)) return `供应商 “${id}”的模型 ID “${modelId}”重复。`; modelIds.add(modelId); } }
  return "";
}
</script>

<style scoped>
.provider-pool,.provider-list,.model-list{display:grid;gap:.85rem}.provider-list,.model-list,.candidate-list{list-style:none;margin:0;padding:0}.provider-card,.model-card,.candidate-panel{display:grid;gap:.8rem;padding:.9rem;border:1px solid var(--border-subtle,rgba(128,128,128,.35));border-radius:.7rem}.provider-card-head,.provider-model-toolbar,.model-actions,.candidate-toolbar{display:flex;align-items:center;gap:.55rem;justify-content:space-between;flex-wrap:wrap}.provider-grid,.model-grid,.purpose-grid{display:grid;gap:.65rem;grid-template-columns:repeat(auto-fit,minmax(12rem,1fr))}.candidate-toolbar h5{margin:0}.candidate-toolbar input{min-width:min(100%,16rem);flex:1 1 14rem}.candidate-list{display:grid;gap:.35rem}.candidate-list label{display:flex;align-items:center;gap:.45rem;cursor:pointer}.candidate-list input[type="checkbox"]{inline-size:auto;block-size:auto;flex:0 0 auto;margin:0}.candidate-list code{overflow-wrap:anywhere}.purpose-panel{display:grid;gap:.55rem;padding-top:.2rem}.purpose-panel h4,.provider-model-toolbar h4{margin:0}.form-error{color:var(--danger,#b42318);margin:0}
</style>
