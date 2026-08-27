"use client";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Globe, Sparkles, ShoppingBag, Trophy, Database, ExternalLink, Loader2, Search, BarChart2 } from "lucide-react";
import * as api from "@/lib/api";
import { Card } from "@/components/Card";
import { Badge } from "@/components/Badge";
import { PageHeader } from "@/components/PageHeader";
import { Button } from "@/components/Button";
import { useToast } from "@/lib/toast";

const ICONS: Record<string, any> = {
  trophy: Trophy,
  sparkles: Sparkles,
  "shopping-bag": ShoppingBag,
  database: Database,
  globe: Globe,
};

const SOURCE_COLORS: Record<string, { from: string; icon: string; ring: string }> = {
  openaffiliate: { from: "from-indigo-500 to-indigo-600", icon: "text-indigo-500", ring: "ring-indigo-200" },
  lovable: { from: "from-pink-500 to-rose-500", icon: "text-pink-500", ring: "ring-pink-200" },
  goaffpro: { from: "from-emerald-500 to-teal-500", icon: "text-emerald-500", ring: "ring-emerald-200" },
  apdb: { from: "from-amber-500 to-orange-500", icon: "text-amber-500", ring: "ring-amber-200" },
  aiaffiliateindex: { from: "from-violet-500 to-purple-600", icon: "text-violet-500", ring: "ring-violet-200" },
  affiliatewatch: { from: "from-cyan-500 to-blue-600", icon: "text-cyan-500", ring: "ring-cyan-200" },
};

export default function SourcesPage() {
  const { push } = useToast();
  const router = useRouter();
  const qc = useQueryClient();
  const sources = useQuery({ queryKey: ["sources"], queryFn: api.listSources });
  const [selected, setSelected] = useState<Record<string, Record<string, string>>>({});
  const [findHomepage, setFindHomepage] = useState<Record<string, boolean>>({});
  const [scanTraffic, setScanTraffic] = useState<Record<string, boolean>>({});

  const discover = useMutation({
    mutationFn: (source: string) => api.discoverHomepages({ source, limit: 50, concurrency: 3, method: "auto" }),
    onSuccess: (_, source) => push({ type: "info", message: `Đã bắt đầu tìm trang chủ cho ${source}` }),
    onError: () => {},
  });

  const crawl = useMutation({
    mutationFn: ({ code, params }: { code: string; params?: Record<string, any> }) => api.startCrawl(code, params),
    onSuccess: async (data) => {
      push({ type: "success", message: `Đã thêm vào hàng đợi — Job #${data.job_id} (${data.source})` });
      if (findHomepage[data.source]) {
        discover.mutate(data.source);
      }
      if (scanTraffic[data.source]) {
        try {
          const ids = await api.listProgramIds({ source: data.source });
          if (ids.length > 0) {
            // Backend limit 100/batch — chỉ lấy 100 ID đầu tiên (mới nhất)
            const batch = ids.slice(0, 100);
            const job = await api.createTrafficScanJob(batch, true, 3, 2);
            push({ type: "info", message: `Đang quét traffic SimilarWeb cho ${batch.length} chương trình — Traffic Job #${job.id}` });
          }
        } catch {
          push({ type: "error", message: "Không thể khởi động quét traffic SimilarWeb" });
        }
      }
      qc.invalidateQueries({ queryKey: ["jobs"] });
      setTimeout(() => router.push("/jobs"), 600);
    },
    onError: (e: Error) => push({ type: "error", message: e.message }),
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title="Nguồn quét"
        description="Chọn 1 trang nguồn — hệ thống sẽ crawl và lưu các affiliate program vào DB."
      />

      <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
        {sources.data?.map((s) => {
          const Icon = ICONS[s.icon_hint] || Globe;
          const c = SOURCE_COLORS[s.code] || { from: "from-gray-400 to-gray-500", icon: "text-gray-500", ring: "ring-gray-200" };
          const isLoading = crawl.isPending && crawl.variables?.code === s.code;
          const opts = s.options || [];
          const currentSel = selected[s.code] || {};
          const buildParams = () => {
            const p: Record<string, string> = {};
            opts.forEach((o) => { p[o.key] = currentSel[o.key] ?? o.default ?? o.choices[0]?.value ?? ""; });
            return p;
          };
          return (
            <Card key={s.code} className="flex flex-col overflow-hidden !p-0 hover:shadow-soft-md transition-shadow duration-200">
              {/* Gradient top strip */}
              <div className={`h-1.5 bg-gradient-to-r ${c.from}`} />
              <div className="p-6 flex flex-col flex-1">
                <div className="flex items-start justify-between mb-4">
                  <div className={`w-12 h-12 rounded-xl ring-2 ${c.ring} bg-white flex items-center justify-center`}>
                    <Icon size={22} className={c.icon} />
                  </div>
                  {s.highlight && (
                    <Badge variant="warning" className="text-[11px]">⭐ Ưu tiên</Badge>
                  )}
                </div>
                <h3 className="text-[15px] font-bold text-ink">{s.name}</h3>
                <p className="text-sm text-gray-400 mt-1.5 flex-1 leading-relaxed">{s.description}</p>
                <a href={s.base_url} target="_blank" rel="noreferrer"
                  className="text-xs text-gray-400 hover:text-primary inline-flex items-center gap-1 mt-3 transition-colors max-w-full">
                  <span className="truncate">{s.base_url}</span> <ExternalLink size={10} className="shrink-0" />
                </a>
                {opts.length > 0 && (
                  <div className="mt-4 space-y-2">
                    {opts.map((o) => (
                      <div key={o.key}>
                        <label className="block text-[11px] uppercase tracking-wide text-gray-400 mb-1">{o.label}</label>
                        <select
                          aria-label={o.label}
                          value={currentSel[o.key] ?? o.default ?? o.choices[0]?.value}
                          onChange={(e) =>
                            setSelected((prev) => ({
                              ...prev,
                              [s.code]: { ...(prev[s.code] || {}), [o.key]: e.target.value },
                            }))
                          }
                          className="w-full text-sm bg-white border border-gray-100 rounded-lg px-3 py-2 shadow-soft-inset focus:outline-none focus:ring-2 focus:ring-primary/30"
                        >
                          {o.choices.map((ch) => (
                            <option key={ch.value} value={ch.value}>{ch.label}</option>
                          ))}
                        </select>
                      </div>
                    ))}
                  </div>
                )}
                <div className="mt-5 pt-4 border-t border-gray-50 space-y-2.5">
                  <Badge variant="success">Đang hoạt động</Badge>
                  <div className="flex items-center gap-4">
                    <label className="flex items-center gap-1.5 cursor-pointer select-none">
                      <input
                        type="checkbox"
                        aria-label="Tìm trang chủ sau khi crawl"
                        checked={!!findHomepage[s.code]}
                        onChange={(e) => setFindHomepage((prev) => ({ ...prev, [s.code]: e.target.checked }))}
                        className="w-3 h-3 rounded accent-violet-600"
                      />
                      <span className="text-[11px] text-gray-500 flex items-center gap-1">
                        <Search size={10} className="text-violet-400" />
                        Tìm trang chủ
                      </span>
                    </label>
                    <label className="flex items-center gap-1.5 cursor-pointer select-none">
                      <input
                        type="checkbox"
                        aria-label="Quét traffic SimilarWeb sau khi crawl"
                        checked={!!scanTraffic[s.code]}
                        onChange={(e) => setScanTraffic((prev) => ({ ...prev, [s.code]: e.target.checked }))}
                        className="w-3 h-3 rounded accent-violet-600"
                      />
                      <span className="text-[11px] text-gray-500 flex items-center gap-1">
                        <BarChart2 size={10} className="text-emerald-400" />
                        SimilarWeb traffic
                      </span>
                    </label>
                  </div>
                  <Button
                    variant="cta"
                    size="sm"
                    disabled={isLoading}
                    onClick={() => crawl.mutate({ code: s.code, params: opts.length ? buildParams() : undefined })}
                    className="gap-1.5 w-full justify-center"
                  >
                    {isLoading ? <><Loader2 size={13} className="animate-spin" /> Đang quét…</> : "Quét ngay"}
                  </Button>
                </div>
              </div>
            </Card>
          );
        })}
      </div>
    </div>
  );
}
