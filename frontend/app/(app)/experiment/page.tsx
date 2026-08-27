"use client";
import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  FlaskConical, Send, StopCircle, Clock, CheckCircle2,
  AlertCircle, Loader2, ChevronDown, ChevronUp, Bot, User,
  Image as ImageIcon, Code2, Eye, Sparkles, RefreshCw, Zap,
  FileText, Download, Monitor,
} from "lucide-react";
import * as api from "@/lib/api";
import type { ExperimentJob } from "@/lib/api";
import { Badge } from "@/components/Badge";
import { Button } from "@/components/Button";
import { useToast } from "@/lib/toast";

// ── Helpers ──────────────────────────────────────────────────────────────────
function statusVariant(s: ExperimentJob["status"]) {
  if (s === "done") return "success";
  if (s === "error") return "error";
  if (s === "cancelled") return "neutral";
  return "info";
}

function statusLabel(s: ExperimentJob["status"]) {
  if (s === "running") return "Đang chạy…";
  if (s === "done") return "Hoàn thành";
  if (s === "error") return "Lỗi";
  if (s === "cancelled") return "Đã hủy";
  return s;
}

// ── Markdown Result Renderer ─────────────────────────────────────────────────
function exportAsPDF(content: string) {
  const win = window.open("", "_blank");
  if (!win) return;
  win.document.write(`
    <!DOCTYPE html><html><head>
    <meta charset="utf-8"><title>Kết quả thử nghiệm</title>
    <style>
      body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 800px; margin: 40px auto; line-height: 1.7; color: #1a1a2e; padding: 0 20px; }
      h1,h2,h3 { color: #1a1a2e; } pre { background:#f4f4f8; padding:12px; border-radius:8px; overflow-x:auto; }
      code { background:#f0f0f8; padding:2px 5px; border-radius:4px; font-size:0.9em; }
      table { border-collapse:collapse; width:100%; } th,td { border:1px solid #ddd; padding:8px 12px; }
      @media print { body { margin: 20px; } }
    </style></head><body>
    <pre style="white-space:pre-wrap;font-family:inherit">${content.replace(/</g, "&lt;").replace(/>/g, "&gt;")}</pre>
    <script>window.onload=()=>window.print();<\/script></body></html>
  `);
  win.document.close();
}

function exportAsWord(content: string) {
  const htmlContent = `
    <html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:w="urn:schemas-microsoft-com:office:word" xmlns="http://www.w3.org/TR/REC-html40">
    <head><meta charset="utf-8"><title>Kết quả thử nghiệm</title>
    <style>body{font-family:'Times New Roman',serif;font-size:12pt;line-height:1.6;}</style>
    </head><body><pre style="font-family:inherit;white-space:pre-wrap">${content.replace(/</g, "&lt;").replace(/>/g, "&gt;")}</pre></body></html>
  `;
  const blob = new Blob([htmlContent], { type: "application/msword" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = `ket-qua-${Date.now()}.doc`;
  a.click(); URL.revokeObjectURL(url);
}

function MarkdownResult({ content, isError }: { content: string; isError?: boolean }) {
  const [rawMode, setRawMode] = useState(false);
  return (
    <div className="flex-1 min-h-0 flex flex-col">
      {/* Toolbar */}
      <div className="flex items-center gap-1.5 mb-3 shrink-0">
        <div className="flex items-center bg-gray-100 rounded-lg p-0.5">
          <button
            onClick={() => setRawMode(false)}
            className={`flex items-center gap-1 text-[11px] px-2.5 py-1 rounded-md transition-all ${
              !rawMode ? "bg-white text-violet-700 font-semibold shadow-sm" : "text-gray-500 hover:text-gray-700"
            }`}
          >
            <Eye size={10} /> Preview
          </button>
          <button
            onClick={() => setRawMode(true)}
            className={`flex items-center gap-1 text-[11px] px-2.5 py-1 rounded-md transition-all ${
              rawMode ? "bg-white text-gray-700 font-semibold shadow-sm" : "text-gray-500 hover:text-gray-700"
            }`}
          >
            <Code2 size={10} /> Raw
          </button>
        </div>
        <div className="flex-1" />
        <button
          onClick={() => exportAsPDF(content)}
          title="Xuất PDF"
          className="flex items-center gap-1.5 text-[11px] px-2.5 py-1.5 rounded-lg bg-red-50 text-red-600 hover:bg-red-100 border border-red-100 transition-colors font-medium"
        >
          <Download size={10} /> PDF
        </button>
        <button
          onClick={() => exportAsWord(content)}
          title="Xuất Word"
          className="flex items-center gap-1.5 text-[11px] px-2.5 py-1.5 rounded-lg bg-blue-50 text-blue-600 hover:bg-blue-100 border border-blue-100 transition-colors font-medium"
        >
          <FileText size={10} /> Word
        </button>
      </div>

      {/* Content */}
      {rawMode ? (
        <pre className={`flex-1 overflow-y-auto rounded-xl border p-4 text-xs leading-relaxed whitespace-pre-wrap font-mono ${
          isError ? "bg-red-50/60 border-red-100 text-red-700" : "bg-gray-50 border-gray-100 text-gray-700"
        }`}>
          {content}
        </pre>
      ) : (
        <div className={`flex-1 overflow-y-auto rounded-xl border p-5 ${
          isError ? "bg-red-50/30 border-red-100" : "bg-white border-violet-50"
        } prose prose-sm max-w-none
          prose-headings:text-ink prose-headings:font-bold prose-headings:mt-4 prose-headings:mb-2
          prose-h1:text-lg prose-h2:text-base prose-h3:text-sm
          prose-p:text-gray-700 prose-p:leading-7 prose-p:my-2
          prose-a:text-violet-600 prose-a:font-medium prose-a:no-underline hover:prose-a:underline
          prose-strong:text-ink prose-strong:font-semibold
          prose-code:text-violet-700 prose-code:bg-violet-50 prose-code:px-1.5 prose-code:py-0.5 prose-code:rounded-md prose-code:text-[0.82em] prose-code:font-medium
          prose-pre:bg-gray-900 prose-pre:text-gray-100 prose-pre:rounded-xl prose-pre:shadow-inner
          prose-blockquote:border-l-4 prose-blockquote:border-violet-300 prose-blockquote:bg-violet-50/50 prose-blockquote:text-gray-600 prose-blockquote:pl-4 prose-blockquote:rounded-r-lg
          prose-ul:my-2 prose-li:my-0.5
          prose-table:w-full prose-thead:bg-gray-50 prose-th:border prose-th:border-gray-200 prose-th:px-3 prose-th:py-2 prose-td:border prose-td:border-gray-100 prose-td:px-3 prose-td:py-1.5
          prose-hr:border-gray-100`}
        >
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
        </div>
      )}
    </div>
  );
}

function formatDuration(sec: number | null) {
  if (!sec) return "";
  if (sec < 60) return `${sec.toFixed(0)}s`;
  return `${Math.floor(sec / 60)}m ${Math.round(sec % 60)}s`;
}

const SUGGESTED_TASKS = [
  "Tìm top 5 công ty AI đang hot nhất hiện nay và tóm tắt sản phẩm của họ",
  "Vào https://news.ycombinator.com và lấy 5 tin hàng đầu hôm nay",
  "Search Google 'best affiliate programs 2025' và liệt kê 10 kết quả đầu tiên",
  "Kiểm tra xem trang web https://lovable.dev có đang hoạt động không, chụp ảnh màn hình",
  "Vào https://github.com/trending và lấy 5 repo trending nhất tuần này",
];

// ── Job Card Component ────────────────────────────────────────────────────────
function JobCard({
  job,
  isActive,
  onSelect,
}: {
  job: ExperimentJob;
  isActive: boolean;
  onSelect: () => void;
}) {
  const isRunning = job.status === "running";
  return (
    <button
      onClick={onSelect}
      className={`w-full text-left px-3 py-2.5 rounded-xl transition-all group ${
        isActive
          ? "bg-violet-600 shadow-md"
          : "hover:bg-gray-100 border border-transparent hover:border-gray-200"
      }`}
    >
      <div className="flex items-center gap-1.5 mb-1">
        {isRunning && <Loader2 size={10} className={`animate-spin shrink-0 ${isActive ? "text-violet-200" : "text-violet-500"}`} />}
        {!isRunning && job.status === "done" && <CheckCircle2 size={10} className={`shrink-0 ${isActive ? "text-green-300" : "text-emerald-500"}`} />}
        {!isRunning && job.status === "error" && <AlertCircle size={10} className={`shrink-0 ${isActive ? "text-red-300" : "text-red-400"}`} />}
        <span className={`text-[10px] font-mono ml-auto ${isActive ? "text-violet-200" : "text-gray-300"}`}>#{job.id}</span>
      </div>
      <p className={`text-[12px] leading-snug line-clamp-2 ${isActive ? "text-white font-medium" : "text-gray-700"}`}>{job.task}</p>
      {job.duration_sec && (
        <p className={`text-[10px] mt-1 flex items-center gap-1 ${isActive ? "text-violet-200" : "text-gray-400"}`}>
          <Clock size={8} /> {formatDuration(job.duration_sec)} · {job.steps}b
        </p>
      )}
    </button>
  );
}

// ── Result Panel ──────────────────────────────────────────────────────────────
function ResultPanel({ job, onCancel }: { job: ExperimentJob; onCancel: () => void }) {
  const [showScreenshot, setShowScreenshot] = useState(false);
  const isRunning = job.status === "running";

  return (
    <div className="flex flex-col h-full gap-4">
      {/* Meta bar */}
      <div className="flex items-center gap-3 shrink-0 pb-3 border-b border-gray-100">
        <div className="flex items-center gap-2 flex-1 min-w-0 flex-wrap">
          <Badge variant={statusVariant(job.status)} className="gap-1 shrink-0">
            {isRunning && <Loader2 size={11} className="animate-spin" />}
            {!isRunning && job.status === "done" && <CheckCircle2 size={11} />}
            {!isRunning && job.status === "error" && <AlertCircle size={11} />}
            {statusLabel(job.status)}
          </Badge>
          {job.duration_sec && (
            <span className="text-xs text-gray-400 flex items-center gap-1"><Clock size={11} /> {formatDuration(job.duration_sec)}</span>
          )}
          {job.steps > 0 && (
            <span className="text-xs text-gray-400 flex items-center gap-1"><Zap size={11} /> {job.steps} bước</span>
          )}
          <span className="text-xs text-gray-300 font-mono">#{job.id} · {job.model}</span>
        </div>
        {isRunning && (
          <Button variant="danger" size="sm" onClick={onCancel} className="gap-1.5 shrink-0">
            <StopCircle size={13} /> Dừng
          </Button>
        )}
      </div>

      {/* User task bubble */}
      <div className="shrink-0 flex gap-3">
        <div className="w-7 h-7 rounded-full bg-gray-100 flex items-center justify-center shrink-0 mt-0.5">
          <User size={13} className="text-gray-500" />
        </div>
        <div className="flex-1 bg-gray-50 border border-gray-100 rounded-2xl rounded-tl-sm px-4 py-3 text-sm text-gray-800 leading-relaxed">
          {job.task}
        </div>
      </div>

      {/* Running state */}
      {isRunning && (
        <div className="flex gap-3 items-start">
          <div className="w-7 h-7 rounded-full bg-violet-100 flex items-center justify-center shrink-0 mt-0.5">
            <Bot size={13} className="text-violet-500" />
          </div>
          <div className="flex items-center gap-2 pt-2.5 text-sm text-gray-400">
            <span className="w-2 h-2 rounded-full bg-violet-400 animate-bounce [animation-delay:0ms]" />
            <span className="w-2 h-2 rounded-full bg-violet-400 animate-bounce [animation-delay:150ms]" />
            <span className="w-2 h-2 rounded-full bg-violet-400 animate-bounce [animation-delay:300ms]" />
            <span className="text-xs ml-1">Bước {job.steps + 1}…</span>
          </div>
        </div>
      )}

      {/* Agent result bubble */}
      {!isRunning && job.result && (
        <div className="flex-1 min-h-0 flex gap-3">
          <div className="w-7 h-7 rounded-full bg-violet-100 flex items-center justify-center shrink-0 mt-0.5">
            <Bot size={13} className="text-violet-500" />
          </div>
          <div className="flex-1 min-h-0 flex flex-col">
            <MarkdownResult content={job.result} isError={job.status === "error"} />
          </div>
        </div>
      )}

      {/* Screenshot */}
      {job.screenshot && (
        <div className="shrink-0 pl-10">
          <button
            onClick={() => setShowScreenshot(v => !v)}
            className="flex items-center gap-1.5 text-xs text-gray-400 hover:text-violet-600 transition-colors mb-2"
          >
            <ImageIcon size={12} />
            {showScreenshot ? "Ẩn ảnh" : "Xem ảnh màn hình"}
            {showScreenshot ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
          </button>
          {showScreenshot && (
            <div className="rounded-xl overflow-hidden border border-gray-100 shadow-sm max-w-2xl">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={job.screenshot} alt="Agent screenshot" className="w-full object-contain max-h-96" />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────────────
export default function ExperimentPage() {
  const { push } = useToast();
  const qc = useQueryClient();
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const [task, setTask] = useState("");
  const [model, setModel] = useState("gemini");
  const [maxSteps, setMaxSteps] = useState(30);
  const [activeJobId, setActiveJobId] = useState<number | null>(null);
  const [showSuggestions, setShowSuggestions] = useState(false);

  // List past jobs
  const { data: jobs = [], refetch: refetchJobs } = useQuery({
    queryKey: ["experiment-jobs"],
    queryFn: () => api.listExperimentJobs(50),
    refetchInterval: 3000,
  });

  // Poll active job
  const { data: activeJob } = useQuery({
    queryKey: ["experiment-job", activeJobId],
    queryFn: () => api.getExperimentJob(activeJobId!),
    enabled: activeJobId !== null,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "running" ? 1500 : false;
    },
  });

  // Sync active job into list
  useEffect(() => {
    if (activeJob) {
      qc.setQueryData(["experiment-jobs"], (old: ExperimentJob[] = []) => {
        const idx = old.findIndex((j) => j.id === activeJob.id);
        if (idx >= 0) {
          const copy = [...old];
          copy[idx] = activeJob;
          return copy;
        }
        return [activeJob, ...old];
      });
    }
  }, [activeJob, qc]);

  // Run experiment
  const run = useMutation({
    mutationFn: () => api.runExperiment(task.trim(), model, maxSteps),
    onSuccess: (r) => {
      setActiveJobId(r.job_id);
      setTask("");
      setShowSuggestions(false);
      refetchJobs();
    },
    onError: (e: any) =>
      push({ type: "error", title: "Không thể chạy", message: e.message }),
  });

  // Cancel
  const cancel = useMutation({
    mutationFn: (id: number) => api.cancelExperimentJob(id),
    onSuccess: () => {
      push({ type: "info", message: "Đã gửi lệnh dừng" });
      qc.invalidateQueries({ queryKey: ["experiment-job", activeJobId] });
      refetchJobs();
    },
    onError: (e: any) =>
      push({ type: "error", title: "Không thể dừng", message: e.message }),
  });

  const handleSubmit = () => {
    if (!task.trim() || run.isPending) return;
    run.mutate();
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const displayJob = activeJob ?? (activeJobId ? jobs.find((j) => j.id === activeJobId) : null);
  const runningJobIds = jobs.filter((j) => j.status === "running").map((j) => j.id);

  return (
    <div className="flex flex-col h-[calc(100vh-64px)] max-w-[1400px] mx-auto px-4 py-4 gap-3">
      {/* Header */}
      <div className="shrink-0 flex items-center gap-3">
        <div className="w-9 h-9 rounded-xl bg-violet-100 flex items-center justify-center">
          <FlaskConical size={18} className="text-violet-600" />
        </div>
        <div>
          <h1 className="text-base font-bold text-ink leading-none">Browser Agent</h1>
          <p className="text-xs text-gray-400 mt-0.5">Chạy agent AI điều khiển trình duyệt tự động</p>
        </div>
        <div className="flex-1" />
        {runningJobIds.length > 0 && (
          <div className="flex items-center gap-2 text-xs text-amber-600 bg-amber-50 border border-amber-200 px-3 py-1.5 rounded-full font-medium">
            <Loader2 size={11} className="animate-spin" />
            {runningJobIds.length} job đang chạy
          </div>
        )}
      </div>

      {/* Browser warning banner */}
      <div className="shrink-0 flex items-start gap-2.5 bg-amber-50 border border-amber-200 rounded-xl px-4 py-2.5 text-xs text-amber-700">
        <Monitor size={14} className="shrink-0 mt-0.5 text-amber-600" />
        <span>
          <strong>Lưu ý:</strong> Mỗi tác vụ sẽ <strong>mở một cửa sổ trình duyệt thật</strong> trên màn hình (CloakBrowser). Đây là browser automation agent, không phải chat AI thông thường.
        </span>
      </div>

      <div className="flex gap-3 flex-1 min-h-0">
        {/* Left: History panel */}
        <div className="w-64 shrink-0 bg-gray-50 rounded-2xl border border-gray-100 flex flex-col min-h-0">
          <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100 shrink-0">
            <span className="text-[11px] font-bold text-gray-400 uppercase tracking-widest">
              Lịch sử ({jobs.length})
            </span>
            <button
              aria-label="Làm mới"
              onClick={() => refetchJobs()}
              className="p-1 rounded-md text-gray-400 hover:text-ink hover:bg-white transition-colors"
            >
              <RefreshCw size={12} />
            </button>
          </div>
          <div className="flex-1 overflow-y-auto p-2 space-y-1 min-h-0">
            {jobs.length === 0 && (
              <div className="text-center py-12 text-gray-300">
                <Bot size={28} className="mx-auto mb-2 opacity-50" />
                <p className="text-[11px]">Chưa có job nào</p>
              </div>
            )}
            {jobs.map((j) => (
              <JobCard
                key={j.id}
                job={j}
                isActive={j.id === activeJobId}
                onSelect={() => setActiveJobId(j.id)}
              />
            ))}
          </div>
        </div>

        {/* Right: Main area */}
        <div className="flex-1 min-w-0 flex flex-col gap-2 min-h-0">
          {/* Conversation / empty state */}
          <div className="flex-1 min-h-0 bg-white rounded-2xl border border-gray-100 shadow-sm overflow-y-auto p-5">
            {displayJob ? (
              <ResultPanel
                job={displayJob}
                onCancel={() => cancel.mutate(displayJob.id)}
              />
            ) : (
              <div className="h-full flex flex-col items-center justify-center text-gray-300 gap-4">
                <div className="w-14 h-14 rounded-2xl bg-violet-50 flex items-center justify-center">
                  <FlaskConical size={28} className="text-violet-300" />
                </div>
                <div className="text-center">
                  <p className="text-sm font-medium text-gray-400">Chưa có tác vụ nào</p>
                  <p className="text-xs text-gray-300 mt-1">Nhập tác vụ bên dưới và nhấn Ctrl+Enter</p>
                </div>
                <div className="mt-2 grid grid-cols-2 gap-2 max-w-lg w-full">
                  {SUGGESTED_TASKS.slice(0, 4).map((s, i) => (
                    <button
                      key={i}
                      onClick={() => { setTask(s); textareaRef.current?.focus(); }}
                      className="text-left text-[11px] px-3 py-2.5 rounded-xl border border-violet-100 bg-violet-50/50 text-violet-600 hover:bg-violet-100 hover:border-violet-200 transition-colors leading-snug line-clamp-2"
                    >
                      <Sparkles size={9} className="inline mr-1 opacity-60" />{s}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* Input dock */}
          <div className="shrink-0 bg-white rounded-2xl border border-gray-100 shadow-sm px-4 py-3">
            <textarea
              ref={textareaRef}
              rows={2}
              value={task}
              onChange={(e) => setTask(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={`Nhập tác vụ cho agent… (Ctrl+Enter để gửi)\nVí dụ: "Tìm top 5 AI startup mới nhất và tóm tắt"`}
              aria-label="Tác vụ cho browser agent"
              className="w-full text-sm bg-transparent resize-none focus:outline-none leading-relaxed text-gray-800 placeholder:text-gray-300 mb-2"
            />

            {/* Suggestion chips */}
            <div className="flex items-center gap-1.5 overflow-x-auto mb-2.5 scrollbar-none">
              <button
                onClick={() => setShowSuggestions((v) => !v)}
                className={`shrink-0 flex items-center gap-1 text-[11px] px-2.5 py-1 rounded-full border transition-colors ${
                  showSuggestions
                    ? "bg-violet-100 border-violet-300 text-violet-700"
                    : "bg-gray-50 border-gray-200 text-gray-500 hover:border-violet-200 hover:text-violet-600"
                }`}
              >
                <Sparkles size={9} /> Gợi ý
              </button>
              {showSuggestions && SUGGESTED_TASKS.map((s, i) => (
                <button
                  key={i}
                  onClick={() => { setTask(s); setShowSuggestions(false); textareaRef.current?.focus(); }}
                  className="shrink-0 text-[11px] px-2.5 py-1 rounded-full border border-gray-200 bg-gray-50 text-gray-600 hover:bg-violet-50 hover:border-violet-200 hover:text-violet-700 transition-colors whitespace-nowrap"
                >
                  {s.length > 50 ? s.slice(0, 50) + "…" : s}
                </button>
              ))}
            </div>

            {/* Controls */}
            <div className="flex items-center gap-3 pt-2.5 border-t border-gray-50">
              <div className="flex items-center gap-1.5">
                <span className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold">Model</span>
                <select
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                  aria-label="Chọn LLM model"
                  className="text-xs bg-gray-50 border border-gray-200 rounded-lg px-2 py-1 focus:outline-none focus:ring-2 focus:ring-violet-400/30 text-gray-700"
                >
                  <option value="gemini">Gemini 2.0 Flash</option>
                  <option value="openai">GPT-4o (OpenAI)</option>
                  <option value="deepseek">DeepSeek Chat</option>
                </select>
              </div>
              <div className="flex items-center gap-1.5">
                <span className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold">Max</span>
                <input
                  type="number" min={5} max={60} aria-label="Max steps"
                  value={maxSteps}
                  onChange={(e) => setMaxSteps(Number(e.target.value))}
                  className="w-14 text-xs bg-gray-50 border border-gray-200 rounded-lg px-2 py-1 focus:outline-none focus:ring-2 focus:ring-violet-400/30 text-center text-gray-700"
                />
              </div>
              <div className="flex-1" />
              <span className="text-[10px] text-gray-300 hidden sm:block">Ctrl+Enter</span>
              <Button
                variant="cta"
                size="sm"
                disabled={!task.trim() || run.isPending}
                onClick={handleSubmit}
                className="gap-2 bg-violet-600 hover:bg-violet-700 px-4"
              >
                {run.isPending ? (
                  <><Loader2 size={13} className="animate-spin" /> Đang gửi…</>
                ) : (
                  <><Send size={13} /> Chạy Agent</>
                )}
              </Button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
