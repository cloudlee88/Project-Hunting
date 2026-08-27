"use client";
import { useState, useMemo, useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Globe, Search, RefreshCw, CheckCircle2, AlertTriangle,
  ExternalLink, Pencil, Check, X, ChevronLeft, ChevronRight,
  Database, Filter, SlidersHorizontal, Link2, Link2Off,
  Layers, ChevronDown, TrendingUp, Square, CheckSquare, MinusSquare,
} from "lucide-react";
import * as api from "@/lib/api";
import { Card } from "@/components/Card";
import { Badge } from "@/components/Badge";
import { Button } from "@/components/Button";
import { useToast } from "@/lib/toast";

// ──────────────────────────────────────────────────────────────────────────────
// Intermediate link detection
// Sync với backend: backend/app/services/crawlers/homepage_finder.py
// Logic: hostname (không phải full URL) kết thúc bằng affiliate subdomain pattern
// ──────────────────────────────────────────────────────────────────────────────
// { suffix, key } — used by both isIntermediate() and getProgramPlatform()
const PLATFORM_PATTERNS: { suffix: string; key: string }[] = [
  { suffix: ".getrewardful.com", key: "rewardful" },
  { suffix: ".rewardful.com",    key: "rewardful" },
  { suffix: ".tolt.io",          key: "tolt" },
  { suffix: ".everflowclient.io",key: "everflow" },
  { suffix: ".tapfiliate.com",   key: "tapfiliate" },
  { suffix: ".firstpromoter.com",key: "firstpromoter" },
  { suffix: ".leaddyno.com",     key: "leaddyno" },
  { suffix: ".promotekit.com",   key: "promotekit" },
  { suffix: ".partnerstack.com", key: "partnerstack" },
  { suffix: ".growsumo.com",     key: "partnerstack" },
  { suffix: ".partnercentric.com",key: "partnercentric" },
  { suffix: ".osomfinance.com",  key: "osom" },
  { suffix: ".refersion.com",    key: "refersion" },
  { suffix: ".affiliatly.com",   key: "affiliatly" },
  { suffix: ".trackdesk.com",    key: "trackdesk" },
  { suffix: ".reditus.io",       key: "reditus" },
  { suffix: ".cello.so",         key: "cello" },
  { suffix: ".viral-loops.com",  key: "viralloops" },
  { suffix: ".tune.com",         key: "tune" },
  { suffix: ".hasoffers.com",    key: "tune" },
  { suffix: ".extole.com",       key: "extole" },
  { suffix: ".friendbuy.com",    key: "friendbuy" },
  { suffix: ".impact.com",       key: "impact" },
];

// Keep the flat array for backward-compat with isIntermediate()
const AFFILIATE_PLATFORMS = PLATFORM_PATTERNS.map((p) => p.suffix);

const PLATFORM_LABELS: Record<string, string> = {
  rewardful: "Rewardful", tolt: "Tolt", everflow: "Everflow",
  tapfiliate: "Tapfiliate", firstpromoter: "FirstPromoter", leaddyno: "LeadDyno",
  promotekit: "PromoteKit", partnerstack: "PartnerStack", partnercentric: "PartnerCentric",
  osom: "Osom Finance", refersion: "Refersion", affiliatly: "Affiliatly",
  trackdesk: "TrackDesk", reditus: "Reditus", cello: "Cello",
  viralloops: "Viral Loops", tune: "Tune/HasOffers", extole: "Extole",
  friendbuy: "FriendBuy", impact: "Impact",
  "in-house": "In-house", dub: "Dub", awin: "AWIN",
  postaffiliatepro: "Post Affiliate Pro", taprefer: "TapRefer", goaffpro: "GoAffPro",
  rewardstack: "RewardStack",
};

function getProgramPlatform(p: api.Program): string | null {
  // Prefer directory_network (set by crawlers) over URL detection
  if (p.directory_network) return p.directory_network.toLowerCase();
  const url = p.url;
  if (!url) return null;
  try {
    const host = new URL(url.includes("://") ? url : `https://${url}`).hostname.toLowerCase();
    for (const { suffix, key } of PLATFORM_PATTERNS) {
      if (host.endsWith(suffix)) return key;
    }
  } catch {
    const lower = url.toLowerCase();
    for (const { suffix, key } of PLATFORM_PATTERNS) {
      if (lower.includes(suffix)) return key;
    }
  }
  return null;
}

/**
 * Detect intermediate/affiliate URL.
 * Dùng hostname parsing (chuẩn) thay vì substring check để tránh false positive.
 * VD: "mypromotekitapp.com" KHÔNG là intermediate dù có "promotekit" trong tên.
 */
function isIntermediate(url: string | null): boolean {
  if (!url) return false;
  try {
    const raw = url.includes("://") ? url : `https://${url}`;
    const host = new URL(raw).hostname.toLowerCase();
    return AFFILIATE_PLATFORMS.some((p) => host.endsWith(p));
  } catch {
    // fallback nếu URL không parse được
    const lower = url.toLowerCase();
    return AFFILIATE_PLATFORMS.some((p) => lower.includes(p));
  }
}

type FilterType = "all" | "intermediate" | "resolved" | "empty";

const PAGE_SIZE = 50;

export default function HomepageManagerPage() {
  const { push } = useToast();
  const qc = useQueryClient();

  const [filter, setFilter] = useState<FilterType>("all");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [showAdvanced, setShowAdvanced] = useState(false);

  // Multi-select source/category/platform filters
  const [selectedSources, setSelectedSources] = useState<string[]>([]);
  const [selectedCategories, setSelectedCategories] = useState<string[]>([]);
  const [selectedPlatforms, setSelectedPlatforms] = useState<string[]>([]);
  const [showSourceFilter, setShowSourceFilter] = useState(false);
  const [showCategoryFilter, setShowCategoryFilter] = useState(false);
  const [showPlatformFilter, setShowPlatformFilter] = useState(false);

  // Row multi-select for batch discover
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [discoverProgress, setDiscoverProgress] = useState<{ done: number; total: number } | null>(null);

  // Batch settings
  const [limit, setLimit] = useState(500);

  // Inline edit
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editValue, setEditValue] = useState("");

  // Fetch sources
  const { data: sourcesData } = useQuery({
    queryKey: ["sources"],
    queryFn: api.listSources,
    staleTime: 60_000,
  });

  // Fetch categories
  const { data: categoriesData } = useQuery({
    queryKey: ["categories-homepage"],
    queryFn: () => api.listProgramCategories(undefined),
    staleTime: 60_000,
  });

  // Fetch all programs (client-side filtering for multi-select)
  const { data: programsData, isLoading } = useQuery({
    queryKey: ["programs-homepage"],
    queryFn: () => api.listPrograms({ page: 1, page_size: 25000 }),
    staleTime: 30_000,
  });

  const allPrograms = programsData?.items ?? [];

  // Source counts for badges
  const sourceCounts = useMemo(() => {
    const m: Record<string, number> = {};
    allPrograms.forEach((p) => { if (p.source) m[p.source] = (m[p.source] ?? 0) + 1; });
    return m;
  }, [allPrograms]);

  // Category counts
  const categoryCounts = useMemo(() => {
    const m: Record<string, number> = {};
    allPrograms.forEach((p) => { if (p.category) m[p.category] = (m[p.category] ?? 0) + 1; });
    return m;
  }, [allPrograms]);

  // Platform counts — computed from programs already filtered by source + category
  // so the dropdown only shows platforms relevant to the current source/category selection
  const { platformCounts, availablePlatforms } = useMemo(() => {
    let base = allPrograms;
    if (selectedSources.length > 0)
      base = base.filter((p) => p.source && selectedSources.includes(p.source));
    if (selectedCategories.length > 0)
      base = base.filter((p) => p.category && selectedCategories.includes(p.category));
    const m: Record<string, number> = {};
    base.forEach((p) => {
      const key = getProgramPlatform(p);
      if (key) m[key] = (m[key] ?? 0) + 1;
    });
    const sorted = Object.entries(m)
      .sort((a, b) => b[1] - a[1])
      .map(([key]) => key);
    return { platformCounts: m, availablePlatforms: sorted };
  }, [allPrograms, selectedSources, selectedCategories]);

  // Stats
  const stats = useMemo(() => {
    const total = allPrograms.length;
    const intermediate = allPrograms.filter((p) => isIntermediate(p.url)).length;
    const empty = allPrograms.filter((p) => !p.url).length;
    return { total, intermediate, resolved: total - intermediate - empty, empty };
  }, [allPrograms]);

  // Apply filters
  const filtered = useMemo(() => {
    let list = allPrograms;
    if (selectedSources.length > 0)
      list = list.filter((p) => p.source && selectedSources.includes(p.source));
    if (selectedCategories.length > 0)
      list = list.filter((p) => p.category && selectedCategories.includes(p.category));
    if (selectedPlatforms.length > 0)
      list = list.filter((p) => { const k = getProgramPlatform(p); return k !== null && selectedPlatforms.includes(k); });
    if (filter === "intermediate") list = list.filter((p) => isIntermediate(p.url));
    else if (filter === "resolved") list = list.filter((p) => !isIntermediate(p.url) && !!p.url);
    else if (filter === "empty") list = list.filter((p) => !p.url);
    if (search.trim()) {
      const q = search.toLowerCase();
      list = list.filter(
        (p) =>
          p.name?.toLowerCase().includes(q) ||
          p.url?.toLowerCase().includes(q) ||
          p.category?.toLowerCase().includes(q)
      );
    }
    return list;
  }, [allPrograms, filter, search, selectedSources, selectedCategories, selectedPlatforms]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const paginated = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
  const pageIds = paginated.map((p) => p.id);

  // Auto-clear platform selections that no longer exist in the current source/category context
  useEffect(() => {
    if (selectedPlatforms.length === 0) return;
    const valid = new Set(availablePlatforms);
    const stillValid = selectedPlatforms.filter((k) => valid.has(k));
    if (stillValid.length !== selectedPlatforms.length) {
      setSelectedPlatforms(stillValid);
      setPage(1);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [availablePlatforms]);

  // Clear selection when filter/search changes
  useEffect(() => { setSelectedIds(new Set()); }, [filter, search, selectedSources, selectedCategories, selectedPlatforms]);

  // Select-all state for current page
  const allPageSelected = pageIds.length > 0 && pageIds.every((id) => selectedIds.has(id));
  const somePageSelected = pageIds.some((id) => selectedIds.has(id)) && !allPageSelected;

  const toggleRow = (id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const togglePageAll = () => {
    if (allPageSelected) {
      // deselect current page
      setSelectedIds((prev) => {
        const next = new Set(prev);
        pageIds.forEach((id) => next.delete(id));
        return next;
      });
    } else {
      // select all current page
      setSelectedIds((prev) => {
        const next = new Set(prev);
        pageIds.forEach((id) => next.add(id));
        return next;
      });
    }
  };

  // Close filter dropdowns on outside click
  useEffect(() => {
    if (!showSourceFilter && !showCategoryFilter && !showPlatformFilter) return;
    const handler = (e: MouseEvent) => {
      const target = e.target as Element;
      if (!target.closest("[data-filter-dropdown]")) {
        setShowSourceFilter(false);
        setShowCategoryFilter(false);
        setShowPlatformFilter(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [showSourceFilter, showCategoryFilter, showPlatformFilter]);

  // Toggle helpers
  const toggleSource = (code: string) => {
    setSelectedSources((prev) =>
      prev.includes(code) ? prev.filter((s) => s !== code) : [...prev, code]
    );
    setPage(1);
  };
  const toggleCategory = (cat: string) => {
    setSelectedCategories((prev) =>
      prev.includes(cat) ? prev.filter((c) => c !== cat) : [...prev, cat]
    );
    setPage(1);
  };
  const togglePlatform = (key: string) => {
    setSelectedPlatforms((prev) =>
      prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]
    );
    setPage(1);
  };

  // Batch discover ALL (by source/limit)
  const batchDiscoverAll = useMutation({
    mutationFn: () =>
      api.discoverHomepages({
        source: selectedSources.length === 1 ? selectedSources[0] : undefined,
        limit,
        concurrency: 1,
        method: "browser",
      }),
    onSuccess: (r) => {
      push({
        title: "Discover hoàn thành",
        type: r.failed > 0 ? "info" : "success",
        message: `Cập nhật ${r.updated}/${r.total} · Thất bại ${r.failed}`,
      });
      qc.invalidateQueries({ queryKey: ["programs-homepage"] });
    },
    onError: (e: any) => push({ type: "error", title: "Lỗi", message: e.message }),
  });

  // Batch discover SELECTED IDs — gọi từng singleDiscover sequentially
  const batchDiscoverSelected = useMutation({
    mutationFn: async () => {
      const ids = Array.from(selectedIds);
      let updated = 0, failed = 0;
      setDiscoverProgress({ done: 0, total: ids.length });
      for (let i = 0; i < ids.length; i++) {
        try {
          const r = await api.discoverProgramHomepage(ids[i], "browser");
          if (r.found) updated++; else failed++;
        } catch {
          failed++;
        }
        setDiscoverProgress({ done: i + 1, total: ids.length });
      }
      return { total: ids.length, updated, failed };
    },
    onSuccess: (r) => {
      setDiscoverProgress(null);
      push({
        title: "Discover đã chọn hoàn thành",
        type: r.failed > 0 ? "info" : "success",
        message: `Cập nhật ${r.updated}/${r.total} · Thất bại ${r.failed}`,
      });
      setSelectedIds(new Set());
      qc.invalidateQueries({ queryKey: ["programs-homepage"] });
    },
    onError: (e: any) => {
      setDiscoverProgress(null);
      push({ type: "error", title: "Lỗi", message: e.message });
    },
  });

  // Single discover
  const singleDiscover = useMutation({
    mutationFn: ({ id }: { id: number }) => api.discoverProgramHomepage(id, "browser"),
    onSuccess: (r) => {
      push({
        title: r.found ? "Tìm được trang chủ" : "Không tìm được",
        type: r.found ? "success" : "info",
        message: r.found ? r.url ?? "" : "Thử phương thức khác hoặc nhập thủ công",
      });
      qc.invalidateQueries({ queryKey: ["programs-homepage"] });
    },
    onError: (e: any) => push({ type: "error", title: "Lỗi", message: e.message }),
  });

  // Manual URL update
  const updateUrl = useMutation({
    mutationFn: ({ id, url }: { id: number; url: string }) => api.updateProgramUrl(id, url),
    onSuccess: () => {
      push({ type: "success", title: "Đã cập nhật URL", message: "" });
      setEditingId(null);
      qc.invalidateQueries({ queryKey: ["programs-homepage"] });
    },
    onError: (e: any) => push({ type: "error", title: "Lỗi", message: e.message }),
  });

  const isSingleLoading = (id: number) =>
    singleDiscover.isPending && (singleDiscover.variables as any)?.id === id;

  const isAnyDiscoverRunning = batchDiscoverAll.isPending || batchDiscoverSelected.isPending;

  return (
    <div className="max-w-[1300px] mx-auto px-4 py-8 space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold text-ink flex items-center gap-2">
            <Link2 size={22} className="text-violet-500" />
            Quản lý Trang chủ
          </h1>
          <p className="text-sm text-gray-400 mt-1">
            Tìm và cập nhật URL trang chủ thực cho tất cả chương trình affiliate — thay thế link intermediate/affiliate bằng domain thực
          </p>
        </div>
        <Button
          variant="cta"
          size="sm"
          disabled={isAnyDiscoverRunning}
          onClick={() => batchDiscoverAll.mutate()}
          className="gap-2 shrink-0 bg-violet-600 hover:bg-violet-700"
        >
          {batchDiscoverAll.isPending ? (
            <><RefreshCw size={14} className="animate-spin" /> Đang chạy…</>
          ) : (
            <><RefreshCw size={14} /> Discover tất cả</>
          )}
        </Button>
      </div>

      {/* Stats cards */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        <Card className="p-4">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-violet-50 flex items-center justify-center shrink-0">
              <Database size={18} className="text-violet-500" />
            </div>
            <div>
              <div className="text-2xl font-bold text-ink leading-none">{stats.total.toLocaleString()}</div>
              <div className="text-xs text-gray-400 mt-0.5">Tổng chương trình</div>
            </div>
          </div>
        </Card>
        <Card className="p-4">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-emerald-50 flex items-center justify-center shrink-0">
              <CheckCircle2 size={18} className="text-emerald-500" />
            </div>
            <div>
              <div className="text-2xl font-bold text-emerald-600 leading-none">{stats.resolved.toLocaleString()}</div>
              <div className="text-xs text-gray-400 mt-0.5">Đã có trang chủ</div>
            </div>
          </div>
        </Card>
        <Card className="p-4">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-amber-50 flex items-center justify-center shrink-0">
              <Link2Off size={18} className="text-amber-500" />
            </div>
            <div>
              <div className="text-2xl font-bold text-amber-600 leading-none">{stats.intermediate.toLocaleString()}</div>
              <div className="text-xs text-gray-400 mt-0.5">Link trung gian</div>
            </div>
          </div>
        </Card>
        <Card className="p-4">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-gray-50 flex items-center justify-center shrink-0">
              <Globe size={18} className="text-gray-400" />
            </div>
            <div>
              <div className="text-2xl font-bold text-gray-500 leading-none">{stats.empty.toLocaleString()}</div>
              <div className="text-xs text-gray-400 mt-0.5">Chưa có URL</div>
            </div>
          </div>
        </Card>
      </div>

      {/* Filters */}
      <Card className="p-4 space-y-4">
        <div className="flex items-center gap-3 flex-wrap">
          {/* Source filter */}
          <div className="relative" data-filter-dropdown>
            <button
              onClick={() => { setShowSourceFilter((v) => !v); setShowCategoryFilter(false); }}
              className={`flex items-center gap-1.5 text-sm px-3 py-1.5 rounded-lg border transition-all ${
                selectedSources.length > 0
                  ? "bg-violet-600 border-violet-700 text-white shadow-sm"
                  : "bg-white border-gray-200 text-gray-600 hover:border-violet-300 hover:text-violet-600"
              }`}
            >
              <Layers size={13} />
              {selectedSources.length > 0 ? `${selectedSources.length} nguồn` : "Tất cả nguồn"}
              <ChevronDown size={12} className={`transition-transform ${showSourceFilter ? "rotate-180" : ""}`} />
            </button>
            {showSourceFilter && (
              <div className="absolute z-50 top-full mt-1.5 left-0 w-64 bg-white border border-gray-100 rounded-xl shadow-lg p-2 space-y-0.5">
                <div className="flex items-center justify-between px-2 pb-2 border-b border-gray-50">
                  <span className="text-[11px] font-bold text-gray-400 uppercase tracking-wide">Nguồn ({sourcesData?.length ?? 0})</span>
                  {selectedSources.length > 0 && (
                    <button onClick={() => { setSelectedSources([]); setPage(1); }} className="text-[10px] text-violet-600 hover:underline">Bỏ chọn tất cả</button>
                  )}
                </div>
                {sourcesData?.map((s) => {
                  const active = selectedSources.includes(s.code);
                  const cnt = sourceCounts[s.code] ?? 0;
                  return (
                    <button
                      key={s.code}
                      onClick={() => toggleSource(s.code)}
                      className={`w-full flex items-center gap-2.5 px-2.5 py-1.5 rounded-lg text-sm transition-colors ${
                        active ? "bg-violet-50 text-violet-700" : "text-gray-700 hover:bg-gray-50"
                      }`}
                    >
                      <span className={`w-4 h-4 rounded border flex items-center justify-center shrink-0 transition-colors ${
                        active ? "bg-violet-600 border-violet-600" : "border-gray-300"
                      }`}>
                        {active && <Check size={10} className="text-white" />}
                      </span>
                      <span className="flex-1 text-left truncate">{s.name || s.code}</span>
                      <span className="text-[11px] text-gray-400 shrink-0">{cnt.toLocaleString()}</span>
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          {/* Category filter */}
          {categoriesData && categoriesData.length > 0 && (
            <div className="relative" data-filter-dropdown>
              <button
                onClick={() => { setShowCategoryFilter((v) => !v); setShowSourceFilter(false); }}
                className={`flex items-center gap-1.5 text-sm px-3 py-1.5 rounded-lg border transition-all ${
                  selectedCategories.length > 0
                    ? "bg-indigo-600 border-indigo-700 text-white shadow-sm"
                    : "bg-white border-gray-200 text-gray-600 hover:border-indigo-300 hover:text-indigo-600"
                }`}
              >
                <Filter size={13} />
                {selectedCategories.length > 0 ? `${selectedCategories.length} danh mục` : "Tất cả danh mục"}
                <ChevronDown size={12} className={`transition-transform ${showCategoryFilter ? "rotate-180" : ""}`} />
              </button>
              {showCategoryFilter && (
                <div className="absolute z-50 top-full mt-1.5 left-0 w-64 bg-white border border-gray-100 rounded-xl shadow-lg p-2 space-y-0.5 max-h-72 overflow-y-auto">
                  <div className="flex items-center justify-between px-2 pb-2 border-b border-gray-50 sticky top-0 bg-white">
                    <span className="text-[11px] font-bold text-gray-400 uppercase tracking-wide">Danh mục ({categoriesData.length})</span>
                    {selectedCategories.length > 0 && (
                      <button onClick={() => { setSelectedCategories([]); setPage(1); }} className="text-[10px] text-indigo-600 hover:underline">Bỏ chọn tất cả</button>
                    )}
                  </div>
                  {categoriesData.map((cat) => {
                    const active = selectedCategories.includes(cat);
                    const cnt = categoryCounts[cat] ?? 0;
                    return (
                      <button
                        key={cat}
                        onClick={() => toggleCategory(cat)}
                        className={`w-full flex items-center gap-2.5 px-2.5 py-1.5 rounded-lg text-sm transition-colors ${
                          active ? "bg-indigo-50 text-indigo-700" : "text-gray-700 hover:bg-gray-50"
                        }`}
                      >
                        <span className={`w-4 h-4 rounded border flex items-center justify-center shrink-0 transition-colors ${
                          active ? "bg-indigo-600 border-indigo-600" : "border-gray-300"
                        }`}>
                          {active && <Check size={10} className="text-white" />}
                        </span>
                        <span className="flex-1 text-left truncate">{cat}</span>
                        <span className="text-[11px] text-gray-400 shrink-0">{cnt.toLocaleString()}</span>
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          )}

          {/* Platform filter */}
          {availablePlatforms.length > 0 && (
            <div className="relative" data-filter-dropdown>
              <button
                onClick={() => { setShowPlatformFilter((v) => !v); setShowSourceFilter(false); setShowCategoryFilter(false); }}
                className={`flex items-center gap-1.5 text-sm px-3 py-1.5 rounded-lg border transition-all ${
                  selectedPlatforms.length > 0
                    ? "bg-emerald-600 border-emerald-700 text-white shadow-sm"
                    : "bg-white border-gray-200 text-gray-600 hover:border-emerald-300 hover:text-emerald-600"
                }`}
              >
                <Layers size={13} />
                {selectedPlatforms.length > 0 ? `${selectedPlatforms.length} nền tảng` : "Tất cả nền tảng"}
                <ChevronDown size={12} className={`transition-transform ${showPlatformFilter ? "rotate-180" : ""}`} />
              </button>
              {showPlatformFilter && (
                <div className="absolute z-50 top-full mt-1.5 left-0 w-60 bg-white border border-gray-100 rounded-xl shadow-lg p-2 space-y-0.5 max-h-72 overflow-y-auto">
                  <div className="flex items-center justify-between px-2 pb-2 border-b border-gray-50 sticky top-0 bg-white">
                    <span className="text-[11px] font-bold text-gray-400 uppercase tracking-wide">Nền tảng ({availablePlatforms.length})</span>
                    {selectedPlatforms.length > 0 && (
                      <button onClick={() => { setSelectedPlatforms([]); setPage(1); }} className="text-[10px] text-emerald-600 hover:underline">Bỏ chọn tất cả</button>
                    )}
                  </div>
                  {availablePlatforms.map((key) => {
                    const active = selectedPlatforms.includes(key);
                    const cnt = platformCounts[key] ?? 0;
                    return (
                      <button
                        key={key}
                        onClick={() => togglePlatform(key)}
                        className={`w-full flex items-center gap-2.5 px-2.5 py-1.5 rounded-lg text-sm transition-colors ${
                          active ? "bg-emerald-50 text-emerald-700" : "text-gray-700 hover:bg-gray-50"
                        }`}
                      >
                        <span className={`w-4 h-4 rounded border flex items-center justify-center shrink-0 transition-colors ${
                          active ? "bg-emerald-600 border-emerald-600" : "border-gray-300"
                        }`}>
                          {active && <Check size={10} className="text-white" />}
                        </span>
                        <span className="flex-1 text-left truncate">{PLATFORM_LABELS[key] ?? key}</span>
                        <span className="text-[11px] text-gray-400 shrink-0">{cnt.toLocaleString()}</span>
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          )}

          {/* Active chips */}
          {(selectedSources.length > 0 || selectedCategories.length > 0 || selectedPlatforms.length > 0) && (
            <div className="flex items-center gap-1 flex-wrap">
              {selectedSources.map((s) => (
                <span key={s} className="flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full bg-violet-100 text-violet-700 font-medium">
                  {sourcesData?.find((x) => x.code === s)?.name || s}
                  <button aria-label={`Bỏ chọn ${s}`} onClick={() => toggleSource(s)} className="hover:text-violet-900"><X size={10} /></button>
                </span>
              ))}
              {selectedCategories.map((c) => (
                <span key={c} className="flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full bg-indigo-100 text-indigo-700 font-medium">
                  {c}
                  <button aria-label={`Bỏ chọn ${c}`} onClick={() => toggleCategory(c)} className="hover:text-indigo-900"><X size={10} /></button>
                </span>
              ))}
              {selectedPlatforms.map((k) => (
                <span key={k} className="flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full bg-emerald-100 text-emerald-700 font-medium">
                  {PLATFORM_LABELS[k] ?? k}
                  <button aria-label={`Bỏ chọn ${k}`} onClick={() => togglePlatform(k)} className="hover:text-emerald-900"><X size={10} /></button>
                </span>
              ))}
              <button
                onClick={() => { setSelectedSources([]); setSelectedCategories([]); setSelectedPlatforms([]); setPage(1); }}
                className="text-[10px] text-gray-400 hover:text-gray-600 underline ml-1"
              >Xóa tất cả</button>
            </div>
          )}

          <div className="h-5 w-px bg-gray-200" />

          {/* Status filter tabs */}
          <div className="flex rounded-lg border border-gray-100 overflow-hidden shadow-soft">
            {(["all", "resolved", "intermediate", "empty"] as FilterType[]).map((f) => {
              const count = f === "all" ? stats.total : f === "resolved" ? stats.resolved : f === "intermediate" ? stats.intermediate : stats.empty;
              const labels: Record<FilterType, string> = { all: "Tất cả", resolved: "Đã có", intermediate: "Trung gian", empty: "Chưa có" };
              return (
                <button
                  key={f}
                  onClick={() => { setFilter(f); setPage(1); }}
                  className={`px-3 py-1.5 text-sm transition-colors ${
                    filter === f ? "bg-primary text-white font-medium" : "bg-white text-gray-500 hover:bg-gray-50"
                  }`}
                >
                  {labels[f]}
                  <span className="ml-1.5 text-[11px] opacity-70">{count.toLocaleString()}</span>
                </button>
              );
            })}
          </div>

          {/* Search */}
          <div className="relative flex-1 min-w-[180px] max-w-xs">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
            <input
              placeholder="Tìm tên, URL, danh mục…"
              value={search}
              onChange={(e) => { setSearch(e.target.value); setPage(1); }}
              className="w-full pl-8 pr-3 py-1.5 text-sm bg-white border border-gray-100 rounded-lg shadow-soft-inset focus:outline-none focus:ring-2 focus:ring-primary/30"
            />
          </div>

          <span className="text-xs text-gray-400 ml-auto shrink-0">{filtered.length.toLocaleString()} chương trình</span>

          <button
            onClick={() => setShowAdvanced((v) => !v)}
            className="flex items-center gap-1.5 text-xs text-violet-600 hover:text-violet-800 font-medium transition-colors"
          >
            <SlidersHorizontal size={13} />
            Cài đặt Discover
            <ChevronDown size={12} className={`transition-transform ${showAdvanced ? "rotate-180" : ""}`} />
          </button>
        </div>

        {/* Advanced settings */}
        {showAdvanced && (
          <div className="border-t border-gray-50 pt-4">
            <div className="flex items-center gap-4">
              <div className="w-48">
                <label className="block text-[11px] uppercase tracking-wide text-gray-400 mb-1">
                  Giới hạn mỗi lần chạy
                </label>
                <input
                  type="number" min={1} max={2000}
                  aria-label="Limit programs"
                  placeholder="500"
                  value={limit}
                  onChange={(e) => setLimit(Number(e.target.value))}
                  className="w-full text-sm bg-white border border-gray-100 rounded-lg px-3 py-2 shadow-soft-inset focus:outline-none focus:ring-2 focus:ring-primary/30"
                />
                <p className="text-[11px] text-gray-400 mt-1">Áp dụng cho "Discover tất cả". "Quét đã chọn" không giới hạn.</p>
              </div>
            </div>
          </div>
        )}
      </Card>

      {/* Selection action bar — hiện ngay trước bảng khi có row được chọn */}
      {selectedIds.size > 0 && (
        <div className="flex items-center gap-3 px-4 py-2.5 bg-violet-50 border border-violet-200 rounded-xl text-sm">
          <CheckSquare size={15} className="text-violet-500 shrink-0" />
          <span className="text-violet-700 font-medium">Đã chọn {selectedIds.size} chương trình</span>
          {selectedIds.size < filtered.length && (
            <button
              onClick={() => setSelectedIds(new Set(filtered.map((p) => p.id)))}
              className="text-violet-600 hover:underline text-xs"
            >
              Chọn tất cả {filtered.length.toLocaleString()} kết quả
            </button>
          )}
          <div className="ml-auto flex items-center gap-2">
            <button
              onClick={() => setSelectedIds(new Set())}
              className="text-gray-400 hover:text-gray-600 flex items-center gap-1 text-xs"
            >
              <X size={13} /> Bỏ chọn
            </button>
            <Button
              variant="cta"
              size="sm"
              disabled={isAnyDiscoverRunning}
              onClick={() => batchDiscoverSelected.mutate()}
              className="gap-2 bg-violet-600 hover:bg-violet-700"
            >
              {batchDiscoverSelected.isPending ? (
                <>
                  <RefreshCw size={14} className="animate-spin" />
                  {discoverProgress
                    ? `${discoverProgress.done}/${discoverProgress.total}…`
                    : "Đang chạy…"}
                </>
              ) : (
                <>
                  <RefreshCw size={14} />
                  Quét đã chọn ({selectedIds.size})
                </>
              )}
            </Button>
          </div>
        </div>
      )}

      {/* Table */}
      <Card className="overflow-hidden">
        {isLoading ? (
          <div className="flex items-center justify-center py-16 text-gray-400">
            <RefreshCw size={20} className="animate-spin mr-2" /> Đang tải…
          </div>
        ) : paginated.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-gray-400">
            <Globe size={32} className="mb-2 opacity-30" />
            <p className="text-sm">Không có chương trình nào phù hợp</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-50">
                  {/* Select-all checkbox */}
                  <th className="px-3 py-3 w-10">
                    <button
                      aria-label="Chọn tất cả trang này"
                      onClick={togglePageAll}
                      className="flex items-center justify-center text-gray-400 hover:text-violet-500 transition-colors"
                    >
                      {allPageSelected ? (
                        <CheckSquare size={16} className="text-violet-500" />
                      ) : somePageSelected ? (
                        <MinusSquare size={16} className="text-violet-400" />
                      ) : (
                        <Square size={16} />
                      )}
                    </button>
                  </th>
                  <th className="px-4 py-3 text-left text-[11px] font-semibold text-gray-400 uppercase tracking-wide w-[220px]">Tên chương trình</th>
                  <th className="px-4 py-3 text-left text-[11px] font-semibold text-gray-400 uppercase tracking-wide w-[100px]">Nguồn</th>
                  <th className="px-4 py-3 text-left text-[11px] font-semibold text-gray-400 uppercase tracking-wide">URL Trang chủ</th>
                  <th className="px-4 py-3 text-left text-[11px] font-semibold text-gray-400 uppercase tracking-wide w-[110px]">Traffic</th>
                  <th className="px-4 py-3 text-left text-[11px] font-semibold text-gray-400 uppercase tracking-wide w-[120px]">Trạng thái</th>
                  <th className="px-4 py-3 text-right text-[11px] font-semibold text-gray-400 uppercase tracking-wide w-[100px]">Thao tác</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {paginated.map((p) => {
                  const intermediate = isIntermediate(p.url);
                  const isEditing = editingId === p.id;
                  const hasTraffic = p.traffic_score && p.traffic_score > 0;
                  const isSelected = selectedIds.has(p.id);

                  return (
                    <tr
                      key={p.id}
                      className={`hover:bg-gray-50/50 transition-colors ${isSelected ? "bg-violet-50/40" : ""}`}
                    >
                      {/* Row checkbox */}
                      <td className="px-3 py-3">
                        <button
                          aria-label={`Chọn ${p.name}`}
                          onClick={() => toggleRow(p.id)}
                          className="flex items-center justify-center text-gray-300 hover:text-violet-500 transition-colors"
                        >
                          {isSelected ? (
                            <CheckSquare size={15} className="text-violet-500" />
                          ) : (
                            <Square size={15} />
                          )}
                        </button>
                      </td>
                      <td className="px-4 py-3">
                        <div className="font-medium text-ink truncate max-w-[200px]" title={p.name}>
                          {p.name}
                        </div>
                        {p.category && (
                          <div className="text-[11px] text-gray-400 mt-0.5">{p.category}</div>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        <span className="text-[11px] px-2 py-0.5 rounded-full bg-gray-100 text-gray-500 font-medium">
                          {p.source || "—"}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        {isEditing ? (
                          <div className="flex items-center gap-2">
                            <input
                              autoFocus
                              aria-label="Nhập URL trang chủ"
                              placeholder="https://example.com/"
                              value={editValue}
                              onChange={(e) => setEditValue(e.target.value)}
                              onKeyDown={(e) => {
                                if (e.key === "Enter") updateUrl.mutate({ id: p.id, url: editValue });
                                if (e.key === "Escape") setEditingId(null);
                              }}
                              className="flex-1 text-sm bg-white border border-primary/40 rounded px-2 py-1 focus:outline-none focus:ring-2 focus:ring-primary/30"
                            />
                            <button onClick={() => updateUrl.mutate({ id: p.id, url: editValue })} className="p-1 text-emerald-500 hover:text-emerald-600" title="Lưu">
                              <Check size={14} />
                            </button>
                            <button onClick={() => setEditingId(null)} className="p-1 text-gray-400 hover:text-gray-600" title="Hủy">
                              <X size={14} />
                            </button>
                          </div>
                        ) : (
                          <div className="flex items-center gap-1.5 group">
                            <span
                              className={`truncate max-w-[280px] font-mono text-[12px] ${
                                !p.url ? "text-gray-300 italic" :
                                intermediate ? "text-amber-600" : "text-gray-600"
                              }`}
                              title={p.url ?? ""}
                            >
                              {p.url || "chưa có URL"}
                            </span>
                            {p.url && (
                              <a
                                href={p.url}
                                target="_blank"
                                rel="noreferrer"
                                aria-label={`Mở ${p.url}`}
                                className="opacity-0 group-hover:opacity-100 transition-opacity text-gray-400 hover:text-primary shrink-0"
                              >
                                <ExternalLink size={11} />
                              </a>
                            )}
                          </div>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        {hasTraffic ? (
                          <div className="flex items-center gap-1 text-[12px] text-emerald-600">
                            <TrendingUp size={11} />
                            {p.traffic_score! >= 1_000_000
                              ? `${(p.traffic_score! / 1_000_000).toFixed(1)}M`
                              : p.traffic_score! >= 1000
                              ? `${(p.traffic_score! / 1000).toFixed(0)}K`
                              : p.traffic_score!.toFixed(0)}
                          </div>
                        ) : (
                          <span className="text-[11px] text-gray-300">—</span>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        {!p.url ? (
                          <Badge variant="neutral" className="text-[11px] gap-1">
                            <Globe size={10} /> Chưa có
                          </Badge>
                        ) : intermediate ? (
                          <Badge variant="warning" className="text-[11px] gap-1">
                            <AlertTriangle size={10} /> Trung gian
                          </Badge>
                        ) : (
                          <Badge variant="success" className="text-[11px] gap-1">
                            <CheckCircle2 size={10} /> Trang chủ
                          </Badge>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center justify-end gap-1">
                          <button
                            title="Sửa URL thủ công"
                            onClick={() => { setEditingId(p.id); setEditValue(p.url ?? ""); }}
                            className="p-1.5 rounded hover:bg-gray-100 text-gray-400 hover:text-ink transition-colors"
                          >
                            <Pencil size={13} />
                          </button>
                          <button
                            title="Tự động tìm trang chủ"
                            disabled={isSingleLoading(p.id) || isAnyDiscoverRunning}
                            onClick={() => singleDiscover.mutate({ id: p.id })}
                            className="p-1.5 rounded hover:bg-violet-50 text-gray-400 hover:text-violet-500 transition-colors disabled:opacity-40"
                          >
                            {isSingleLoading(p.id) ? (
                              <RefreshCw size={13} className="animate-spin" />
                            ) : (
                              <Globe size={13} />
                            )}
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-2">
          <button
            aria-label="Trang trước"
            disabled={page === 1}
            onClick={() => setPage((p) => p - 1)}
            className="p-1.5 rounded hover:bg-gray-100 disabled:opacity-30 transition-colors"
          >
            <ChevronLeft size={16} />
          </button>
          <span className="text-sm text-gray-500">
            Trang {page} / {totalPages}
          </span>
          <button
            aria-label="Trang sau"
            disabled={page === totalPages}
            onClick={() => setPage((p) => p + 1)}
            className="p-1.5 rounded hover:bg-gray-100 disabled:opacity-30 transition-colors"
          >
            <ChevronRight size={16} />
          </button>
        </div>
      )}
    </div>
  );
}
