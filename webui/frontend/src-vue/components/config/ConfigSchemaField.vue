<template>
  <div class="config-schema-field">
    <SwitchField
      v-if="controlKind === 'switch'"
      :id="controlId"
      :model-value="booleanValue"
      :label="item.display_name"
      :description="item.description"
      :required="item.required"
      :error="error"
      @update:model-value="emitValue"
    />

    <NumberField
      v-else-if="controlKind === 'number'"
      :id="controlId"
      :model-value="numberValue"
      :label="item.display_name"
      :description="numberDescription"
      :required="item.required"
      :error="error"
      :min="numberMin"
      :max="numberMax"
      :step="numberStep"
      :placeholder="placeholder"
      @update:model-value="emitValue"
    />

    <SearchableSelect
      v-else-if="controlKind === 'select' && useSearchableSelect"
      :id="controlId"
      :model-value="stringValue"
      :label="item.display_name"
      :description="item.description"
      :required="item.required"
      :error="error"
      :options="selectOptions"
      :placeholder="placeholder || '搜索或选择'"
      @update:model-value="emitValue"
    />

    <SelectField
      v-else-if="controlKind === 'select'"
      :id="controlId"
      :model-value="stringValue"
      :label="item.display_name"
      :description="item.description"
      :required="item.required"
      :error="error"
      :options="selectOptions"
      :placeholder="placeholder"
      @update:model-value="emitValue"
    />

    <TextareaField
      v-else-if="controlKind === 'textarea'"
      :id="controlId"
      :model-value="stringValue"
      :label="item.display_name"
      :description="item.description"
      :required="item.required"
      :error="error"
      :rows="5"
      :placeholder="placeholder"
      @update:model-value="emitValue"
    />

    <TextField
      v-else-if="controlKind === 'text'"
      :id="controlId"
      :model-value="stringValue"
      :label="item.display_name"
      :description="item.description"
      :required="item.required"
      :error="error"
      :type="item.secret ? 'password' : 'text'"
      :autocomplete="item.secret ? 'new-password' : 'off'"
      :placeholder="placeholder"
      @update:model-value="emitValue"
    />

    <StructuredListEditor
      v-else-if="controlKind === 'string_list'"
      :id="controlId"
      :model-value="stringListValue"
      :label="item.display_name"
      :description="item.description"
      :required="item.required"
      :error="error"
      :add-label="`添加${listItemLabel}`"
      :empty-label="`暂未添加${listItemLabel}`"
      @update:model-value="emitValue"
    />

    <FormField
      v-else-if="isStructuredControl"
      :label="item.display_name"
      :control-id="controlId"
      :description="item.description"
      :required="item.required"
      :error="error"
      group
    >
      <template #default="{ controlId: resolvedId, labelledBy, describedBy, invalid }">
        <div
          :id="resolvedId"
          class="schema-structured-editor"
          role="group"
          :aria-labelledby="labelledBy"
          :aria-describedby="describedBy || undefined"
        >
          <ol v-if="structuredRows.length" class="schema-structured-editor-rows">
            <li v-for="(row, rowIndex) in structuredRows" :key="`${rowIndex}-${structuredRowKey(row)}`">
              <div class="schema-structured-editor-fields">
                <template v-for="field in structuredFields" :key="field.key">
                  <SwitchField
                    v-if="field.control_kind === 'switch'"
                    :id="structuredControlId(rowIndex, field.key)"
                    :model-value="toBoolean(row[field.key])"
                    :label="field.label"
                    :required="Boolean(field.required)"
                    :error="invalid ? error : ''"
                    @update:model-value="updateStructuredField(rowIndex, field, $event)"
                  />
                  <NumberField
                    v-else-if="field.control_kind === 'number'"
                    :id="structuredControlId(rowIndex, field.key)"
                    :model-value="toNumber(row[field.key])"
                    :label="field.label"
                    :required="Boolean(field.required)"
                    :error="invalid ? error : ''"
                    :min="field.min"
                    :max="field.max"
                    :step="field.step ?? 1"
                    :placeholder="field.placeholder"
                    @update:model-value="updateStructuredField(rowIndex, field, $event)"
                  />
                  <SearchableSelect
                    v-else-if="field.control_kind === 'select' && fieldOptions(field).length > 8"
                    :id="structuredControlId(rowIndex, field.key)"
                    :model-value="toText(row[field.key])"
                    :label="field.label"
                    :required="Boolean(field.required)"
                    :error="invalid ? error : ''"
                    :options="fieldOptions(field)"
                    :placeholder="field.placeholder || '搜索或选择'"
                    @update:model-value="updateStructuredField(rowIndex, field, $event)"
                  />
                  <SelectField
                    v-else-if="field.control_kind === 'select'"
                    :id="structuredControlId(rowIndex, field.key)"
                    :model-value="toText(row[field.key])"
                    :label="field.label"
                    :required="Boolean(field.required)"
                    :error="invalid ? error : ''"
                    :options="fieldOptions(field)"
                    :placeholder="field.placeholder"
                    @update:model-value="updateStructuredField(rowIndex, field, $event)"
                  />
                  <TextareaField
                    v-else-if="field.control_kind === 'textarea'"
                    :id="structuredControlId(rowIndex, field.key)"
                    :model-value="toText(row[field.key])"
                    :label="field.label"
                    :required="Boolean(field.required)"
                    :error="invalid ? error : ''"
                    :rows="3"
                    :placeholder="field.placeholder"
                    @update:model-value="updateStructuredField(rowIndex, field, $event)"
                  />
                  <TextField
                    v-else
                    :id="structuredControlId(rowIndex, field.key)"
                    :model-value="toText(row[field.key])"
                    :label="field.label"
                    :required="Boolean(field.required)"
                    :error="invalid ? error : ''"
                    :type="isSensitiveField(field.key, field) ? 'password' : 'text'"
                    :autocomplete="isSensitiveField(field.key, field) ? 'new-password' : 'off'"
                    :placeholder="field.placeholder"
                    @update:model-value="updateStructuredField(rowIndex, field, $event)"
                  />
                </template>
              </div>
              <button class="button button-secondary" type="button" :aria-label="`删除${item.display_name}第 ${rowIndex + 1} 项`" @click="removeStructuredRow(rowIndex)">
                删除此项
              </button>
            </li>
          </ol>
          <p v-else class="structured-list-editor-empty">暂未添加{{ item.display_name }}。</p>
          <button class="button button-secondary" type="button" @click="addStructuredRow">添加一项</button>
        </div>
      </template>
    </FormField>

    <div v-else-if="controlKind === 'json_advanced'" class="config-json-drawer-launcher">
      <FormField
        :label="item.display_name"
        :control-id="controlId"
        :description="item.description"
        :required="item.required"
        :error="error"
      >
        <template #default="{ controlId: resolvedId, describedBy, invalid }">
          <button
            :id="resolvedId"
            class="button button-secondary"
            type="button"
            :aria-invalid="invalid ? 'true' : undefined"
            :aria-describedby="describedBy || undefined"
            @click="openJsonDrawer"
          >
            在侧边栏编辑 JSON
          </button>
        </template>
      </FormField>

      <Drawer v-model:open="jsonDrawerOpen" :title="`编辑 ${item.display_name}`" side="right">
        <div class="page-stack">
          <p class="muted-copy">仅此未知结构使用格式化 JSON；保存前会校验格式与顶层类型。</p>
          <TextareaField
            :id="`${controlId}-json`"
            v-model="jsonDraft"
            label="JSON 内容"
            :error="jsonError"
            required
            :rows="18"
            :spellcheck="false"
          />
          <button class="button" type="button" @click="requestJsonApply">校验并应用草稿</button>
        </div>
      </Drawer>
      <ConfirmDialog
        v-model:open="jsonConfirmOpen"
        title="应用 JSON 草稿"
        description="确认后将把已校验的 JSON 加入本页草稿，仍需在配置中心点击原子保存。"
        confirm-label="应用草稿"
        @confirm="commitJson"
      />
    </div>

    <FormField
      v-else
      :label="item.display_name"
      :control-id="controlId"
      :description="item.description"
      :error="error || '后端未提供可安全使用的编辑契约；此项暂不能编辑。'"
      group
    >
      <template #default="{ controlId: resolvedId, labelledBy, describedBy }">
        <code :id="resolvedId" :aria-labelledby="labelledBy" :aria-describedby="describedBy || undefined">
          {{ controlKind === 'unsupported' ? 'ui_schema 缺失' : `未知控件：${controlKind}` }}
        </code>
      </template>
    </FormField>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";

import type { ConfigListItem, ConfigUiField, ConfigUiOption } from "@/api/types";
import ConfirmDialog from "@vue-app/components/ConfirmDialog.vue";
import Drawer from "@vue-app/components/Drawer.vue";
import FormField from "@vue-app/components/forms/FormField.vue";
import NumberField from "@vue-app/components/forms/NumberField.vue";
import SearchableSelect from "@vue-app/components/forms/SearchableSelect.vue";
import SelectField from "@vue-app/components/forms/SelectField.vue";
import StructuredListEditor from "@vue-app/components/forms/StructuredListEditor.vue";
import SwitchField from "@vue-app/components/forms/SwitchField.vue";
import TextareaField from "@vue-app/components/forms/TextareaField.vue";
import TextField from "@vue-app/components/forms/TextField.vue";

type StructuredRow = Record<string, unknown>;

const props = withDefaults(defineProps<{
  item: ConfigListItem;
  modelValue: unknown;
  error?: string;
}>(), {
  error: "",
});

const emit = defineEmits<{
  "update:modelValue": [value: unknown];
  "update:error": [value: string];
}>();

const controlId = computed(() => `config-${props.item.field_name}`);
const schema = computed(() => props.item.ui_schema ?? {});
const controlKind = computed(() => String(schema.value.control_kind || "unsupported"));
const placeholder = computed(() => String(schema.value.placeholder || ""));
const stringValue = computed(() => toText(props.modelValue));
const booleanValue = computed(() => toBoolean(props.modelValue));
const numberValue = computed(() => toNumber(props.modelValue));
const numberMin = computed(() => numericSchemaValue(schema.value.min, props.item.min_value));
const numberMax = computed(() => numericSchemaValue(schema.value.max, props.item.max_value));
const numberStep = computed<number | "any">(() => schema.value.step ?? (props.item.value_type === "int" ? 1 : 0.01));
const numberDescription = computed(() => {
  const unit = String(schema.value.unit || "").trim();
  return unit ? `${props.item.description}${props.item.description ? "；" : ""}单位：${unit}` : props.item.description;
});
const selectOptions = computed<ConfigUiOption[]>(() => normalizeOptions(schema.value.options, props.item.choices));
const useSearchableSelect = computed(() => selectOptions.value.length > 8);
const listItemLabel = computed(() => String(schema.value.item_schema?.label || "列表项"));
const stringListValue = computed(() => Array.isArray(props.modelValue)
  ? props.modelValue.filter((value): value is string => typeof value === "string")
  : []);
const isStructuredControl = computed(() => ["provider_list", "key_value", "level_table", "behavior_band_table"].includes(controlKind.value));
const structuredFields = computed<ConfigUiField[]>(() => Array.isArray(schema.value.item_schema?.fields)
  ? schema.value.item_schema?.fields ?? []
  : []);
const structuredRows = computed(() => rowsForStructuredValue(props.modelValue, controlKind.value));

const jsonDrawerOpen = ref(false);
const jsonConfirmOpen = ref(false);
const jsonDraft = ref("");
const jsonError = ref("");
const pendingJson = ref<unknown>(undefined);
const hasPendingJson = ref(false);

watch(jsonDrawerOpen, (isOpen) => {
  if (!isOpen) {
    if (jsonError.value) emit("update:error", "");
    return;
  }
  jsonDraft.value = formatJson(props.modelValue, props.item.value_type);
  jsonError.value = "";
  emit("update:error", "");
  hasPendingJson.value = false;
});

function emitValue(value: unknown): void {
  emit("update:error", "");
  emit("update:modelValue", value);
}

function toText(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}

function toBoolean(value: unknown): boolean {
  return value === true || value === "true";
}

function toNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function numericSchemaValue(preferred: unknown, fallback: number | null): number | undefined {
  const schemaNumber = toNumber(preferred);
  return schemaNumber ?? fallback ?? undefined;
}

function normalizeOptions(value: unknown, legacyChoices: readonly string[]): ConfigUiOption[] {
  if (Array.isArray(value)) {
    return value.flatMap((item) => {
      if (typeof item === "string") return [{ value: item, label: item }];
      if (!item || typeof item !== "object" || Array.isArray(item)) return [];
      const option = item as Record<string, unknown>;
      const rawValue = toText(option.value);
      if (!rawValue) return [];
      return [{
        value: rawValue,
        label: toText(option.label) || rawValue,
        description: toText(option.description) || undefined,
        disabled: option.disabled === true,
      }];
    });
  }
  return legacyChoices.map((value) => ({ value, label: value }));
}

function fieldOptions(field: ConfigUiField): ConfigUiOption[] {
  return normalizeOptions(field.options, []);
}

function isSensitiveField(key: string, field: ConfigUiField): boolean {
  return field.sensitive === true || (schema.value.sensitive_fields ?? []).includes(key);
}

function structuredRowKey(row: StructuredRow): string {
  return toText(row.key) || toText(row.name) || toText(row.band) || toText(row.id) || "new";
}

function structuredControlId(rowIndex: number, key: string): string {
  return `${controlId.value}-item-${rowIndex}-${key}`;
}

function rowsForStructuredValue(value: unknown, kind: string): StructuredRow[] {
  if (kind === "provider_list") {
    return Array.isArray(value)
      ? value.filter(isRecord).map((item) => ({ ...item }))
      : [];
  }
  if (!isRecord(value)) return [];
  if (kind === "key_value") {
    return Object.entries(value).map(([key, item]) => ({ key, value: item }));
  }
  if (kind === "level_table") {
    return Object.entries(value).map(([name, threshold]) => ({ name, threshold }));
  }
  if (kind === "behavior_band_table") {
    return Object.entries(value).map(([band, details]) => ({ band, ...(isRecord(details) ? details : {}) }));
  }
  return [];
}

function updateStructuredField(rowIndex: number, field: ConfigUiField, value: unknown): void {
  const nextRows = structuredRows.value.map((row) => ({ ...row }));
  const row = nextRows[rowIndex];
  if (!row) return;
  row[field.key] = value;
  commitStructuredRows(nextRows);
}

function removeStructuredRow(rowIndex: number): void {
  commitStructuredRows(structuredRows.value.filter((_, index) => index !== rowIndex).map((row) => ({ ...row })));
}

function addStructuredRow(): void {
  const row: StructuredRow = {};
  for (const field of structuredFields.value) {
    row[field.key] = field.control_kind === "switch" ? false : "";
  }
  commitStructuredRows([...structuredRows.value.map((current) => ({ ...current })), row]);
}

function commitStructuredRows(rows: StructuredRow[]): void {
  emit("update:error", validateStructuredRows(rows));
  emit("update:modelValue", serializeStructuredRows(rows, controlKind.value));
}

function validateStructuredRows(rows: readonly StructuredRow[]): string {
  for (let rowIndex = 0; rowIndex < rows.length; rowIndex += 1) {
    const row = rows[rowIndex];
    if (!row) continue;
    for (const field of structuredFields.value) {
      if (!field.required) continue;
      const value = row[field.key];
      if (value === null || value === undefined || (typeof value === "string" && !value.trim())) {
        return `第 ${rowIndex + 1} 项的“${field.label}”不能为空。`;
      }
    }
  }
  return "";
}

function serializeStructuredRows(rows: readonly StructuredRow[], kind: string): unknown {
  if (kind === "provider_list") return rows.map((row) => ({ ...row }));
  const result: Record<string, unknown> = {};
  for (const row of rows) {
    if (kind === "key_value") {
      const key = toText(row.key).trim();
      if (key) result[key] = row.value;
      continue;
    }
    if (kind === "level_table") {
      const name = toText(row.name).trim();
      if (name) result[name] = row.threshold;
      continue;
    }
    if (kind === "behavior_band_table") {
      const band = toText(row.band).trim();
      if (!band) continue;
      const { band: _band, ...details } = row;
      result[band] = details;
    }
  }
  return result;
}

function isRecord(value: unknown): value is StructuredRow {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function formatJson(value: unknown, valueType: string): string {
  if (value === undefined || value === null || value === "") return valueType === "list" ? "[]" : "{}";
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return "{}";
  }
}

function openJsonDrawer(): void {
  jsonDrawerOpen.value = true;
}

function requestJsonApply(): void {
  try {
    const parsed: unknown = JSON.parse(jsonDraft.value);
    if (props.item.value_type === "list" && !Array.isArray(parsed)) {
      throw new Error("配置值必须是 JSON 数组。")
    }
    if (props.item.value_type === "dict" && !isRecord(parsed)) {
      throw new Error("配置值必须是 JSON 对象。")
    }
    pendingJson.value = parsed;
    hasPendingJson.value = true;
    jsonError.value = "";
    emit("update:error", "");
    jsonConfirmOpen.value = true;
  } catch (caught) {
    const message = caught instanceof Error ? caught.message : "JSON 格式无法解析。";
    jsonError.value = message;
    emit("update:error", message);
  }
}

function commitJson(): void {
  if (!hasPendingJson.value) return;
  emitValue(pendingJson.value);
  jsonConfirmOpen.value = false;
  jsonDrawerOpen.value = false;
  hasPendingJson.value = false;
}
</script>

<style scoped>
.schema-structured-editor-rows {
  display: grid;
  gap: 0.85rem;
  margin: 0;
  padding: 0;
  list-style: none;
}

.schema-structured-editor-rows > li {
  display: grid;
  gap: 0.65rem;
  padding: 0.85rem;
  border: 1px solid var(--border-subtle, rgba(128, 128, 128, 0.35));
  border-radius: 0.65rem;
}

.schema-structured-editor-fields {
  display: grid;
  gap: 0.65rem;
  grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr));
}

.config-json-drawer-launcher {
  display: grid;
  gap: 0.5rem;
}
</style>
