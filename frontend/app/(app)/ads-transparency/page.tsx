"use client";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Megaphone, Search, ExternalLink, Calendar, Clock, X, Loader2, History, Trash2, Layers } from "lucide-react";
import * as api from "@/lib/api";
import { Card } from "@/components/Card";
import { PageHeader } from "@/components/PageHeader";
import { Button } from "@/components/Button";
import { EmptyState } from "@/components/EmptyState";
import { Modal } from "@/components/Modal";
import { Input, Select, Label } from "@/components/Input";
import { Badge } from "@/components/Badge";
import { useToast } from "@/lib/toast";

const REGIONS: { value: string; label: string }[] = [
  { value: "2704", label: "🇻🇳 Việt Nam" },
  { value: "", label: "🌐 Toàn cầu" },
  { value: "2840", label: "🇺🇸 Mỹ" },
  { value: "2702", label: "🇸🇬 Singapore" },
  { value: "2764", label: "🇹🇭 Thái Lan" },
  { value: "2360", label: "🇮🇩 Indonesia" },
  { value: "2458", label: "🇲🇾 Malaysia" },
  { value: "2608", label: "🇵🇭 Philippines" },
  { value: "2392", label: "🇯🇵 Nhật" },
  { value: "2410", label: "🇰🇷 Hàn Quốc" },
];

const PLATFORMS = [
  { value: "", label: "Tất cả nền tảng" },
  { value: "GOOGLE_SEARCH", label: "Google Search" },
  { value: "YOUTUBE", label: "YouTube" },
  { value: "PLAY", label: "Play Store" },
  { value: "MAPS", label: "Maps" },
  { value: "SHOPPING", label: "Shopping" },
];

const FORMATS = [
  { value: "", label: "Mọi định dạng" },
  { value: "text", label: "Text" },
  { value: "image", label: "Image" },
  { value: "video", label: "Video" },
];

function fmtDate(ts?: number) {
  if (!ts) return "—";
  try {
    return new Date(ts * 1000).toLocaleDateString("vi-VN", { day: "2-digit", month: "2-digit", year: "numeric" });
  } catch {
    return "—";
  }
}

function FormatBadge({ format }: { format?: string }) {
  const map: Record<string, { variant: "primary" | "info" | "warning" | "success"; label: string }> = {
    text: { variant: "info", label: "Text" },
    image: { variant: "primary", label: "Image" },
    video: { variant: "warning", label: "Video" },
  };
  const m = (format && map[format]) || { variant: "success" as const, label: format || "—" };
  return <Badge variant={m.variant}>{m.label}</Badge>;
}

export default function AdsTransparencyPage() {
  const { push } = useToast();
  const qc = useQueryClient();

  const todayISO = () => new Date().toISOString().slice(0, 10);
  const daysAgoISO = (n: number) => {
    const d = new Date();
    d.setDate(d.getDate() - n);
    return d.toISOString().slice(0, 10);
  };

  const searchParams = useSearchParams();
  const [text, setText] = useState("");
  // Khi lọc theo 1 nhà quảng cáo cụ thể (từ nút "Xem thêm quảng cáo của NQC này")
  const [advertiserId, setAdvertiserId] = useState("");
  const [advertiserName, setAdvertiserName] = useState("");
  const [advInput, setAdvInput] = useState("");   // Cách 3: dán advertiser ID (AR…) hoặc link ATC
  const [exporting, setExporting] = useState(false);
  const [platform, setPlatform] = useState("");
  const [creativeFormat, setCreativeFormat] = useState("text");
  const [region, setRegion] = useState("2704");
  const [startDate, setStartDate] = useState(() => daysAgoISO(30));
  const [endDate, setEndDate] = useState(() => todayISO());
  const [numStr, setNumStr] = useState("10");
  const num = Math.max(1, Math.min(100, Number(numStr) || 10));

  const [creatives, setCreatives] = useState<api.AdCreative[]>([]);
  const [nextToken, setNextToken] = useState<string>("");
  const [total, setTotal] = useState<number | undefined>(undefined);
  const [selected, setSelected] = useState<api.AdCreative | null>(null);
  const [detail, setDetail] = useState<any>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [searched, setSearched] = useState(false);
  const [serpNotice, setSerpNotice] = useState<string>("");

  const search = useMutation({
    // `adv`/`txt` truyền tường minh (tránh đọc state cũ khi bấm "xem thêm của NQC");
    // bỏ trống → dùng state hiện tại (dùng cho nút "Tải thêm").
    mutationFn: (arg: { token?: string; adv?: string; txt?: string; region?: string; format?: string; startDate?: string; endDate?: string; num?: number }) => {
      // Khi tìm theo NHÀ QUẢNG CÁO (advertiser_id), bỏ lọc Khu vực/Nền tảng/Định dạng
      // để thấy TOÀN BỘ footprint của NQC (khớp Google ATC region=anywhere). Lọc theo
      // VN + Text chỉ hợp cho tìm domain/keyword; với NQC nó cắt kết quả rất nhiều
      // (vd 3000 → 14). Vẫn giữ lọc Từ ngày–Đến ngày.
      // arg.region/format/startDate/endDate: override tường minh (dùng khi mở từ cột "Số NQC"
      // ở màn Chương trình → phải TOÀN CẦU + đúng khung ngày để KHỚP số đã đếm; tránh state cũ).
      const adv = arg.adv !== undefined ? arg.adv : advertiserId;
      const isAdv = !!adv;
      const reg = arg.region !== undefined ? arg.region : region;
      const fmt = arg.format !== undefined ? arg.format : creativeFormat;
      const sd = arg.startDate !== undefined ? arg.startDate : startDate;
      const ed = arg.endDate !== undefined ? arg.endDate : endDate;
      return api.searchAdsTransparency({
        text: arg.txt !== undefined ? arg.txt : text.trim(),
        advertiser_id: adv,
        platform: isAdv ? "" : platform,
        creative_format: isAdv ? "" : fmt,
        region: isAdv ? "" : reg,
        start_date: sd.replace(/-/g, ""),
        end_date: ed.replace(/-/g, ""),
        num: arg.num !== undefined ? arg.num : num,
        next_page_token: arg.token || "",
      });
    },
    onSuccess: (data, arg) => {
      const token = arg.token || "";
      const list = data.ad_creatives || [];
      setCreatives((prev) => (token ? [...prev, ...list] : list));
      setTotal(data.search_information?.total_results);
      setNextToken(data.pagination?.next_page_token || data.serpapi_pagination?.next_page_token || "");
      // Cách 3: dán ID không kèm tên → lấy tên NQC từ kết quả để hiển thị đẹp.
      if (!token && arg.adv && list.length) {
        setAdvertiserName((prev) => prev || list[0].advertiser || "");
      }
      // Capture SerpAPI-side empty/error notice for nice UI
      const errMsg: string | undefined = (data as any)?.error;
      const state: string | undefined = (data as any)?.search_information?.results_state;
      if (!token) {
        if (!list.length) {
          setSerpNotice(errMsg || (state === "Fully empty" ? "Google ATC không trả về quảng cáo nào cho truy vấn này." : "Không tìm thấy quảng cáo nào."));
        } else {
          setSerpNotice("");
        }
        qc.invalidateQueries({ queryKey: ["ads-history"] });
      }
    },
    onError: (e: Error) => {
      setSerpNotice(e.message);
      push({ type: "error", message: e.message });
    },
  });

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!text.trim()) {
      push({ type: "error", message: "Cần nhập từ khoá / domain (vd: shopee.vn)" });
      return;
    }
    setAdvertiserId("");
    setAdvertiserName("");
    setCreatives([]);
    setNextToken("");
    setTotal(undefined);
    setSerpNotice("");
    setSearched(true);
    search.mutate({ token: "", txt: text.trim(), adv: "" });
  };

  // Mở từ màn Chương trình với ?text=<domain>[&start=YYYYMMDD&end=YYYYMMDD] → tự tìm ngay.
  // Cột "Số NQC" đếm TOÀN CẦU → click sang đây phải tìm TOÀN CẦU + đủ định dạng + đúng
  // khung ngày đã đếm thì kết quả mới KHỚP số (nếu để mặc định VN+Text sẽ ra ít/không ra).
  useEffect(() => {
    const t = (searchParams.get("text") || "").trim();
    if (!t) return;
    const iso = (yyyymmdd: string) => yyyymmdd && yyyymmdd.length === 8
      ? `${yyyymmdd.slice(0, 4)}-${yyyymmdd.slice(4, 6)}-${yyyymmdd.slice(6, 8)}` : "";
    const sd = iso((searchParams.get("start") || "").trim()) || startDate;
    const ed = iso((searchParams.get("end") || "").trim()) || endDate;
    setText(t);
    setAdvertiserId(""); setAdvertiserName("");
    setRegion(""); setCreativeFormat("");            // Toàn cầu + tất cả định dạng
    setStartDate(sd); setEndDate(ed);
    setNumStr("100");                                 // khớp cỡ mẫu num=100 của cột "Số NQC" ở Programs
    setCreatives([]); setNextToken(""); setTotal(undefined); setSerpNotice("");
    setSearched(true);
    search.mutate({ token: "", txt: t, adv: "", region: "", format: "", startDate: sd, endDate: ed, num: 100 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Cách 3: dán advertiser ID (AR…) hoặc link ATC → tách mã → xem QC của NQC.
  // Lọc Khu vực/Định dạng/Từ ngày–Đến ngày dùng chung với form trên.
  const extractAdvertiserId = (raw: string): string => {
    const m = raw.match(/AR\d{6,}/i);
    if (m) return m[0].toUpperCase();
    const digits = raw.trim();
    return /^\d{6,}$/.test(digits) ? `AR${digits}` : "";
  };
  const onSubmitAdvertiser = (e: React.FormEvent) => {
    e.preventDefault();
    const id = extractAdvertiserId(advInput);
    if (!id) {
      push({ type: "error", message: "Dán advertiser ID (AR…) hoặc link adstransparency.google.com/advertiser/AR…" });
      return;
    }
    searchByAdvertiser(id);
  };

  // Từ 1 quảng cáo → lọc tất cả quảng cáo khác của cùng nhà quảng cáo.
  const searchByAdvertiser = (advId: string, advName?: string) => {
    if (!advId) return;
    setSelected(null);
    setDetail(null);
    setText("");
    setAdvertiserId(advId);
    setAdvertiserName(advName || "");
    setCreatives([]);
    setNextToken("");
    setTotal(undefined);
    setSerpNotice("");
    setSearched(true);
    search.mutate({ token: "", adv: advId, txt: "" });
  };

  // Trích domain quảng cáo của NQC (visurl + Gemini-vision cho ảnh) → tạo candidate
  // trong màn Tìm kiếm dự án để dò affiliate.
  const handleExportToDiscovery = async (allTime: boolean) => {
    if (!advertiserId || creatives.length === 0) return;
    setExporting(true);
    try {
      await api.exportAdsToDiscovery(advertiserId, advertiserName, creatives, {
        start_date: allTime ? "" : startDate.replace(/-/g, ""),
        end_date: allTime ? "" : endDate.replace(/-/g, ""),
        all_time: allTime,
      });
      const scope = allTime ? "TẤT CẢ quảng cáo (mọi thời gian)" : `quảng cáo trong khoảng ${startDate} → ${endDate}`;
      push({
        type: "info",
        title: "Đang trích domain",
        message: `Đang lấy ${scope} của NQC này và trích domain ở nền (ảnh đọc bằng AI, có thể mất vài chục giây; video ad bỏ qua). Kết quả báo ở chuông; sau đó vào Tìm kiếm dự án, lọc nguồn "Google Ads: ${advertiserName || advertiserId}" rồi bấm "Dò affiliate (Google)".`,
      });
    } catch (e: any) {
      push({ type: "error", message: e.message || "Không thể gửi sang Tìm kiếm dự án" });
    } finally {
      setExporting(false);
    }
  };

  const openDetail = async (c: api.AdCreative) => {
    setSelected(c);
    setDetail(null);
    setDetailLoading(true);
    try {
      const d = await api.getAdDetails(c.advertiser_id, c.ad_creative_id, region);
      setDetail(d);
    } catch (e: any) {
      push({ type: "error", message: e.message });
    } finally {
      setDetailLoading(false);
    }
  };

  // ---- History ----
  const history = useQuery({
    queryKey: ["ads-history"],
    queryFn: () => api.listAdsHistory(30),
    staleTime: 30_000,
  });

  const restoreHistory = (h: api.AdsHistoryItem) => {
    if (h.advertiser_id) {
      setAdvertiserId(h.advertiser_id);
      setAdvertiserName("");
      setText("");
    } else {
      setText(h.text || "");
      setAdvertiserId("");
      setAdvertiserName("");
    }
    setPlatform(h.platform || "");
    setCreativeFormat(h.creative_format || "");
    setRegion(h.region || "");
    const toISO = (s: string) => (s && s.length === 8 ? `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)}` : s);
    if (h.start_date) setStartDate(toISO(h.start_date));
    if (h.end_date) setEndDate(toISO(h.end_date));
    if (h.num) setNumStr(String(h.num));
    setSerpNotice("");
    setSearched(true);
    // Restore cached results if available — không tốn SerpAPI quota
    if (h.results_json) {
      try {
        const cached = JSON.parse(h.results_json);
        setCreatives(cached.ad_creatives || []);
        setTotal(cached.search_information?.total_results);
        setNextToken(cached.pagination?.next_page_token || cached.serpapi_pagination?.next_page_token || "");
        return;
      } catch {
        // fallback: re-search nếu parse lỗi
      }
    }
    // Không có cache → gọi lại API
    setCreatives([]);
    setNextToken("");
    setTotal(undefined);
    setTimeout(() => search.mutate({ token: "" }), 0);
  };

  const removeHistory = async (id: number) => {
    try {
      await api.deleteAdsHistory(id);
      qc.invalidateQueries({ queryKey: ["ads-history"] });
    } catch (e: any) {
      push({ type: "error", message: e.message });
    }
  };

  const clearHistory = async () => {
    if (!confirm("Xoá toàn bộ lịch sử search?")) return;
    try {
      await api.clearAdsHistory();
      qc.invalidateQueries({ queryKey: ["ads-history"] });
    } catch (e: any) {
      push({ type: "error", message: e.message });
    }
  };

  const relTime = (iso: string | null) => {
    if (!iso) return "";
    const t = new Date(iso).getTime();
    if (Number.isNaN(t)) return "";
    const diff = Math.max(0, Date.now() - t);
    const s = Math.floor(diff / 1000);
    if (s < 60) return `${s}s trước`;
    const m = Math.floor(s / 60);
    if (m < 60) return `${m} phút trước`;
    const hr = Math.floor(m / 60);
    if (hr < 24) return `${hr} giờ trước`;
    const d = Math.floor(hr / 24);
    return `${d} ngày trước`;
  };

  // Trích iframe URLs từ ad-details (SerpAPI variations) cho preview
  const iframeUrls = useMemo<string[]>(() => {
    if (!detail) return [];
    const urls: string[] = [];
    const candidates: any[] = ([] as any[])
      .concat(detail.variations || [])
      .concat(detail.ad_creative ? [detail.ad_creative] : []);
    const isUrl = (v: any) => typeof v === "string" && /^https?:\/\//i.test(v);
    const keys = ["iframe", "iframe_src", "preview_iframe", "content_url", "creative_url", "preview", "url"];
    for (const v of candidates) {
      if (!v || typeof v !== "object") continue;
      for (const k of keys) {
        if (isUrl(v[k]) && !urls.includes(v[k])) urls.push(v[k]);
      }
    }
    return urls.slice(0, 3);
  }, [detail]);

  const totalLabel = useMemo(() => {
    if (typeof total !== "number") return null;
    return total.toLocaleString("vi-VN");
  }, [total]);

  // Số NHÀ QUẢNG CÁO distinct trong các creative đã tải — để khớp cột "Số NQC" ở
  // màn Chương trình (cùng đếm distinct advertiser_id). Màn này liệt kê ad_creatives
  // nên 1 NQC có thể xuất hiện nhiều lần; không có dòng này dễ tưởng ít NQC hơn thực.
  const distinctAdvertisers = useMemo(
    () => new Set(creatives.map((c) => c.advertiser_id).filter(Boolean)).size,
    [creatives],
  );

  return (
    <>
      <PageHeader
        title="Google Ads Transparency"
        description="Tra cứu quảng cáo Google đã/đang chạy theo domain hoặc nhà quảng cáo. Dữ liệu từ Trung tâm Minh bạch của Google (qua SerpAPI)."
      />

      <Card className="mb-6">
        <form onSubmit={onSubmit} className="grid grid-cols-1 md:grid-cols-12 gap-3">
          <div className="md:col-span-4">
            <Label>Từ khoá / Domain *</Label>
            <Input
              placeholder="vd: shopee.vn, lazada, …"
              value={text}
              onChange={(e) => setText(e.target.value)}
            />
          </div>
          <div className="md:col-span-2">
            <Label>Khu vực</Label>
            <Select value={region} onChange={(e) => setRegion(e.target.value)}>
              {REGIONS.map((r) => <option key={r.value || "global"} value={r.value}>{r.label}</option>)}
            </Select>
          </div>
          <div className="md:col-span-2">
            <Label>Nền tảng</Label>
            <Select value={platform} onChange={(e) => setPlatform(e.target.value)}>
              {PLATFORMS.map((p) => <option key={p.value || "all"} value={p.value}>{p.label}</option>)}
            </Select>
          </div>
          <div className="md:col-span-2">
            <Label>Định dạng</Label>
            <Select value={creativeFormat} onChange={(e) => setCreativeFormat(e.target.value)}>
              {FORMATS.map((f) => <option key={f.value || "any"} value={f.value}>{f.label}</option>)}
            </Select>
          </div>
          <div className="md:col-span-2">
            <Label>Số lượng / trang</Label>
            <Input
              type="number"
              min={1}
              max={100}
              value={numStr}
              onChange={(e) => setNumStr(e.target.value)}
              onBlur={() => setNumStr(String(num))}
            />
          </div>
          <div className="md:col-span-3">
            <Label>Từ ngày</Label>
            <Input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} />
          </div>
          <div className="md:col-span-3">
            <Label>Đến ngày</Label>
            <Input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} />
          </div>
          <div className="md:col-span-6 flex items-end justify-end">
            <Button type="submit" disabled={search.isPending}>
              {search.isPending ? <Loader2 size={16} className="animate-spin" /> : <Search size={16} />}
              Tìm quảng cáo
            </Button>
          </div>
        </form>

        <div className="mt-4 pt-4 border-t border-gray-100">
          <form onSubmit={onSubmitAdvertiser} className="grid grid-cols-1 md:grid-cols-12 gap-3 items-end">
            <div className="md:col-span-9">
              <Label>Hoặc xem theo nhà quảng cáo — dán Advertiser ID (AR…) hoặc link ATC</Label>
              <Input
                placeholder="AR13367361920911278081  ·  https://adstransparency.google.com/advertiser/AR…"
                value={advInput}
                onChange={(e) => setAdvInput(e.target.value)}
              />
            </div>
            <div className="md:col-span-3">
              <Button type="submit" variant="secondary" disabled={search.isPending} className="w-full">
                {search.isPending ? <Loader2 size={16} className="animate-spin" /> : <Layers size={16} />}
                Xem quảng cáo NQC
              </Button>
            </div>
          </form>
          <p className="text-xs text-gray-400 mt-1.5">
            Lấy mã ở web Google ATC (tìm theo tên → mở NQC → copy link), hoặc từ nguồn “Google Ads: …” trong màn Tìm kiếm dự án. Tìm theo NQC sẽ xem <b>Toàn cầu · Mọi nền tảng · Mọi định dạng</b> (đủ quảng cáo như Google ATC), chỉ lọc theo <b>Từ ngày–Đến ngày</b>.
          </p>
        </div>
      </Card>

      {history.data && history.data.length > 0 && (
        <Card className="mb-6">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2 text-sm font-semibold text-ink">
              <History size={16} className="text-primary" />
              Lịch sử tìm kiếm
              <span className="text-xs font-normal text-gray-400">({history.data.length})</span>
            </div>
            <button
              type="button"
              onClick={clearHistory}
              className="text-xs text-gray-500 hover:text-red-500 inline-flex items-center gap-1"
            >
              <Trash2 size={12} /> Xoá tất cả
            </button>
          </div>
          <div className="flex flex-wrap gap-2">
            {history.data.map((h) => {
              const reg = REGIONS.find((r) => r.value === h.region);
              return (
                <div
                  key={h.id}
                  className="group flex items-center gap-2 rounded-full border border-gray-200 hover:border-primary/40 hover:bg-primary/5 transition pl-3 pr-1 py-1 text-xs"
                >
                  <button
                    type="button"
                    onClick={() => restoreHistory(h)}
                    className="flex items-center gap-1.5 text-ink"
                    title={h.results_json ? "Khôi phục kết quả đã lưu (không tốn quota)" : "Chạy lại tìm kiếm này"}
                  >
                    <Clock size={11} className="text-gray-400 group-hover:text-primary" />
                    <span className="font-semibold truncate max-w-[160px]">{h.text || h.advertiser_id || "—"}</span>
                    {reg && <span className="text-gray-400">· {reg.label.split(" ")[0]}</span>}
                    {h.creative_format && <span className="text-gray-400">· {h.creative_format}</span>}
                    <span className="text-gray-400">· {h.result_count} kq</span>
                    <span className="text-gray-300">· {relTime(h.created_at)}</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => removeHistory(h.id)}
                    className="rounded-full p-1 text-gray-300 hover:text-red-500 hover:bg-red-50"
                    title="Xoá"
                  >
                    <X size={11} />
                  </button>
                </div>
              );
            })}
          </div>
        </Card>
      )}

      {advertiserId && (
        <div className="flex flex-wrap items-center gap-2 mb-3 text-sm">
          <span className="inline-flex items-center gap-1.5 rounded-full bg-primary/10 text-primary px-3 py-1 font-medium">
            <Layers size={13} />
            Quảng cáo của nhà quảng cáo: {advertiserName || advertiserId}
            <button
              type="button"
              aria-label="Bỏ lọc theo nhà quảng cáo"
              onClick={() => { setAdvertiserId(""); setAdvertiserName(""); setCreatives([]); setTotal(undefined); setNextToken(""); setSearched(false); }}
              className="ml-1 hover:text-primary/70"
            >
              <X size={13} />
            </button>
          </span>
          <Button
            variant="secondary"
            className="text-xs !py-1"
            onClick={() => handleExportToDiscovery(false)}
            disabled={exporting || creatives.length === 0}
            title="Chỉ gửi domain của quảng cáo TRONG khoảng Từ ngày–Đến ngày đang chọn"
          >
            {exporting ? <Loader2 size={13} className="animate-spin mr-1" /> : <Search size={13} className="mr-1" />}
            Gửi domain (khoảng ngày)
          </Button>
          <Button
            variant="secondary"
            className="text-xs !py-1"
            onClick={() => handleExportToDiscovery(true)}
            disabled={exporting || creatives.length === 0}
            title="Gửi domain của TẤT CẢ quảng cáo NQC này (mọi thời gian, bỏ lọc ngày)"
          >
            {exporting ? <Loader2 size={13} className="animate-spin mr-1" /> : <Layers size={13} className="mr-1" />}
            Gửi toàn bộ domain
          </Button>
        </div>
      )}

      {totalLabel && (
        <div className="text-sm text-gray-500 mb-3">
          Khớp <span className="font-semibold text-ink">{totalLabel}</span> quảng cáo
          {distinctAdvertisers > 0 && <> từ <span className="font-semibold text-ink">{distinctAdvertisers}</span> nhà quảng cáo</>}
          {creatives.length > 0 && <> · Đang hiển thị <span className="font-semibold text-ink">{creatives.length}</span></>}
        </div>
      )}

      {search.isPending && creatives.length === 0 && (
        <Card><p className="text-sm text-gray-400">Đang truy vấn SerpAPI…</p></Card>
      )}

      {!search.isPending && creatives.length === 0 && searched && (
        <EmptyState
          icon={Megaphone}
          title="Google ATC không có kết quả cho truy vấn này"
          description={
            (serpNotice ? serpNotice + " " : "") +
            "Gợi ý: dùng full domain (vd: binance.com thay vì binance), thử đổi Khu vực sang “Toàn cầu”, bỏ lọc Định dạng, hoặc mở rộng khoảng ngày."
          }
        />
      )}

      {!search.isPending && creatives.length === 0 && !searched && (
        <EmptyState
          icon={Megaphone}
          title="Chưa có kết quả"
          description="Nhập domain / từ khoá (vd: shopee.vn) rồi bấm Tìm quảng cáo để xem các quảng cáo Google đã/đang chạy."
        />
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        {creatives.map((c) => (
          <Card key={`${c.advertiser_id}_${c.ad_creative_id}`} className="hover:shadow-lg transition cursor-pointer flex flex-col" >
            <div onClick={() => openDetail(c)} className="flex-1 flex flex-col">
              {c.image ? (
                <div className="aspect-video w-full bg-gray-50 rounded-lg overflow-hidden mb-3 flex items-center justify-center">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={c.image} alt={c.advertiser || ""} className="max-w-full max-h-full object-contain" />
                </div>
              ) : (
                <div className="aspect-video w-full rounded-lg mb-3 p-4 flex flex-col justify-center bg-gradient-to-br from-primary/5 via-white to-primary/10 border border-primary/10 relative overflow-hidden">
                  <div className="absolute top-2 left-2 text-[10px] font-semibold text-primary/70 uppercase tracking-wider flex items-center gap-1">
                    <Megaphone size={10} /> Ad · {(c.format || "text").toString()}
                  </div>
                  <div className="text-primary text-sm font-semibold truncate mt-3">
                    {c.target_domain || c.advertiser || "Quảng cáo"}
                  </div>
                  <div className="text-ink text-base font-bold leading-snug line-clamp-2 mt-1">
                    {c.advertiser || "—"}
                  </div>
                  <div className="text-gray-500 text-xs line-clamp-2 mt-1">
                    Quảng cáo dạng văn bản — bấm để xem chi tiết các biến thể.
                  </div>
                </div>
              )}
              <div className="flex items-start justify-between gap-2 mb-2">
                <div className="font-semibold text-ink text-sm truncate flex-1">{c.advertiser || "—"}</div>
                <FormatBadge format={c.format} />
              </div>
              {c.target_domain && (
                <div className="text-xs text-primary truncate mb-2">{c.target_domain}</div>
              )}
              <div className="mt-auto flex items-center justify-between text-xs text-gray-500 pt-2 border-t border-gray-100">
                <span className="flex items-center gap-1"><Clock size={12} /> {c.total_days_shown ?? "—"} ngày</span>
                <span className="flex items-center gap-1"><Calendar size={12} /> {fmtDate(c.last_shown)}</span>
              </div>
            </div>
            <button
              type="button"
              onClick={() => searchByAdvertiser(c.advertiser_id, c.advertiser)}
              title="Lọc tất cả quảng cáo khác của nhà quảng cáo này"
              className="mt-2 w-full text-xs text-primary hover:bg-primary/5 rounded-md py-1.5 flex items-center justify-center gap-1 border-t border-gray-50"
            >
              <Layers size={12} /> Quảng cáo khác của NQC
            </button>
          </Card>
        ))}
      </div>

      {nextToken && (
        <div className="flex justify-center mt-6">
          <Button
            variant="secondary"
            onClick={() => search.mutate({ token: nextToken })}
            disabled={search.isPending}
          >
            {search.isPending ? <Loader2 size={16} className="animate-spin" /> : null}
            Tải thêm
          </Button>
        </div>
      )}

      <Modal open={!!selected} onClose={() => { setSelected(null); setDetail(null); }} title={selected?.advertiser || "Chi tiết quảng cáo"}>
        {selected && (
          <div className="space-y-4">
            {selected.image && (
              <div className="w-full bg-gray-50 rounded-lg overflow-hidden flex items-center justify-center">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={selected.image} alt="" className="max-w-full max-h-72 object-contain" />
              </div>
            )}
            <div className="grid grid-cols-2 gap-3 text-sm">
              <div>
                <div className="text-xs text-gray-400">Định dạng</div>
                <div><FormatBadge format={selected.format} /></div>
              </div>
              <div>
                <div className="text-xs text-gray-400">Domain đích</div>
                <div className="text-ink truncate">{selected.target_domain || "—"}</div>
              </div>
              <div>
                <div className="text-xs text-gray-400">Số ngày hiển thị</div>
                <div className="text-ink">{selected.total_days_shown ?? "—"}</div>
              </div>
              <div>
                <div className="text-xs text-gray-400">Lần đầu / Lần cuối</div>
                <div className="text-ink">{fmtDate(selected.first_shown)} → {fmtDate(selected.last_shown)}</div>
              </div>
              <div className="col-span-2">
                <div className="text-xs text-gray-400">Advertiser ID</div>
                <div className="text-ink font-mono text-xs break-all">{selected.advertiser_id}</div>
              </div>
              <div className="col-span-2">
                <div className="text-xs text-gray-400">Creative ID</div>
                <div className="text-ink font-mono text-xs break-all">{selected.ad_creative_id}</div>
              </div>
            </div>

            <Button
              variant="secondary"
              className="w-full"
              onClick={() => searchByAdvertiser(selected.advertiser_id, selected.advertiser)}
            >
              <Layers size={14} className="mr-1.5" />
              Xem thêm quảng cáo của nhà quảng cáo này
            </Button>

            {selected.details_link && (
              <a
                href={selected.details_link}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1.5 text-sm text-primary hover:underline"
              >
                Xem trên Trung tâm Minh bạch <ExternalLink size={14} />
              </a>
            )}

            <div className="border-t border-gray-100 pt-3">
              <div className="text-xs font-semibold text-gray-500 mb-2 uppercase tracking-wide">Chi tiết bổ sung</div>
              {detailLoading && (
                <div className="flex items-center gap-2 text-sm text-gray-400">
                  <Loader2 size={14} className="animate-spin" /> Đang tải…
                </div>
              )}
              {!detailLoading && iframeUrls.length > 0 && (
                <div className="space-y-2 mb-3">
                  <div className="text-[11px] text-gray-400">
                    Preview iframe (Google có thể chặn embed bằng X-Frame-Options — nếu trắng thì mở link trực tiếp):
                  </div>
                  {iframeUrls.map((u) => (
                    <div key={u} className="rounded-lg border border-gray-100 overflow-hidden bg-white">
                      <iframe
                        src={u}
                        title="Ad creative preview"
                        sandbox="allow-scripts allow-same-origin allow-popups"
                        loading="lazy"
                        className="w-full h-72 bg-white"
                      />
                      <a href={u} target="_blank" rel="noreferrer" className="block px-2 py-1 text-[10px] text-gray-400 hover:text-primary truncate border-t border-gray-100">
                        {u}
                      </a>
                    </div>
                  ))}
                </div>
              )}
              {!detailLoading && detail && (
                <pre className="text-xs bg-gray-50 rounded-lg p-3 overflow-auto max-h-64 text-gray-600">
                  {JSON.stringify(
                    {
                      regions_detail: detail.regions_detail,
                      variations: (detail.variations || []).slice(0, 3),
                      ad_creative: detail.ad_creative,
                    },
                    null,
                    2,
                  )}
                </pre>
              )}
            </div>
          </div>
        )}
      </Modal>
    </>
  );
}
