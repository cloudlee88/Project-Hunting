"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  getSignupHistory,
  exportSignupHistoryUrl,
  SignupHistoryItem,
  SignupHistoryResponse,
  SignupHistoryStats,
} from "@/lib/api";
import { PageHeader } from "@/components/PageHeader";
import { Card } from "@/components/Card";
import { Button } from "@/components/Button";
import { Badge } from "@/components/Badge";
import { Modal } from "@/components/Modal";
import { Pagination } from "@/components/Pagination";
import {
  CheckCircle2,
  XCircle,
  AlertTriangle,
  Clock,
  TrendingUp,
  Download,
  FileText,
  Search,
  Filter,
  ExternalLink,
  Image as ImageIcon,
  ChevronDown,
  RefreshCw,
  Square,
  CheckSquare,
  X as XIcon,
} from "lucide-react";

// ────────────────────────────────────────────────────────────
// Helpers
// ────────────────────────────────────────────────────────────

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

function screenshotUrl(filename: string) {
  if (!filename) return null;
  return `${API_BASE}/api/signup/screenshots/${filename}`;
}

async function fetchImgAsDataUrl(url: string | null): Promise<string | null> {
  if (!url) return null;
  try {
    const res = await fetch(url, { credentials: "include" });
    if (!res.ok) return null;
    const blob = await res.blob();
    return new Promise((resolve) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result as string);
      reader.onerror = () => resolve(null);
      reader.readAsDataURL(blob);
    });
  } catch {
    return null;
  }
}

function fmtDate(iso: string | null) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("vi-VN", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function fmtDuration(s: number | null) {
  if (s == null) return "—";
  if (s < 60) return `${s.toFixed(0)}s`;
  const m = Math.floor(s / 60);
  const rem = Math.round(s % 60);
  return `${m}m ${rem}s`;
}

const STATUS_META: Record<string, { label: string; variant: "success" | "warning" | "error" | "neutral"; icon: React.ReactNode }> = {
  success:        { label: "Thành công",   variant: "success", icon: <CheckCircle2 size={13} /> },
  pending_verify: { label: "Chờ duyệt",   variant: "warning", icon: <Clock size={13} /> },
  failed:         { label: "Thất bại",   variant: "error",   icon: <XCircle size={13} /> },
  error:          { label: "Lỗi",          variant: "error",   icon: <XCircle size={13} /> },
  captcha:        { label: "Captcha",    variant: "warning", icon: <AlertTriangle size={13} /> },
  running:        { label: "Đang chạy",  variant: "neutral", icon: <Clock size={13} /> },
  unknown:        { label: "Không rõ",   variant: "neutral", icon: <Clock size={13} /> },
};

function StatusBadge({ status }: { status: string }) {
  const meta = STATUS_META[status] || STATUS_META.unknown;
  return (
    <Badge variant={meta.variant}>
      <span className="flex items-center gap-1">{meta.icon}{meta.label}</span>
    </Badge>
  );
}

// ────────────────────────────────────────────────────────────
// Stat card
// ────────────────────────────────────────────────────────────

function StatCard({
  label,
  value,
  sub,
  icon,
  color,
}: {
  label: string;
  value: number | string;
  sub?: string;
  icon: React.ReactNode;
  color: string;
}) {
  return (
    <Card className="flex items-center gap-4 px-5 py-4">
      <div className={`w-11 h-11 rounded-xl flex items-center justify-center shrink-0 ${color}`}>
        {icon}
      </div>
      <div className="min-w-0">
        <p className="text-2xl font-bold text-ink leading-none">{value}</p>
        <p className="text-xs text-gray-500 mt-0.5">{label}</p>
        {sub && <p className="text-xs text-gray-400 mt-0.5">{sub}</p>}
      </div>
    </Card>
  );
}

// ────────────────────────────────────────────────────────────
// Detail Modal
// ────────────────────────────────────────────────────────────

function HistoryDetailModal({ item, onClose }: { item: SignupHistoryItem | null; onClose: () => void }) {
  if (!item) return null;
  const imgUrl = item.screenshot ? screenshotUrl(item.screenshot) : null;
  const snap = item.profile_snapshot || {};
  const hasRuntimeData = item.email_used || item.proxy_label || item.proxy_host || (item.instruction_names_used || []).length > 0;
  const hasFormData = snap.full_name || snap.phone || snap.website || snap.company || snap.country || (snap.niche || []).length > 0;

  return (
    <Modal open={!!item} onClose={onClose} title={`Chi tiết: ${item.program_name || "?"}`} size="2xl">
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 p-1 max-h-[75vh] overflow-y-auto pr-2">
        {/* ───── LEFT COLUMN ───── */}
        <div className="space-y-5 text-sm">

          {/* Chương trình */}
          <section className="bg-gray-50 rounded-xl p-4">
            <h3 className="font-semibold text-gray-800 mb-3 flex items-center gap-1.5">
              <span className="w-1.5 h-4 bg-blue-500 rounded-full inline-block" />
              Chương trình
            </h3>
            <dl className="space-y-2">
              <Row label="Tên" value={item.program_name} />
              <Row label="Nguồn" value={item.program_source} />
              <Row label="Hoa hồng" value={item.program_commission} />
              <Row label="URL đăng ký" value={item.program_signup_url} link />
              <Row label="Job ID" value={`#${item.job_id}`} />
            </dl>
          </section>

          {/* Thông tin thực tế dùng */}
          <section className="bg-amber-50 border border-amber-100 rounded-xl p-4">
            <h3 className="font-semibold text-amber-800 mb-3 flex items-center gap-1.5">
              <span className="w-1.5 h-4 bg-amber-500 rounded-full inline-block" />
              Dữ liệu thực tế dùng lần này
            </h3>
            {hasRuntimeData ? (
              <dl className="space-y-2">
                <Row label="Email dùng" value={item.email_used || "—"} />
                <Row
                  label="Proxy"
                  value={
                    item.proxy_label
                      ? `${item.proxy_label}${item.proxy_host ? ` (${item.proxy_host})` : ""}`
                      : item.proxy_host || item.proxy_url_used || "Không dùng"
                  }
                />
                <Row
                  label="Hướng dẫn"
                  value={
                    (item.instruction_names_used || []).length > 0
                      ? (item.instruction_names_used || []).join(", ")
                      : "—"
                  }
                />
              </dl>
            ) : (
              <p className="text-amber-600 text-xs italic">
                Dữ liệu này chỉ có ở các lần chạy mới (sau khi cập nhật hệ thống).
              </p>
            )}
          </section>

          {/* Dữ liệu điền form */}
          <section className="bg-green-50 border border-green-100 rounded-xl p-4">
            <h3 className="font-semibold text-green-800 mb-3 flex items-center gap-1.5">
              <span className="w-1.5 h-4 bg-green-500 rounded-full inline-block" />
              Dữ liệu agent điền vào form
            </h3>
            {hasFormData ? (
              <dl className="space-y-2">
                <Row label="Họ tên" value={snap.full_name || `${snap.ho || ""} ${snap.ten || ""}`.trim() || "—"} />
                <Row label="Email" value={snap.email || item.email_used || "—"} />
                <Row label="Số điện thoại" value={snap.phone || "—"} />
                <Row label="Website" value={snap.website || "—"} link />
                <Row label="Công ty" value={snap.company || "—"} />
                <Row label="Quốc gia" value={snap.country || "—"} />
                <Row label="Niche" value={(snap.niche || []).join(", ") || "—"} />
                {snap.notes && <Row label="Ghi chú" value={snap.notes} />}
              </dl>
            ) : (
              <p className="text-green-700 text-xs italic">
                Dữ liệu này chỉ có ở các lần chạy mới (sau khi cập nhật hệ thống).
              </p>
            )}
          </section>
        </div>

        {/* ───── RIGHT COLUMN ───── */}
        <div className="space-y-5 text-sm">

          {/* Kết quả */}
          <section className="bg-gray-50 rounded-xl p-4">
            <h3 className="font-semibold text-gray-800 mb-3 flex items-center gap-1.5">
              <span className="w-1.5 h-4 bg-purple-500 rounded-full inline-block" />
              Kết quả
            </h3>
            <dl className="space-y-2">
              <Row label="Trạng thái" value={<StatusBadge status={item.status} />} raw />
              <Row label="Thời gian" value={fmtDuration(item.duration_sec)} />
              <Row label="Số bước AI" value={item.steps != null ? String(item.steps) : "—"} />
              <Row label="Bắt đầu" value={fmtDate(item.started_at)} />
              <Row label="Kết thúc" value={fmtDate(item.finished_at)} />
              <Row label="URL cuối" value={item.final_url} link />
            </dl>
          </section>

          {/* Message */}
          {item.message && (
            <section className="bg-gray-50 rounded-xl p-4">
              <h3 className="font-semibold text-gray-800 mb-2 flex items-center gap-1.5">
                <span className="w-1.5 h-4 bg-gray-400 rounded-full inline-block" />
                Thông báo từ agent
              </h3>
              <p className="text-gray-600 text-xs bg-white rounded-lg p-3 whitespace-pre-wrap break-words border border-gray-200 max-h-32 overflow-y-auto">
                {item.message}
              </p>
            </section>
          )}

          {/* Screenshot */}
          <section>
            <h3 className="font-semibold text-gray-800 mb-2 flex items-center gap-1.5">
              <span className="w-1.5 h-4 bg-teal-500 rounded-full inline-block" />
              Ảnh minh chứng
            </h3>
            {imgUrl ? (
              <a href={imgUrl} target="_blank" rel="noopener noreferrer">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={imgUrl}
                  alt="screenshot"
                  className="w-full rounded-xl border border-gray-200 shadow-sm hover:shadow-md transition-shadow cursor-zoom-in"
                />
                <p className="text-xs text-gray-400 mt-1.5 text-center flex items-center justify-center gap-1">
                  <ExternalLink size={11} /> Nhấn để xem toàn màn hình
                </p>
              </a>
            ) : (
              <div className="flex flex-col items-center justify-center h-40 rounded-xl border-2 border-dashed border-gray-200 text-gray-400 gap-2">
                <ImageIcon size={28} className="opacity-40" />
                <span className="text-xs">Không có ảnh</span>
              </div>
            )}
          </section>
        </div>
      </div>
    </Modal>
  );
}

function Row({
  label,
  value,
  link,
  raw,
}: {
  label: string;
  value: React.ReactNode;
  link?: boolean;
  raw?: boolean;
}) {
  const display = raw ? (
    value
  ) : link && typeof value === "string" && value && value !== "—" ? (
    <a
      href={value}
      target="_blank"
      rel="noopener noreferrer"
      className="text-blue-600 hover:underline truncate max-w-[240px] inline-block"
    >
      {value}
    </a>
  ) : (
    <span className="text-gray-700 break-words">{(value as string) || "—"}</span>
  );
  return (
    <div className="flex gap-2 items-start">
      <span className="text-gray-400 shrink-0 w-28 leading-5">{label}</span>
      <span className="flex-1 min-w-0 leading-5 font-medium">{display}</span>
    </div>
  );
}

// ────────────────────────────────────────────────────────────
// Main Page
// ────────────────────────────────────────────────────────────

const PAGE_SIZE = 50;
const STATUSES = ["", "success", "failed", "captcha", "error", "running", "pending_verify"];

export default function HistoryPage() {
  const [data, setData] = useState<SignupHistoryResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);

  // Filters
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");

  // Search input debounce
  const searchDebounce = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [debouncedSearch, setDebouncedSearch] = useState("");

  // Detail modal
  const [selectedItem, setSelectedItem] = useState<SignupHistoryItem | null>(null);

  // Multi-select
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(new Set());
  const rowKey = (item: SignupHistoryItem, idx: number) =>
    `${item.job_id ?? "?"}-${item.program_id ?? idx}`;

  const toggleRow = (key: string) => {
    setSelectedKeys(prev => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  };

  const allPageKeys = data?.items.map((item, idx) => rowKey(item, idx)) ?? [];
  const allPageSelected = allPageKeys.length > 0 && allPageKeys.every(k => selectedKeys.has(k));

  const toggleAll = () => {
    if (allPageSelected) {
      setSelectedKeys(prev => {
        const next = new Set(prev);
        allPageKeys.forEach(k => next.delete(k));
        return next;
      });
    } else {
      setSelectedKeys(prev => new Set([...prev, ...allPageKeys]));
    }
  };

  const selectedItems = data?.items.filter((item, idx) => selectedKeys.has(rowKey(item, idx))) ?? [];

  const handleExportSelectedXLSX = () => {
    const jobIds = [...new Set(selectedItems.map((item) => item.job_id).filter(Boolean))].join(",");
    const url = exportSignupHistoryUrl({ job_ids: jobIds });
    window.open(url, "_blank");
  };

  const handleExportSelectedPDF = async () => {
    const statusLabel = (s: string) => {
      const map: Record<string, string> = {
        success: 'Thành công', failed: 'Thất bại', captcha: 'Captcha',
        error: 'Lỗi', running: 'Đang chạy', pending_verify: 'Chờ duyệt',
      };
      return map[s] ?? s;
    };
    // Pre-fetch screenshots as base64 data URLs (avoid cross-origin issues in print window)
    const imgMap = new Map<string, string>();
    await Promise.all(selectedItems.map(async (item) => {
      if (item.screenshot) {
        const dataUrl = await fetchImgAsDataUrl(screenshotUrl(item.screenshot));
        if (dataUrl) imgMap.set(item.screenshot, dataUrl);
      }
    }));
    const rows = selectedItems.map((item, i) => {
      const imgTag = item.screenshot && imgMap.has(item.screenshot)
        ? `<img src="${imgMap.get(item.screenshot)}" style="max-width:160px;max-height:90px;border-radius:3px;display:block;">`
        : '—';
      return `
      <tr>
        <td>${i + 1}</td>
        <td>${item.program_name ?? '—'}</td>
        <td>${item.program_source ?? '—'}</td>
        <td>${item.email_used ?? '—'}</td>
        <td>${statusLabel(item.status)}</td>
        <td>${item.message ? item.message.slice(0, 80) + (item.message.length > 80 ? '…' : '') : '—'}</td>
        <td>${item.duration_sec != null ? item.duration_sec + 's' : '—'}</td>
        <td>${item.started_at ? new Date(item.started_at).toLocaleString('vi-VN') : '—'}</td>
        <td>${imgTag}</td>
      </tr>`;
    }).join('');
    const html = `<!DOCTYPE html><html><head><meta charset="utf-8">
      <title>Lịch sử đăng ký (đã chọn)</title>
      <style>
        body { font-family: Arial, sans-serif; font-size: 11px; margin: 20px; color: #111; }
        h1 { font-size: 16px; margin-bottom: 4px; }
        .meta { color: #666; font-size: 10px; margin-bottom: 16px; }
        table { border-collapse: collapse; width: 100%; }
        th { background: #4f46e5; color: white; padding: 6px 8px; text-align: left; font-size: 10px; }
        td { border-bottom: 1px solid #e5e7eb; padding: 5px 8px; vertical-align: top; }
        td img { border: 1px solid #e5e7eb; }
        tr:nth-child(even) td { background: #f9fafb; }
        @media print { @page { size: A4 landscape; margin: 15mm; } img { max-width: 150px !important; } }
      </style>
    </head><body>
      <h1>Lịch sử đăng ký Affiliate (đã chọn)</h1>
      <div class="meta">Xuất lúc: ${new Date().toLocaleString('vi-VN')} · Tổng: ${selectedItems.length} bản ghi</div>
      <table><thead><tr>
        <th>#</th><th>Chương trình</th><th>Nguồn</th><th>Email dùng</th>
        <th>Trạng thái</th><th>Thông báo</th><th>Thời gian</th><th>Ngày bắt đầu</th><th>Screenshot</th>
      </tr></thead><tbody>${rows}</tbody></table>
    </body></html>`;
    const w = window.open('', '_blank');
    if (w) { w.document.write(html); w.document.close(); w.focus(); setTimeout(() => w.print(), 800); }
  };

  const fetchData = useCallback(async (p: number, s: string, sf: string, df: string, dt: string) => {
    setLoading(true);
    try {
      const res = await getSignupHistory({ page: p, limit: PAGE_SIZE, search: s, status: sf, date_from: df, date_to: dt });
      setData(res);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, []);

  // Initial + on filter change
  useEffect(() => {
    fetchData(page, debouncedSearch, statusFilter, dateFrom, dateTo);
  }, [page, debouncedSearch, statusFilter, dateFrom, dateTo, fetchData]);

  // Auto-refresh every 8s if any running item
  useEffect(() => {
    const hasRunning = data?.items.some((i) => i.status === "running");
    if (!hasRunning) return;
    const timer = setInterval(() => {
      fetchData(page, debouncedSearch, statusFilter, dateFrom, dateTo);
    }, 8000);
    return () => clearInterval(timer);
  }, [data, page, debouncedSearch, statusFilter, dateFrom, dateTo, fetchData]);

  const handleSearchChange = (val: string) => {
    setSearch(val);
    if (searchDebounce.current) clearTimeout(searchDebounce.current);
    searchDebounce.current = setTimeout(() => {
      setPage(1);
      setDebouncedSearch(val);
    }, 400);
  };

  const handleFilterChange = (key: string, val: string) => {
    setPage(1);
    if (key === "status") setStatusFilter(val);
    if (key === "dateFrom") setDateFrom(val);
    if (key === "dateTo") setDateTo(val);
  };

  const handleExport = () => {
    const url = exportSignupHistoryUrl({
      status: statusFilter,
      search: debouncedSearch,
      date_from: dateFrom,
      date_to: dateTo,
    });
    window.open(url, "_blank");
  };

  const handleExportPDF = async () => {
    // Fetch all matching records (up to 5000)
    let allItems: SignupHistoryItem[] = [];
    try {
      const res = await getSignupHistory({
        page: 1,
        limit: 5000,
        search: debouncedSearch,
        status: statusFilter,
        date_from: dateFrom,
        date_to: dateTo,
      });
      allItems = res.items;
    } catch {
      allItems = data?.items ?? [];
    }

    const statusLabel = (s: string) => {
      const map: Record<string, string> = {
        success: "Thành công", failed: "Thất bại", captcha: "Captcha",
        error: "Lỗi", running: "Đang chạy", pending_verify: "Chờ duyệt",
      };
      return map[s] ?? s;
    };

    // Pre-fetch screenshots as base64 data URLs (avoid cross-origin issues in print window)
    const imgMap = new Map<string, string>();
    await Promise.all(allItems.map(async (item) => {
      if (item.screenshot) {
        const dataUrl = await fetchImgAsDataUrl(screenshotUrl(item.screenshot));
        if (dataUrl) imgMap.set(item.screenshot, dataUrl);
      }
    }));

    const rows = allItems.map((item, i) => {
      const imgTag = item.screenshot && imgMap.has(item.screenshot)
        ? `<img src="${imgMap.get(item.screenshot)}" style="max-width:160px;max-height:90px;border-radius:3px;display:block;">`
        : "—";
      return `
      <tr>
        <td>${i + 1}</td>
        <td>${item.program_name ?? "—"}</td>
        <td>${item.program_source ?? "—"}</td>
        <td>${item.email_used ?? "—"}</td>
        <td>${statusLabel(item.status)}</td>
        <td>${item.message ? item.message.slice(0, 80) + (item.message.length > 80 ? "…" : "") : "—"}</td>
        <td>${item.duration_sec != null ? item.duration_sec + "s" : "—"}</td>
        <td>${item.started_at ? new Date(item.started_at).toLocaleString("vi-VN") : "—"}</td>
        <td>${imgTag}</td>
      </tr>`;
    }).join("");

    const html = `<!DOCTYPE html><html><head><meta charset="utf-8">
      <title>Lịch sử đăng ký</title>
      <style>
        body { font-family: Arial, sans-serif; font-size: 11px; margin: 20px; color: #111; }
        h1 { font-size: 16px; margin-bottom: 4px; }
        .meta { color: #666; font-size: 10px; margin-bottom: 16px; }
        table { border-collapse: collapse; width: 100%; }
        th { background: #4f46e5; color: white; padding: 6px 8px; text-align: left; font-size: 10px; }
        td { border-bottom: 1px solid #e5e7eb; padding: 5px 8px; vertical-align: top; }
        td img { border: 1px solid #e5e7eb; }
        tr:nth-child(even) td { background: #f9fafb; }
        @media print { @page { size: A4 landscape; margin: 15mm; } img { max-width: 150px !important; } }
      </style>
    </head><body>
      <h1>Lịch sử đăng ký Affiliate</h1>
      <div class="meta">Xuất lúc: ${new Date().toLocaleString("vi-VN")} · Tổng: ${allItems.length} bản ghi</div>
      <table><thead><tr>
        <th>#</th><th>Chương trình</th><th>Nguồn</th><th>Email dùng</th>
        <th>Trạng thái</th><th>Thông báo</th><th>Thời gian</th><th>Ngày bắt đầu</th><th>Screenshot</th>
      </tr></thead><tbody>${rows}</tbody></table>
    </body></html>`;

    const w = window.open("", "_blank");
    if (w) {
      w.document.write(html);
      w.document.close();
      w.focus();
      setTimeout(() => w.print(), 800);
    }
  };

  const stats: SignupHistoryStats | undefined = data?.stats;
  const successRate =
    stats && stats.total > 0 ? ((stats.success / stats.total) * 100).toFixed(1) : "0";

  return (
    <div className="p-6 space-y-6 min-h-screen">
      <PageHeader
        title="Lịch sử đăng ký"
        description="Toàn bộ kết quả các lần đăng ký affiliate, thống kê và ảnh minh chứng"
        action={
          <div className="flex gap-2">
            <Button variant="secondary" size="sm" onClick={() => fetchData(page, debouncedSearch, statusFilter, dateFrom, dateTo)} disabled={loading}>
              <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
              Làm mới
            </Button>
            {selectedKeys.size > 0 ? (
              <>
                <Button variant="primary" size="sm" onClick={handleExportSelectedXLSX}>
                  <Download size={14} />
                  Xuất Excel ({selectedKeys.size})
                </Button>
                <Button variant="secondary" size="sm" onClick={handleExportSelectedPDF}>
                  <FileText size={14} />
                  Xuất PDF ({selectedKeys.size})
                </Button>
              </>
            ) : (
              <>
                <Button variant="primary" size="sm" onClick={handleExport}>
                  <Download size={14} />
                  Xuất Excel
                </Button>
                <Button variant="secondary" size="sm" onClick={handleExportPDF}>
                  <FileText size={14} />
                  Xuất PDF
                </Button>
              </>
            )}
          </div>
        }
      />

      {/* Stat cards */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
        <StatCard
          label="Tổng attempts"
          value={stats?.total ?? "—"}
          icon={<TrendingUp size={20} className="text-white" />}
          color="bg-indigo-600"
        />
        <StatCard
          label="Thành công"
          value={stats?.success ?? "—"}
          icon={<CheckCircle2 size={20} className="text-white" />}
          color="bg-emerald-500"
        />
        <StatCard
          label="Chờ duyệt"
          value={stats?.pending_verify ?? "—"}
          icon={<Clock size={20} className="text-white" />}
          color="bg-sky-500"
        />
        <StatCard
          label="Thất bại"
          value={stats?.failed ?? "—"}
          icon={<XCircle size={20} className="text-white" />}
          color="bg-red-500"
        />
        <StatCard
          label="Captcha"
          value={stats?.captcha ?? "—"}
          icon={<AlertTriangle size={20} className="text-white" />}
          color="bg-amber-500"
        />
        <StatCard
          label="Tỉ lệ thành công"
          value={`${successRate}%`}
          sub={`trên ${stats?.total ?? 0} lần`}
          icon={<TrendingUp size={20} className="text-white" />}
          color="bg-violet-500"
        />
      </div>

      {/* Source breakdown */}
      {stats && Object.keys(stats.by_source).length > 0 && (
        <Card className="px-5 py-4">
          <h3 className="text-sm font-semibold text-gray-700 mb-3">Theo nguồn</h3>
          <div className="flex flex-wrap gap-2">
            {Object.entries(stats.by_source)
              .sort((a, b) => b[1] - a[1])
              .map(([src, count]) => (
                <span
                  key={src}
                  className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-medium bg-gray-100 text-gray-700"
                >
                  <span className="font-semibold text-brand">{src}</span>
                  <span className="text-gray-400">{count}</span>
                </span>
              ))}
          </div>
        </Card>
      )}

      {/* Daily trend */}
      {stats && stats.by_day.length > 0 && (
        <Card className="px-5 py-4">
          <h3 className="text-sm font-semibold text-gray-700 mb-3">Xu hướng theo ngày</h3>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-gray-100">
                  <th className="text-left py-1.5 pr-4 text-gray-500 font-medium">Ngày</th>
                  <th className="text-right py-1.5 px-2 text-emerald-600 font-medium">Thành công</th>
                  <th className="text-right py-1.5 px-2 text-red-500 font-medium">Thất bại</th>
                  <th className="text-right py-1.5 px-2 text-gray-500 font-medium">Tổng</th>
                  <th className="pl-4 py-1.5 w-40 text-gray-500 font-medium">Tỷ lệ</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {[...stats.by_day].reverse().map((row) => {
                  const rate = row.total > 0 ? Math.round((row.success / row.total) * 100) : 0;
                  return (
                    <tr key={row.date} className="hover:bg-gray-50">
                      <td className="py-1.5 pr-4 text-gray-700 font-medium">{row.date}</td>
                      <td className="py-1.5 px-2 text-right text-emerald-600 font-semibold">{row.success}</td>
                      <td className="py-1.5 px-2 text-right text-red-500">{row.failed}</td>
                      <td className="py-1.5 px-2 text-right text-gray-500">{row.total}</td>
                      <td className="pl-4 py-1.5">
                        <div className="flex items-center gap-1.5">
                          <div className="flex-1 h-1.5 bg-gray-100 rounded-full overflow-hidden">
                            <div className="h-full bg-emerald-400 rounded-full" style={{ width: `${rate}%` }} />
                          </div>
                          <span className="text-gray-400 w-8 text-right">{rate}%</span>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {/* Filter bar */}
      <Card className="px-5 py-4">
        <div className="flex flex-wrap gap-3 items-center">
          {/* Search */}
          <div className="relative flex-1 min-w-[180px]">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
            <input
              type="text"
              placeholder="Tìm tên chương trình..."
              value={search}
              onChange={(e) => handleSearchChange(e.target.value)}
              className="w-full pl-8 pr-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-brand/30"
            />
          </div>
          {/* Status filter */}
          <div className="relative">
            <Filter size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
            <select
              title="Lọc theo trạng thái"
              value={statusFilter}
              onChange={(e) => handleFilterChange("status", e.target.value)}
              className="pl-8 pr-8 py-2 text-sm border border-gray-200 rounded-lg bg-white focus:outline-none appearance-none"
            >
              {STATUSES.map((s) => (
                <option key={s} value={s}>
                  {s === "" ? "Tất cả trạng thái" : STATUS_META[s]?.label || s}
                </option>
              ))}
            </select>
            <ChevronDown size={13} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-gray-400 pointer-events-none" />
          </div>
          {/* Date range */}
          <input
            type="date"
            title="Từ ngày"
            placeholder="Từ ngày"
            value={dateFrom}
            onChange={(e) => handleFilterChange("dateFrom", e.target.value)}
            className="px-3 py-2 text-sm border border-gray-200 rounded-lg bg-white focus:outline-none"
          />
          <span className="text-gray-400 text-sm">–</span>
          <input
            type="date"
            title="Đến ngày"
            placeholder="Đến ngày"
            value={dateTo}
            onChange={(e) => handleFilterChange("dateTo", e.target.value)}
            className="px-3 py-2 text-sm border border-gray-200 rounded-lg bg-white focus:outline-none"
          />
          {(search || statusFilter || dateFrom || dateTo) && (
            <Button
              variant="secondary"
              size="sm"
              onClick={() => {
                setSearch("");
                setDebouncedSearch("");
                setStatusFilter("");
                setDateFrom("");
                setDateTo("");
                setPage(1);
              }}
            >
              Xóa bộ lọc
            </Button>
          )}
        </div>
      </Card>

      {/* Selection bar */}
      {selectedKeys.size > 0 && (
        <div className="flex items-center gap-3 px-4 py-2.5 bg-violet-50 border border-violet-200 rounded-xl text-sm">
          <CheckSquare size={16} className="text-violet-600 shrink-0" />
          <span className="text-violet-800 font-medium">{selectedKeys.size} mục đã chọn</span>
          <button
            className="ml-auto flex items-center gap-1.5 text-xs text-violet-600 hover:text-violet-800 transition-colors"
            onClick={() => setSelectedKeys(new Set())}
          >
            <XIcon size={13} /> Bỏ chọn tất cả
          </button>
        </div>
      )}

      {/* Table */}
      <Card className="overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-50 border-b border-gray-100">
                <th className="px-4 py-3 w-8">
                  <button onClick={toggleAll} className="flex items-center justify-center text-gray-400 hover:text-violet-600 transition-colors">
                    {allPageSelected ? <CheckSquare size={15} className="text-violet-600" /> : <Square size={15} />}
                  </button>
                </th>
                <th className="text-left px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wide w-8">#</th>
                <th className="text-left px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wide">Chương trình</th>
                <th className="text-left px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wide">Nguồn</th>
                <th className="text-left px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wide">Profile</th>
                <th className="text-left px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wide">Trạng thái</th>
                <th className="text-left px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wide">Kết quả</th>
                <th className="text-left px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wide">Thời gian</th>
                <th className="text-left px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wide">Ngày</th>
                <th className="text-left px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wide">Ảnh</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50">
              {loading && !data ? (
                <tr>
                  <td colSpan={10} className="px-4 py-16 text-center text-gray-400">
                    <RefreshCw size={20} className="animate-spin mx-auto mb-2 opacity-50" />
                    Đang tải...
                  </td>
                </tr>
              ) : !data?.items.length ? (
                <tr>
                  <td colSpan={10} className="px-4 py-16 text-center text-gray-400 text-sm">
                    Chưa có dữ liệu lịch sử nào.
                  </td>
                </tr>
              ) : (
                data.items.map((item, idx) => {
                  const imgUrl = item.screenshot ? screenshotUrl(item.screenshot) : null;
                  const key = rowKey(item, idx);
                  const isChecked = selectedKeys.has(key);
                  return (
                    <tr
                      key={`${item.job_id}-${item.program_id}-${idx}`}
                      className={`hover:bg-gray-50/70 transition-colors cursor-pointer ${isChecked ? 'bg-violet-50/60' : ''}`}
                      onClick={() => setSelectedItem(item)}
                    >
                      <td className="px-4 py-3" onClick={(e) => { e.stopPropagation(); toggleRow(key); }}>
                        <button className="flex items-center justify-center text-gray-400 hover:text-violet-600 transition-colors">
                          {isChecked ? <CheckSquare size={15} className="text-violet-600" /> : <Square size={15} />}
                        </button>
                      </td>
                      <td className="px-4 py-3 text-gray-400 text-xs">
                        {(page - 1) * PAGE_SIZE + idx + 1}
                      </td>
                      <td className="px-4 py-3 max-w-[200px]">
                        <p className="font-medium text-ink truncate">{item.program_name || "—"}</p>
                        {item.program_commission && (
                          <p className="text-xs text-gray-400 mt-0.5">{item.program_commission}</p>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        {item.program_source ? (
                          <span className="inline-block px-2 py-0.5 rounded-full text-xs font-medium bg-blue-50 text-blue-700">
                            {item.program_source}
                          </span>
                        ) : "—"}
                      </td>
                      <td className="px-4 py-3">
                        <p className="text-ink text-xs">{item.profile_name || item.profile_id || "—"}</p>
                        {item.profile_email && (
                          <p className="text-xs text-gray-400 mt-0.5 truncate max-w-[140px]">{item.profile_email}</p>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        <StatusBadge status={item.status} />
                      </td>
                      <td className="px-4 py-3 max-w-[200px]">
                        <p className="text-xs text-gray-500 line-clamp-2 break-words">{item.message || "—"}</p>
                      </td>
                      <td className="px-4 py-3 text-xs text-gray-500 whitespace-nowrap">
                        {fmtDuration(item.duration_sec)}
                      </td>
                      <td className="px-4 py-3 text-xs text-gray-500 whitespace-nowrap">
                        {fmtDate(item.started_at)}
                      </td>
                      <td className="px-4 py-3">
                        {imgUrl ? (
                          // eslint-disable-next-line @next/next/no-img-element
                          <img
                            src={imgUrl}
                            alt=""
                            className="w-14 h-10 object-cover rounded border border-gray-200 cursor-zoom-in hover:scale-110 transition-transform"
                            onClick={(e) => { e.stopPropagation(); setSelectedItem(item); }}
                          />
                        ) : (
                          <span className="text-gray-300 text-xs">—</span>
                        )}
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>

        {data && data.total_count > PAGE_SIZE && (
          <Pagination
            page={page}
            pageSize={PAGE_SIZE}
            total={data.total_count}
            onPageChange={setPage}
          />
        )}
      </Card>

      {/* Detail modal */}
      <HistoryDetailModal item={selectedItem} onClose={() => setSelectedItem(null)} />
    </div>
  );
}
