"use client";
import { useEffect, useRef, useState } from "react";
import clsx from "clsx";
import { Check, ChevronDown } from "lucide-react";

const boxClass =
  "block w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-ink " +
  "focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/30 " +
  "disabled:bg-gray-50 disabled:text-gray-400 disabled:cursor-not-allowed transition";

/** Dropdown chọn NHIỀU phương án (checkbox + ô tìm). Dùng cho danh sách dài (vd Sub-category). */
export function MultiSelect({
  options,
  value,
  onChange,
  placeholder = "Tất cả",
  disabled,
  searchPlaceholder = "Tìm...",
  "aria-label": ariaLabel,
}: {
  options: string[];
  value: string[];
  onChange: (v: string[]) => void;
  placeholder?: string;
  disabled?: boolean;
  searchPlaceholder?: string;
  "aria-label"?: string;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const filtered = options.filter((o) => o.toLowerCase().includes(q.trim().toLowerCase()));
  const toggle = (o: string) =>
    onChange(value.includes(o) ? value.filter((x) => x !== o) : [...value, o]);
  const summary =
    value.length === 0 ? placeholder : value.length === 1 ? value[0] : `${value.length} đã chọn`;

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        aria-label={ariaLabel}
        className={clsx(boxClass, "pr-8 relative flex items-center text-left")}
      >
        <span className={clsx("truncate", value.length === 0 && "text-gray-400")}>{summary}</span>
        <ChevronDown size={16} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-gray-400 pointer-events-none" />
      </button>

      {open && !disabled && (
        <div className="absolute z-30 mt-1 w-full rounded-lg border border-gray-200 bg-white shadow-lg max-h-72 overflow-hidden flex flex-col">
          <div className="p-2 border-b border-gray-100">
            <input
              autoFocus
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder={searchPlaceholder}
              className="w-full rounded-md border border-gray-200 px-2 py-1.5 text-sm focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary/30"
            />
          </div>
          <div className="flex items-center justify-between px-3 py-1.5 text-xs border-b border-gray-100">
            <span className="text-gray-500 tabular-nums">{value.length} / {options.length}</span>
            <div className="flex gap-3">
              <button type="button" className="text-primary hover:underline" onClick={() => onChange(options.slice())}>Chọn tất cả</button>
              <button type="button" className="text-gray-500 hover:underline" onClick={() => onChange([])}>Bỏ chọn</button>
            </div>
          </div>
          <div className="overflow-y-auto">
            {filtered.length === 0 ? (
              <div className="px-3 py-3 text-sm text-gray-400">Không có kết quả</div>
            ) : (
              filtered.map((o) => {
                const sel = value.includes(o);
                return (
                  <button
                    key={o}
                    type="button"
                    onClick={() => toggle(o)}
                    className={clsx(
                      "w-full flex items-center gap-2 px-3 py-1.5 text-sm text-left hover:bg-primary-50/60",
                      sel && "bg-primary-50/40",
                    )}
                  >
                    <span className={clsx(
                      "w-4 h-4 rounded border flex items-center justify-center shrink-0",
                      sel ? "bg-primary border-primary text-white" : "border-gray-300",
                    )}>
                      {sel && <Check size={12} />}
                    </span>
                    <span className="truncate">{o}</span>
                  </button>
                );
              })
            )}
          </div>
        </div>
      )}
    </div>
  );
}
