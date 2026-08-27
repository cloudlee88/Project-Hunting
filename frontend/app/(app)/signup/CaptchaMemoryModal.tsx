"use client";
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Brain, X, Save, Loader2, RefreshCw } from "lucide-react";
import { getCaptchaMemory, putCaptchaMemory, type CaptchaMemory } from "@/lib/api";
import { useToast } from "@/lib/toast";

export function CaptchaMemoryModal({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const { push } = useToast();
  const { data, isLoading } = useQuery({ queryKey: ["captcha-memory"], queryFn: getCaptchaMemory });
  const [raw, setRaw] = useState<string>("");
  const [parseErr, setParseErr] = useState<string>("");
  const [mounted, setMounted] = useState(false);

  useEffect(() => { setMounted(true); }, []);

  useEffect(() => {
    if (data) setRaw(JSON.stringify(data, null, 2));
  }, [data]);

  const save = useMutation({
    mutationFn: () => {
      let parsed: CaptchaMemory;
      try {
        parsed = JSON.parse(raw);
      } catch {
        throw new Error("JSON không hợp lệ — vui lòng kiểm tra lại");
      }
      return putCaptchaMemory(parsed);
    },
    onSuccess: (updated) => {
      qc.setQueryData(["captcha-memory"], updated);
      setRaw(JSON.stringify(updated, null, 2));
      push({ type: "success", message: "Đã lưu bộ nhớ captcha" });
    },
    onError: (e: Error) => push({ type: "error", message: e.message }),
  });

  function handleChange(val: string) {
    setRaw(val);
    try {
      JSON.parse(val);
      setParseErr("");
    } catch (e: any) {
      setParseErr(e.message);
    }
  }

  if (!mounted) return null;

  return createPortal(
    <div className="fixed inset-0 z-[200] flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div
        className="bg-white rounded-2xl shadow-2xl w-full max-w-2xl flex flex-col max-h-[90vh]"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b">
          <h2 className="font-semibold flex items-center gap-2 text-violet-700">
            <Brain size={18} /> Bộ nhớ Captcha
          </h2>
          <button onClick={onClose} title="Đóng" aria-label="Đóng" className="text-gray-400 hover:text-gray-700 transition">
            <X size={20} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-auto p-5 flex flex-col gap-3">
          <p className="text-xs text-gray-500 leading-relaxed">
            Agent tự ghi nhớ kinh nghiệm giải captcha sau mỗi lần thành công.
            Bạn có thể chỉnh sửa trực tiếp <code>user_notes</code> để thêm mẹo cá nhân.
          </p>

          {isLoading ? (
            <div className="flex justify-center py-10">
              <Loader2 size={24} className="animate-spin text-violet-500" />
            </div>
          ) : (
            <div className="flex flex-col gap-1">
              <textarea
                title="Captcha Memory JSON"
                aria-label="Captcha Memory JSON editor"
                className={[
                  "font-mono text-xs border rounded-lg p-3 resize-none min-h-[320px] focus:outline-none focus:ring-2",
                  parseErr ? "border-red-400 focus:ring-red-300" : "border-gray-300 focus:ring-violet-300",
                ].join(" ")}
                value={raw}
                onChange={(e) => handleChange(e.target.value)}
                spellCheck={false}
              />
              {parseErr && (
                <p className="text-xs text-red-600 mt-1">{parseErr}</p>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between gap-2 px-5 py-3 border-t bg-gray-50 rounded-b-2xl">
          <button
            className="text-xs text-gray-500 hover:text-gray-700 flex items-center gap-1"
            onClick={() => {
              qc.invalidateQueries({ queryKey: ["captcha-memory"] });
              push({ type: "info", message: "Đã làm mới" });
            }}
          >
            <RefreshCw size={12} /> Làm mới
          </button>
          <div className="flex items-center gap-2">
            <button
              className="text-sm px-4 py-1.5 rounded-lg border border-gray-300 text-gray-700 hover:bg-gray-100 transition"
              onClick={onClose}
            >
              Đóng
            </button>
            <button
              className="text-sm px-4 py-1.5 rounded-lg bg-violet-600 text-white hover:bg-violet-700 transition flex items-center gap-1.5 disabled:opacity-60"
              onClick={() => save.mutate()}
              disabled={save.isPending || !!parseErr || isLoading}
            >
              {save.isPending ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}
              Lưu
            </button>
          </div>
        </div>
      </div>
    </div>
  , document.body);
}
