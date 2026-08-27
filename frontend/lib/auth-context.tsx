"use client";
import { createContext, useContext, useEffect, useState, ReactNode } from "react";
import * as api from "./api";

type Ctx = {
  user: api.User | null;
  isAdmin: boolean;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  /** Đăng ký KHÔNG tự đăng nhập — tài khoản phải chờ admin duyệt. */
  register: (email: string, password: string) => Promise<api.RegisterResult>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
};

const AuthCtx = createContext<Ctx | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<api.User | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = async () => {
    try {
      const u = await api.getMe();
      setUser(u);
    } catch {
      setUser(null);
    }
  };

  useEffect(() => {
    (async () => { await refresh(); setLoading(false); })();
  }, []);

  const value: Ctx = {
    user, loading,
    isAdmin: user?.role === api.UserRole.ADMIN,
    login: async (e, p) => { const u = await api.login(e, p); setUser(u); },
    register: (e, p) => api.register(e, p),
    logout: async () => { await api.logout(); setUser(null); },
    refresh,
  };
  return <AuthCtx.Provider value={value}>{children}</AuthCtx.Provider>;
}

export function useAuth() {
  const v = useContext(AuthCtx);
  if (!v) throw new Error("useAuth phải dùng trong AuthProvider");
  return v;
}
