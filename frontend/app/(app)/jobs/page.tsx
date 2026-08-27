"use client";
import { Fragment, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ListChecks, ChevronDown, ChevronUp } from "lucide-react";
import * as api from "@/lib/api";
import { Card } from "@/components/Card";
import { PageHeader } from "@/components/PageHeader";
import { JobStatusBadge } from "@/components/Badge";
import { EmptyState } from "@/components/EmptyState";
import { Button } from "@/components/Button";

export default function JobsPage() {
  const qc = useQueryClient();
  const jobs = useQuery({ queryKey: ["jobs"], queryFn: api.listJobs, refetchInterval: 2000 });
  const [expandedId, setExpandedId] = useState<number | null>(null);

  const toggle = (id: number) => setExpandedId(prev => prev === id ? null : id);

  return (
    <div>
      <PageHeader
        title="Jobs"
        description="Lịch sử các phiên crawl. Tự động làm mới mỗi 2 giây."
        action={<Button variant="secondary" onClick={() => qc.invalidateQueries({ queryKey: ["jobs"] })}>Làm mới</Button>}
      />

      <Card className="!p-0 overflow-hidden">
        {!jobs.data || jobs.data.length === 0 ? (
          <EmptyState icon={ListChecks} title="Chưa có job nào" description='Vào "Nguồn quét" để bắt đầu một phiên crawl.' />
        ) : (
          <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-canvas text-xs uppercase text-gray-500 tracking-wider">
              <tr>
                <th className="px-4 py-3 text-left w-16">ID</th>
                <th className="px-4 py-3 text-left">Source</th>
                <th className="px-4 py-3 text-left">Trạng thái</th>
                <th className="px-4 py-3 text-right">Found / Saved</th>
                <th className="px-4 py-3 text-left">Bắt đầu</th>
                <th className="px-4 py-3 text-left">Kết thúc</th>
                <th className="px-4 py-3 text-left">Error</th>
                <th className="px-4 py-3 w-8"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {jobs.data.map((j) => {
                const isExpanded = expandedId === j.id;
                return (
                  <Fragment key={j.id}>
                    <tr
                      className="hover:bg-primary-50/40 transition-colors cursor-pointer"
                      onClick={() => toggle(j.id)}
                    >
                      <td className="px-4 py-3 font-mono text-gray-400">#{j.id}</td>
                      <td className="px-4 py-3 font-medium text-ink">
                        {j.source}
                        {j.params && Object.keys(j.params).length > 0 && (
                          <span className="ml-2 text-[11px] text-gray-400">
                            ({Object.entries(j.params).map(([k, v]) => `${k}=${v}`).join(", ")})
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3"><JobStatusBadge status={j.status} /></td>
                      <td className="px-4 py-3 text-right tabular-nums">{j.total_saved} / {j.total_found}</td>
                      <td className="px-4 py-3 text-gray-500">{j.started_at ? new Date(j.started_at + "Z").toLocaleTimeString("vi-VN") : "—"}</td>
                      <td className="px-4 py-3 text-gray-500">{j.finished_at ? new Date(j.finished_at + "Z").toLocaleTimeString("vi-VN") : "—"}</td>
                      <td className="px-4 py-3 text-red-600 text-xs max-w-[300px] truncate">{j.error || "—"}</td>
                      <td className="px-4 py-3 text-gray-400">
                        {isExpanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                      </td>
                    </tr>
                    {isExpanded && (
                      <tr key={`${j.id}-detail`} className="bg-gray-50/70">
                        <td colSpan={8} className="px-6 py-4">
                          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs">
                            <div className="space-y-2">
                              <p className="font-semibold text-gray-700 mb-2">Chi tiết job</p>
                              <div className="flex gap-2"><span className="text-gray-500 w-24 shrink-0">Job ID</span><span className="font-mono text-gray-800">#{j.id}</span></div>
                              <div className="flex gap-2"><span className="text-gray-500 w-24 shrink-0">Source</span><span className="text-gray-800">{j.source}</span></div>
                              {j.params && Object.keys(j.params).length > 0 && Object.entries(j.params).map(([k, v]) => (
                                <div key={k} className="flex gap-2"><span className="text-gray-500 w-24 shrink-0">{k}</span><span className="text-gray-800">{String(v)}</span></div>
                              ))}
                              <div className="flex gap-2"><span className="text-gray-500 w-24 shrink-0">Found</span><span className="tabular-nums text-gray-800">{j.total_found}</span></div>
                              <div className="flex gap-2"><span className="text-gray-500 w-24 shrink-0">Saved</span><span className="tabular-nums text-gray-800">{j.total_saved}</span></div>
                            </div>
                            <div className="space-y-2">
                              <p className="font-semibold text-gray-700 mb-2">Thời gian</p>
                              <div className="flex gap-2"><span className="text-gray-500 w-24 shrink-0">Bắt đầu</span><span className="text-gray-800">{j.started_at ? new Date(j.started_at + "Z").toLocaleString("vi-VN") : "—"}</span></div>
                              <div className="flex gap-2"><span className="text-gray-500 w-24 shrink-0">Kết thúc</span><span className="text-gray-800">{j.finished_at ? new Date(j.finished_at + "Z").toLocaleString("vi-VN") : "—"}</span></div>
                              {j.error && (
                                <>
                                  <p className="font-semibold text-red-700 mt-3 mb-1">Lỗi đầy đủ</p>
                                  <pre className="bg-red-50 border border-red-100 rounded-lg p-3 text-red-700 text-[11px] whitespace-pre-wrap break-words max-h-48 overflow-y-auto">{j.error}</pre>
                                </>
                              )}
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
          </div>
        )}
      </Card>
    </div>
  );
}
