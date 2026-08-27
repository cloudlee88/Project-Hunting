"use client";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BookOpen, Plus, Search, RefreshCw,
  ChevronDown, ChevronUp, Check, X, Archive, RotateCcw,
} from "lucide-react";
import * as api from "@/lib/api";
import { Card } from "@/components/Card";
import { PageHeader } from "@/components/PageHeader";
import { Button } from "@/components/Button";
import { Badge } from "@/components/Badge";
import { EmptyState } from "@/components/EmptyState";
import { useToast } from "@/lib/toast";

type QAEntry = api.QAEntryOut;

const PLATFORMS = ["everflow", "partnerstack", "post_affiliate_pro", "goaffpro", "firstpromoter"];
const PLATFORM_LABEL: Record<string, string> = {
  everflow: "Everflow",
  partnerstack: "PartnerStack",
  post_affiliate_pro: "Post Affiliate Pro",
  goaffpro: "GoAffPro",
  firstpromoter: "FirstPromoter",
};
const ANSWER_TYPE_LABEL: Record<string, string> = {
  profile_field: "Profile",
  static: "Static",
  template: "Template",
  select_option: "Select",
  traffic_source_lookup: "Traffic",
};
const STATUS_STYLE: Record<string, { variant: "success" | "warning" | "neutral"; label: string }> = {
  active: { variant: "success", label: "Hoạt động" },
  needs_review: { variant: "warning", label: "Cần review" },
  archived: { variant: "neutral", label: "Đã lưu trữ" },
};

// ── QA Row (expandable) ───────────────────────────────────────────────────────

function QARow({ entry, onUpdate }: { entry: QAEntry; onUpdate: () => void }) {
  const { push } = useToast();
  const qc = useQueryClient();
  const [expanded, setExpanded] = useState(false);
  const [editAnswer, setEditAnswer] = useState(entry.answer_template);
  const [editNote, setEditNote] = useState(entry.note || "");
  const [editCategory, setEditCategory] = useState(entry.category || "");
  const [dirty, setDirty] = useState(false);

  const saveMut = useMutation({
    mutationFn: () => api.updateQA(entry.id, {
      answer_template: editAnswer,
      note: editNote || undefined,
      category: editCategory || undefined,
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["qa-library"] });
      setDirty(false);
      push({ type: "success", message: "Đã lưu" });
    },
    onError: (e: Error) => push({ type: "error", message: e.message }),
  });

  const activateMut = useMutation({
    mutationFn: () => api.updateQA(entry.id, { status: "active" }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["qa-library"] }); onUpdate(); push({ type: "success", message: "Đã kích hoạt lại" }); },
    onError: (e: Error) => push({ type: "error", message: e.message }),
  });

  const archiveMut = useMutation({
    mutationFn: () => api.archiveQA(entry.id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["qa-library"] }); onUpdate(); push({ type: "success", message: "Đã lưu trữ" }); },
    onError: (e: Error) => push({ type: "error", message: e.message }),
  });

  const restoreMut = useMutation({
    mutationFn: () => api.restoreQA(entry.id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["qa-library"] }); onUpdate(); push({ type: "success", message: "Đã khôi phục" }); },
    onError: (e: Error) => push({ type: "error", message: e.message }),
  });

  const st = STATUS_STYLE[entry.status] || STATUS_STYLE.active;

  return (
    <div className="border-b last:border-b-0">
      {/* Main row */}
      <div
        className="flex items-center gap-3 px-4 py-3 hover:bg-zinc-50 cursor-pointer"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium truncate">{entry.question_pattern}</p>
          <p className="text-xs text-zinc-400 truncate mt-0.5">
            {entry.answer_template || <span className="italic">Rỗng</span>}
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <span className="text-xs text-zinc-400">{ANSWER_TYPE_LABEL[entry.answer_type] || entry.answer_type}</span>
          <span className="text-xs text-zinc-500 w-8 text-right">{Math.round(entry.confidence * 100)}%</span>
          {entry.usage_count > 0 && (
            <span className="text-xs text-zinc-400">{entry.success_count}/{entry.usage_count}</span>
          )}
          <Badge variant={st.variant}>{st.label}</Badge>
          {expanded ? <ChevronUp size={14} className="text-zinc-400" /> : <ChevronDown size={14} className="text-zinc-400" />}
        </div>
      </div>

      {/* Expanded detail */}
      {expanded && (
        <div className="px-4 pb-4 bg-zinc-50 border-t space-y-3">
          <div className="grid grid-cols-1 gap-3 pt-3">
            <div>
              <label className="text-xs font-medium text-zinc-600 block mb-1">Câu hỏi pattern</label>
              <p className="text-sm font-mono bg-white border rounded px-2 py-1.5">{entry.question_pattern}</p>
            </div>
            <div>
              <label className="text-xs font-medium text-zinc-600 block mb-1">Đáp án template</label>
              <textarea
                className="w-full border rounded px-2 py-1.5 text-sm font-mono resize-none"
                rows={Math.min(6, Math.floor(entry.answer_template.length / 80) + 2)}
                value={editAnswer}
                onChange={(e) => { setEditAnswer(e.target.value); setDirty(true); }}
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs font-medium text-zinc-600 block mb-1">Category</label>
                <input
                  className="w-full border rounded px-2 py-1.5 text-sm"
                  value={editCategory}
                  placeholder="(chung)"
                  onChange={(e) => { setEditCategory(e.target.value); setDirty(true); }}
                />
              </div>
              <div>
                <label className="text-xs font-medium text-zinc-600 block mb-1">Ghi chú</label>
                <input
                  className="w-full border rounded px-2 py-1.5 text-sm"
                  value={editNote}
                  placeholder="(không có)"
                  onChange={(e) => { setEditNote(e.target.value); setDirty(true); }}
                />
              </div>
            </div>
          </div>

          <div className="flex items-center gap-2">
            {dirty && (
              <Button size="sm" onClick={() => saveMut.mutate()} loading={saveMut.isPending}>
                <Check size={13} className="mr-1" />Lưu
              </Button>
            )}
            {dirty && (
              <Button size="sm" variant="secondary" onClick={() => {
                setEditAnswer(entry.answer_template);
                setEditNote(entry.note || "");
                setEditCategory(entry.category || "");
                setDirty(false);
              }}>
                <X size={13} className="mr-1" />Huỷ
              </Button>
            )}
            {entry.status === "needs_review" && (
              <Button size="sm" variant="secondary" onClick={() => activateMut.mutate()} loading={activateMut.isPending}>
                <Check size={13} className="mr-1" />Kích hoạt lại
              </Button>
            )}
            {entry.status !== "archived" ? (
              <Button size="sm" variant="secondary" onClick={() => archiveMut.mutate()} loading={archiveMut.isPending}>
                <Archive size={13} className="mr-1" />Lưu trữ
              </Button>
            ) : (
              <Button size="sm" variant="secondary" onClick={() => restoreMut.mutate()} loading={restoreMut.isPending}>
                <RotateCcw size={13} className="mr-1" />Khôi phục
              </Button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Add QA Modal ──────────────────────────────────────────────────────────────

function AddQAModal({ platform, onClose }: { platform: string; onClose: () => void }) {
  const { push } = useToast();
  const qc = useQueryClient();
  const [form, setForm] = useState({
    question_pattern: "",
    answer_template: "",
    answer_type: "static",
    required: true,
    category: "",
    note: "",
  });

  const createMut = useMutation({
    mutationFn: () => api.createQA({
      platform,
      question_pattern: form.question_pattern,
      answer_template: form.answer_template,
      answer_type: form.answer_type,
      required: form.required,
      category: form.category || undefined,
      note: form.note || undefined,
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["qa-library"] });
      push({ type: "success", message: "Đã thêm Q&A" });
      onClose();
    },
    onError: (e: Error) => push({ type: "error", message: e.message }),
  });

  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-white rounded-lg shadow-xl w-full max-w-md" onClick={(e) => e.stopPropagation()}>
        <div className="px-5 py-4 border-b flex items-center justify-between">
          <h3 className="font-semibold">Thêm Q&A — {PLATFORM_LABEL[platform] || platform}</h3>
          <button onClick={onClose} className="text-zinc-400 hover:text-zinc-600"><X size={18} /></button>
        </div>
        <div className="px-5 py-4 space-y-3">
          <div>
            <label className="text-xs font-medium text-zinc-600 block mb-1">Câu hỏi / Pattern *</label>
            <input
              className="w-full border rounded px-3 py-2 text-sm"
              placeholder="VD: How would you promote us?"
              value={form.question_pattern}
              onChange={(e) => setForm({ ...form, question_pattern: e.target.value })}
            />
          </div>
          <div>
            <label className="text-xs font-medium text-zinc-600 block mb-1">Đáp án template *</label>
            <textarea
              className="w-full border rounded px-3 py-2 text-sm resize-none"
              rows={4}
              placeholder="Dùng {profile.first_name}, {profile.website}, ..."
              value={form.answer_template}
              onChange={(e) => setForm({ ...form, answer_template: e.target.value })}
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs font-medium text-zinc-600 block mb-1">Loại đáp án</label>
              <select
                className="w-full border rounded px-2 py-2 text-sm"
                value={form.answer_type}
                onChange={(e) => setForm({ ...form, answer_type: e.target.value })}
              >
                <option value="static">Static</option>
                <option value="profile_field">Profile field</option>
                <option value="template">Template</option>
                <option value="select_option">Select option</option>
              </select>
            </div>
            <div>
              <label className="text-xs font-medium text-zinc-600 block mb-1">Category</label>
              <input
                className="w-full border rounded px-2 py-2 text-sm"
                placeholder="(chung)"
                value={form.category}
                onChange={(e) => setForm({ ...form, category: e.target.value })}
              />
            </div>
          </div>
          <div>
            <label className="text-xs font-medium text-zinc-600 block mb-1">Ghi chú</label>
            <input
              className="w-full border rounded px-3 py-2 text-sm"
              value={form.note}
              onChange={(e) => setForm({ ...form, note: e.target.value })}
            />
          </div>
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input
              type="checkbox"
              checked={form.required}
              onChange={(e) => setForm({ ...form, required: e.target.checked })}
            />
            Bắt buộc
          </label>
        </div>
        <div className="px-5 py-3 border-t flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose}>Huỷ</Button>
          <Button
            onClick={() => createMut.mutate()}
            loading={createMut.isPending}
            disabled={!form.question_pattern || !form.answer_template}
          >
            Thêm
          </Button>
        </div>
      </div>
    </div>
  );
}

// ── Main page ──────────────────────────────────────────────────────────────────

export default function QALibraryPage() {
  const qc = useQueryClient();
  const { push } = useToast();
  const [activePlatform, setActivePlatform] = useState("firstpromoter");
  const [filterStatus, setFilterStatus] = useState("");
  const [search, setSearch] = useState("");
  const [showAddModal, setShowAddModal] = useState(false);

  const statsQ = useQuery({
    queryKey: ["qa-stats"],
    queryFn: api.getQAStats,
  });

  const entriesQ = useQuery({
    queryKey: ["qa-library", activePlatform, filterStatus, search],
    queryFn: () => api.listQA({ platform: activePlatform, status: filterStatus || undefined, search: search || undefined }),
  });

  const seedMut = useMutation({
    mutationFn: (force: boolean) => api.seedQA(force),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["qa-library"] });
      qc.invalidateQueries({ queryKey: ["qa-stats"] });
      push({ type: "info", message: r.seeded > 0 ? `Đã seed ${r.seeded} entries` : "Bảng đã có dữ liệu, dùng nút ⟳ và chọn Force để seed lại" });
    },
    onError: (e: Error) => push({ type: "error", message: e.message }),
  });

  const statsMap: Record<string, api.QAStatsOut> = {};
  (statsQ.data || []).forEach((s) => { statsMap[s.platform] = s; });

  const entries = entriesQ.data || [];
  const needsReview = entries.filter((e) => e.status === "needs_review");
  const activeEntries = entries.filter((e) => e.status === "active");
  const archived = entries.filter((e) => e.status === "archived");

  const shown = filterStatus === "needs_review" ? needsReview
    : filterStatus === "archived" ? archived
    : filterStatus === "active" ? activeEntries
    : entries;

  return (
    <div className="max-w-5xl mx-auto space-y-6 pb-8">
      <PageHeader
        title="Q&A Library"
        description="Kho câu hỏi & đáp án mẫu cho từng affiliate platform — dùng bởi Script Engine"
        action={
          <div className="flex gap-2">
            <Button
              variant="secondary"
              size="sm"
              onClick={() => seedMut.mutate(false)}
              onContextMenu={(e) => { e.preventDefault(); if (confirm("Force seed lại toàn bộ 98 entries từ file?")) seedMut.mutate(true); }}
              loading={seedMut.isPending}
              title="Click: seed nếu trống | Chuột phải: force seed lại"
            >
              <RefreshCw size={14} className="mr-1" />Seed
            </Button>
            <Button size="sm" onClick={() => setShowAddModal(true)}>
              <Plus size={14} className="mr-1" />Thêm Q&A
            </Button>
          </div>
        }
      />

      {/* Platform tabs */}
      <Card className="p-0 overflow-hidden">
        <div className="flex overflow-x-auto border-b">
          {PLATFORMS.map((p) => {
            const st = statsMap[p];
            const isActive = p === activePlatform;
            return (
              <button
                key={p}
                onClick={() => setActivePlatform(p)}
                className={`px-4 py-3 text-sm font-medium whitespace-nowrap border-r last:border-r-0 transition-colors ${
                  isActive ? "bg-blue-50 text-blue-700 border-b-2 border-b-blue-600" : "text-zinc-600 hover:bg-zinc-50"
                }`}
              >
                {PLATFORM_LABEL[p] || p}
                {st && (
                  <span className={`ml-1.5 text-xs ${isActive ? "text-blue-500" : "text-zinc-400"}`}>
                    ({st.active}
                    {st.needs_review > 0 && <span className="text-amber-500">+{st.needs_review}</span>}
                    )
                  </span>
                )}
              </button>
            );
          })}
        </div>

        {/* Filters */}
        <div className="px-4 py-3 flex items-center gap-3 border-b bg-zinc-50">
          <div className="relative flex-1 max-w-sm">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-400" />
            <input
              className="w-full pl-8 pr-3 py-1.5 border rounded text-sm"
              placeholder="Tìm câu hỏi..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <select
            className="border rounded px-3 py-1.5 text-sm"
            value={filterStatus}
            onChange={(e) => setFilterStatus(e.target.value)}
          >
            <option value="">Tất cả trạng thái</option>
            <option value="active">Hoạt động</option>
            <option value="needs_review">Cần review</option>
            <option value="archived">Đã lưu trữ</option>
          </select>

          {/* Stats */}
          <div className="ml-auto flex items-center gap-3 text-xs text-zinc-500">
            <span>{entries.length} tổng</span>
            <span className="text-green-600">{activeEntries.length} active</span>
            {needsReview.length > 0 && <span className="text-amber-600">{needsReview.length} cần review</span>}
          </div>
        </div>

        {/* Entries */}
        {entriesQ.isLoading ? (
          <div className="flex items-center justify-center h-32 text-zinc-400 text-sm">Đang tải...</div>
        ) : shown.length === 0 ? (
          <EmptyState
            icon={BookOpen}
            title="Không có Q&A"
            description={filterStatus ? "Không có entry ở trạng thái này" : "Chưa có Q&A cho platform này"}
          />
        ) : (
          <div>
            {/* Needs review section */}
            {!filterStatus && needsReview.length > 0 && (
              <div>
                <div className="px-4 py-2 bg-amber-50 border-b flex items-center gap-2">
                  <span className="text-xs font-medium text-amber-700">⚠️ Cần review ({needsReview.length})</span>
                  <span className="text-xs text-amber-600">— Fail ≥ 3 lần liên tiếp</span>
                </div>
                {needsReview.map((e) => (
                  <QARow key={e.id} entry={e} onUpdate={() => qc.invalidateQueries({ queryKey: ["qa-library"] })} />
                ))}
                <div className="px-4 py-2 bg-zinc-50 border-b">
                  <span className="text-xs font-medium text-zinc-600">Hoạt động ({activeEntries.length})</span>
                </div>
              </div>
            )}
            {shown.filter(e => filterStatus || e.status !== "needs_review").map((e) => (
              <QARow key={e.id} entry={e} onUpdate={() => qc.invalidateQueries({ queryKey: ["qa-library"] })} />
            ))}
          </div>
        )}
      </Card>

      {showAddModal && <AddQAModal platform={activePlatform} onClose={() => setShowAddModal(false)} />}
    </div>
  );
}
