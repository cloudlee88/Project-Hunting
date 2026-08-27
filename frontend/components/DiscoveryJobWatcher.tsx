"use client";
import { useEffect, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import * as api from "@/lib/api";
import { useToast } from "@/lib/toast";

/**
 * Global watcher for discovery crawl jobs. Polls /api/jobs and pushes a bell
 * notification when a discovery job starts, completes, or fails — so the user
 * always knows from the bell whether a scan is still running or finished, even
 * when on another screen.
 *
 * Shares the ["jobs"] query cache with the Jobs + Discovery screens (no extra
 * polling). Renders nothing.
 */
export function DiscoveryJobWatcher() {
  const { push } = useToast();
  const qc = useQueryClient();
  const seen = useRef<Map<number, string>>(new Map());
  const initialized = useRef(false);

  const jobs = useQuery({
    queryKey: ["jobs"],
    queryFn: api.listJobs,
    refetchInterval: 3000,
  });

  useEffect(() => {
    if (!jobs.data) return;
    const DISCOVERY_SOURCES = ["discovery", "discovery_traffic", "discovery_affiliate", "discovery_affiliate_search", "discovery_ads_export"];
    const list = jobs.data.filter((j) => DISCOVERY_SOURCES.includes(j.source));

    // First load: seed current statuses WITHOUT notifying (avoid re-announcing
    // jobs that finished before this tab was opened).
    if (!initialized.current) {
      for (const j of list) seen.current.set(j.id, j.status);
      initialized.current = true;
      return;
    }

    for (const j of list) {
      const prev = seen.current.get(j.id);
      if (prev === j.status) continue;
      seen.current.set(j.id, j.status);

      const total = (j.params?.total as number) ?? 0;

      if (j.source === "discovery_traffic") {
        if (j.status === "running" && prev === undefined)
          push({ type: "info", title: "Quét traffic", message: `Bắt đầu quét traffic cho ${total} dự án…`, href: "/discovery" });
        else if (j.status === "success")
          push({ type: "success", title: "Quét traffic xong", message: `Đã quét ${j.total_saved}/${total} domain · ${j.total_found} có data.`, href: "/discovery" });
        else if (j.status === "failed")
          push({ type: "error", title: "Quét traffic lỗi", message: (j.error || "").split("\n")[0] || "lỗi", href: "/jobs" });
        if (j.status === "success") qc.invalidateQueries({ queryKey: ["discovery-candidates"] });
        continue;
      }

      if (j.source === "discovery_affiliate" || j.source === "discovery_affiliate_search") {
        if (j.status === "running" && prev === undefined)
          push({ type: "info", title: "Dò affiliate", message: `Bắt đầu dò affiliate cho ${total} domain…`, href: "/discovery" });
        else if (j.status === "success")
          push({ type: "success", title: "Dò affiliate xong", message: `Đã dò ${j.total_saved}/${total} · tìm thấy ${j.total_found} affiliate.`, href: "/discovery" });
        else if (j.status === "failed")
          push({ type: "error", title: "Dò affiliate lỗi", message: (j.error || "").split("\n")[0] || "lỗi", href: "/jobs" });
        if (j.status === "success") {
          qc.invalidateQueries({ queryKey: ["discovery-candidates"] });
          qc.invalidateQueries({ queryKey: ["discovery-summary"] });
        }
        continue;
      }

      if (j.source === "discovery_ads_export") {
        const adv = (j.params?.advertiser as string) || "NQC";
        if (j.status === "running" && prev === undefined)
          push({ type: "info", title: "Trích domain quảng cáo", message: `Đang trích domain từ ${total} quảng cáo của ${adv}…`, href: "/discovery" });
        else if (j.status === "success")
          push({ type: "success", title: "Trích domain xong", message: `${adv}: lấy ${j.total_found} domain (${j.total_saved} mới) → nguồn "Google Ads: ${adv}". Vào Tìm kiếm dự án để dò affiliate.`, href: "/discovery" });
        else if (j.status === "failed")
          push({ type: "error", title: "Trích domain lỗi", message: (j.error || "").split("\n")[0] || "lỗi", href: "/jobs" });
        if (j.status === "success") {
          qc.invalidateQueries({ queryKey: ["discovery-sources"] });
          qc.invalidateQueries({ queryKey: ["discovery-candidates"] });
          qc.invalidateQueries({ queryKey: ["discovery-summary"] });
        }
        continue;
      }

      // Crawl job (source === "discovery")
      const name = (j.params?.name as string) || (j.params?.url as string) || `#${j.id}`;
      if (j.status === "running" && prev === undefined) {
        push({ type: "info", title: "Tìm kiếm dự án", message: `Bắt đầu quét nguồn ${name}…`, href: "/discovery" });
      } else if (j.status === "success") {
        push({ type: "success", title: "Quét hoàn tất", message: `Nguồn ${name}: tìm thấy +${j.total_found} dự án mới.`, href: "/discovery" });
        qc.invalidateQueries({ queryKey: ["discovery-sources"] });
        qc.invalidateQueries({ queryKey: ["discovery-candidates"] });
        qc.invalidateQueries({ queryKey: ["discovery-summary"] });
      } else if (j.status === "failed") {
        push({ type: "error", title: "Quét thất bại", message: `Nguồn ${name}: ${(j.error || "").split("\n")[0] || "lỗi không xác định"}`, href: "/jobs" });
      }
    }
  }, [jobs.data, push, qc]);

  return null;
}
