"use client";
import { useState, useEffect } from "react";
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
import { Radar, Plus, Trash2, RefreshCw, Play, ChevronDown, ChevronUp, ExternalLink, X, Loader2, CheckCircle2, ListChecks, Gauge, Square, Search, Download, Eye, User, Users, Upload } from "lucide-react";
import Link from "next/link";
import * as api from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { Card } from "@/components/Card";
import { PageHeader } from "@/components/PageHeader";
import { Button } from "@/components/Button";
import { EmptyState } from "@/components/EmptyState";
import clsx from "clsx";

// ─── Detection badge ──────────────────────────────────────────────────────────
const BADGE: Record<string, { label: string; color: string; emoji: string }> = {
  direct_outbound:  { label: "direct_outbound",  color: "bg-blue-100 text-blue-700",   emoji: "📋" },
  label_match:      { label: "label_match",       color: "bg-green-100 text-green-700", emoji: "⭐" },
  fallback_outbound:{ label: "fallback_outbound", color: "bg-yellow-100 text-yellow-700", emoji: "🔹" },
};

function DetectionBadge({ method }: { method: string | null }) {
  if (!method) return null;
  const b = BADGE[method] || { label: method, color: "bg-gray-100 text-gray-600", emoji: "•" };
  return (
    <span className={clsx("inline-flex items-center gap-1 text-[11px] font-medium px-1.5 py-0.5 rounded-full", b.color)}>
      {b.emoji} {b.label}
    </span>
  );
}

// ─── Status badge ─────────────────────────────────────────────────────────────
const STATUS_COLOR: Record<string, string> = {
  discovered:         "bg-gray-100 text-gray-600",
  homepage_resolved:  "bg-blue-100 text-blue-700",
  traffic_scanned:    "bg-purple-100 text-purple-700",
  affiliate_found:    "bg-green-100 text-green-700",
  promoted:           "bg-emerald-100 text-emerald-700",
  no_affiliate_found: "bg-red-100 text-red-500",
};

function StatusBadge({ status }: { status: string }) {
  const color = STATUS_COLOR[status] || "bg-gray-100 text-gray-500";
  return <span className={clsx("text-[11px] font-medium px-2 py-0.5 rounded-full", color)}>{status}</span>;
}

const PILL = "text-[11px] font-medium px-2 py-0.5 rounded-full inline-block";

// Trạng thái quét traffic. "not_found" = ĐÃ quét thành công nhưng SimilarWeb
// không có dữ liệu (0 visits) — vẫn là quét xong, khác với "chưa quét".
function TrafficStatusBadge({ c }: { c: api.DiscoveryCandidate }) {
  const st = c.traffic_status;
  if (st === "failed") return <span className={clsx(PILL, "bg-red-100 text-red-500")}>❌ lỗi</span>;
  if (st === "scanned") return <span className={clsx(PILL, "bg-green-100 text-green-700")}>✅ có traffic</span>;
  if (st === "not_found") return <span className={clsx(PILL, "bg-emerald-50 text-emerald-600")}>✅ đã quét · 0</span>;
  return <span className={clsx(PILL, "bg-gray-50 text-gray-400")}>⏳ chưa quét</span>;
}

// Trạng thái dò affiliate
function AffiliateStatusBadge({ c }: { c: api.DiscoveryCandidate }) {
  if (c.affiliate_url) return <span className={clsx(PILL, "bg-green-100 text-green-700")}>✅ có</span>;
  if (c.status === "no_affiliate_found") return <span className={clsx(PILL, "bg-red-100 text-red-500")}>❌ không có</span>;
  return <span className={clsx(PILL, "bg-gray-50 text-gray-400")}>⏳ chưa dò</span>;
}

// Đánh giá link affiliate: sống / 404 / chưa kết luận (đã thử, site chặn) / chưa KT
function LinkStatusBadge({ c }: { c: api.DiscoveryCandidate }) {
  if (!c.affiliate_url) return <span className="text-gray-300">—</span>;
  if (c.affiliate_url_status === "ok") return <span className={clsx(PILL, "bg-green-100 text-green-700")}>✅ sống</span>;
  if (c.affiliate_url_status === "dead") return <span className={clsx(PILL, "bg-red-100 text-red-600")} title="Link 404 / không tồn tại">❌ 404</span>;
  if (c.affiliate_url_status === "unknown")
    return <span className={clsx(PILL, "bg-amber-50 text-amber-600")} title="Đã kiểm tra nhưng site chặn/timeout — thử lại với proxy">⚠️ chưa kết luận</span>;
  return <span className={clsx(PILL, "bg-gray-50 text-gray-400")}>⏳ chưa KT</span>;
}

// ─── Add source modal ─────────────────────────────────────────────────────────
function AddSourceModal({ onClose, onAdded }: { onClose: () => void; onAdded: () => void }) {
  const [url, setUrl] = useState("");
  const [name, setName] = useState("");
  const [category, setCategory] = useState("");
  const [field, setField] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const submit = async () => {
    if (!url.trim()) { setError("URL không được để trống"); return; }
    setLoading(true); setError("");
    try {
      await api.createDiscoverySource(url.trim(), name.trim() || undefined, category.trim() || undefined, field.trim() || undefined);
      onAdded();
      onClose();
    } catch (e: any) { setError(e.message); }
    finally { setLoading(false); }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm" onClick={onClose}>
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-md p-6" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-base font-semibold text-ink">Thêm nguồn review/forum</h2>
          <button onClick={onClose} className="p-1 rounded-lg hover:bg-gray-100 text-gray-400 cursor-pointer"><X size={16} /></button>
        </div>
        <div className="space-y-3">
          <div>
            <label className="text-xs text-gray-500 font-medium">URL trang nguồn *</label>
            <input
              autoFocus
              value={url}
              onChange={e => setUrl(e.target.value)}
              placeholder="https://forexpeacearmy.com/forex-reviews"
              className="mt-1 w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/30"
            />
          </div>
          <div>
            <label className="text-xs text-gray-500 font-medium">Tên gợi nhớ (tuỳ chọn)</label>
            <input
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder="Forex Peace Army"
              className="mt-1 w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/30"
            />
          </div>
          <div>
            <label className="text-xs text-gray-500 font-medium">Category</label>
            <select
              value={category}
              onChange={e => setCategory(e.target.value)}
              className="mt-1 w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/30"
            >
              <option value="">— Chọn category —</option>
              {SOURCE_CATEGORIES.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
            <p className="text-[11px] text-gray-400 mt-1">Khi promote sang màn Chương trình: dự án mang <b>Category</b> này (nhóm rộng) và <b>Tên gợi nhớ</b> làm <b>Sub-category</b> (ngách).</p>
          </div>
          <div>
            <label className="text-xs text-gray-500 font-medium">Field / Lĩnh vực (tuỳ chọn)</label>
            <input
              value={field}
              onChange={e => setField(e.target.value)}
              placeholder="VD: CRM, Accounting, HR…"
              className="mt-1 w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/30"
            />
            <p className="text-[11px] text-gray-400 mt-1">Áp cho mọi dự án crawl từ nguồn này (lọc theo <b>Field</b> ở màn Chương trình). Dự án cũ không đổi.</p>
          </div>
          {error && <p className="text-red-500 text-xs">{error}</p>}
          <div className="flex gap-2 pt-1">
            <Button variant="secondary" onClick={onClose} className="flex-1">Huỷ</Button>
            <Button onClick={submit} disabled={loading} className="flex-1">
              {loading ? "Đang thêm..." : "Thêm nguồn"}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── Import domains modal (dán / tải file danh sách domain) ────────────────────
function ImportDomainsModal({ onClose, onAdded }: { onClose: () => void; onAdded: () => void }) {
  const [name, setName] = useState("");
  const [category, setCategory] = useState("");
  const [field, setField] = useState("");
  const [domains, setDomains] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<api.ImportDomainsResult | null>(null);

  const submit = async () => {
    if (!name.trim()) { setError("Cần Tên gợi nhớ"); return; }
    if (!domains.trim() && !file) { setError("Dán danh sách domain hoặc chọn file"); return; }
    setLoading(true); setError("");
    try {
      const r = await api.importDiscoveryDomains({ name: name.trim(), category: category.trim() || undefined, field: field.trim() || undefined, domains, file });
      setResult(r);
      onAdded();
    } catch (e: any) { setError(e.message); }
    finally { setLoading(false); }
  };

  // File CSV mẫu: cột domain, category, sub_category (BOM để Excel đọc đúng UTF-8/tiếng Việt).
  const downloadTemplate = () => {
    const csv = "﻿" + "domain,category,sub_category,field\n"
      + "brightdata.com,SP số,Proxy,Web Scraping\n"
      + "oxylabs.io,SP số,Proxy,Web Scraping\n"
      + "semrush.com,SP số,SEO,Marketing\n";
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8;" }));
    const a = document.createElement("a");
    a.href = url; a.download = "mau-danh-sach-domain.csv";
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm" onClick={onClose}>
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-md p-6" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-base font-semibold text-ink">Nhập danh sách domain</h2>
          <button onClick={onClose} className="p-1 rounded-lg hover:bg-gray-100 text-gray-400 cursor-pointer"><X size={16} /></button>
        </div>
        {result ? (
          <div className="space-y-3">
            <div className="rounded-lg bg-emerald-50 border border-emerald-200 p-3 text-sm text-ink">
              ✅ Tải lên thành công <b>{result.created}</b> domain vào nguồn “<b>{result.name}</b>”.
            </div>
            <ul className="text-xs text-gray-600 space-y-1 pl-1">
              <li>• Tổng dòng nhập: <b>{result.total_parsed}</b></li>
              <li>• Bỏ qua vì trùng: <b>{result.skipped_duplicate}</b></li>
              <li>• Bỏ qua không hợp lệ: <b>{result.skipped_invalid}</b></li>
            </ul>
            <p className="text-[11px] text-gray-400">Giờ bạn có thể chọn nguồn này và dùng “Dò affiliate” / “Quét traffic” như bình thường.</p>
            <Button onClick={onClose} className="w-full">Xong</Button>
          </div>
        ) : (
          <div className="space-y-3">
            <div>
              <label className="text-xs text-gray-500 font-medium">Tên gợi nhớ *</label>
              <input autoFocus value={name} onChange={e => setName(e.target.value)}
                placeholder="VD: Danh sách proxy tháng 8"
                className="mt-1 w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/30" />
            </div>
            <div>
              <label className="text-xs text-gray-500 font-medium">Category</label>
              <select value={category} onChange={e => setCategory(e.target.value)}
                className="mt-1 w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/30">
                <option value="">— Chọn category —</option>
                {SOURCE_CATEGORIES.map(c => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>
            <div>
              <label className="text-xs text-gray-500 font-medium">Field / Lĩnh vực</label>
              <input value={field} onChange={e => setField(e.target.value)}
                placeholder="VD: CRM, Accounting, HR…"
                className="mt-1 w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/30" />
            </div>
            <div>
              <label className="text-xs text-gray-500 font-medium">Dán domain (mỗi dòng 1 domain, hoặc cách nhau bởi dấu phẩy)</label>
              <textarea value={domains} onChange={e => setDomains(e.target.value)} rows={6}
                placeholder={"brightdata.com\noxylabs.io\nhttps://www.semrush.com/"}
                className="mt-1 w-full border border-gray-200 rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-primary/30" />
            </div>
            <div>
              <div className="flex items-center justify-between">
                <label className="text-xs text-gray-500 font-medium">Hoặc tải file (.txt / .csv / .numbers)</label>
                <button type="button" onClick={downloadTemplate}
                  className="text-[11px] text-primary hover:underline font-medium cursor-pointer">↓ Tải file CSV mẫu</button>
              </div>
              <input type="file" accept=".txt,.csv,.numbers,text/plain,text/csv"
                onChange={e => setFile(e.target.files?.[0] || null)}
                className="mt-1 w-full text-sm text-gray-600 file:mr-3 file:rounded-lg file:border-0 file:bg-primary-50 file:px-3 file:py-1.5 file:text-primary file:text-sm cursor-pointer" />
              <p className="text-[11px] text-gray-400 mt-1">Nhận <b>.csv</b>, <b>.txt</b> và <b>.numbers</b> (Apple Numbers). Dùng <b>Excel</b>? Hãy <b>Export sang CSV</b> (File → Save As → CSV UTF-8).</p>
            </div>
            <p className="text-[11px] text-gray-400">File CSV có thể thêm cột <b>category</b>, <b>sub_category</b>, <b>field</b> cho từng domain (đồng bộ sang màn Chương trình khi promote); dòng để trống → dùng <b>Category</b> / <b>Tên gợi nhớ</b> / <b>Field</b> ở trên. Trùng domain (trong danh sách hoặc đã có ở nguồn khác của bạn) sẽ tự bỏ qua.</p>
            {error && <p className="text-red-500 text-xs">{error}</p>}
            <div className="flex gap-2 pt-1">
              <Button variant="secondary" onClick={onClose} className="flex-1">Huỷ</Button>
              <Button onClick={submit} disabled={loading} className="flex-1">{loading ? "Đang tải..." : "Tải lên"}</Button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Crawl page-count presets ──────────────────────────────────────────────────
const PAGE_PRESETS = [
  { label: "3 trang (~150 dự án) — an toàn nhất", value: 3 },
  { label: "5 trang (~250 dự án)", value: 5 },
  { label: "10 trang (~500 dự án)", value: 10 },
  { label: "20 trang (~1.000 dự án)", value: 20 },
  { label: "50 trang (~2.500 dự án)", value: 50 },
  { label: "Tất cả (toàn bộ)", value: 0 },
];

const ALL_PROXIES = "__all__";  // sentinel: dùng toàn bộ pool proxy
const PAGE_SIZE_OPTIONS = [30, 50, 100];   // số dự án hiện mỗi trang
// 5 category cố định — dự án promote từ nguồn sẽ mang category của nguồn
const SOURCE_CATEGORIES = ["Forex", "Crypto", "SP số", "SP vật lý", "SP Tài chính"];

function fmtTraffic(v: number | null): string {
  if (v == null) return "—";
  if (v <= 0) return "0";
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(0)}K`;
  return String(v);
}

// Ngày quảng cáo (ISO từ API) → dd/mm/yy
function fmtAdDate(s: string | null): string {
  if (!s) return "—";
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString("vi-VN", { day: "2-digit", month: "2-digit", year: "2-digit" });
}

// ─── Add blacklist modal ──────────────────────────────────────────────────────
function AddBlacklistModal({ onClose, onAdded }: { onClose: () => void; onAdded: () => void }) {
  const [domain, setDomain] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const submit = async () => {
    if (!domain.trim()) { setError("Domain không được để trống"); return; }
    setLoading(true); setError("");
    try {
      await api.addDiscoveryBlacklist(domain.trim().toLowerCase());
      onAdded();
      onClose();
    } catch (e: any) { setError(e.message); }
    finally { setLoading(false); }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm" onClick={onClose}>
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-sm p-6" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-base font-semibold text-ink">Thêm domain vào blacklist</h2>
          <button onClick={onClose} className="p-1 rounded-lg hover:bg-gray-100 text-gray-400 cursor-pointer"><X size={16} /></button>
        </div>
        <div className="space-y-3">
          <input
            autoFocus
            value={domain}
            onChange={e => setDomain(e.target.value)}
            placeholder="example.com"
            className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/30"
          />
          {error && <p className="text-red-500 text-xs">{error}</p>}
          <div className="flex gap-2">
            <Button variant="secondary" onClick={onClose} className="flex-1">Huỷ</Button>
            <Button onClick={submit} disabled={loading} className="flex-1">{loading ? "..." : "Thêm"}</Button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── Main page ────────────────────────────────────────────────────────────────
export default function DiscoveryPage() {
  const qc = useQueryClient();
  const [showAddSource, setShowAddSource] = useState(false);
  const [showImport, setShowImport] = useState(false);
  const [showAddBlacklist, setShowAddBlacklist] = useState(false);
  const [filterStatus, setFilterStatus] = useState("");
  const [filterDetection, setFilterDetection] = useState("");
  const [filterTraffic, setFilterTraffic] = useState("");
  const [filterMinTraffic, setFilterMinTraffic] = useState("");   // ngưỡng traffic tối thiểu ("" = tất cả)
  const [filterAffiliate, setFilterAffiliate] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [filterSourceId, setFilterSourceId] = useState<number | undefined>();
  const [candidatePage, setCandidatePage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [crawlingIds, setCrawlingIds] = useState<Set<number>>(new Set());
  const [stoppingIds, setStoppingIds] = useState<Set<number>>(new Set());
  const [selectedProxyId, setSelectedProxyId] = useState<string>("");  // "" | "__all__" | proxy id
  const [selectedMaxPages, setSelectedMaxPages] = useState<number>(3);
  const [incrementalMode, setIncrementalMode] = useState<boolean>(true);  // chỉ quét mới
  const [runningPipeline, setRunningPipeline] = useState(false);
  const [scanningTraffic, setScanningTraffic] = useState(false);
  const [detectingAff, setDetectingAff] = useState(false);
  const [detectingAffSearch, setDetectingAffSearch] = useState(false);
  const [checkingLink, setCheckingLink] = useState(false);
  const [expandedBlacklist, setExpandedBlacklist] = useState(false);
  // Admin xem dự án của user khác: undefined = dự án của mình. Chỉ xem, không sửa được.
  const [viewOwnerId, setViewOwnerId] = useState<number | undefined>();
  const readOnly = viewOwnerId !== undefined;

  const { isAdmin } = useAuth();
  const owners = useQuery({ queryKey: ["discovery-owners"], queryFn: api.listDiscoveryOwners, enabled: isAdmin });
  const ownerEmail = (owners.data || []).find(o => o.id === viewOwnerId)?.email;

  const sources = useQuery({ queryKey: ["discovery-sources", viewOwnerId], queryFn: () => api.listDiscoverySources(viewOwnerId), refetchInterval: 3000 });
  const jobs = useQuery({ queryKey: ["jobs"], queryFn: api.listJobs, refetchInterval: 3000 });
  const summary = useQuery({ queryKey: ["discovery-summary", filterSourceId, viewOwnerId], queryFn: () => api.getDiscoverySummary(filterSourceId, viewOwnerId), refetchInterval: 4000 });
  const candidates = useQuery({
    queryKey: ["discovery-candidates", filterStatus, filterDetection, filterTraffic, filterMinTraffic, filterAffiliate, filterSourceId, debouncedQuery, candidatePage, pageSize, viewOwnerId],
    queryFn: () => api.listDiscoveryCandidates({
      status: filterStatus || undefined,
      detection_method: filterDetection || undefined,
      traffic_state: filterTraffic || undefined,
      min_traffic: filterMinTraffic ? Number(filterMinTraffic) : undefined,
      affiliate_state: filterAffiliate || undefined,
      source_id: filterSourceId,
      q: debouncedQuery || undefined,
      owner_id: viewOwnerId,
      page: candidatePage,
      page_size: pageSize,
    }),
    refetchInterval: 4000,
  });
  const blacklist = useQuery({ queryKey: ["discovery-blacklist"], queryFn: api.listDiscoveryBlacklist });
  const proxies = useQuery({ queryKey: ["proxies"], queryFn: api.listProxies });

  // Debounce ô tìm domain (tránh gọi API mỗi phím gõ)
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQuery(searchQuery.trim()), 300);
    return () => clearTimeout(t);
  }, [searchQuery]);

  // Về trang 1 mỗi khi đổi bộ lọc
  useEffect(() => { setCandidatePage(1); }, [filterStatus, filterDetection, filterTraffic, filterMinTraffic, filterAffiliate, filterSourceId, debouncedQuery]);

  // Đổi người xem → bỏ lọc theo nguồn + bỏ chọn (id thuộc về tập dữ liệu khác)
  useEffect(() => {
    setFilterSourceId(undefined);
    setSelectedIds(new Set());
    setCandidatePage(1);
  }, [viewOwnerId]);

  const candidateItems = candidates.data?.items || [];
  const candidateTotal = candidates.data?.total || 0;
  const totalPages = Math.max(1, Math.ceil(candidateTotal / pageSize));

  // Map source_id → nguồn (để hiển thị candidate đến từ nguồn review/forum nào)
  const sourceById = new Map((sources.data || []).map(s => [s.id, s]));
  const sourceLabel = (id: number) => {
    const s = sourceById.get(id);
    if (!s) return "—";
    return s.name || s.url.replace(/^https?:\/\/(www\.)?/, "");
  };

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["discovery-sources"] });
    qc.invalidateQueries({ queryKey: ["discovery-summary"] });
    qc.invalidateQueries({ queryKey: ["discovery-candidates"] });
    qc.invalidateQueries({ queryKey: ["discovery-blacklist"] });
  };

  const handleCrawl = async (id: number, opts?: { proxy_ids?: string[]; max_pages?: number; incremental?: boolean }) => {
    setCrawlingIds(prev => new Set(prev).add(id));
    try {
      await api.crawlDiscoverySource(id, opts);
    } catch (e: any) {
      alert(e?.message || "Không thể bắt đầu quét");
    } finally {
      // server flips is_crawling within the next poll; clear optimistic local flag after
      setTimeout(() => setCrawlingIds(prev => { const s = new Set(prev); s.delete(id); return s; }), 3000);
      qc.invalidateQueries({ queryKey: ["jobs"] });
      refresh();
    }
  };

  const handleDeleteSource = async (id: number) => {
    if (!confirm("Xoá nguồn này và toàn bộ candidates?")) return;
    await api.deleteDiscoverySource(id);
    refresh();
  };

  const handleSetCategory = async (id: number, category: string) => {
    try {
      await api.updateDiscoverySource(id, { category });
      qc.invalidateQueries({ queryKey: ["discovery-sources"] });
      qc.invalidateQueries({ queryKey: ["programs"] });  // re-sync category ở màn Chương trình
    } catch (e: any) {
      alert(e?.message || "Không thể đặt category");
    }
  };

  const handleStop = async (id: number) => {
    setStoppingIds(prev => new Set(prev).add(id));
    try {
      await api.stopDiscoverySource(id);
      qc.invalidateQueries({ queryKey: ["jobs"] });
    } catch (e: any) {
      alert(e?.message || "Không thể dừng");
    } finally {
      // clear cờ khi source hết crawling (poll 3s), fallback sau 8s
      setTimeout(() => setStoppingIds(prev => { const s = new Set(prev); s.delete(id); return s; }), 8000);
      refresh();
    }
  };

  const currentProxyIds = (): string[] | undefined => {
    if (selectedProxyId === ALL_PROXIES) return (proxies.data || []).map(p => p.id);
    if (selectedProxyId) return [selectedProxyId];
    return undefined;
  };

  const handleRunPipeline = async () => {
    if (selectedIds.size === 0) return;
    setRunningPipeline(true);
    try {
      const r = await api.runDiscoveryPipeline(Array.from(selectedIds), currentProxyIds());
      setSelectedIds(new Set());
      refresh();
      alert(`Đã bắt đầu chạy pipeline cho ${r.started} dự án ở nền — kết quả cập nhật dần trong bảng.`);
    } catch (e: any) {
      alert(e?.message || "Không thể bắt đầu pipeline");
    } finally { setRunningPipeline(false); }
  };

  const handleScanTraffic = async () => {
    if (selectedIds.size === 0) return;
    setScanningTraffic(true);
    try {
      const r = await api.scanDiscoveryTrafficBatch(Array.from(selectedIds));
      refresh();
      alert(`Đã bắt đầu quét traffic cho ${r.started} domain ở nền — cột Traffic cập nhật dần.`);
    } catch (e: any) {
      alert(e?.message || "Không thể bắt đầu quét traffic");
    } finally { setScanningTraffic(false); }
  };

  const handleDetectAffiliate = async () => {
    if (selectedIds.size === 0) return;
    setDetectingAff(true);
    try {
      const r = await api.detectAffiliateBatch(Array.from(selectedIds), currentProxyIds());
      refresh();
      alert(`Đã bắt đầu dò affiliate cho ${r.started} domain ở nền — cột Affiliate cập nhật dần (mỗi domain ~5-15s).`);
    } catch (e: any) {
      alert(e?.message || "Không thể bắt đầu dò affiliate");
    } finally { setDetectingAff(false); }
  };

  const handleDetectAffiliateSearch = async () => {
    if (selectedIds.size === 0) return;
    setDetectingAffSearch(true);
    try {
      const r = await api.detectAffiliateSearchBatch(Array.from(selectedIds), currentProxyIds());
      refresh();
      alert(`Đã bắt đầu dò affiliate qua Google cho ${r.started} domain ở nền — dùng khi site chặn fetch (Cloudflare) hoặc affiliate ở path sâu. Tuần tự ~5-15s/domain, cột Affiliate cập nhật dần.`);
    } catch (e: any) {
      alert(e?.message || "Không thể bắt đầu dò affiliate qua Google");
    } finally { setDetectingAffSearch(false); }
  };

  const handleCheckLink = async () => {
    if (selectedIds.size === 0) return;
    setCheckingLink(true);
    try {
      const r = await api.checkAffiliateLinkBatch(Array.from(selectedIds), currentProxyIds());
      refresh();
      alert(`Đã bắt đầu kiểm tra ${r.started} link affiliate ở nền — cột TT Link cập nhật dần (bỏ qua domain chưa có link).`);
    } catch (e: any) {
      alert(e?.message || "Không thể kiểm tra link");
    } finally { setCheckingLink(false); }
  };

  const toggleSelect = (id: number) => setSelectedIds(prev => {
    const s = new Set(prev);
    s.has(id) ? s.delete(id) : s.add(id);
    return s;
  });

  const toggleSelectAll = () => {
    const allIds = candidateItems.map(c => c.id);
    if (selectedIds.size === allIds.length) setSelectedIds(new Set());
    else setSelectedIds(new Set(allIds));
  };

  const s = summary.data;

  // Discovery jobs (source="discovery") — running one drives the progress banner
  const discoveryJobs = (jobs.data || []).filter(j => j.source === "discovery");
  const runningJob = discoveryJobs.find(j => j.status === "running");
  const lastFinishedJob = discoveryJobs.find(j => j.status === "success" || j.status === "failed");
  // Job quét traffic / dò affiliate đang chạy
  const runningTraffic = (jobs.data || []).find(j => j.source === "discovery_traffic" && j.status === "running");
  const runningAffiliate = (jobs.data || []).find(j => (j.source === "discovery_affiliate" || j.source === "discovery_affiliate_search") && j.status === "running");
  const fmtElapsed = (startIso?: string | null) => {
    if (!startIso) return "";
    const sec = Math.max(0, Math.floor((Date.now() - new Date(startIso + "Z").getTime()) / 1000));
    const m = Math.floor(sec / 60), ss = sec % 60;
    return m > 0 ? `${m}m ${ss}s` : `${ss}s`;
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="Tìm kiếm dự án"
        description="Tự động phát hiện dự án có affiliate program từ trang review/forum."
        action={
          <div className="flex gap-2">
            <Button variant="secondary" onClick={refresh}><RefreshCw size={14} className="mr-1.5" />Làm mới</Button>
            {!readOnly && (
              <Button variant="secondary" onClick={() => setShowImport(true)}><Upload size={14} className="mr-1.5" />Nhập domain</Button>
            )}
            {!readOnly && (
              <Button onClick={() => setShowAddSource(true)}><Plus size={14} className="mr-1.5" />Thêm nguồn</Button>
            )}
          </div>
        }
      />

      {/* ── Tab chọn người xem (chỉ admin) ── */}
      {isAdmin && (owners.data?.length || 0) > 0 && (
        <div className="flex flex-wrap items-center gap-2 border-b border-gray-100 pb-2">
          <button
            onClick={() => setViewOwnerId(undefined)}
            className={clsx(
              "text-sm font-medium px-3 py-1.5 rounded-lg cursor-pointer transition-colors",
              !readOnly ? "bg-primary-50 text-primary-600" : "text-gray-500 hover:bg-gray-50",
            )}
          >
            Dự án của tôi
          </button>
          <button
            onClick={() => setViewOwnerId(viewOwnerId ?? owners.data![0].id)}
            className={clsx(
              "text-sm font-medium px-3 py-1.5 rounded-lg cursor-pointer transition-colors flex items-center gap-1.5",
              readOnly ? "bg-primary-50 text-primary-600" : "text-gray-500 hover:bg-gray-50",
            )}
          >
            <Users size={13} /> Dự án người khác
            <span className="text-[11px] text-gray-400 tabular-nums">({owners.data!.length})</span>
          </button>
          {/* Chọn user — dropdown thay vì mỗi user một tab, để không vỡ layout khi đông người */}
          {readOnly && (
            <div className="flex items-center gap-1.5 ml-1">
              <User size={13} className="text-gray-400" />
              <select
                value={viewOwnerId}
                onChange={e => setViewOwnerId(Number(e.target.value))}
                className="text-xs border border-gray-200 rounded-lg px-2 py-1.5 max-w-[280px] focus:outline-none focus:ring-2 focus:ring-primary/30"
              >
                {(owners.data || []).map(o => (
                  <option key={o.id} value={o.id}>
                    {o.email} — {o.candidate_count} dự án / {o.source_count} nguồn
                  </option>
                ))}
              </select>
            </div>
          )}
        </div>
      )}

      {/* ── Banner chế độ chỉ xem ── */}
      {readOnly && (
        <Card className="!p-0 overflow-hidden border-l-4 border-l-amber-400">
          <div className="px-4 py-2.5 flex items-center gap-2 text-sm">
            <Eye size={16} className="text-amber-500 shrink-0" />
            <span className="text-ink">
              Đang xem dự án của <span className="font-semibold">{ownerEmail || `user #${viewOwnerId}`}</span>
              <span className="text-gray-500"> — chế độ chỉ xem, không quét/sửa/xoá được.</span>
            </span>
          </div>
        </Card>
      )}

      {/* ── Job progress banner ── */}
      {runningJob ? (
        <Card className="!p-0 overflow-hidden border-l-4 border-l-primary">
          <div className="px-4 py-3 flex items-center gap-3">
            <Loader2 size={20} className="text-primary animate-spin shrink-0" />
            <div className="flex-1 min-w-0">
              <div className="text-sm font-semibold text-ink flex items-center gap-2">
                Đang quét nguồn
                {runningJob.params?.name && <span className="text-gray-400 font-normal truncate">· {String(runningJob.params.name)}</span>}
              </div>
              <div className="text-xs text-gray-500 mt-0.5">
                <span className="font-medium text-primary tabular-nums">{runningJob.total_found}</span> dự án mới tìm thấy
                {" · "}đã chạy {fmtElapsed(runningJob.started_at)}
                {" · "}job <span className="font-mono">#{runningJob.id}</span>
              </div>
            </div>
            <div className="flex h-1.5 w-28 overflow-hidden rounded-full bg-primary-50 shrink-0">
              <div className="h-full w-1/3 animate-pulse rounded-full bg-primary" />
            </div>
            <Link href="/jobs" className="text-xs text-primary hover:underline flex items-center gap-1 shrink-0">
              <ListChecks size={13} /> Xem Jobs
            </Link>
          </div>
        </Card>
      ) : lastFinishedJob ? (
        <Card className={clsx(
          "!p-0 overflow-hidden border-l-4",
          lastFinishedJob.status === "success" ? "border-l-green-500" : "border-l-red-500",
        )}>
          <div className="px-4 py-2.5 flex items-center gap-3 text-sm">
            {lastFinishedJob.status === "success"
              ? <CheckCircle2 size={18} className="text-green-500 shrink-0" />
              : <X size={18} className="text-red-500 shrink-0" />}
            <div className="flex-1 min-w-0">
              <span className="text-ink font-medium">
                {lastFinishedJob.status === "success" ? "Quét hoàn tất" : "Quét thất bại"}
              </span>
              <span className="text-gray-500">
                {" "}· {lastFinishedJob.total_found} dự án mới
                {lastFinishedJob.finished_at && ` · lúc ${new Date(lastFinishedJob.finished_at + "Z").toLocaleTimeString("vi-VN")}`}
              </span>
              {lastFinishedJob.error && (
                <span className="text-red-500 text-xs ml-2 truncate">{lastFinishedJob.error.split("\n")[0]}</span>
              )}
            </div>
            <Link href="/jobs" className="text-xs text-gray-400 hover:text-primary flex items-center gap-1 shrink-0">
              <ListChecks size={13} /> Jobs
            </Link>
          </div>
        </Card>
      ) : null}

      {/* ── Banner job quét traffic đang chạy ── */}
      {runningTraffic && (
        <Card className="!p-0 overflow-hidden border-l-4 border-l-purple-500">
          <div className="px-4 py-3 flex items-center gap-3">
            <Gauge size={20} className="text-purple-500 animate-pulse shrink-0" />
            <div className="flex-1 min-w-0 text-sm">
              <span className="font-semibold text-ink">Đang quét traffic</span>
              <span className="text-gray-500">
                {" "}· <span className="font-medium text-purple-600 tabular-nums">{runningTraffic.total_saved}</span>/{String(runningTraffic.params?.total ?? "?")} domain
                {" · "}{runningTraffic.total_found} có data · {fmtElapsed(runningTraffic.started_at)}
              </span>
            </div>
          </div>
        </Card>
      )}

      {/* ── Banner job dò affiliate đang chạy ── */}
      {runningAffiliate && (
        <Card className="!p-0 overflow-hidden border-l-4 border-l-primary">
          <div className="px-4 py-3 flex items-center gap-3">
            <Radar size={20} className="text-primary animate-pulse shrink-0" />
            <div className="flex-1 min-w-0 text-sm">
              <span className="font-semibold text-ink">Đang dò affiliate</span>
              <span className="text-gray-500">
                {" "}· <span className="font-medium text-primary tabular-nums">{runningAffiliate.total_saved}</span>/{String(runningAffiliate.params?.total ?? "?")} domain
                {" · "}tìm thấy {runningAffiliate.total_found} · {fmtElapsed(runningAffiliate.started_at)}
                {runningAffiliate.params?.proxy && <> · proxy: {String(runningAffiliate.params.proxy)}</>}
              </span>
            </div>
          </div>
        </Card>
      )}

      {/* ── Sources panel ── */}
      <Card className="!p-0 overflow-hidden">
        <div className="px-4 py-3 border-b border-gray-100 flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold text-ink">Nguồn review / forum</h3>
            <span className="text-xs text-gray-400">· {sources.data?.length || 0} nguồn</span>
          </div>
          {/* Tuỳ chọn quét — hiển thị ngay trên màn hình */}
          <div className={clsx("flex flex-wrap items-center gap-x-4 gap-y-2", readOnly && "hidden")}>
            {/* Chế độ quét: chỉ mới vs toàn bộ */}
            <label
              className="flex items-center gap-1.5 cursor-pointer select-none"
              title="Chỉ quét dự án mới: quét từ mới nhất, gặp trang toàn dự án đã biết thì dừng (nhanh). Bỏ tick = quét lại toàn bộ."
            >
              <input
                type="checkbox"
                checked={incrementalMode}
                onChange={e => setIncrementalMode(e.target.checked)}
                className="accent-primary"
              />
              <span className="text-xs text-gray-600 font-medium whitespace-nowrap">Chỉ quét mới</span>
            </label>
            {/* Số trang quét */}
            <div className="flex items-center gap-2">
              <span className="text-xs text-gray-500 font-medium whitespace-nowrap">
                {incrementalMode ? "Quét tối đa:" : "Số trang:"}
              </span>
              <select
                value={selectedMaxPages}
                onChange={e => setSelectedMaxPages(Number(e.target.value))}
                className="text-xs border border-gray-200 rounded-lg px-2 py-1.5 focus:outline-none focus:ring-2 focus:ring-primary/30"
                title="Mỗi trang ~50 dự án. Ở chế độ 'Chỉ quét mới' đây là giới hạn trên — thường dừng sớm hơn nhiều."
              >
                {PAGE_PRESETS.map(p => (
                  <option key={p.value} value={p.value}>{p.label}</option>
                ))}
              </select>
            </div>
            {/* Proxy — pool nhiều IP từ Thư viện → Proxies */}
            <div className="flex items-center gap-2">
              <span className="text-xs text-gray-500 font-medium whitespace-nowrap">Proxy:</span>
              <select
                value={selectedProxyId}
                onChange={e => setSelectedProxyId(e.target.value)}
                className="text-xs border border-gray-200 rounded-lg px-2 py-1.5 max-w-[230px] focus:outline-none focus:ring-2 focus:ring-primary/30"
                title="Proxy áp dụng khi quét — 'Dùng tất cả' sẽ xoay vòng qua các IP (chống rate-limit)"
              >
                <option value="">Không dùng (IP trực tiếp)</option>
                {(proxies.data?.length || 0) > 0 && (
                  <option value={ALL_PROXIES}>
                    🔄 Dùng tất cả {proxies.data!.length} proxy (pool xoay vòng)
                  </option>
                )}
                {(proxies.data || []).map(p => (
                  <option key={p.id} value={p.id}>
                    {p.label || `${p.host}:${p.port}`}{p.country ? ` · ${p.country}` : ""}
                  </option>
                ))}
              </select>
              <Link
                href="/library?tab=proxy"
                className="text-xs text-primary hover:underline whitespace-nowrap flex items-center gap-1"
                title="Thêm/quản lý proxy (dán list Webshare ip:port:user:pass)"
              >
                <Plus size={12} /> Thư viện
              </Link>
            </div>
          </div>
        </div>
        {!sources.data || sources.data.length === 0 ? (
          <div className="px-4 py-6 text-center text-sm text-gray-400">
            {readOnly ? "User này chưa có nguồn nào." : (
              <>Chưa có nguồn nào. <button className="text-primary underline cursor-pointer" onClick={() => setShowAddSource(true)}>Thêm ngay</button></>
            )}
          </div>
        ) : (
          <div className="divide-y divide-gray-50">
            {sources.data.map(src => {
              const isCrawling = src.is_crawling || crawlingIds.has(src.id);
              return (
              <div key={src.id} className={clsx(
                "px-4 py-3 flex items-center gap-3 hover:bg-gray-50 transition-colors cursor-pointer",
                filterSourceId === src.id && "bg-primary-50",
              )} onClick={() => setFilterSourceId(filterSourceId === src.id ? undefined : src.id)}>
                <input type="checkbox" checked={filterSourceId === src.id} readOnly className="accent-primary" />
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-medium text-ink truncate flex items-center gap-2">
                    {src.name || src.url}
                    {isCrawling && (
                      <span className="inline-flex items-center gap-1 text-[10px] font-semibold text-primary bg-primary-50 px-1.5 py-0.5 rounded-full">
                        <Loader2 size={9} className="animate-spin" /> đang quét
                      </span>
                    )}
                  </div>
                  {src.name && <div className="text-xs text-gray-400 truncate">{src.url}</div>}
                  <div className="text-xs text-gray-400 mt-0.5 flex items-center gap-2 flex-wrap">
                    <span className={clsx(isCrawling && "text-primary font-medium tabular-nums")}>
                      {src.total_candidates_found} candidates
                    </span>
                    {src.last_crawled_at && <span>· quét lúc {new Date(src.last_crawled_at + "Z").toLocaleString("vi-VN")}</span>}
                    {/* Category của nguồn → gán cho mọi dự án promote */}
                    <span className="inline-flex items-center gap-1" onClick={e => e.stopPropagation()}>
                      · Category:
                      <select
                        value={src.category || ""}
                        disabled={readOnly}
                        onChange={e => handleSetCategory(src.id, e.target.value)}
                        className={clsx(
                          "text-[11px] border rounded-md px-1.5 py-0.5 focus:outline-none focus:ring-1 focus:ring-primary/40 cursor-pointer",
                          src.category ? "border-primary-200 text-primary font-medium bg-primary-50/50" : "border-gray-200 text-gray-400",
                        )}
                        title="Category gán cho mọi dự án tìm được từ nguồn này"
                      >
                        <option value="">— chưa đặt —</option>
                        {SOURCE_CATEGORIES.map(c => <option key={c} value={c}>{c}</option>)}
                      </select>
                    </span>
                  </div>
                </div>
                <div className={clsx("flex items-center gap-1 shrink-0", readOnly && "hidden")}>
                  <button
                    onClick={e => {
                      e.stopPropagation();
                      const proxyIds =
                        selectedProxyId === ALL_PROXIES
                          ? (proxies.data || []).map(p => p.id)
                          : selectedProxyId
                          ? [selectedProxyId]
                          : [];
                      handleCrawl(src.id, {
                        proxy_ids: proxyIds.length ? proxyIds : undefined,
                        max_pages: selectedMaxPages > 0 ? selectedMaxPages : undefined,
                        incremental: incrementalMode,
                      });
                    }}
                    hidden={isCrawling}
                    className="flex items-center gap-1 text-xs px-2 py-1 rounded-lg bg-primary text-white hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer transition-colors"
                  >
                    <Play size={12} /> Quét
                  </button>
                  {isCrawling && (
                    <button
                      onClick={e => { e.stopPropagation(); handleStop(src.id); }}
                      disabled={stoppingIds.has(src.id)}
                      className="flex items-center gap-1 text-xs px-2 py-1 rounded-lg bg-red-500 text-white hover:bg-red-600 disabled:opacity-60 cursor-pointer transition-colors"
                      title="Dừng quét — giữ lại các dự án đã tìm được"
                    >
                      {stoppingIds.has(src.id)
                        ? <><RefreshCw size={12} className="animate-spin" /> Đang dừng…</>
                        : <><Square size={11} /> Dừng</>}
                    </button>
                  )}
                  <button
                    onClick={e => { e.stopPropagation(); handleDeleteSource(src.id); }}
                    disabled={isCrawling}
                    className="p-1.5 rounded-lg text-gray-400 hover:text-red-500 hover:bg-red-50 disabled:opacity-30 disabled:cursor-not-allowed cursor-pointer transition-colors"
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
              </div>
              );
            })}
          </div>
        )}
      </Card>

      {/* ── Pipeline summary ── */}
      {s && (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
          {[
            { label: "Discovered", value: s.discovered, color: "text-gray-600" },
            { label: "Homepage", value: s.homepage_resolved, color: "text-blue-600" },
            { label: "Traffic", value: s.traffic_scanned, color: "text-purple-600" },
            { label: "Affiliate found", value: s.affiliate_found, color: "text-green-600" },
            { label: "Promoted", value: s.promoted, color: "text-emerald-600" },
            { label: "No affiliate", value: s.no_affiliate_found, color: "text-red-500" },
          ].map(item => (
            <Card key={item.label} className="text-center !py-3 !px-2">
              <div className={clsx("text-2xl font-bold", item.color)}>{item.value}</div>
              <div className="text-[11px] text-gray-400 mt-0.5">{item.label}</div>
            </Card>
          ))}
        </div>
      )}

      {/* ── Candidates table ── */}
      <Card className="!p-0 overflow-hidden">
        <div className="px-4 py-3 border-b border-gray-100 flex flex-wrap items-center gap-2">
          <h3 className="text-sm font-semibold text-ink mr-2">Candidates</h3>
          <div className="relative">
            <Search size={13} className="absolute left-2 top-1/2 -translate-y-1/2 text-gray-400 pointer-events-none" />
            <input
              type="text"
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              placeholder="Tìm theo domain…"
              className="text-xs border border-gray-200 rounded-lg pl-7 pr-6 py-1.5 w-44 focus:outline-none focus:ring-2 focus:ring-primary/30"
            />
            {searchQuery && (
              <button
                type="button"
                onClick={() => setSearchQuery("")}
                aria-label="Xoá tìm kiếm"
                className="absolute right-1.5 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600 cursor-pointer"
              >
                <X size={12} />
              </button>
            )}
          </div>
          <select
            value={filterStatus}
            onChange={e => setFilterStatus(e.target.value)}
            className="text-xs border border-gray-200 rounded-lg px-2 py-1.5 focus:outline-none focus:ring-2 focus:ring-primary/30"
          >
            <option value="">Tất cả status</option>
            <option value="discovered">discovered</option>
            <option value="homepage_resolved">homepage_resolved</option>
            <option value="traffic_scanned">traffic_scanned</option>
            <option value="affiliate_found">affiliate_found</option>
            <option value="promoted">promoted</option>
            <option value="no_affiliate_found">no_affiliate_found</option>
          </select>
          <select
            value={filterDetection}
            onChange={e => setFilterDetection(e.target.value)}
            className="text-xs border border-gray-200 rounded-lg px-2 py-1.5 focus:outline-none focus:ring-2 focus:ring-primary/30"
          >
            <option value="">Tất cả detection</option>
            <option value="direct_outbound">📋 direct_outbound</option>
            <option value="label_match">⭐ label_match</option>
            <option value="fallback_outbound">🔹 fallback_outbound</option>
          </select>
          <select
            value={filterTraffic}
            onChange={e => setFilterTraffic(e.target.value)}
            className="text-xs border border-gray-200 rounded-lg px-2 py-1.5 focus:outline-none focus:ring-2 focus:ring-primary/30"
          >
            <option value="">TT Traffic: tất cả</option>
            <option value="pending">⏳ chưa quét</option>
            <option value="scanned">✅ có traffic</option>
            <option value="not_found">✅ đã quét · 0</option>
            <option value="failed">❌ lỗi</option>
          </select>
          <select
            value={filterMinTraffic}
            onChange={e => setFilterMinTraffic(e.target.value)}
            title="Chỉ hiện dự án có traffic ≥ ngưỡng"
            className="text-xs border border-gray-200 rounded-lg px-2 py-1.5 focus:outline-none focus:ring-2 focus:ring-primary/30"
          >
            <option value="">Traffic: không lọc</option>
            <option value="10000">≥ 10K</option>
            <option value="50000">≥ 50K</option>
            <option value="100000">≥ 100K</option>
            <option value="200000">≥ 200K</option>
            <option value="500000">≥ 500K</option>
            <option value="1000000">≥ 1M</option>
          </select>
          <select
            value={filterAffiliate}
            onChange={e => setFilterAffiliate(e.target.value)}
            className="text-xs border border-gray-200 rounded-lg px-2 py-1.5 focus:outline-none focus:ring-2 focus:ring-primary/30"
          >
            <option value="">TT Affiliate: tất cả</option>
            <option value="pending">⏳ chưa dò</option>
            <option value="found">✅ có</option>
            <option value="not_found">❌ không có</option>
          </select>
          {candidateTotal > 0 && (
            <a
              href={api.exportDiscoveryCandidatesByFilterCsvUrl({
                source_id: filterSourceId,
                status: filterStatus || undefined,
                detection_method: filterDetection || undefined,
                traffic_state: filterTraffic || undefined,
                affiliate_state: filterAffiliate || undefined,
                min_traffic: filterMinTraffic ? Number(filterMinTraffic) : undefined,
                q: debouncedQuery || undefined,
                owner_id: viewOwnerId,
              })}
              title="Xuất CSV TẤT CẢ dự án khớp bộ lọc hiện tại (không cần tích chọn)"
            >
              <Button variant="secondary" className="text-xs !py-1.5">
                <Download size={12} className="mr-1" /> Xuất tất cả ({candidateTotal})
              </Button>
            </a>
          )}
          {!readOnly && selectedIds.size > 0 && (
            <div className="ml-auto flex items-center gap-2">
              {/* Proxy áp dụng cho Dò affiliate / Chạy pipeline (đồng bộ với thanh Nguồn) */}
              <div className="flex items-center gap-1.5">
                <Radar size={13} className="text-gray-400" />
                <select
                  value={selectedProxyId}
                  onChange={e => setSelectedProxyId(e.target.value)}
                  className="text-xs border border-gray-200 rounded-lg px-2 py-1.5 max-w-[190px] focus:outline-none focus:ring-2 focus:ring-primary/30"
                  title="Proxy dùng khi Dò affiliate / Chạy pipeline (site chặn như titanfx/coinexx cần proxy)"
                >
                  <option value="">Proxy: không dùng</option>
                  {(proxies.data?.length || 0) > 0 && (
                    <option value={ALL_PROXIES}>🔄 Proxy: tất cả ({proxies.data!.length})</option>
                  )}
                  {(proxies.data || []).map(p => (
                    <option key={p.id} value={p.id}>Proxy: {p.label || `${p.host}:${p.port}`}</option>
                  ))}
                </select>
              </div>
              <Button
                variant="secondary"
                onClick={handleScanTraffic}
                disabled={scanningTraffic || runningPipeline || detectingAff}
                className="text-xs !py-1.5"
                title="Chỉ quét traffic SimilarWeb cho các candidate đã chọn"
              >
                {scanningTraffic ? <RefreshCw size={12} className="animate-spin mr-1" /> : <Gauge size={12} className="mr-1" />}
                Quét traffic ({selectedIds.size})
              </Button>
              <Button
                variant="secondary"
                onClick={handleDetectAffiliate}
                disabled={detectingAff || runningPipeline || scanningTraffic || checkingLink}
                className="text-xs !py-1.5"
                title="Dò chương trình affiliate/partner của các domain đã chọn (CloakBrowser + keyword)"
              >
                {detectingAff ? <RefreshCw size={12} className="animate-spin mr-1" /> : <Radar size={12} className="mr-1" />}
                Dò affiliate ({selectedIds.size})
              </Button>
              <Button
                variant="secondary"
                onClick={handleDetectAffiliateSearch}
                disabled={detectingAffSearch || detectingAff || runningPipeline || scanningTraffic || checkingLink}
                className="text-xs !py-1.5"
                title="Dò affiliate qua Google search (miễn phí, như 'Tìm trang chủ') — dùng khi site chặn fetch (Cloudflare) hoặc trang affiliate ở path sâu/lạ"
              >
                {detectingAffSearch ? <RefreshCw size={12} className="animate-spin mr-1" /> : <Search size={12} className="mr-1" />}
                Dò affiliate (Google) ({selectedIds.size})
              </Button>
              <Button
                variant="secondary"
                onClick={handleCheckLink}
                disabled={checkingLink || detectingAff || runningPipeline || scanningTraffic}
                className="text-xs !py-1.5"
                title="Đánh giá link affiliate: mở thật để phát hiện 404/soft-404 (link chết)"
              >
                {checkingLink ? <RefreshCw size={12} className="animate-spin mr-1" /> : <CheckCircle2 size={12} className="mr-1" />}
                Kiểm tra link ({selectedIds.size})
              </Button>
              <Button
                onClick={handleRunPipeline}
                disabled={runningPipeline || scanningTraffic || detectingAff || checkingLink}
                className="text-xs !py-1.5"
              >
                {runningPipeline ? <RefreshCw size={12} className="animate-spin mr-1" /> : <Play size={12} className="mr-1" />}
                Chạy pipeline ({selectedIds.size})
              </Button>
              <a href={api.exportDiscoveryCandidatesCsvUrl(Array.from(selectedIds))}
                 title="Xuất CSV các dự án đã chọn (đầy đủ thông tin)">
                <Button variant="secondary" className="text-xs !py-1.5">
                  <Download size={12} className="mr-1" /> Xuất CSV ({selectedIds.size})
                </Button>
              </a>
            </div>
          )}
        </div>

        {candidates.isError && candidateItems.length === 0 ? (
          <div className="py-16 text-center">
            <p className="text-sm font-medium text-red-500">Không tải được danh sách candidates</p>
            <p className="text-xs text-gray-400 mt-1">Máy chủ đang bận hoặc mất kết nối — không phải là chưa có dữ liệu.</p>
            <Button variant="secondary" className="mt-3" onClick={() => candidates.refetch()}>
              <RefreshCw size={14} className="mr-1" /> Thử lại
            </Button>
          </div>
        ) : candidateItems.length === 0 ? (
          <EmptyState
            icon={Radar}
            title="Chưa có candidates"
            description={readOnly
              ? "User này chưa quét được dự án nào."
              : 'Chọn một nguồn và nhấn "Quét" để bắt đầu tìm kiếm dự án.'}
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-canvas text-xs uppercase text-gray-500 tracking-wider">
                <tr>
                  <th className="px-4 py-3 w-8">
                    {!readOnly && (
                      <input type="checkbox"
                        checked={selectedIds.size === candidateItems.length && candidateItems.length > 0}
                        onChange={toggleSelectAll}
                        className="accent-primary"
                      />
                    )}
                  </th>
                  <th className="px-4 py-3 text-left">Domain</th>
                  <th className="px-4 py-3 text-left">Nguồn</th>
                  <th className="px-4 py-3 text-left">Tên gợi ý</th>
                  <th className="px-4 py-3 text-left">Detection</th>
                  <th className="px-4 py-3 text-right">Traffic</th>
                  <th className="px-4 py-3 text-left">TT Traffic</th>
                  <th className="px-4 py-3 text-left">TT Affiliate</th>
                  <th className="px-4 py-3 text-left">Affiliate link</th>
                  <th className="px-4 py-3 text-left">TT Link</th>
                  <th className="px-4 py-3 text-left whitespace-nowrap">Số ngày QC</th>
                  <th className="px-4 py-3 text-left whitespace-nowrap">QC lần đầu</th>
                  <th className="px-4 py-3 text-left whitespace-nowrap">QC lần cuối</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {candidateItems.map(c => (
                  <tr key={c.id} className="hover:bg-primary-50/30 transition-colors">
                    <td className="px-4 py-3">
                      {!readOnly && (
                        <input type="checkbox" checked={selectedIds.has(c.id)} onChange={() => toggleSelect(c.id)} className="accent-primary" />
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-1.5">
                        {c.is_primary && <span title="Primary candidate" className="text-amber-500">★</span>}
                        <a
                          href={c.homepage_url || c.raw_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="font-mono text-xs text-primary hover:underline flex items-center gap-1"
                        >
                          {c.domain}
                          <ExternalLink size={10} />
                        </a>
                      </div>
                      {c.source_page_url && (
                        <div className="text-[10px] text-gray-400 mt-0.5 truncate max-w-[200px]" title={c.source_page_url}>
                          ↳ {c.source_page_url.replace(/^https?:\/\/[^/]+/, '')}
                        </div>
                      )}
                    </td>
                    <td className="px-4 py-3 text-xs text-gray-600 max-w-[160px]">
                      <span className="truncate block" title={sourceById.get(c.source_id)?.url || ""}>
                        {sourceLabel(c.source_id)}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-xs text-gray-600 max-w-[140px] truncate">{c.suggested_name || "—"}</td>
                    <td className="px-4 py-3"><DetectionBadge method={c.detection_method} /></td>
                    <td className="px-4 py-3 text-right tabular-nums text-xs">
                      {/* Đã quét (scanned/not_found) → hiện số, kể cả 0. Chưa quét/lỗi → "—" */}
                      {(c.traffic_status === "scanned" || c.traffic_status === "not_found")
                        ? <span title={`${(c.traffic_monthly ?? 0).toLocaleString("vi-VN")} visits/tháng`}>{fmtTraffic(c.traffic_monthly ?? 0)}</span>
                        : "—"}
                    </td>
                    <td className="px-4 py-3"><TrafficStatusBadge c={c} /></td>
                    <td className="px-4 py-3"><AffiliateStatusBadge c={c} /></td>
                    <td className="px-4 py-3 text-xs max-w-[220px]">
                      {c.affiliate_url ? (
                        <a href={c.affiliate_url} target="_blank" rel="noopener noreferrer"
                          className="text-green-600 hover:underline flex items-center gap-1 truncate"
                          title={c.affiliate_url}>
                          <span className="truncate">{c.affiliate_url.replace(/^https?:\/\/(www\.)?/, "")}</span>
                          <ExternalLink size={10} className="shrink-0" />
                        </a>
                      ) : (
                        <span className="text-gray-300">—</span>
                      )}
                    </td>
                    <td className="px-4 py-3"><LinkStatusBadge c={c} /></td>
                    <td className="px-4 py-3 text-xs whitespace-nowrap tabular-nums text-ink">
                      {c.ad_days_shown != null ? c.ad_days_shown : <span className="text-gray-300">—</span>}
                    </td>
                    <td className="px-4 py-3 text-xs whitespace-nowrap text-gray-500">
                      {c.ad_first_shown ? fmtAdDate(c.ad_first_shown) : <span className="text-gray-300">—</span>}
                    </td>
                    <td className="px-4 py-3 text-xs whitespace-nowrap text-gray-500">
                      {c.ad_last_shown ? fmtAdDate(c.ad_last_shown) : <span className="text-gray-300">—</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* ── Phân trang ── */}
        {candidateTotal > 0 && (
          <div className="flex items-center justify-between px-4 py-3 border-t border-gray-100 text-xs text-gray-500">
            <div className="flex items-center gap-3">
              <span>
                Hiển thị <b className="text-ink">{(candidatePage - 1) * pageSize + 1}</b>–
                <b className="text-ink">{Math.min(candidatePage * pageSize, candidateTotal)}</b> / {candidateTotal} dự án
              </span>
              <select
                value={pageSize}
                onChange={e => { setPageSize(Number(e.target.value)); setCandidatePage(1); }}
                aria-label="Số dự án mỗi trang"
                className="border border-gray-200 rounded-lg px-2 py-1 focus:outline-none focus:ring-2 focus:ring-primary/30"
              >
                {PAGE_SIZE_OPTIONS.map(n => <option key={n} value={n}>{n}/trang</option>)}
              </select>
            </div>
            <div className="flex items-center gap-1">
              <button
                onClick={() => setCandidatePage(p => Math.max(1, p - 1))}
                disabled={candidatePage <= 1}
                className="px-2.5 py-1 rounded-lg border border-gray-200 hover:border-primary-200 disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer transition-colors"
              >
                ‹ Trước
              </button>
              <span className="px-2 tabular-nums">Trang {candidatePage} / {totalPages}</span>
              <button
                onClick={() => setCandidatePage(p => Math.min(totalPages, p + 1))}
                disabled={candidatePage >= totalPages}
                className="px-2.5 py-1 rounded-lg border border-gray-200 hover:border-primary-200 disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer transition-colors"
              >
                Sau ›
              </button>
            </div>
          </div>
        )}
      </Card>

      {/* ── Blacklist panel ── */}
      <Card className="!p-0 overflow-hidden">
        <button
          className="w-full px-4 py-3 flex items-center justify-between border-b border-gray-100 cursor-pointer hover:bg-gray-50"
          onClick={() => setExpandedBlacklist(b => !b)}
        >
          <h3 className="text-sm font-semibold text-ink">
            Blacklist domain ({blacklist.data?.length || 0})
          </h3>
          {expandedBlacklist ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </button>
        {expandedBlacklist && (
          <div className="p-4">
            <div className="flex flex-wrap gap-1.5 mb-3">
              {(blacklist.data || []).map(b => (
                <span key={b.id} className="inline-flex items-center gap-1 text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded-full">
                  {b.domain}
                  <button
                    onClick={async () => { await api.deleteDiscoveryBlacklist(b.id); qc.invalidateQueries({ queryKey: ["discovery-blacklist"] }); }}
                    className="text-gray-400 hover:text-red-500 cursor-pointer"
                  >
                    <X size={10} />
                  </button>
                </span>
              ))}
            </div>
            <Button variant="secondary" onClick={() => setShowAddBlacklist(true)} className="text-xs !py-1.5">
              <Plus size={12} className="mr-1" /> Thêm domain
            </Button>
          </div>
        )}
      </Card>

      {showAddSource && (
        <AddSourceModal
          onClose={() => setShowAddSource(false)}
          onAdded={() => qc.invalidateQueries({ queryKey: ["discovery-sources"] })}
        />
      )}
      {showImport && (
        <ImportDomainsModal
          onClose={() => setShowImport(false)}
          onAdded={() => { qc.invalidateQueries({ queryKey: ["discovery-sources"] }); qc.invalidateQueries({ queryKey: ["discovery-candidates"] }); }}
        />
      )}
      {showAddBlacklist && (
        <AddBlacklistModal
          onClose={() => setShowAddBlacklist(false)}
          onAdded={() => qc.invalidateQueries({ queryKey: ["discovery-blacklist"] })}
        />
      )}
    </div>
  );
}
