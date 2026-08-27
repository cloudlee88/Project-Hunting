"use client";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ScrollText, AlertTriangle, RefreshCw,
  Archive, RotateCcw, Brain, ChevronDown, ChevronUp, Pencil, Check, X, Clock,
} from "lucide-react";
import * as api from "@/lib/api";
import { Card } from "@/components/Card";
import { PageHeader } from "@/components/PageHeader";
import { Button } from "@/components/Button";
import { Badge } from "@/components/Badge";
import { EmptyState } from "@/components/EmptyState";
import { useToast } from "@/lib/toast";

type PlaybookOut = api.PlaybookOut;

const STATUS_STYLE: Record<string, { variant: "success" | "error" | "warning" | "info" | "neutral"; label: string }> = {
  active: { variant: "success", label: "Hoạt động" },
  needs_llm: { variant: "warning", label: "Cần LLM" },
  archived: { variant: "neutral", label: "Đã lưu trữ" },
};

function PlaybookCard({ pb, onUpdate }: { pb: PlaybookOut; onUpdate: () => void }) {
  const { push } = useToast();
  const qc = useQueryClient();
  const [expanded, setExpanded] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editName, setEditName] = useState(pb.name);

  const archiveMut = useMutation({
    mutationFn: () => api.archivePlaybook(pb.id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["playbooks"] }); push({ type: "success", message: "Đã lưu trữ playbook" }); },
    onError: (e: Error) => push({ type: "error", message: e.message }),
  });

  const restoreMut = useMutation({
    mutationFn: () => api.restorePlaybook(pb.id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["playbooks"] }); push({ type: "success", message: "Đã kích hoạt lại playbook" }); },
    onError: (e: Error) => push({ type: "error", message: e.message }),
  });

  const approveMut = useMutation({
    mutationFn: () => api.approvePlaybookLlm(pb.id),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["playbooks"] });
      push({ type: "success", message: `Đã tạo LLM re-record job #${res.rerecord_job_id}` });
    },
    onError: (e: Error) => push({ type: "error", message: e.message }),
  });

  const renameMut = useMutation({
    mutationFn: () => api.updatePlaybook(pb.id, { name: editName.trim() }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["playbooks"] }); setEditing(false); push({ type: "success", message: "Đã đổi tên" }); },
    onError: (e: Error) => push({ type: "error", message: e.message }),
  });

  const st = STATUS_STYLE[pb.status] || STATUS_STYLE.active;
  const needsReset = pb.status === "needs_llm" || pb.pending_llm_approval || pb.pending_review;

  return (
    <Card className="p-4 space-y-3">
      {/* Header row */}
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          {editing ? (
            <div className="flex items-center gap-2">
              <input
                className="border rounded px-2 py-1 text-sm w-full max-w-xs"
                value={editName}
                onChange={(e) => setEditName(e.target.value)}
                autoFocus
              />
              <button onClick={() => renameMut.mutate()} disabled={renameMut.isPending} className="text-green-600 hover:text-green-700">
                <Check size={16} />
              </button>
              <button onClick={() => { setEditing(false); setEditName(pb.name); }} className="text-zinc-400 hover:text-zinc-600">
                <X size={16} />
              </button>
            </div>
          ) : (
            <div className="flex items-center gap-2">
              <span className="font-medium truncate">{pb.name}</span>
              <button onClick={() => setEditing(true)} className="text-zinc-400 hover:text-zinc-600 shrink-0">
                <Pencil size={13} />
              </button>
            </div>
          )}
          <div className="text-xs text-zinc-500 mt-0.5">
            {pb.program_name && <span className="mr-2">📦 {pb.program_name}</span>}
            {pb.platform && <span className="mr-2">🌐 {pb.platform}</span>}
            {pb.category && <span>🏷 {pb.category}</span>}
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <Badge variant={st.variant}>{st.label}</Badge>
          {pb.pending_llm_approval && (
            <Badge variant="warning"><AlertTriangle size={11} /> Chờ duyệt LLM</Badge>
          )}
        </div>
      </div>

      {/* Stats row */}
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-zinc-500">
        <span>✅ {pb.success_runs} / {pb.total_runs} lần ({pb.success_rate}%)</span>
        <span>❌ Fails liên tiếp: {pb.consecutive_fails}</span>
        {pb.last_used_at && (
          <span>🕐 Lần cuối: {new Date(pb.last_used_at).toLocaleDateString("vi-VN")}</span>
        )}
        <span>📝 {pb.steps.length} bước</span>
      </div>

      {/* Action buttons */}
      <div className="flex flex-wrap gap-2">
        {/* Kích hoạt lại — hiện khi script bị kẹt ở trạng thái không active */}
        {needsReset && (
          <Button
            size="sm"
            variant="primary"
            onClick={() => restoreMut.mutate()}
            disabled={restoreMut.isPending}
            className="flex items-center gap-1"
          >
            <RotateCcw size={13} />
            {restoreMut.isPending ? "Đang kích hoạt..." : "Kích hoạt lại"}
          </Button>
        )}
        {/* Re-record LLM — admin yêu cầu thủ công, chỉ khi có program_id */}
        {pb.program_id && pb.status !== "archived" && (
          <Button
            size="sm"
            variant={needsReset ? "secondary" : "primary"}
            onClick={() => approveMut.mutate()}
            disabled={approveMut.isPending}
            className="flex items-center gap-1"
          >
            <Brain size={13} />
            {approveMut.isPending ? "Đang tạo job..." : "Re-record LLM"}
          </Button>
        )}
        {pb.status !== "archived" ? (
          <Button
            size="sm"
            variant="secondary"
            onClick={() => archiveMut.mutate()}
            disabled={archiveMut.isPending}
            className="flex items-center gap-1 text-zinc-500"
          >
            <Archive size={13} />
            Lưu trữ
          </Button>
        ) : (
          <Button
            size="sm"
            variant="secondary"
            onClick={() => restoreMut.mutate()}
            disabled={restoreMut.isPending}
            className="flex items-center gap-1"
          >
            <RotateCcw size={13} />
            Khôi phục
          </Button>
        )}
        <button
          onClick={() => setExpanded((v) => !v)}
          className="flex items-center gap-1 text-xs text-zinc-400 hover:text-zinc-600"
        >
          {expanded ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
          {expanded ? "Ẩn steps" : "Xem steps"}
        </button>
      </div>

      {/* Expanded steps */}
      {expanded && (
        <div className="border rounded p-3 bg-zinc-50 max-h-64 overflow-y-auto">
          {pb.steps.length === 0 ? (
            <p className="text-xs text-zinc-400 text-center">Không có steps</p>
          ) : (
            <ol className="space-y-1">
              {pb.steps.map((step, i) => (
                <li key={i} className="text-xs font-mono text-zinc-700">
                  <span className="text-zinc-400 mr-1">{i + 1}.</span>
                  <span className="font-semibold text-blue-700">{step.action}</span>
                  {step.selector && <span className="text-zinc-500"> [{step.selector}]</span>}
                  {step.value && <span className="text-green-700"> = {String(step.value).slice(0, 60)}</span>}
                  {step.url && <span className="text-purple-600"> → {String(step.url).slice(0, 60)}</span>}
                </li>
              ))}
            </ol>
          )}
        </div>
      )}
    </Card>
  );
}

// ── Pending review card ───────────────────────────────────────────────────────

function PendingReviewCard({ pb, onUpdate }: { pb: PlaybookOut; onUpdate: () => void }) {
  const { push } = useToast();
  const qc = useQueryClient();
  const [expanded, setExpanded] = useState(false);

  const approveMut = useMutation({
    mutationFn: () => api.approveReviewPlaybook(pb.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["playbooks", "pending"] });
      qc.invalidateQueries({ queryKey: ["playbooks"] });
      push({ type: "success", message: `Đã duyệt playbook "${pb.name}"` });
      onUpdate();
    },
    onError: (e: Error) => push({ type: "error", message: e.message }),
  });

  const rejectMut = useMutation({
    mutationFn: () => api.rejectReviewPlaybook(pb.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["playbooks", "pending"] });
      qc.invalidateQueries({ queryKey: ["playbooks"] });
      push({ type: "info", message: `Đã từ chối playbook "${pb.name}"` });
      onUpdate();
    },
    onError: (e: Error) => push({ type: "error", message: e.message }),
  });

  return (
    <div className="border rounded-lg p-4 bg-blue-50 border-blue-200 space-y-2">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="font-medium text-sm">{pb.name}</p>
          <p className="text-xs text-zinc-500 mt-0.5">
            {pb.platform && <span className="mr-2">🌐 {pb.platform}</span>}
            {pb.category && <span className="mr-2">🏷 {pb.category}</span>}
            {pb.program_name && <span>📦 {pb.program_name}</span>}
            {pb.created_at && (
              <span className="ml-2 text-zinc-400">· {new Date(pb.created_at).toLocaleDateString("vi-VN")}</span>
            )}
          </p>
        </div>
        <Badge variant="info">Tier 3 tạo</Badge>
      </div>
      <div className="flex items-center gap-2">
        <Button size="sm" onClick={() => approveMut.mutate()} loading={approveMut.isPending}>
          <Check size={13} className="mr-1" />Duyệt
        </Button>
        <Button size="sm" variant="secondary" onClick={() => rejectMut.mutate()} loading={rejectMut.isPending}>
          <X size={13} className="mr-1" />Từ chối
        </Button>
        <button
          onClick={() => setExpanded(!expanded)}
          className="text-xs text-zinc-500 hover:text-zinc-700 flex items-center gap-1 ml-2"
        >
          {expanded ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
          {pb.steps.length} bước
        </button>
      </div>
      {expanded && (
        <div className="border rounded p-3 bg-white max-h-48 overflow-y-auto mt-2">
          <ol className="space-y-1">
            {pb.steps.map((step, i) => (
              <li key={i} className="text-xs font-mono text-zinc-700">
                <span className="text-zinc-400 mr-1">{i + 1}.</span>
                <span className="font-semibold text-blue-700">{step.action}</span>
                {step.selector && <span className="text-zinc-500"> [{String(step.selector).slice(0, 60)}]</span>}
                {step.value && <span className="text-green-700"> = {String(step.value).slice(0, 40)}</span>}
              </li>
            ))}
          </ol>
        </div>
      )}
    </div>
  );
}


export default function PlaybooksPage() {
  const [statusFilter, setStatusFilter] = useState<"" | "active" | "needs_llm" | "archived">("");

  const { data: playbooks = [], isLoading, error } = useQuery({
    queryKey: ["playbooks", statusFilter],
    queryFn: () => api.listPlaybooks(statusFilter ? { status: statusFilter } : undefined),
    refetchInterval: 15000,
  });

  const { data: pendingPlaybooks = [] } = useQuery({
    queryKey: ["playbooks", "pending"],
    queryFn: api.listPendingPlaybooks,
    refetchInterval: 15000,
  });

  const qc = useQueryClient();

  const needsApproval = playbooks.filter((p) => p.pending_llm_approval).length;
  const active = playbooks.filter((p) => p.status === "active" && !p.pending_llm_approval).length;
  const archived = playbooks.filter((p) => p.status === "archived").length;

  return (
    <div className="p-6 space-y-6 max-w-5xl mx-auto">
      <PageHeader
        title="Quản lý Playbook"
        description="Script đăng ký tự động — ghi một lần, chạy nhiều lần"
      />

      {/* Pending review section (Tier 3 generated) */}
      {pendingPlaybooks.length > 0 && (
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <Clock size={16} className="text-blue-600" />
            <h3 className="font-medium text-sm text-blue-700">Chờ duyệt ({pendingPlaybooks.length}) — Do Tier 3 AI tạo ra</h3>
          </div>
          {pendingPlaybooks.map((pb) => (
            <PendingReviewCard
              key={pb.id}
              pb={pb}
              onUpdate={() => {
                qc.invalidateQueries({ queryKey: ["playbooks"] });
                qc.invalidateQueries({ queryKey: ["playbooks", "pending"] });
              }}
            />
          ))}
        </div>
      )}

      {/* Summary chips */}
      <div className="flex flex-wrap gap-3">
        <button
          onClick={() => setStatusFilter("")}
          className={`px-3 py-1.5 rounded-full text-sm border transition ${
            statusFilter === "" ? "bg-zinc-800 text-white border-zinc-800" : "border-zinc-300 text-zinc-600 hover:bg-zinc-50"
          }`}
        >
          Tất cả ({playbooks.length || "-"})
        </button>
        <button
          onClick={() => setStatusFilter("active")}
          className={`px-3 py-1.5 rounded-full text-sm border transition ${
            statusFilter === "active" ? "bg-green-600 text-white border-green-600" : "border-zinc-300 text-zinc-600 hover:bg-zinc-50"
          }`}
        >
          ✅ Hoạt động ({active})
        </button>
        <button
          onClick={() => setStatusFilter("needs_llm")}
          className={`px-3 py-1.5 rounded-full text-sm border transition ${
            statusFilter === "needs_llm" ? "bg-amber-500 text-white border-amber-500" : "border-zinc-300 text-zinc-600 hover:bg-zinc-50"
          }`}
        >
          ⚠️ Cần LLM ({needsApproval})
        </button>
        <button
          onClick={() => setStatusFilter("archived")}
          className={`px-3 py-1.5 rounded-full text-sm border transition ${
            statusFilter === "archived" ? "bg-zinc-500 text-white border-zinc-500" : "border-zinc-300 text-zinc-600 hover:bg-zinc-50"
          }`}
        >
          📦 Đã lưu trữ ({archived})
        </button>
        <button
          onClick={() => qc.invalidateQueries({ queryKey: ["playbooks"] })}
          className="px-3 py-1.5 rounded-full text-sm border border-zinc-300 text-zinc-600 hover:bg-zinc-50 flex items-center gap-1.5"
        >
          <RefreshCw size={13} />
          Làm mới
        </button>
      </div>

      {/* Needs-reset banner */}
      {needsApproval > 0 && !statusFilter && (
        <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 flex items-center gap-3 text-sm text-amber-800">
          <AlertTriangle size={16} className="shrink-0" />
          <span>
            <strong>{needsApproval} playbook</strong> cần được kích hoạt lại.
            Nhấn &ldquo;Kích hoạt lại&rdquo; để về trạng thái hoạt động, hoặc &ldquo;Re-record LLM&rdquo; để ghi script mới.
          </span>
        </div>
      )}

      {/* Content */}
      {isLoading ? (
        <div className="text-center text-zinc-400 py-12">
          <RefreshCw size={24} className="animate-spin mx-auto mb-2" />
          Đang tải...
        </div>
      ) : error ? (
        <div className="text-center text-red-500 py-12">
          Lỗi: {(error as Error).message}
        </div>
      ) : playbooks.length === 0 ? (
        <EmptyState
          icon={ScrollText}
          title="Chưa có playbook nào"
          description="Playbook sẽ tự động tạo sau khi LLM đăng ký thành công lần đầu. Hãy chạy Đăng ký tự động trước."
        />
      ) : (
        <div className="space-y-3">
          {playbooks.map((pb) => (
            <PlaybookCard key={pb.id} pb={pb} onUpdate={() => qc.invalidateQueries({ queryKey: ["playbooks"] })} />
          ))}
        </div>
      )}
    </div>
  );
}
