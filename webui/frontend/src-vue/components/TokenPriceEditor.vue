<template>
  <section class="price-editor" aria-labelledby="token-price-title">
    <header>
      <div>
        <h4 id="token-price-title">Token 价格版本</h4>
        <p class="muted-copy">
          新建不可变价格版本；空模型表示该路由的显式默认价。金额单位均为每百万
          Token。
        </p>
      </div>
    </header>
    <QueryBoundary
      :pending="routesQuery.isPending.value || pricesQuery.isPending.value"
      :error="routesQuery.error.value || pricesQuery.error.value"
    >
      <form class="price-grid" @submit.prevent="submit">
        <label
          >路由<select v-model="draft.route_id" required>
            <option value="" disabled>选择路由</option>
            <option
              v-for="route in routes"
              :key="route.route_id"
              :value="route.route_id"
            >
              {{ route.name }}（{{ route.route_id }}）
            </option>
          </select></label
        >
        <label
          >模型 ID<input
            v-model.trim="draft.model"
            list="token-price-models"
            placeholder="留空为默认价；也可输入路由实际模型 ID"
        /></label>
        <datalist id="token-price-models">
          <option v-for="model in models" :key="model" :value="model" />
        </datalist>
        <label
          >币种<input
            v-model.trim="draft.currency"
            required
            maxlength="12"
            placeholder="USD"
        /></label>
        <label
          >生效时间<input v-model="effectiveAt" required type="datetime-local"
        /></label>
        <label v-for="field in decimalFields" :key="field.key"
          >{{ field.label
          }}<input
            v-model.trim="draft[field.key]"
            inputmode="decimal"
            :placeholder="field.placeholder"
            @blur="validateDecimal(field.key)"
        /></label>
        <p
          v-if="formError || createMutation.error.value"
          class="form-error"
          role="alert"
        >
          {{ formError || "服务器未确认保存结果，请刷新价格版本核对后再试。" }}
        </p>
        <p v-if="successMessage" class="form-success" role="status">
          {{ successMessage }}
        </p>
        <button
          class="button button-secondary"
          type="submit"
          :disabled="createMutation.isPending.value"
        >
          {{ createMutation.isPending.value ? "保存中…" : "新建价格版本" }}
        </button>
      </form>
      <div v-if="prices.length" class="price-table-wrap">
        <table class="forensic-table">
          <thead>
            <tr>
              <th>版本 ID</th>
              <th>路由 / 模型</th>
              <th>币种</th>
              <th>输入 / 输出</th>
              <th>缓存读 / 通用创建</th>
              <th>创建 5m / 1h</th>
              <th>生效时间</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="price in prices" :key="price.version_id">
              <td>
                <code>{{ price.version_id }}</code>
              </td>
              <td>
                {{ routeName(price.route_id)
                }}<small>{{ price.model || "默认价" }}</small>
              </td>
              <td>{{ price.currency }}</td>
              <td>
                {{ show(price.input_per_million) }} /
                {{ show(price.output_per_million) }}
              </td>
              <td>
                {{ show(price.cache_read_per_million) }} /
                {{ show(price.cache_create_per_million) }}
              </td>
              <td>
                {{ show(price.cache_create_5m_per_million) }} /
                {{ show(price.cache_create_1h_per_million) }}
              </td>
              <td>{{ formatTime(price.effective_from) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else class="muted-copy">
        尚未创建价格版本。未计价调用会继续明确显示，不会按 0 费用处理。
      </p>
    </QueryBoundary>
  </section>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from "vue";
import { useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";
import {
  tokenBillingApi,
  type DecimalString,
  type TokenPriceInput,
} from "@/api/tokenBilling";
import QueryBoundary from "@vue-app/components/QueryBoundary.vue";

type DecimalKey =
  | "input_per_million"
  | "output_per_million"
  | "cache_read_per_million"
  | "cache_create_per_million"
  | "cache_create_5m_per_million"
  | "cache_create_1h_per_million";
type Draft = Omit<TokenPriceInput, "effective_from" | DecimalKey> &
  Record<DecimalKey, string>;
const queryClient = useQueryClient();
const routesQuery = useQuery({
  queryKey: ["token-price-routes"],
  queryFn: ({ signal }) => tokenBillingApi.routes(signal),
});
const pricesQuery = useQuery({
  queryKey: ["token-prices"],
  queryFn: ({ signal }) => tokenBillingApi.prices(signal),
});
const routes = computed(() => routesQuery.data.value?.items ?? []);
const prices = computed(() => pricesQuery.data.value?.items ?? []);
const draft = reactive<Draft>({
  route_id: "",
  model: "",
  currency: "USD",
  input_per_million: "",
  output_per_million: "",
  cache_read_per_million: "",
  cache_create_per_million: "",
  cache_create_5m_per_million: "",
  cache_create_1h_per_million: "",
});
const effectiveAt = ref(toLocalInput(Math.floor(Date.now() / 1000)));
const formError = ref("");
const successMessage = ref("");
const decimalFields: Array<{
  key: DecimalKey;
  label: string;
  placeholder: string;
}> = [
  {
    key: "input_per_million",
    label: "输入",
    placeholder: "例如 2.50；留空未知",
  },
  {
    key: "output_per_million",
    label: "输出",
    placeholder: "例如 10.00；留空未知",
  },
  { key: "cache_read_per_million", label: "缓存读取", placeholder: "可空" },
  {
    key: "cache_create_per_million",
    label: "缓存创建（通用）",
    placeholder: "可空",
  },
  {
    key: "cache_create_5m_per_million",
    label: "缓存创建 5 分钟",
    placeholder: "可空",
  },
  {
    key: "cache_create_1h_per_million",
    label: "缓存创建 1 小时",
    placeholder: "可空",
  },
];
const models = computed(
  () =>
    routes.value.find((route) => route.route_id === draft.route_id)?.models ??
    [],
);
const createMutation = useMutation({
  mutationFn: (input: TokenPriceInput) => tokenBillingApi.createPrice(input),
  onSuccess: async (created) => {
    await queryClient.invalidateQueries({ queryKey: ["token-prices"] });
    formError.value = "";
    successMessage.value = `已创建不可变价格版本 ${created.version_id}。`;
  },
  onError: () => {
    successMessage.value = "";
  },
});
function decimal(value: string): DecimalString {
  return value.trim() || null;
}
function validateDecimal(key: DecimalKey): boolean {
  const value = draft[key].trim();
  if (value && !/^\d+(?:\.\d+)?$/.test(value)) {
    formError.value = "价格必须是非负十进制字符串，或留空表示未知。";
    return false;
  }
  return true;
}
function submit(): void {
  formError.value = "";
  successMessage.value = "";
  if (
    !draft.route_id ||
    !draft.currency ||
    !effectiveAt.value ||
    !decimalFields.every((field) => validateDecimal(field.key))
  )
    return;
  const effective_from = Math.floor(
    new Date(effectiveAt.value).getTime() / 1000,
  );
  if (!Number.isFinite(effective_from)) {
    formError.value = "生效时间无效。";
    return;
  }
  createMutation.mutate({
    route_id: draft.route_id,
    model: draft.model,
    currency: draft.currency.toUpperCase(),
    effective_from,
    input_per_million: decimal(draft.input_per_million),
    output_per_million: decimal(draft.output_per_million),
    cache_read_per_million: decimal(draft.cache_read_per_million),
    cache_create_per_million: decimal(draft.cache_create_per_million),
    cache_create_5m_per_million: decimal(draft.cache_create_5m_per_million),
    cache_create_1h_per_million: decimal(draft.cache_create_1h_per_million),
  });
}
function routeName(id: string): string {
  return routes.value.find((route) => route.route_id === id)?.name || id;
}
function show(value: DecimalString): string {
  return value === null || value === "" ? "未知" : value;
}
function formatTime(value: number): string {
  return new Date(value * 1000).toLocaleString("zh-CN");
}
function toLocalInput(seconds: number): string {
  const date = new Date(
    seconds * 1000 - new Date().getTimezoneOffset() * 60000,
  );
  return date.toISOString().slice(0, 16);
}
</script>

<style scoped>
.price-editor {
  display: grid;
  gap: 0.8rem;
  padding-top: 0.85rem;
  border-top: 1px solid var(--color-line);
}
.price-editor h4 {
  margin: 0;
}
.price-grid {
  display: grid;
  gap: 0.65rem;
  grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr));
  align-items: end;
}
.price-grid label {
  display: grid;
  gap: 0.3rem;
  font-size: 0.82rem;
}
.price-grid input,
.price-grid select {
  min-height: 2.55rem;
  padding: 0.55rem 0.65rem;
  border: 1px solid var(--color-line);
  border-radius: 0.5rem;
  background: var(--color-surface-raised);
  color: inherit;
}
.form-error,
.form-success {
  grid-column: 1/-1;
  margin: 0;
}
.form-error {
  color: var(--color-danger);
}
.form-success {
  color: var(--color-positive);
}
.price-table-wrap {
  overflow: auto;
}
.forensic-table small {
  display: block;
  color: var(--color-ink-muted);
}
</style>
