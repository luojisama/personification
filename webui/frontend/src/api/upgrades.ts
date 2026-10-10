import { api, apiRequest } from './client';
export interface ConnectionDraft { token: string; name: string; proxy: string; api_server: string; persona: string; scope: string; }
export interface Connection { id: string; bot_id: string; name?: string; username?: string; source: 'managed' | 'native'; persona?: string; scope?: string; webhook_conflict?: boolean; state?: string; status?: Record<string, unknown>; test_url?: string; last_error?: string; }
export interface TelegramRoom { chat_id: string; topic_id: string; title: string; participation: "off" | "shadow" | "on"; proactive_enabled: boolean; scope_key: string; }
export interface ExperienceScope { platform: string; bot_id: string; group_id: string; user_id: string; topic_id: string; }
export interface Experience { memory_id: string | number; summary: string; participants: string[]; source_refs: {message_id: string; user_id?: string; timestamp?: string}[]; revision: number; confirmation: string; kind: string; expires_at?: number; }
export interface QualityCase { id: string; title: string; split: string; case: Record<string, unknown>; }
export interface QualityJob { id: string; status: string; model: string; request_budget: number; scored_ballot_ids?: string[]; progress?: {completed: number; total: number}; ballots?: {id: string; case_id: string; candidates: {id: string; text: string; status: string}[]}[]; }
export interface QualityIndex { cases: QualityCase[]; jobs: QualityJob[]; providers: {id: string; model: string; api_type: string}[]; dimensions: string[]; }
export const upgrades = {
  connections: () => api.get<{items: Connection[]}>('/connections'),
  probe: (draft: ConnectionDraft) => api.post<{connection?: Connection; status?: Record<string, unknown>}>('/connections/probe', draft),
  saveConnection: (draft: ConnectionDraft) => api.post<{connection: Connection}>('/connections', draft),
  connectionAction: (id: string, action: string, body?: unknown) => api.post<{connection: Connection}>(`/connections/${encodeURIComponent(id)}/${action}`, body),
  updateConnection: (id: string, body: unknown) => api.patch(`/connections/${encodeURIComponent(id)}`, body),
  deleteConnection: (id: string) => api.delete(`/connections/${encodeURIComponent(id)}`),
  rooms: (id: string) => api.get<{items: TelegramRoom[]}>(`/connections/${encodeURIComponent(id)}/rooms`),
  saveRoom: (id: string, body: unknown) => api.patch(`/connections/${encodeURIComponent(id)}/rooms`, body),
  moderate: (id: string, body: unknown) => api.post<{state: string; code: string; message_id: string}>(`/connections/${encodeURIComponent(id)}/moderation`, body),
  experiences: (scope: ExperienceScope) => api.get<{items: Experience[]; enabled?: boolean}>('/experiences', {...scope}),
  reviseExperience: (id: Experience['memory_id'], scope: ExperienceScope, body: unknown) => apiRequest(`/experiences/${encodeURIComponent(id)}`, {method:"PATCH",query:{...scope},body}),
  forgetExperience: (id: Experience['memory_id'], scope: ExperienceScope, revision: number) => api.delete(`/experiences/${encodeURIComponent(id)}?${new URLSearchParams({...scope, expected_revision: String(revision)})}`),
  quality: () => api.get<QualityIndex>('/quality'),
  saveCase: (body: unknown) => api.post<QualityCase>('/quality/cases', body),
  previewQuality: (body: unknown) => api.post<{model: string; request_budget: number; candidate_count: number; warnings: string[]}>('/quality/preview', body),
  startQuality: (body: unknown) => api.post<QualityJob>('/quality/jobs', body),
  qualityJob: (id: string) => api.get<QualityJob>(`/quality/jobs/${encodeURIComponent(id)}`),
  scoreQuality: (id: string, body: unknown) => api.post(`/quality/jobs/${encodeURIComponent(id)}/scores`, body),
  qualityReport: (id: string) => api.get<Record<string, unknown>>(`/quality/jobs/${encodeURIComponent(id)}/report`),
  cancelQuality: (id: string) => api.post(`/quality/jobs/${encodeURIComponent(id)}/cancel`),
};