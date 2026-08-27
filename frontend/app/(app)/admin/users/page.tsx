"use client";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Users, Check, Lock, Trash2, ShieldCheck, Clock } from "lucide-react";
import * as api from "@/lib/api";
import { Card } from "@/components/Card";
import { PageHeader } from "@/components/PageHeader";
import { Button } from "@/components/Button";
import { Badge } from "@/components/Badge";
import { EmptyState } from "@/components/EmptyState";
import { useAuth } from "@/lib/auth-context";
import { useToast } from "@/lib/toast";

function fmt(d: string) {
  return new Date(d).toLocaleString("vi-VN", { dateStyle: "short", timeStyle: "short" });
}

export default function AdminUsersPage() {
  const { isAdmin, loading } = useAuth();
  const router = useRouter();
  const qc = useQueryClient();
  const { push } = useToast();

  useEffect(() => {
    if (!loading && !isAdmin) router.replace("/dashboard");
  }, [loading, isAdmin, router]);

  const { data, isLoading } = useQuery({
    queryKey: ["admin-users"],
    queryFn: api.listAdminUsers,
    enabled: isAdmin,
  });

  const act = useMutation({
    mutationFn: async ({ id, kind }: { id: number; kind: "approve" | "deactivate" | "delete" }) => {
      if (kind === "approve") await api.approveUser(id);
      else if (kind === "deactivate") await api.deactivateUser(id);
      else await api.deleteUser(id);
    },
    onSuccess: (_r, v) => {
      qc.invalidateQueries({ queryKey: ["admin-users"] });
      qc.invalidateQueries({ queryKey: ["pending-users"] });
      push({
        type: "success",
        message: v.kind === "approve" ? "Đã duyệt tài khoản"
          : v.kind === "deactivate" ? "Đã khoá tài khoản" : "Đã xoá tài khoản",
      });
    },
    onError: (e: any) => push({ type: "error", message: e.message || "Thao tác thất bại" }),
  });

  if (!isAdmin) return null;

  const users = data?.items || [];
  const pending = users.filter((u) => !u.is_active);
  const active = users.filter((u) => u.is_active);

  const row = (u: api.AdminUser) => (
    <tr key={u.id} className="border-t border-gray-100 hover:bg-gray-50/60">
      <td className="px-4 py-3">
        <div className="font-medium text-ink flex items-center gap-2">
          {u.email}
          {u.role === api.UserRole.ADMIN && (
            <Badge variant="primary"><ShieldCheck size={11} /> admin</Badge>
          )}
        </div>
        <div className="text-xs text-gray-400 mt-0.5">#{u.id} · tạo {fmt(u.created_at)}</div>
      </td>
      <td className="px-4 py-3">
        {u.is_active
          ? <Badge variant="success">Đang hoạt động</Badge>
          : <Badge variant="warning"><Clock size={11} /> Chờ duyệt</Badge>}
      </td>
      <td className="px-4 py-3 text-sm text-gray-500 tabular-nums">
        {u.source_count} nguồn · {u.candidate_count} dự án
      </td>
      <td className="px-4 py-3">
        <div className="flex justify-end gap-2">
          {u.role === api.UserRole.ADMIN ? (
            <span className="text-xs text-gray-400">—</span>
          ) : u.is_active ? (
            <Button size="sm" variant="secondary" loading={act.isPending}
              onClick={() => act.mutate({ id: u.id, kind: "deactivate" })}>
              <Lock size={14} /> Khoá
            </Button>
          ) : (
            <>
              <Button size="sm" variant="cta" loading={act.isPending}
                onClick={() => act.mutate({ id: u.id, kind: "approve" })}>
                <Check size={14} /> Duyệt
              </Button>
              <Button size="sm" variant="danger" loading={act.isPending}
                onClick={() => {
                  if (confirm(`Xoá tài khoản ${u.email}?`)) act.mutate({ id: u.id, kind: "delete" });
                }}>
                <Trash2 size={14} />
              </Button>
            </>
          )}
        </div>
      </td>
    </tr>
  );

  return (
    <div>
      <PageHeader
        title="Người dùng"
        description="Duyệt tài khoản mới và quản lý quyền truy cập. Role admin chỉ đổi được trực tiếp trong database."
      />

      {isLoading ? (
        <Card><p className="text-sm text-gray-400">Đang tải…</p></Card>
      ) : (
        <div className="space-y-6">
          {/* Hàng đợi việc cần làm — chỉ hiện khi thật sự có người chờ duyệt */}
          {pending.length > 0 && (
            <Card className="!p-0 overflow-hidden border-l-4 border-l-amber-400">
              <div className="px-4 py-3 border-b border-gray-100 flex items-center gap-2">
                <Clock size={16} className="text-amber-500" />
                <span className="font-semibold text-ink text-sm">Chờ duyệt</span>
                <span className="text-xs font-semibold text-white bg-amber-500 rounded-full px-2 py-0.5 tabular-nums">
                  {pending.length}
                </span>
              </div>
              <table className="w-full text-sm"><tbody>{pending.map(row)}</tbody></table>
            </Card>
          )}

          <Card className="!p-0 overflow-hidden">
            <div className="px-4 py-3 border-b border-gray-100 flex flex-wrap items-center gap-2">
              <Users size={16} className="text-primary" />
              <span className="font-semibold text-ink text-sm">Tất cả người dùng ({users.length})</span>
              <span className="text-xs text-gray-400">
                · {active.length} đang hoạt động · {pending.length} chờ duyệt
              </span>
            </div>
            {users.length === 0 ? (
              <EmptyState icon={Check} title="Chưa có tài khoản nào" />
            ) : (
              <table className="w-full text-sm"><tbody>{users.map(row)}</tbody></table>
            )}
          </Card>
        </div>
      )}
    </div>
  );
}
