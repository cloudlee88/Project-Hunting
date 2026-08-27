"use client";
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Play, ScrollText, RefreshCw,
  ChevronDown, ChevronUp, Search, ExternalLink, Info, Zap, CheckCircle2, XCircle, KeyRound,
} from "lucide-react";
import * as api from "@/lib/api";
import { Card } from "@/components/Card";
import { PageHeader } from "@/components/PageHeader";
import { Button } from "@/components/Button";
import { Badge } from "@/components/Badge";
import { EmptyState } from "@/components/EmptyState";
import { useToast } from "@/lib/toast";

// ── Types ─────────────────────────────────────────────────────────────────────

type Tier3Behavior = "ask" | "auto" | "never";

const JOB_STATUS: Record<string, { variant: "success" | "error" | "warning" | "info" | "neutral"; label: string }> = {
  pending:  { variant: "neutral",  label: "Chờ" },
  running:  { variant: "info",     label: "Đang chạy" },
  success:  { variant: "success",  label: "Thành công" },
  partial:  { variant: "warning",  label: "Một phần" },
  failed:   { variant: "error",    label: "Thất bại" },
  error:    { variant: "error",    label: "Lỗi" },
};

// ── Helpers ───────────────────────────────────────────────────────────────────

function SectionLabel({ children }: { children: React.ReactNode }) {
  return <h3 className="font-medium text-sm text-zinc-700 mb-2">{children}</h3>;
}

function MultiCheckList<T extends { id: string; label: string; sub?: string }>({
  items, selected, onToggle, emptyText,
}: {
  items: T[]; selected: string[]; onToggle: (id: string) => void; emptyText?: string;
}) {
  if (!items.length) return <p className="text-xs text-zinc-400 px-2 py-1">{emptyText ?? "Không có dữ liệu"}</p>;
  return (
    <div className="space-y-0.5 max-h-44 overflow-y-auto">
      {items.map((item) => (
        <label key={item.id} className="flex items-center gap-2 px-2 py-1 rounded text-sm cursor-pointer hover:bg-zinc-50">
          <input type="checkbox" checked={selected.includes(item.id)} onChange={() => onToggle(item.id)} />
          <span className="truncate">{item.label}</span>
          {item.sub && <span className="text-zinc-400 text-xs shrink-0">({item.sub})</span>}
        </label>
      ))}
    </div>
  );
}

// Platform detector
const NETWORK_ALIAS: Record<string, string> = { post_affiliate_pro: "post_affiliate_pro", postaffiliatepro: "post_affiliate_pro" };
function detectPlatform(url: string, directoryNetwork?: string | null): string {
  if (url) {
    const u = url.toLowerCase();
    if (u.includes("firstpromoter.com"))                               return "firstpromoter";
    if (u.includes("everflowclient.io") || u.includes("everflow.com")) return "everflow";
    if (u.includes("partnerstack.com") || u.includes("growsumo.com"))  return "partnerstack";
    if (u.includes("post.affiliate") || u.includes("postaffiliatepro")) return "post_affiliate_pro";
    if (u.includes("goaffpro"))                                        return "goaffpro";
    if (u.includes("rewardful.com"))                                   return "rewardful";
    if (u.includes("tapfiliate.com"))                                  return "tapfiliate";
  }
  if (directoryNetwork) {
    const n = directoryNetwork.toLowerCase().replace(/[-\s]/g, "_");
    return NETWORK_ALIAS[n] ?? n;
  }
  return "";
}

// Script badge: ✅ | ✅~ | 🆕 | ⚠️
function getScriptBadge(pb: api.PlaybookStatusEntry | undefined): { icon: string; label: string; cls: string } {
  if (!pb) return { icon: "🆕", label: "Chưa có script", cls: "text-blue-600 bg-blue-50" };
  if (pb.pending_review) return { icon: "⚠️", label: "Chờ duyệt", cls: "text-amber-600 bg-amber-50" };
  if (pb.status !== "active") return { icon: "⚠️", label: "Cần xem lại", cls: "text-amber-600 bg-amber-50" };
  if (pb.is_fallback) return { icon: "✅~", label: "Dùng tạm", cls: "text-indigo-600 bg-indigo-50" };
  return { icon: "✅", label: "Khớp", cls: "text-emerald-600 bg-emerald-50" };
}

// Tier prediction badge
function getTierBadge(pb: api.PlaybookStatusEntry | undefined, hasOverride: boolean, runMode: string, t3behavior: Tier3Behavior): string {
  if (hasOverride) return "T1*";
  if (!pb) {
    if (runMode !== "script_llm") return "Skip";
    if (t3behavior === "never") return "Skip";
    if (t3behavior === "ask")   return "T3?";
    return "T3";
  }
  if (pb.pending_review || pb.status !== "active") return "T2";
  if (pb.is_fallback) return "T1~";
  return "T1";
}

const TIER_COLORS: Record<string, string> = {
  "T1":   "bg-emerald-100 text-emerald-700",
  "T1*":  "bg-violet-100 text-violet-700",
  "T1~":  "bg-indigo-100 text-indigo-700",
  "T2":   "bg-amber-100 text-amber-700",
  "T3":   "bg-blue-100 text-blue-700",
  "T3?":  "bg-purple-100 text-purple-700",
  "Skip": "bg-zinc-100 text-zinc-500",
};

// ── Tier 3 approval banner ────────────────────────────────────────────────────

function Tier3Banner({ job, onApprove, onSkip }: {
  job: api.SignupJob; onApprove: () => void; onSkip: () => void;
}) {
  const waiting = (job.results || []).filter((r) => r.status === "tier3_waiting");
  if (!waiting.length || job.tier3_status !== "waiting_approval") return null;
  return (
    <div className="border border-purple-200 bg-purple-50 rounded-lg p-3 flex items-center justify-between gap-3">
      <div className="flex items-start gap-2 text-sm">
        <Zap size={16} className="text-purple-600 shrink-0 mt-0.5" />
        <div>
          <span className="font-medium text-purple-800">
            {waiting.length} chương trình cần Tier 3 (~$0.15/run)
          </span>
          <div className="text-xs text-purple-600 mt-0.5">
            Job #{job.id} — Script không tìm thấy, cần full AI agent để đăng ký
          </div>
        </div>
      </div>
      <div className="flex gap-2 shrink-0">
        <Button size="sm" variant="primary" onClick={onApprove} className="gap-1">
          <CheckCircle2 size={13} /> Cho phép
        </Button>
        <Button size="sm" variant="secondary" onClick={onSkip} className="gap-1">
          <XCircle size={13} /> Bỏ qua
        </Button>
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function ScriptSignupPage() {
  const qc = useQueryClient();
  const { push } = useToast();

  // Config state
  const [shortlistId, setShortlistId] = useState<number | null>(null);
  const [selectedPrograms, setSelectedPrograms] = useState<number[]>([]);
  const [selectedProfiles, setSelectedProfiles] = useState<string[]>([]);
  const [selectedEmails, setSelectedEmails] = useState<string[]>([]);
  const [selectedProxies, setSelectedProxies] = useState<string[]>([]);
  const [headless, setHeadless] = useState(false);
  const [runMode, setRunMode] = useState<"script" | "script_llm">("script_llm");
  const [tier3Behavior, setTier3Behavior] = useState<Tier3Behavior>("ask");
  const [fSearch, setFSearch] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [expandedJobs, setExpandedJobs] = useState<Set<number>>(new Set());
  // Global script override — áp dụng cho toàn bộ dự án đang chọn
  const [selectedPlaybookId, setSelectedPlaybookId] = useState<number | null>(null);
  // SMS Profile
  const [selectedSmsProfile, setSelectedSmsProfile] = useState<string>("");
  // LLM API Key
  const [selectedLlmProvider, setSelectedLlmProvider] = useState<"" | "gemini" | "openai" | "deepseek">("");
  const [selectedLlmKeyIndex, setSelectedLlmKeyIndex] = useState<number>(0);

  // Queries
  const shortlistsQ = useQuery({ queryKey: ["shortlists"], queryFn: api.listShortlists });
  const itemsQ = useQuery({
    queryKey: ["shortlist-items", shortlistId],
    queryFn: () => api.getShortlistItems(shortlistId as number),
    enabled: shortlistId != null,
  });
  const profilesQ = useQuery({ queryKey: ["profiles"], queryFn: api.listProfiles });
  const emailsQ = useQuery({ queryKey: ["emails"], queryFn: api.listEmails });
  const proxiesQ = useQuery({ queryKey: ["proxies"], queryFn: api.listProxies });
  const playbooksQ = useQuery({
    queryKey: ["playbooks-active"],
    queryFn: () => api.listPlaybooks({ status: "active" }),
    staleTime: 30000,
  });
  const smsProfilesQ = useQuery({ queryKey: ["sms-profiles"], queryFn: api.listSmsProfiles });
  const llmKeysQ = {
    gemini:   useQuery({ queryKey: ["llm-keys", "gemini"],   queryFn: () => api.listLlmKeys("gemini"),   enabled: selectedLlmProvider === "gemini" }),
    openai:   useQuery({ queryKey: ["llm-keys", "openai"],   queryFn: () => api.listLlmKeys("openai"),   enabled: selectedLlmProvider === "openai" }),
    deepseek: useQuery({ queryKey: ["llm-keys", "deepseek"], queryFn: () => api.listLlmKeys("deepseek"), enabled: selectedLlmProvider === "deepseek" }),
  };
  const jobsQ = useQuery({
    queryKey: ["signup-jobs", "script"],
    queryFn: () => api.listSignupJobs({ run_mode: "script" }),
    refetchInterval: 3000,
  });

  // Auto-pick first shortlist
  useEffect(() => {
    if (shortlistId == null && shortlistsQ.data?.length) {
      setShortlistId(shortlistsQ.data[0].id);
    }
  }, [shortlistsQ.data, shortlistId]);

  const rawPrograms = useMemo(
    () => (itemsQ.data || []).map((it) => it.program).filter((p): p is api.Program => !!p),
    [itemsQ.data],
  );

  const programs = useMemo(() => {
    if (!fSearch) return rawPrograms;
    const q = fSearch.toLowerCase();
    return rawPrograms.filter(
      (p) => p.name.toLowerCase().includes(q) || (p.category || "").toLowerCase().includes(q),
    );
  }, [rawPrograms, fSearch]);

  // Batch playbook status
  const programIds = rawPrograms.map((p) => p.id);
  const pbStatusQ = useQuery({
    queryKey: ["playbook-status", programIds.join(",")],
    queryFn: () => api.batchPlaybookStatus(programIds),
    enabled: programIds.length > 0,
    refetchInterval: 15000,
  });
  const pbStatus = pbStatusQ.data || {};

  // Q&A stats
  const qaStatsQ = useQuery({ queryKey: ["qa-stats"], queryFn: api.getQAStats, staleTime: 60000 });
  const qaCountByPlatform: Record<string, number> = {};
  (qaStatsQ.data || []).forEach((s) => { qaCountByPlatform[s.platform] = s.active; });

  // Selected playbook info
  const selectedPlaybook = (playbooksQ.data || []).find((pb) => pb.id === selectedPlaybookId) ?? null;

  // Classify runnable programs
  const canRunIds = useMemo(
    () => new Set(
      programs.filter((p) => {
        if (runMode === "script_llm") return true;
        const pb = pbStatus[p.id];
        // In script-only mode: runnable if has active script OR global override chosen
        return selectedPlaybookId != null || (pb && pb.status === "active" && !pb.pending_llm_approval && !pb.pending_review);
      }).map((p) => p.id),
    ),
    [programs, pbStatus, runMode, selectedPlaybookId],
  );

  // Stats: T1* / T1 / T1~ / T3 / Skip
  const stats = useMemo(() => {
    let t1override = 0, t1 = 0, t1fallback = 0, t3 = 0, skip = 0;
    for (const p of rawPrograms) {
      const pb = pbStatus[p.id];
      const tier = getTierBadge(pb, !!selectedPlaybookId, runMode, tier3Behavior);
      if (tier === "T1*") t1override++;
      else if (tier === "T1") t1++;
      else if (tier === "T1~") t1fallback++;
      else if (tier === "T3" || tier === "T3?") t3++;
      else skip++;
    }
    return { t1override, t1, t1fallback, t3, skip };
  }, [rawPrograms, pbStatus, runMode, tier3Behavior, selectedPlaybookId]);

  const handleModeChange = (mode: "script" | "script_llm") => {
    setRunMode(mode);
    if (mode === "script" && !selectedPlaybookId) {
      setSelectedPrograms((prev) =>
        prev.filter((id) => {
          const pb = pbStatus[id];
          return pb && pb.status === "active" && !pb.pending_llm_approval && !pb.pending_review;
        }),
      );
    }
  };

  const toggleProgram = (id: number) => {
    if (!canRunIds.has(id)) return;
    setSelectedPrograms((prev) => prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]);
  };

  const toggleItem = (list: string[], setList: (v: string[]) => void, id: string) => {
    setList(list.includes(id) ? list.filter((x) => x !== id) : [...list, id]);
  };

  const selectAllRunnable = () => setSelectedPrograms(Array.from(canRunIds));
  const clearSelection = () => setSelectedPrograms([]);

  // Build script_overrides — global script áp dụng cho tất cả program đang chọn
  const builtOverrides = useMemo(() => {
    if (!selectedPlaybookId || !selectedPrograms.length) return undefined;
    const out: Record<string, number> = {};
    for (const pid of selectedPrograms) {
      out[String(pid)] = selectedPlaybookId;
    }
    return out;
  }, [selectedPlaybookId, selectedPrograms]);

  // Submit job
  const createJobMut = useMutation({
    mutationFn: () =>
      api.createSignupJob({
        program_ids: selectedPrograms,
        profile_ids: selectedProfiles,
        email_ids: selectedEmails.length ? selectedEmails : undefined,
        proxy_ids: selectedProxies.length ? selectedProxies : undefined,
        headless,
        run_mode: runMode,
        tier3_behavior: runMode === "script_llm" ? tier3Behavior : undefined,
        script_overrides: builtOverrides,
        sms_profile_id: selectedSmsProfile || undefined,
        llm_provider: selectedLlmProvider || undefined,
        llm_key_index: selectedLlmKeyIndex > 0 ? selectedLlmKeyIndex : undefined,
      }),
    onSuccess: (job) => {
      const count = selectedPrograms.length;
      setSelectedPrograms([]);
      qc.invalidateQueries({ queryKey: ["signup-jobs"] });
      push({ type: "success", message: `✅ Đã tạo job #${job.id} — ${count} chương trình` });
    },
    onError: (e: Error) => push({ type: "error", message: e.message }),
  });

  const canSubmit = selectedPrograms.length > 0 && selectedProfiles.length > 0 && !createJobMut.isPending;

  // Script jobs only
  const scriptJobs = useMemo(
    () => (jobsQ.data || []).filter((j) => j.run_mode === "script" || j.run_mode === "script_llm"),
    [jobsQ.data],
  );
  const waitingJobs = scriptJobs.filter((j) => j.tier3_status === "waiting_approval");

  // Tier3 approve/skip
  const approveTier3Mut = useMutation({
    mutationFn: (id: number) => api.approveTier3(id),
    onSuccess: (job) => {
      qc.invalidateQueries({ queryKey: ["signup-jobs"] });
      push({ type: "success", message: `✅ Đã duyệt Tier 3 cho job #${job.id}` });
    },
    onError: (e: Error) => push({ type: "error", message: e.message }),
  });
  const skipTier3Mut = useMutation({
    mutationFn: (id: number) => api.skipTier3(id),
    onSuccess: (job) => {
      qc.invalidateQueries({ queryKey: ["signup-jobs"] });
      push({ type: "info", message: `Đã bỏ qua Tier 3 cho job #${job.id}` });
    },
    onError: (e: Error) => push({ type: "error", message: e.message }),
  });

  const profileItems = (profilesQ.data || []).map((p) => ({ id: p.id, label: p.full_name || p.id, sub: p.email }));
  const emailItems = (emailsQ.data || []).map((e) => ({ id: e.id, label: e.address, sub: e.label }));
  const proxyItems = (proxiesQ.data || []).map((p) => ({ id: p.id, label: p.label || p.host || p.id, sub: p.host }));
  const activePlaybooks = playbooksQ.data || [];

  return (
    <div className="p-6 space-y-6 max-w-6xl mx-auto">
      <PageHeader
        title="Đăng ký theo Script"
        description="Script + Q&A Library tự động — tiết kiệm ~90% API cost so với Full AI"
      />

      {/* Info banner */}
      <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 flex gap-3 text-sm text-blue-800">
        <Info size={16} className="shrink-0 mt-0.5" />
        <div>
          <strong>3 tầng tự động:</strong> T1 Script (miễn phí) → T2 Sửa selector bằng AI → T3 Full AI Agent (~$0.15).
          Badge <strong>✅</strong> = khớp đúng · <strong>✅~</strong> = dùng tạm (cùng nền tảng) · <strong>🆕</strong> = chưa có script
        </div>
      </div>

      {/* Tier 3 approval banners */}
      {waitingJobs.map((job) => (
        <Tier3Banner
          key={job.id}
          job={job}
          onApprove={() => approveTier3Mut.mutate(job.id)}
          onSkip={() => skipTier3Mut.mutate(job.id)}
        />
      ))}

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
        {/* ── Left: config ─────────────────────────────────────── */}
        <div className="xl:col-span-1 space-y-4">

          {/* Shortlist */}
          <Card className="p-4">
            <SectionLabel>Shortlist</SectionLabel>
            <select
              className="w-full border rounded px-3 py-2 text-sm"
              value={shortlistId ?? ""}
              onChange={(e) => { setShortlistId(Number(e.target.value) || null); setSelectedPrograms([]); }}
            >
              {(shortlistsQ.data || []).map((s) => (
                <option key={s.id} value={s.id}>{s.name}</option>
              ))}
            </select>
          </Card>

          {/* Profile */}
          <Card className="p-4">
            <SectionLabel>Profile <span className="text-red-500">*</span></SectionLabel>
            <MultiCheckList
              items={profileItems}
              selected={selectedProfiles}
              onToggle={(id) => toggleItem(selectedProfiles, setSelectedProfiles, id)}
              emptyText="Chưa có profile. Tạo tại Thư viện."
            />
          </Card>

          {/* Script (global override) */}
          <Card className="p-4">
            <SectionLabel>Script (tuỳ chọn)</SectionLabel>
            <select
              className="w-full border rounded px-3 py-2 text-sm"
              value={selectedPlaybookId ?? ""}
              onChange={(e) => setSelectedPlaybookId(e.target.value ? Number(e.target.value) : null)}
            >
              <option value="">-- Tự động theo platform --</option>
              {activePlaybooks.map((pb) => (
                <option key={pb.id} value={pb.id}>
                  {pb.name}{pb.platform ? ` · ${pb.platform}` : ""}
                </option>
              ))}
            </select>
            {selectedPlaybook && (
              <p className="mt-1.5 text-xs text-violet-700 bg-violet-50 rounded px-2 py-1">
                Script này sẽ áp dụng cho <strong>tất cả {selectedPrograms.length > 0 ? selectedPrograms.length : "các"} dự án</strong> đã chọn
              </p>
            )}
            {!selectedPlaybookId && (
              <p className="mt-1 text-xs text-zinc-400">Mặc định: tự khớp script theo nền tảng của từng dự án</p>
            )}
          </Card>

          {/* SMS Profile */}
          <Card className="p-4">
            <SectionLabel>SMS Profile (tuỳ chọn)</SectionLabel>
            <div className="border rounded divide-y max-h-44 overflow-y-auto">
              <button
                type="button"
                onClick={() => setSelectedSmsProfile("")}
                className={`w-full text-left flex items-center gap-3 px-3 py-2 hover:bg-zinc-50 ${!selectedSmsProfile ? "bg-zinc-50" : ""}`}
              >
                <div className={`w-4 h-4 rounded-full border-2 flex items-center justify-center ${!selectedSmsProfile ? "border-blue-500" : "border-zinc-300"}`}>
                  {!selectedSmsProfile && <div className="w-2 h-2 rounded-full bg-blue-500" />}
                </div>
                <div>
                  <div className="text-sm font-medium">Mặc định</div>
                  <div className="text-xs text-zinc-400">Theo .env hoặc preset của từng program</div>
                </div>
              </button>
              {(smsProfilesQ.data || []).map((p) => {
                const sel = selectedSmsProfile === p.id;
                return (
                  <button
                    key={p.id}
                    type="button"
                    onClick={() => setSelectedSmsProfile(p.id)}
                    className={`w-full text-left flex items-center gap-3 px-3 py-2 hover:bg-zinc-50 ${sel ? "bg-zinc-50" : ""}`}
                  >
                    <div className={`w-4 h-4 rounded-full border-2 flex items-center justify-center shrink-0 ${sel ? "border-blue-500" : "border-zinc-300"}`}>
                      {sel && <div className="w-2 h-2 rounded-full bg-blue-500" />}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="text-sm font-medium truncate">{p.name}</div>
                      <div className="text-xs text-zinc-400 truncate">{p.country_name || `#${p.country_id}`} · {p.service_name || `#${p.service_id}`}</div>
                    </div>
                    {p.last_test_result === "ok" && <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200 shrink-0">OK</span>}
                    {p.last_test_result === "fail" && <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-red-50 text-red-700 border border-red-200 shrink-0">Fail</span>}
                  </button>
                );
              })}
              {!smsProfilesQ.data?.length && (
                <div className="p-3 text-xs text-zinc-400">
                  Chưa có profile. <a className="text-blue-600 underline" href="/library?tab=sms">Tạo profile SMS</a>
                </div>
              )}
            </div>
          </Card>

          {/* Advanced: Email + Proxy */}
          <Card className="p-4">
            <button
              className="flex items-center justify-between w-full text-sm font-medium text-zinc-700"
              onClick={() => setShowAdvanced((v) => !v)}
            >
              <span>Nâng cao (Email / Proxy)</span>
              {showAdvanced ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            </button>
            {showAdvanced && (
              <div className="mt-3 space-y-4">
                <div>
                  <SectionLabel>Email (tuỳ chọn)</SectionLabel>
                  <MultiCheckList items={emailItems} selected={selectedEmails}
                    onToggle={(id) => toggleItem(selectedEmails, setSelectedEmails, id)}
                    emptyText="Chưa có email." />
                </div>
                <div>
                  <SectionLabel>Proxy (tuỳ chọn)</SectionLabel>
                  <MultiCheckList items={proxyItems} selected={selectedProxies}
                    onToggle={(id) => toggleItem(selectedProxies, setSelectedProxies, id)}
                    emptyText="Chưa có proxy." />
                </div>
              </div>
            )}
          </Card>

          {/* Run config */}
          <Card className="p-4 space-y-4">
            <SectionLabel>Cấu hình chạy</SectionLabel>

            {/* Mode */}
            <div className="space-y-1.5">
              <p className="text-xs font-medium text-zinc-500 uppercase tracking-wide">Chế độ</p>
              <div className="grid grid-cols-2 gap-2">
                <button
                  onClick={() => handleModeChange("script_llm")}
                  className={`text-left px-3 py-2 rounded border text-xs transition-colors ${
                    runMode === "script_llm" ? "border-blue-500 bg-blue-50 text-blue-800" : "border-zinc-200 text-zinc-600 hover:bg-zinc-50"
                  }`}
                >
                  <div className="font-semibold">Script + Q&A + AI</div>
                  <div className="text-zinc-500 mt-0.5">3-tier, mặc định</div>
                </button>
                <button
                  onClick={() => handleModeChange("script")}
                  className={`text-left px-3 py-2 rounded border text-xs transition-colors ${
                    runMode === "script" ? "border-blue-500 bg-blue-50 text-blue-800" : "border-zinc-200 text-zinc-600 hover:bg-zinc-50"
                  }`}
                >
                  <div className="font-semibold">Script + Q&A thuần</div>
                  <div className="text-zinc-500 mt-0.5">$0, không gọi LLM</div>
                </button>
              </div>
            </div>

            {/* Tier 3 behavior */}
            {runMode === "script_llm" && (
              <div className="space-y-1.5">
                <p className="text-xs font-medium text-zinc-500 uppercase tracking-wide">Khi cần Tier 3 (~$0.15/run)</p>
                <div className="space-y-1">
                  {(
                    [
                      { val: "ask",   label: "Hỏi trước khi chạy",  desc: "Mặc định" },
                      { val: "auto",  label: "Tự động cho phép",     desc: "Có phí" },
                      { val: "never", label: "Không bao giờ chạy",   desc: "Bỏ qua" },
                    ] as { val: Tier3Behavior; label: string; desc: string }[]
                  ).map(({ val, label, desc }) => (
                    <label key={val} className="flex items-center gap-2 cursor-pointer group">
                      <input
                        type="radio"
                        name="tier3behavior"
                        checked={tier3Behavior === val}
                        onChange={() => setTier3Behavior(val)}
                        className="accent-blue-600"
                      />
                      <span className="text-sm text-zinc-700 group-hover:text-zinc-900">{label}</span>
                      <span className="text-xs text-zinc-400">{desc}</span>
                    </label>
                  ))}
                </div>
              </div>
            )}

            {/* LLM API Key — chỉ cần khi script_llm mode */}
            {runMode === "script_llm" && (
              <div className="space-y-1.5">
                <p className="text-xs font-medium text-zinc-500 uppercase tracking-wide flex items-center gap-1">
                  <KeyRound size={11} /> API Key LLM
                </p>
                <div className="flex gap-1.5 flex-wrap">
                  {([
                    { value: "" as const, label: "Tự động" },
                    { value: "gemini" as const, label: "Gemini" },
                    { value: "openai" as const, label: "ChatGPT" },
                    { value: "deepseek" as const, label: "DeepSeek" },
                  ]).map((opt) => (
                    <button
                      key={opt.value}
                      type="button"
                      onClick={() => { setSelectedLlmProvider(opt.value); setSelectedLlmKeyIndex(0); }}
                      className={`px-2.5 py-1 rounded border text-xs font-medium transition ${
                        selectedLlmProvider === opt.value
                          ? "bg-blue-500 text-white border-blue-500"
                          : "bg-white text-zinc-600 border-zinc-300 hover:border-blue-400"
                      }`}
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>
                {selectedLlmProvider && (
                  <select
                    value={selectedLlmKeyIndex}
                    onChange={(e) => setSelectedLlmKeyIndex(Number(e.target.value || 0))}
                    className="w-full border rounded px-3 py-1.5 text-sm"
                  >
                    <option value={0}>Tự động xoay key</option>
                    {(llmKeysQ[selectedLlmProvider]?.data?.items || []).map((item) => (
                      <option key={item.index} value={item.index}>
                        Key #{item.index} — {item.masked}
                      </option>
                    ))}
                  </select>
                )}
              </div>
            )}

            <label className="flex items-center gap-2 text-sm cursor-pointer">
              <input type="checkbox" checked={headless} onChange={(e) => setHeadless(e.target.checked)} />
              Chạy ẩn (headless)
            </label>
          </Card>

          {/* Submit */}
          <Button onClick={() => createJobMut.mutate()} disabled={!canSubmit} loading={createJobMut.isPending} className="w-full">
            <Play size={14} />
            Chạy Script ({selectedPrograms.length} chọn)
          </Button>
          {selectedProfiles.length === 0 && (
            <p className="text-xs text-amber-600 text-center">Hãy chọn ít nhất 1 profile</p>
          )}
        </div>

        {/* ── Right: program list ───────────────────────────────── */}
        <div className="xl:col-span-2 space-y-3">
          {/* Search + actions */}
          <div className="flex items-center gap-2">
            <div className="relative flex-1">
              <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-400" />
              <input
                className="w-full border rounded pl-8 pr-3 py-2 text-sm"
                placeholder="Tìm theo tên, category..."
                value={fSearch}
                onChange={(e) => setFSearch(e.target.value)}
              />
            </div>
            <Button variant="secondary" size="sm" onClick={selectAllRunnable}>
              Chọn {canRunIds.size}
            </Button>
            <Button variant="secondary" size="sm" onClick={clearSelection}>Bỏ chọn</Button>
          </div>

          {/* Stats */}
          <div className="flex flex-wrap gap-3 text-xs text-zinc-500">
            {stats.t1override > 0 && (
              <span className="flex items-center gap-1.5">
                <span className="w-2 h-2 rounded-full bg-violet-500 inline-block" />
                T1*: {stats.t1override}
              </span>
            )}
            <span className="flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full bg-emerald-500 inline-block" />
              ✅ T1: {stats.t1}
            </span>
            <span className="flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full bg-indigo-400 inline-block" />
              ✅~ T1~: {stats.t1fallback}
            </span>
            <span className="flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full bg-blue-400 inline-block" />
              🆕 T3: {stats.t3}
            </span>
            <span className="flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full bg-zinc-300 inline-block" />
              Skip: {stats.skip}
            </span>
            {pbStatusQ.isFetching && <RefreshCw size={11} className="animate-spin text-zinc-400" />}
          </div>

          {/* Program list */}
          <div className="space-y-1.5 max-h-[540px] overflow-y-auto pr-1">
            {itemsQ.isLoading ? (
              <div className="text-center py-10 text-zinc-400">
                <RefreshCw size={20} className="animate-spin mx-auto mb-2" />
                Đang tải...
              </div>
            ) : programs.length === 0 ? (
              <EmptyState icon={ScrollText} title="Không có chương trình" description="Shortlist này chưa có chương trình nào." />
            ) : (
              programs.map((p) => {
                const pb = pbStatus[p.id];
                const runnable = canRunIds.has(p.id);
                const checked = selectedPrograms.includes(p.id);
                const platform = detectPlatform(p.signup_url || p.url || "", p.directory_network);
                const qaCount = platform ? (qaCountByPlatform[platform] || 0) : 0;
                const badge = getScriptBadge(pb);
                const tier = getTierBadge(pb, !!selectedPlaybookId, runMode, tier3Behavior);

                return (
                  <div
                    key={p.id}
                    className={`px-3 py-2.5 rounded-lg border transition ${
                      checked
                        ? "border-blue-400 bg-blue-50"
                        : runnable && selectedPlaybookId
                        ? "border-violet-200 bg-violet-50/30 hover:bg-violet-50/60"
                        : runnable && !pb?.is_fallback && pb?.status === "active"
                        ? "border-emerald-200 bg-emerald-50/40 hover:bg-emerald-50"
                        : runnable && pb
                        ? "border-indigo-200 bg-indigo-50/30 hover:bg-indigo-50/60"
                        : runnable
                        ? "border-blue-100 bg-blue-50/20 hover:bg-blue-50/40"
                        : "border-zinc-200 opacity-60"
                    }`}
                  >
                    <div className="flex items-start gap-3">
                      <input
                        type="checkbox"
                        className="mt-0.5 shrink-0 cursor-pointer"
                        checked={checked}
                        onChange={() => toggleProgram(p.id)}
                        disabled={!runnable}
                      />
                      <div className="flex-1 min-w-0">
                        {/* Program name + category */}
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="text-sm font-medium">{p.name}</span>
                          {p.category && <span className="text-xs text-zinc-400">{p.category}</span>}
                          {platform && <span className="text-xs text-zinc-300">· {platform}</span>}
                        </div>

                        {/* Script badge + tier + override indicator */}
                        <div className="mt-1 flex flex-wrap items-center gap-2">
                          {/* Auto-matched script */}
                          <span className={`text-xs font-medium rounded px-1.5 py-0.5 ${badge.cls}`}>
                            {badge.icon} {pb ? pb.name.slice(0, 28) : "Chưa có script"}
                          </span>

                          {/* Global override arrow */}
                          {selectedPlaybookId && selectedPlaybook && (
                            <span className="text-xs text-violet-700 bg-violet-50 rounded px-1.5 py-0.5 font-medium">
                              → {selectedPlaybook.name.slice(0, 22)}
                            </span>
                          )}

                          {/* Q&A count */}
                          {qaCount > 0 && (
                            <span className="text-xs text-zinc-500 bg-zinc-100 rounded px-1.5 py-0.5">
                              {qaCount}Q
                            </span>
                          )}

                          {/* Tier badge */}
                          <span className={`text-xs font-semibold rounded px-1.5 py-0.5 ${TIER_COLORS[tier] || TIER_COLORS.Skip}`}>
                            [{tier}]
                          </span>

                          {/* Success rate */}
                          {pb?.success_rate != null && (
                            <span className="text-xs text-zinc-400">
                              {pb.success_rate}%
                              {pb.consecutive_fails > 0 && ` · ${pb.consecutive_fails} fail`}
                            </span>
                          )}
                        </div>
                      </div>

                      {p.signup_url && (
                        <a href={p.signup_url} target="_blank" rel="noopener noreferrer"
                          onClick={(e) => e.stopPropagation()}
                          className="text-zinc-400 hover:text-zinc-600 shrink-0 mt-0.5">
                          <ExternalLink size={13} />
                        </a>
                      )}
                    </div>
                  </div>
                );
              })
            )}
          </div>

          {selectedPrograms.length > 0 && (
            <p className="text-xs text-blue-600 font-medium">
              Đã chọn {selectedPrograms.length} chương trình
              {selectedPlaybook && (
                <span className="text-violet-600"> · Script: {selectedPlaybook.name}</span>
              )}
            </p>
          )}
        </div>
      </div>

      {/* ── Job history ─────────────────────────────────────────── */}
      <div className="space-y-3 pt-2 border-t">
        <h2 className="font-semibold text-base flex items-center gap-2">
          <ScrollText size={16} />
          Lịch sử Script
          <button onClick={() => qc.invalidateQueries({ queryKey: ["signup-jobs"] })} className="text-zinc-400 hover:text-zinc-600 ml-auto">
            <RefreshCw size={14} />
          </button>
        </h2>

        {scriptJobs.length === 0 ? (
          <p className="text-sm text-zinc-400">Chưa có job script nào.</p>
        ) : (
          scriptJobs.slice(0, 20).map((job) => {
            const st = JOB_STATUS[job.status] || JOB_STATUS.pending;
            const expanded = expandedJobs.has(job.id);
            const hasWaiting = job.tier3_status === "waiting_approval";

            return (
              <Card key={job.id} className="p-4">
                <div
                  className="flex items-center justify-between cursor-pointer"
                  onClick={() => {
                    setExpandedJobs((prev) => {
                      const n = new Set(prev);
                      n.has(job.id) ? n.delete(job.id) : n.add(job.id);
                      return n;
                    });
                  }}
                >
                  <div className="flex items-center gap-3 flex-wrap">
                    <Badge variant={st.variant}>{st.label}</Badge>
                    {hasWaiting && <Badge variant="info"><Zap size={10} className="mr-0.5" />Chờ duyệt T3</Badge>}
                    <span className="text-sm font-medium">Job #{job.id}</span>
                    <span className="text-xs text-zinc-500">✅ {job.succeeded} / {job.total} &nbsp; ❌ {job.failed}</span>
                    {job.started_at && (
                      <span className="text-xs text-zinc-400">{new Date(job.started_at).toLocaleString("vi-VN")}</span>
                    )}
                  </div>
                  {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                </div>

                {hasWaiting && (
                  <div className="mt-2">
                    <Tier3Banner
                      job={job}
                      onApprove={() => approveTier3Mut.mutate(job.id)}
                      onSkip={() => skipTier3Mut.mutate(job.id)}
                    />
                  </div>
                )}

                {expanded && (
                  <div className="mt-3 space-y-1.5 max-h-64 overflow-y-auto">
                    {(job.results || []).map((r, i) => {
                      const rs = JOB_STATUS[r.status] || { variant: "neutral" as const, label: r.status };
                      return (
                        <div key={i} className="flex items-start gap-2 text-xs">
                          <Badge variant={rs.variant} className="shrink-0">{rs.label}</Badge>
                          <span className="font-medium shrink-0">{r.program_id}</span>
                          <span className="text-zinc-500 truncate">{r.message}</span>
                        </div>
                      );
                    })}
                    {!job.results?.length && <p className="text-xs text-zinc-400">Chưa có kết quả</p>}
                  </div>
                )}
              </Card>
            );
          })
        )}
      </div>
    </div>
  );
}
