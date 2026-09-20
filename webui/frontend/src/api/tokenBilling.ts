import { api } from "./client";

export type UsageWindow = "24h" | "7d" | "30d" | "all";
export type DecimalString = string | null;

export interface TokenUsageFilters {
  window: UsageWindow;
  bot_id?: string;
  group_id?: string;
  provider?: string;
  route_id?: string;
  model?: string;
  purpose?: string;
}

export interface TokenCostSummary {
  currency: string;
  cost_decimal: string;
  priced_call_count: number;
}

export interface TokenUsageAggregate {
  call_count: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cache_read_tokens: number | null;
  cache_creation_tokens: number | null;
  cache_read_known_calls: number;
  cache_creation_known_calls: number;
  cache_usage_complete_calls: number;
  cache_usage_coverage: number | null;
  cache_read_input_ratio: number | null;
  costs: TokenCostSummary[];
  unpriced_call_count: number;
  incomplete_priced_call_count: number;
  legacy_unattributed: number;
}

export interface TokenUsageSeriesPoint extends Partial<TokenUsageAggregate> {
  bucket: string;
  label?: string;
  bucket_start?: number;
}

export interface TokenUsageEvent extends Partial<TokenUsageAggregate> {
  event_id: string;
  observed_at: number;
  provider?: string;
  route_id?: string;
  route_name?: string;
  model?: string;
  purpose?: string;
  bot_id?: string;
  group_id?: string;
  price_version_id?: string | null;
  currency?: string | null;
  cost_decimal?: string | null;
  pricing_complete: boolean;
}

export type UsageDimensionValue = string | { value: string; label?: string };
export interface TokenUsageDimensions {
  bot_ids?: UsageDimensionValue[];
  group_ids?: UsageDimensionValue[];
  providers?: UsageDimensionValue[];
  route_ids?: UsageDimensionValue[];
  models?: UsageDimensionValue[];
  purposes?: UsageDimensionValue[];
}

export interface TokenUsageResponse extends TokenUsageAggregate {
  window: UsageWindow;
  filters: Record<string, string>;
  series: TokenUsageSeriesPoint[];
  recent_events: TokenUsageEvent[];
  dimensions: TokenUsageDimensions;
}

export interface TokenPriceVersion {
  version_id: string;
  route_id: string;
  model: string;
  currency: string;
  effective_from: number;
  input_per_million: DecimalString;
  output_per_million: DecimalString;
  cache_read_per_million: DecimalString;
  cache_create_per_million: DecimalString;
  cache_create_5m_per_million: DecimalString;
  cache_create_1h_per_million: DecimalString;
  created_at?: number;
}

export type TokenPriceInput = Omit<
  TokenPriceVersion,
  "version_id" | "created_at"
>;
export interface TokenPriceRoute {
  route_id: string;
  name: string;
  models: string[];
}

export interface RepricePreviewResponse {
  version_id: string;
  event_count: number;
  costs: TokenCostSummary[];
  unpriced_call_count: number;
  incomplete_priced_call_count: number;
  items?: Array<{
    event_id: string;
    currency?: string;
    cost_decimal?: string | null;
    status?: string;
  }>;
}

function compactQuery(filters: TokenUsageFilters): Record<string, string> {
  return Object.fromEntries(
    Object.entries(filters).filter(
      ([, value]) => value !== undefined && value !== "",
    ),
  ) as Record<string, string>;
}

export const tokenBillingApi = {
  usage(
    filters: TokenUsageFilters,
    signal?: AbortSignal,
  ): Promise<TokenUsageResponse> {
    return api.get("/metrics/usage", compactQuery(filters), signal);
  },
  prices(signal?: AbortSignal): Promise<{ items: TokenPriceVersion[] }> {
    return api.get("/metrics/prices", undefined, signal);
  },
  createPrice(input: TokenPriceInput): Promise<TokenPriceVersion> {
    return api.post("/metrics/prices", input);
  },
  routes(signal?: AbortSignal): Promise<{ items: TokenPriceRoute[] }> {
    return api.get("/metrics/price-routes", undefined, signal);
  },
  repricePreview(
    version_id: string,
    event_ids: string[],
  ): Promise<RepricePreviewResponse> {
    return api.post("/metrics/reprice-preview", { version_id, event_ids });
  },
};
