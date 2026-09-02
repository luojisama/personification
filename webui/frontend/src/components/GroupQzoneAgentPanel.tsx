import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";

import { diagnosticFromError, safeDiagnostic } from "../api/diagnostics";
import { resources } from "../api/resources";
import type { GroupQzoneAgentSettings } from "../api/types";
import { formatDateTime } from "../lib/format";
import { DiagnosticPanel, useDiagnosticHistory } from "./DiagnosticPanel";
import { EmptyState, Panel } from "./Panel";
import { QueryBoundary } from "./QueryBoundary";
import { StateBadge } from "./StateBadge";

type QzoneSettingsDraft = {
  enabled: boolean;
  groupDailyLimit: string;
  targetDailyLimit: string;
  targetCooldownSeconds: string;
};

function operationTone(status: string): "ok" | "warn" | "error" | "running" | "unknown" {
  if (status === "succeeded") return "ok";
  if (status === "definite_failure") return "error";
  if (status === "reserved" || status === "dispatching") return "running";
  if (status === "unknown") return "unknown";
  return "warn";
}

export function GroupQzoneAgentPanel({ groupId }: { groupId: string }) {
  const history = useDiagnosticHistory(`group-qzone-agent-${groupId}`);
  const query = useQuery({
    queryKey: ["group-qzone-agent", groupId],
    queryFn: ({ signal }) => resources.groupQzoneAgent(groupId, signal),
    enabled: Boolean(groupId),
  });
  const data = query.data;
  const [draft, setDraft] = useState<QzoneSettingsDraft>({
    enabled: false,
    groupDailyLimit: "3",
    targetDailyLimit: "1",
    targetCooldownSeconds: "1800",
  });
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    if (!data || dirty) return;
    setDraft({
      enabled: data.settings.enabled,
      groupDailyLimit: String(data.settings.group_daily_limit),
      targetDailyLimit: String(data.settings.target_daily_limit),
      targetCooldownSeconds: String(data.settings.target_cooldown_seconds),
    });
  }, [data, dirty]);

  useEffect(() => setDirty(false), [groupId]);

  const fieldErrors = useMemo(() => {
    if (!data) return { group: "", target: "", cooldown: "" };
    const groupLimit = Number(draft.groupDailyLimit);
    const targetLimit = Number(draft.targetDailyLimit);
    const cooldown = Number(draft.targetCooldownSeconds);
    return {
      group: !Number.isInteger(groupLimit) || groupLimit < 0 || groupLimit > data.limits.group_daily_limit
        ? `本群每日上限必须是 0 到 ${data.limits.group_daily_limit} 的整数。`
        : "",
      target: !Number.isInteger(targetLimit) || targetLimit < 0 || targetLimit > data.limits.target_daily_limit
        ? `同一目标每日上限必须是 0 到 ${data.limits.target_daily_limit} 的整数。`
        : "",
      cooldown: !Number.isFinite(cooldown) || cooldown < data.limits.target_cooldown_seconds || cooldown > 86400
        ? `同一目标冷却不能低于全局下限 ${data.limits.target_cooldown_seconds} 秒，且不能超过 86400 秒。`
        : "",
    };
  }, [data, draft]);
  const validationError = fieldErrors.group || fieldErrors.target || fieldErrors.cooldown;

  const mutation = useMutation({
    mutationFn: (settings: GroupQzoneAgentSettings) => resources.updateGroupQzoneAgent(groupId, settings),
    onSuccess: (result) => {
      history.record(safeDiagnostic(result));
      void query.refetch().then(() => setDirty(false));
    },
    onError: (error) => history.record(diagnosticFromError(error)),
  });

  const save = () => {
    if (validationError || !data) return;
    if (data.settings.enabled && !draft.enabled && !window.confirm("确认停用本群 QQ 空间 Agent 互动？")) return;
    mutation.mutate({
      enabled: draft.enabled,
      group_daily_limit: Number(draft.groupDailyLimit),
      target_daily_limit: Number(draft.targetDailyLimit),
      target_cooldown_seconds: Number(draft.targetCooldownSeconds),
    });
  };

  const update = (patch: Partial<QzoneSettingsDraft>) => {
    setDraft((current) => ({ ...current, ...patch }));
    setDirty(true);
  };

  return <div className="page-stack qzone-agent-page">
    <QueryBoundary isPending={query.isPending} error={query.error}>
      {data && <>
        <div className="summary-grid qzone-agent-summary">
          <Panel eyebrow="QZONE AGENT / GATES" title="空间互动门禁">
            <div className="qzone-gate-strip" aria-label="空间互动三重门禁">
              <div><span>QZone 总开关</span><StateBadge tone={data.qzone_enabled ? "ok" : "warn"}>{data.qzone_enabled ? "已开启" : "未开启"}</StateBadge></div>
              <div><span>Agent 全局开关</span><StateBadge tone={data.global_enabled ? "ok" : "warn"}>{data.global_enabled ? "已开启" : "未开启"}</StateBadge></div>
              <div><span>本群开关</span><StateBadge tone={data.settings.enabled ? "ok" : "unknown"}>{data.settings.enabled ? "已开启" : "未开启"}</StateBadge></div>
            </div>
            <p className="muted-copy">三个开关全部开启后，Agent 才能读取当前群友空间并执行受控点赞或评论。这里不提供在线试发按钮。</p>
            <div className="form-grid qzone-agent-settings">
              <label className="checkbox-label"><input type="checkbox" checked={draft.enabled} onChange={(event) => update({ enabled: event.target.checked })} />启用本群空间互动</label>
              <label>本群每日写入上限<input aria-invalid={Boolean(fieldErrors.group)} aria-describedby={fieldErrors.group ? "qzone-agent-group-limit-error" : undefined} type="number" min="0" max={data.limits.group_daily_limit} value={draft.groupDailyLimit} onChange={(event) => update({ groupDailyLimit: event.target.value })} />{fieldErrors.group && <span id="qzone-agent-group-limit-error" className="state-error" role="alert">{fieldErrors.group}</span>}</label>
              <label>同一目标每日上限<input aria-invalid={Boolean(fieldErrors.target)} aria-describedby={fieldErrors.target ? "qzone-agent-target-limit-error" : undefined} type="number" min="0" max={data.limits.target_daily_limit} value={draft.targetDailyLimit} onChange={(event) => update({ targetDailyLimit: event.target.value })} />{fieldErrors.target && <span id="qzone-agent-target-limit-error" className="state-error" role="alert">{fieldErrors.target}</span>}</label>
              <label>同一目标冷却（秒）<input aria-invalid={Boolean(fieldErrors.cooldown)} aria-describedby={fieldErrors.cooldown ? "qzone-agent-cooldown-error" : undefined} type="number" min={data.limits.target_cooldown_seconds} max="86400" value={draft.targetCooldownSeconds} onChange={(event) => update({ targetCooldownSeconds: event.target.value })} />{fieldErrors.cooldown && <span id="qzone-agent-cooldown-error" className="state-error" role="alert">{fieldErrors.cooldown}</span>}</label>
            </div>
            <div className="inline-controls"><button className="button" type="button" disabled={!dirty || Boolean(validationError) || mutation.isPending} onClick={save}>保存群级空间策略</button></div>
          </Panel>
          <Panel eyebrow="QZONE AGENT / QUOTA" title="今日额度与边界">
            <dl className="compact-kv">
              <dt>今日已占用</dt><dd>{data.quota.used_today} / {data.settings.group_daily_limit}</dd>
              <dt>同一目标每日</dt><dd>{data.settings.target_daily_limit} 次</dd>
              <dt>同一目标冷却</dt><dd>{data.settings.target_cooldown_seconds} 秒</dd>
              <dt>结果未知</dt><dd>占用额度，不自动重试</dd>
              <dt>可执行动作</dt><dd>仅读取、点赞、评论</dd>
              <dt>禁止动作</dt><dd>转发、代发、删除、跨群操作</dd>
            </dl>
          </Panel>
        </div>

        <Panel eyebrow="QZONE AGENT / OPERATIONS" title={`脱敏近期操作（${data.recent_operations.length}）`}>
          {data.recent_operations.length ? <div className="trace-table-wrap"><table className="forensic-table"><thead><tr><th>Operation ID</th><th>动作</th><th>状态</th><th>诊断码</th><th>开始</th><th>更新</th></tr></thead><tbody>{data.recent_operations.map((operation) => <tr key={operation.operation_id}><td><code>{operation.operation_id}</code></td><td>{operation.action === "like" ? "点赞" : "评论"}</td><td><StateBadge tone={operationTone(operation.status)}>{operation.status}</StateBadge></td><td><code>{operation.result_code || "—"}</code></td><td>{formatDateTime(operation.created_at)}</td><td>{formatDateTime(operation.updated_at)}</td></tr>)}</tbody></table></div> : <EmptyState code="qzone_agent_operations_empty">暂无互动操作摘要。此处不会展示 QQ 号、动态正文、评论正文、Cookie 或原始响应。</EmptyState>}
        </Panel>
      </>}
    </QueryBoundary>
    {history.diagnostics.map((item, index) => <DiagnosticPanel key={`${item.code}:${index}`} diagnostic={item} defaultOpen={index === 0} />)}
  </div>;
}
