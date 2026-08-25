import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router-dom";

import { resources } from "../api/resources";
import { safeDiagnostic } from "../api/diagnostics";
import { asRecord, BusinessTable, recordsAt, SafeStatus, textAt } from "../components/BusinessTable";
import { DiagnosticPanel } from "../components/DiagnosticPanel";
import { EmptyState, PageHeader, Panel } from "../components/Panel";
import { QueryBoundary } from "../components/QueryBoundary";
import { StateBadge } from "../components/StateBadge";
import { formatDateTime } from "../lib/format";

function SummaryList({ value, fields }: { value: unknown; fields: Array<{ key: string; label: string }> }) {
  const row = asRecord(value);
  return <dl className="detail-list">{fields.map((field) => <div key={field.key}><dt>{field.label}</dt><dd>{textAt(row, field.key)}</dd></div>)}</dl>;
}

export function MemoryPalacePage() {
  const { section = "graph" } = useParams();
  const endpoint = section === "zones" ? "palace-zones" : "graph";
  const query = useQuery({ queryKey: ["memory-palace", endpoint], queryFn: ({ signal }) => resources.memoryBusiness(endpoint, signal) });
  const rows = section === "zones"
    ? recordsAt(query.data, "zones", "items")
    : section === "conflicts"
      ? [...recordsAt(query.data, "conflicts"), ...recordsAt(query.data, "relations", "edges")]
      : recordsAt(query.data, "nodes", "memories", "items");
  return <div className="page-stack">
    <PageHeader index="15" title="记忆宫殿" description="按图谱节点、分区、关系与冲突展示可追踪记忆；这里不会把嵌套对象扁平化成字段转储。" />
    <QueryBoundary isPending={query.isPending} error={query.error}>
      <Panel eyebrow={`MEMORY / ${section.toUpperCase()}`} title={section === "zones" ? "宫殿分区" : section === "conflicts" ? "关系与冲突" : "记忆图谱"}>
        <BusinessTable
          rows={rows}
          rowKey={(row, index) => textAt(row, "id", "memory_id", "zone_id", "source_id") + index}
          emptyCode={`memory_palace_${section}_empty`}
          emptyText="当前没有可展示的结构化记忆记录。"
          columns={[
            { key: "name", label: "节点 / 分区", render: (row) => <><strong>{textAt(row, "name", "label", "summary", "zone", "source")}</strong><br /><code>{textAt(row, "id", "memory_id", "zone_id")}</code></> },
            { key: "kind", label: "类型", render: (row) => textAt(row, "kind", "type", "memory_type", "relation") },
            { key: "source", label: "可追踪来源", render: (row) => textAt(row, "source_kind", "source", "evidence_source") },
            { key: "status", label: "状态", render: (row) => <SafeStatus row={row} keys={["conflict_state", "status", "state", "trust"]} /> },
          ]}
        />
      </Panel>
    </QueryBoundary>
  </div>;
}

export function PersonaPreviewPage() {
  const { section = "prompt" } = useParams();
  const query = useQuery({ queryKey: ["persona-prompt-preview"], queryFn: ({ signal }) => resources.personaPromptPreview(signal) });
  const data = asRecord(query.data);
  const warnings = recordsAt(data, "warnings", "quality_warnings");
  const sources = recordsAt(data, "sources", "source_files");
  const prompt = textAt(data, "prompt", "prompt_preview", "system_prompt", "persona_prompt");
  return <div className="page-stack">
    <PageHeader index="17" title="人设预览" description="展示实际可见 Prompt、安全上下文、来源与质量告警；隐藏思维链和未清洗工具上下文不会出现在这里。" />
    <QueryBoundary isPending={query.isPending} error={query.error}>
      {section === "warnings" ? <Panel eyebrow="PERSONA / QUALITY" title="质量告警与来源">
        <BusinessTable rows={warnings.length ? warnings : sources} rowKey={(row, index) => textAt(row, "code", "path", "source") + index} emptyCode="persona_preview_warnings_empty" emptyText="当前没有质量告警。" columns={[
          { key: "code", label: "诊断码 / 来源", render: (row) => <code>{textAt(row, "code", "path", "source", "name")}</code> },
          { key: "message", label: "说明", render: (row) => textAt(row, "message", "summary", "description") },
          { key: "level", label: "级别", render: (row) => <SafeStatus row={row} keys={["level", "status", "state"]} /> },
        ]} />
      </Panel> : <Panel eyebrow="PERSONA / EFFECTIVE PROMPT" title="实际 Prompt">
        {prompt === "—" ? <EmptyState code="persona_prompt_empty">服务端没有返回可见 Prompt 预览。</EmptyState> : <pre className="safe-prompt-preview">{prompt.slice(0, 20_000)}</pre>}
      </Panel>}
    </QueryBoundary>
  </div>;
}

export function PersonaBuilderPage() {
  const { section = "tasks" } = useParams();
  const client = useQueryClient();
  const [workTitle, setWorkTitle] = useState("");
  const [characterName, setCharacterName] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const history = useQuery({ queryKey: ["persona-builder-history"], queryFn: ({ signal }) => resources.personaBuilderGet("history", signal) });
  const detail = useQuery({ queryKey: ["persona-builder-detail", selectedId], queryFn: ({ signal }) => resources.personaBuilderGet(`history/${encodeURIComponent(selectedId)}`, signal), enabled: Boolean(selectedId) });
  const build = useMutation({
    mutationFn: () => resources.personaBuilderPost("build-task", { work_title: workTitle, character_name: characterName }),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["persona-builder-history"] }),
  });
  const apply = useMutation({ mutationFn: (recordId: string) => resources.personaBuilderPost("apply", { record_id: recordId }) });
  const records = recordsAt(history.data, "records", "items");
  const diagnostic = build.data ? safeDiagnostic(asRecord(build.data).diagnostic as Record<string, unknown> ?? build.data) : apply.data ? safeDiagnostic(asRecord(apply.data).diagnostic as Record<string, unknown> ?? apply.data) : null;
  return <div className="page-stack">
    <PageHeader index="18" title="人设构建" description="构建任务、候选、历史和模板操作保持 revision 与服务端结构化校验，不再展示接口字段转储。" />
    {(section === "tasks" || section === "candidate") && <Panel eyebrow="PERSONA BUILDER / TASK" title="创建构建任务">
      <div className="inline-controls filter-control-row">
        <input value={workTitle} onChange={(event) => setWorkTitle(event.target.value)} placeholder="作品名称" aria-label="作品名称" />
        <input value={characterName} onChange={(event) => setCharacterName(event.target.value)} placeholder="角色名称" aria-label="角色名称" />
        <button className="button button-primary" type="button" disabled={!workTitle.trim() || !characterName.trim() || build.isPending} onClick={() => { if (window.confirm(`确认创建 ${workTitle} / ${characterName} 的人设构建任务？`)) build.mutate(); }}>创建任务</button>
      </div>
      {build.data && <SummaryList value={build.data} fields={[{ key: "task_id", label: "任务 ID" }, { key: "status", label: "状态" }, { key: "stage", label: "阶段" }, { key: "message", label: "进度说明" }]} />}
    </Panel>}
    <Panel eyebrow="PERSONA BUILDER / HISTORY" title="构建历史">
      <QueryBoundary isPending={history.isPending} error={history.error}>
        <BusinessTable rows={records} rowKey={(row, index) => textAt(row, "record_id") + index} emptyCode="persona_builder_history_empty" emptyText="尚无人设构建历史。" columns={[
          { key: "record", label: "候选", render: (row) => <button className="text-link" type="button" onClick={() => setSelectedId(textAt(row, "record_id"))}><strong>{textAt(row, "character_name", "persona_name")}</strong><br /><code>{textAt(row, "record_id")}</code></button> },
          { key: "work_title", label: "作品", render: (row) => textAt(row, "work_title") },
          { key: "status", label: "校验", render: (row) => <StateBadge tone={row.template_valid === false ? "error" : "ok"}>{row.template_valid === false ? "未通过" : "已校验"}</StateBadge> },
          { key: "updated_at", label: "更新时间", render: (row) => formatDateTime(row.updated_at as string | number | null) },
          { key: "actions", label: "操作", render: (row) => <button className="button button-secondary" type="button" disabled={apply.isPending} onClick={() => { const id = textAt(row, "record_id"); if (window.confirm(`确认应用人设记录 ${id}？`)) apply.mutate(id); }}>应用</button> },
        ]} />
      </QueryBoundary>
    </Panel>
    {selectedId && <Panel eyebrow="PERSONA BUILDER / DETAIL" title={`历史详情 ${selectedId}`}>
      <QueryBoundary isPending={detail.isPending} error={detail.error}><SummaryList value={detail.data} fields={[{ key: "work_title", label: "作品" }, { key: "character_name", label: "角色" }, { key: "updated_at", label: "更新时间" }, { key: "edited_by", label: "最后修改人" }]} /></QueryBoundary>
    </Panel>}
    {diagnostic && <DiagnosticPanel diagnostic={diagnostic} defaultOpen />}
  </div>;
}
