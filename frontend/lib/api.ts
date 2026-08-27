const BASE = "/api";

async function req<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(BASE + path, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(init.headers || {}) },
    ...init,
  });
  // Phiên hết hạn / chưa đăng nhập → mọi API trả 401. Chuyển thẳng về trang đăng
  // nhập (trừ khi đang ở /login để lỗi đăng nhập sai vẫn hiển thị) thay vì để 401
  // nổi lên thành lỗi khó hiểu "máy chủ đang bận / mất kết nối".
  if (res.status === 401 && typeof window !== "undefined"
      && !window.location.pathname.startsWith("/login")) {
    window.location.href = "/login?expired=1";
    throw new Error("Phiên đăng nhập đã hết hạn — vui lòng đăng nhập lại.");
  }
  if (!res.ok) {
    let msg = "Lỗi không xác định";
    try {
      const j = await res.json();
      if (typeof j.detail === "string") msg = j.detail;
      else if (Array.isArray(j.detail)) msg = j.detail.map((d: { msg?: string }) => d.msg || JSON.stringify(d)).join("; ");
      else if (j.message) msg = String(j.message);
      else msg = JSON.stringify(j);
    } catch {}
    throw new Error(msg);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

// ---- Auth ----
/** Khớp 1-1 với UserRole ở backend (app/models/user.py). */
export const UserRole = { USER: "user", ADMIN: "admin" } as const;
export type UserRole = (typeof UserRole)[keyof typeof UserRole];

export type User = { id: number; email: string; role: UserRole; is_active: boolean; created_at: string };
export type RegisterResult = { id: number; email: string; pending_approval: boolean; message: string };
/** Tạo tài khoản — CHƯA đăng nhập được, phải chờ admin duyệt. */
export const register = (email: string, password: string) =>
  req<RegisterResult>("/auth/register", { method: "POST", body: JSON.stringify({ email, password }) });
export const login = (email: string, password: string) =>
  req<User>("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) });
export const logout = () => req<{ ok: boolean }>("/auth/logout", { method: "POST" });
export const getMe = () => req<User>("/auth/me");

// ---- Admin (quản lý tài khoản) ----
export type AdminUser = {
  id: number; email: string; role: UserRole; is_active: boolean; created_at: string;
  source_count: number; candidate_count: number;
};
export const listAdminUsers = () =>
  req<{ items: AdminUser[]; pending_count: number }>("/admin/users");
export const getPendingUserCount = () =>
  req<{ pending_count: number }>("/admin/users/pending-count");
export const approveUser = (id: number) =>
  req<{ ok: boolean }>(`/admin/users/${id}/approve`, { method: "POST" });
export const deactivateUser = (id: number) =>
  req<{ ok: boolean }>(`/admin/users/${id}/deactivate`, { method: "POST" });
export const deleteUser = (id: number) =>
  req<void>(`/admin/users/${id}`, { method: "DELETE" });

// ---- Sources ----
export type SourceOption = { key: string; label: string; choices: { value: string; label: string }[]; default?: string };
export type Source = { code: string; name: string; base_url: string; description: string; icon_hint: string; highlight: boolean; options?: SourceOption[] };
export const listSources = () => req<Source[]>("/sources");

// ---- Crawl ----
export const startCrawl = (source: string, params?: Record<string, any>) =>
  req<{ job_id: number; source: string; status: string }>(`/crawl/${source}`, {
    method: "POST",
    body: JSON.stringify({ params: params || null }),
  });

// ---- Jobs ----
export type Job = {
  id: number; source: string; status: string;
  total_found: number; total_saved: number;
  error: string | null; started_at: string | null; finished_at: string | null; created_at: string;
  params?: Record<string, any> | null;
};
export const listJobs = () => req<Job[]>("/jobs");
export const getJob = (id: number) => req<Job>(`/jobs/${id}`);

// ---- Programs ----
export type Program = {
  id: number; source: string; external_id: string; name: string;
  url: string | null; signup_url: string | null;
  category: string | null; sub_category?: string | null; field?: string | null; note?: string | null; review_status?: string | null; commission: string | null;
  commission_value: number | null; commission_type: string | null;
  payout: string | null; cookie_duration: string | null;
  description: string | null; tags_json: string | null;
  raw_json: string | null; source_url: string | null;
  directory_traffic: string | null;
  directory_popularity: string | null;
  directory_status: string | null;
  logo_url?: string | null;
  short_description?: string | null;
  directory_network?: string | null;
  directory_approval?: string | null;
  directory_approval_time?: string | null;
  directory_attribution?: string | null;
  directory_tracking?: string | null;
  directory_last_verified_at?: string | null;
  directory_program_age?: string | null;
  payout_min?: number | null;
  payout_currency?: string | null;
  payout_frequency?: string | null;
  payout_methods_json?: string | null;
  commission_duration?: string | null;
  commission_conditions?: string | null;
  restrictions_json?: string | null;
  agents_json?: string | null;
  registrations_open?: number | null;
  traffic_score: number | null;
  traffic_period_month: string | null;
  traffic_details_json: string | null;
  traffic_scanned_at: string | null;
  domain_created_at: string | null;
  domain_expires_at: string | null;
  domain_whois_scanned_at: string | null;
  domain_whois_status: string | null;
  launch_year: number | null;
  ad_days_shown: number | null; ad_first_shown: string | null; ad_last_shown: string | null;
  ads_advertisers_count: number | null;
  ads_advertisers_scanned_at: string | null;
  ads_advertisers_json: string | null;
  sms_country_id?: string | null;
  sms_service_id?: string | null;
  sms_profile_id?: string | null;
  crawled_at: string; updated_at: string;
};
export type TrafficGlobalPoint = {
  period_month: string;
  total_visits_monthly: number;
  avg_visits_monthly?: number;
  unique_visits_monthly: number;
  repeat_visits_monthly: number;
  pages_per_visit: number;
  avg_visit_duration: number;
  bounce_rate_percentage: number;
};
export type TrafficCountryRow = {
  country_code: string;
  country_name: string;
  traffic_share_percentage: number;
  total_visits_monthly: number | null;
  pages_per_visit: number;
  avg_visit_duration: number;
  bounce_rate_percentage: number;
};
export type TrafficSourceBreakdown = {
  period_month: string;
  organic_search: number;
  paid_search: number;
  social: number;
  email: number;
  direct: number;
  referrals: number;
  display_ads: number;
};
export type TrafficSocialPoint = { platform_name: string; share_percentage: number | null };
export type TrafficDetails = {
  global?: TrafficGlobalPoint[];
  country?: TrafficCountryRow[];
  source?: TrafficSourceBreakdown | null;
  social?: TrafficSocialPoint[];
};
export type ProgramList = { items: Program[]; total: number; page: number; page_size: number };
export type ProgramFilter = {
  source?: string;
  category?: string;
  sub_category?: string[];
  field?: string[];
  review_status?: string;
  search?: string;
  min_commission?: number;
  max_commission?: number;
  min_traffic?: number;
  min_cookie_days?: number;
  has_traffic?: boolean;
  traffic_state?: string;   // has | zero | pending
  has_signup?: boolean;
  directory_status?: string;
  networks?: string[];
  approval?: string;
  registrations_open?: boolean;
  payout_currency?: string;
  payout_frequency?: string;
  min_domain_age_years?: number;
  max_domain_age_years?: number;
  whois_state?: string;   // pending | found | not_found
  min_launch_year?: number;   // năm ra mắt sản phẩm >= (affiliate.watch)
  max_launch_year?: number;   // năm ra mắt sản phẩm <=
  domain_age_ranges?: string[];  // mốc tuổi domain "loY:hiY" (chọn nhiều)
  duration_ranges?: string[];    // mốc thời lượng TB "loSec:hiSec" (chọn nhiều)
  dedupe_domain?: boolean;   // gộp trùng: 1 dòng/domain (bản nhiều dữ liệu nhất)
};
const toParams = (q: Record<string, any>): URLSearchParams => {
  const params = new URLSearchParams();
  Object.entries(q).forEach(([k, v]) => {
    if (v === undefined || v === "" || v === null) return;
    if (Array.isArray(v)) v.forEach((x) => params.append(k, String(x)));
    else params.set(k, String(v));
  });
  return params;
};
export const listPrograms = (q: ProgramFilter & { page?: number; page_size?: number; sort_by?: string; order?: "asc" | "desc" } = {}) => {
  const qs = toParams(q).toString();
  return req<ProgramList>(`/programs${qs ? "?" + qs : ""}`);
};
export const getProgram = (id: number) => req<Program>(`/programs/${id}`);
export const updateProgramSmsPreset = (id: number, body: { sms_country_id?: string; sms_service_id?: string; sms_profile_id?: string }) =>
  req<Program>(`/programs/${id}/sms-preset`, { method: "PATCH", body: JSON.stringify(body) });
export const updateProgramNote = (id: number, note: string) =>
  req<Program>(`/programs/${id}/note`, { method: "PATCH", body: JSON.stringify({ note }) });
export const updateProgramReviewStatus = (id: number, review_status: string) =>
  req<Program>(`/programs/${id}/review-status`, { method: "PATCH", body: JSON.stringify({ review_status }) });
export type SmsOption = { id: string; name: string };
export const listSmsCountries = () => req<SmsOption[]>(`/sms/countries`);
export const listSmsServices = () => req<SmsOption[]>(`/sms/services`);
export const listProgramIds = (q: ProgramFilter & { sort_by?: string; order?: "asc" | "desc" } = {}) => {
  const qs = toParams(q).toString();
  return req<number[]>(`/programs/ids${qs ? "?" + qs : ""}`);
};
export type ProgramFacets = {
  networks: string[];
  currencies: string[];
  frequencies: string[];
  statuses: string[];
  approvals: string[];
};
export const listProgramFacets = (source?: string) => {
  const qs = source ? `?source=${encodeURIComponent(source)}` : "";
  return req<ProgramFacets>(`/programs/facets${qs}`);
};
export const deleteProgram = (id: number) => req<{ ok: boolean }>(`/programs/${id}`, { method: "DELETE" });
export const bulkDeletePrograms = (ids: number[]) =>
  req<{ deleted: number }>(`/programs/bulk-delete`, { method: "POST", body: JSON.stringify({ ids }) });
export const listProgramCategories = (source?: string) => {
  const qs = source ? `?source=${encodeURIComponent(source)}` : "";
  return req<string[]>(`/programs/categories${qs}`);
};
export const listProgramSubCategories = (source?: string) => {
  const qs = source ? `?source=${encodeURIComponent(source)}` : "";
  return req<string[]>(`/programs/sub-categories${qs}`);
};
export const listProgramFields = (source?: string) => {
  const qs = source ? `?source=${encodeURIComponent(source)}` : "";
  return req<string[]>(`/programs/fields${qs}`);
};
export const exportProgramsCsvUrl = (q: { source?: string; category?: string; sub_category?: string[]; field?: string[]; review_status?: string; search?: string; min_commission?: number; max_commission?: number; min_traffic?: number; min_cookie_days?: number; has_traffic?: boolean; traffic_state?: string; has_signup?: boolean; networks?: string[]; domain_age_ranges?: string[]; duration_ranges?: string[]; ids?: number[] } = {}) => {
  const params = new URLSearchParams();
  Object.entries(q).forEach(([k, v]) => {
    if (v === undefined || v === "" || v === null) return;
    if (Array.isArray(v)) {
      if (!v.length) return;
      // `ids` → backend nhận CHUỖI "id,id,id" (Optional[str] rồi split). Các mảng khác
      // (sub_category, networks) là List[str] → phải LẶP param (?k=a&k=b), KHÔNG gộp phẩy
      // (gộp phẩy khiến FastAPI coi cả chuỗi là 1 giá trị → lọc trượt → CSV rỗng).
      if (k === "ids") params.set(k, v.join(","));
      else v.forEach((x) => params.append(k, String(x)));
    } else params.set(k, String(v));
  });
  const qs = params.toString();
  return `${BASE}/programs/export.csv${qs ? "?" + qs : ""}`;
};
export type ImportProgramsResult = { saved: number; skipped: number; errors: { row: number; error: string }[] };
export const importProgramsCsv = async (file: File): Promise<ImportProgramsResult> => {
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch(`${BASE}/programs/import.csv`, {
    method: "POST",
    credentials: "include",
    body: fd,
  });
  if (!res.ok) {
    let msg = "Import thất bại";
    try { const j = await res.json(); msg = j.detail || msg; } catch {}
    throw new Error(msg);
  }
  return res.json();
};

// ---- Profiles ----
export type EmailEntry = { label: string; value: string; app_password: string; notes: string; primary: boolean; status: string };
export type PhoneEntry = { label: string; value: string; country: string; sms_provider: string; notes: string; primary: boolean };
export type ProxyEntry = { label: string; url: string; type: string; region: string; notes: string; primary: boolean; status: string };
export type ProfileMeta = {
  id: string; full_name: string; ho: string; ten: string; niche: string[]; country: string; notes: string; updated_at: string;
  tags?: string[];
};
export type Profile = ProfileMeta & {
  password: string; website: string;
  payment: Record<string, any>; created_at: string;
  tags?: string[];
};
export const listProfiles = () => req<ProfileMeta[]>("/profiles");
export const getProfile = (id: string) => req<Profile>(`/profiles/${id}`);
export const createProfile = (data: Partial<Profile> & { id: string }) =>
  req<Profile>("/profiles", { method: "POST", body: JSON.stringify(data) });
export const updateProfile = (id: string, data: Partial<Profile> & { id: string }) =>
  req<Profile>(`/profiles/${id}`, { method: "PUT", body: JSON.stringify(data) });
export const deleteProfile = (id: string) => req<{ ok: boolean }>(`/profiles/${id}`, { method: "DELETE" });
export const duplicateProfile = (id: string) => req<{ id: string }>(`/profiles/${id}/duplicate`, { method: "POST" });

// ---- Instructions ----
export type InstructionMeta = { name: string; size: number; updated_at: string };
export type Instruction = InstructionMeta & { content: string };
export const listInstructions = () => req<InstructionMeta[]>("/instructions");
export const getInstruction = (name: string) => req<Instruction>(`/instructions/${encodeURIComponent(name)}`);
export const createInstruction = (name: string, content: string) =>
  req<Instruction>("/instructions", { method: "POST", body: JSON.stringify({ name, content }) });
export const updateInstruction = (name: string, content: string) =>
  req<Instruction>(`/instructions/${encodeURIComponent(name)}`, { method: "PUT", body: JSON.stringify({ name, content }) });
export const deleteInstruction = (name: string) => req<{ ok: boolean }>(`/instructions/${encodeURIComponent(name)}`, { method: "DELETE" });

// ---- Emails (standalone resource) ----
export type EmailMeta = {
  id: string; address: string; label: string; provider: string;
  has_app_password: boolean; has_totp: boolean;
  recovery_email: string; phone: string; status: string;
  tags: string[]; notes: string;
  last_tested_at: string; last_test_result: string; last_test_error: string;
  updated_at: string;
};
export type EmailItem = EmailMeta & {
  password: string; app_password: string; totp_secret: string;
  otp_link: string; created_at?: string;
};
export const listEmails = () => req<EmailMeta[]>("/emails");
export const getEmail = (id: string) => req<EmailItem>(`/emails/${id}`);
export const createEmail = (data: Partial<EmailItem>) =>
  req<EmailItem>("/emails", { method: "POST", body: JSON.stringify(data) });
export const updateEmail = (id: string, data: Partial<EmailItem>) =>
  req<EmailItem>(`/emails/${id}`, { method: "PUT", body: JSON.stringify(data) });
export const deleteEmail = (id: string) => req<{ ok: boolean }>(`/emails/${id}`, { method: "DELETE" });
export const bulkImportEmails = (raw: string) =>
  req<{ created: number; items: EmailItem[]; skipped: string[] }>(`/emails/bulk-import`, {
    method: "POST", body: JSON.stringify({ raw }),
  });
export const testEmail = (id: string) =>
  req<{ ok: boolean; error: string; elapsed_ms: number; inbox_count: number }>(`/emails/${id}/test`, { method: "POST" });

// ---- SMS OTP provider status ----
export type SmsStatus = {
  provider: string;
  enabled: boolean;
  api_key_masked: string;
  ok: boolean;
  balance: string;
  currency: string;
  error: string;
  default_country: string;
  default_country_name: string;
  default_product: string;
  default_product_name: string;
  default_operator: string;
  timeout_sec: number;
  docs_url: string;
};
export const getSmsStatus = () => req<SmsStatus>("/sms/status");

// ---- SMS Profiles (multi-config sharing 1 API key) ----
export type SmsProfileMeta = {
  id: string; name: string;
  country_id: string; country_name: string;
  service_id: string; service_name: string;
  operator: string; notes: string; tags: string[]; status: string;
  last_tested_at: string; last_test_result: string; last_test_error: string; last_test_phone: string;
  created_at: string; updated_at: string;
};
export type SmsProfileTestOut = {
  ok: boolean; stock?: number; error?: string;
  country_name?: string; service_name?: string;
  balance?: string; currency?: string;
};
export const listSmsProfiles = () => req<SmsProfileMeta[]>("/sms-profiles");
export const getSmsProfile = (id: string) => req<SmsProfileMeta>(`/sms-profiles/${id}`);
export const createSmsProfile = (data: Partial<SmsProfileMeta>) =>
  req<SmsProfileMeta>("/sms-profiles", { method: "POST", body: JSON.stringify(data) });
export const updateSmsProfile = (id: string, data: Partial<SmsProfileMeta>) =>
  req<SmsProfileMeta>(`/sms-profiles/${id}`, { method: "PUT", body: JSON.stringify(data) });
export const deleteSmsProfile = (id: string) => req<{ ok: boolean }>(`/sms-profiles/${id}`, { method: "DELETE" });
export const testSmsProfile = (id: string) =>
  req<SmsProfileTestOut>(`/sms-profiles/${id}/test`, { method: "POST" });
export const checkSmsCombo = (body: { country_id: string; service_id: string }) =>
  req<{ ok: boolean; stock: number; country_name?: string; service_name?: string; error?: string }>(
    `/sms-profiles/check`,
    { method: "POST", body: JSON.stringify(body) },
  );

// ---- Proxies (standalone resource) ----
export type ProxyMeta = {
  id: string; label: string; host: string; port: number; type: string;
  country: string; provider: string; username: string; has_password: boolean;
  url: string; status: string; last_tested_at: string; last_test_result: string;
  last_test_ip: string; tags: string[]; notes: string; updated_at: string;
};
export type ProxyItem = ProxyMeta & { password: string; created_at?: string };
export const listProxies = () => req<ProxyMeta[]>("/proxies");
export const getProxy = (id: string) => req<ProxyItem>(`/proxies/${id}`);
export const createProxy = (data: Partial<ProxyItem>) =>
  req<ProxyItem>("/proxies", { method: "POST", body: JSON.stringify(data) });
export const updateProxy = (id: string, data: Partial<ProxyItem>) =>
  req<ProxyItem>(`/proxies/${id}`, { method: "PUT", body: JSON.stringify(data) });
export const deleteProxy = (id: string) => req<{ ok: boolean }>(`/proxies/${id}`, { method: "DELETE" });
export const bulkImportProxies = (raw: string, default_type = "http") =>
  req<{ created: number; items: ProxyItem[]; skipped: string[] }>(`/proxies/bulk-import`, {
    method: "POST", body: JSON.stringify({ raw, default_type }),
  });
export const testProxy = (id: string) =>
  req<{ ok: boolean; ip: string; error: string; elapsed_ms: number }>(`/proxies/${id}/test`, { method: "POST" });

// ---- Shortlists ----
export type Weights = { traffic: number; commission: number; cookie: number };
export type Thresholds = { min_traffic: number; min_commission: number; min_cookie_days: number };
export type Criteria = {
  weights: Weights;
  thresholds: Thresholds;
  sources: string[];
  categories: string[];
  search: string;
  missing_traffic_policy: "zero" | "ignore" | "include";
};
export const DEFAULT_CRITERIA: Criteria = {
  weights: { traffic: 0.4, commission: 0.3, cookie: 0.3 },
  thresholds: { min_traffic: 300000, min_commission: 15, min_cookie_days: 30 },
  sources: [], categories: [], search: "",
  missing_traffic_policy: "zero",
};
export type Shortlist = {
  id: number; name: string; description: string | null;
  criteria: Criteria; item_count: number;
  created_at: string; updated_at: string;
};
export type ShortlistItem = {
  id: number; program_id: number; added_manually: boolean;
  score: number | null; note: string | null; added_at: string;
  program: Program | null;
};
export type ScoredProgram = { program: Program; score: number; breakdown: { traffic: number; commission: number; cookie: number } };

export const listShortlists = () => req<Shortlist[]>("/shortlists");
export const getShortlist = (id: number) => req<Shortlist>(`/shortlists/${id}`);
export const createShortlist = (body: { name: string; description?: string; criteria: Criteria }) =>
  req<Shortlist>("/shortlists", { method: "POST", body: JSON.stringify(body) });
export const updateShortlist = (id: number, body: { name?: string; description?: string; criteria?: Criteria }) =>
  req<Shortlist>(`/shortlists/${id}`, { method: "PUT", body: JSON.stringify(body) });
export const deleteShortlist = (id: number) => req<void>(`/shortlists/${id}`, { method: "DELETE" });

export const previewCriteria = (criteria: Criteria, limit = 100) =>
  req<{ items: ScoredProgram[]; total: number }>(`/shortlists/preview?limit=${limit}`, {
    method: "POST", body: JSON.stringify(criteria),
  });
export const previewShortlist = (id: number, limit = 100) =>
  req<{ items: ScoredProgram[]; total: number }>(`/shortlists/${id}/preview?limit=${limit}`, { method: "POST" });

export const getShortlistItems = (id: number) => req<ShortlistItem[]>(`/shortlists/${id}/items`);
export const addShortlistItem = (id: number, program_id: number, note = "") =>
  req<ShortlistItem>(`/shortlists/${id}/items`, { method: "POST", body: JSON.stringify({ program_id, note }) });
export const removeShortlistItem = (id: number, program_id: number) =>
  req<void>(`/shortlists/${id}/items/${program_id}`, { method: "DELETE" });
export const autoFillShortlist = (id: number, limit = 50, replace = false) =>
  req<{ added: number }>(`/shortlists/${id}/auto-fill`, { method: "POST", body: JSON.stringify({ limit, replace }) });
export const updateProgramTraffic = (program_id: number, traffic_score: number) =>
  req<{ id: number; traffic_score: number }>(`/shortlists/programs/${program_id}/traffic`, {
    method: "PATCH", body: JSON.stringify({ traffic_score }),
  });
export const scanProgramTraffic = (program_id: number) =>
  req<{ program_id: number; url: string; domain: string; monthly_visits: number; period_month: string; found: boolean; traffic_score: number; has_details: boolean }>(
    `/programs/${program_id}/scan-traffic`, { method: "POST" }
  );

export const scanProgramWhois = (program_id: number) =>
  req<{ program_id: number; domain: string; created: string | null; expires: string | null; found: boolean }>(
    `/programs/${program_id}/scan-whois`, { method: "POST" }
  );
export const createWhoisScanJob = (ids: number[], skip_existing = true) =>
  req<TrafficScanJob>(`/programs/bulk-scan-whois-job`, {
    method: "POST", body: JSON.stringify({ ids, skip_existing }),
  });

export type BulkScanTrafficResult = {
  total: number;
  matched: number;
  scanned: number;
  found: number;
  skipped: number;
  failed: number;
  items: Array<{
    program_id: number;
    name: string;
    status: "ok" | "empty" | "skipped" | "failed";
    monthly_visits?: number;
    period_month?: string;
    traffic_score?: number;
    error?: string;
  }>;
};
export const bulkScanProgramTraffic = (
  ids: number[],
  skip_existing = true,
  months = 3,
  concurrency = 2,
) =>
  req<BulkScanTrafficResult>(`/programs/bulk-scan-traffic`, {
    method: "POST",
    body: JSON.stringify({ ids, skip_existing, months, concurrency }),
  });

export type TrafficScanJob = {
  id: number;
  kind?: "traffic" | "whois" | "advertisers";
  status: "pending" | "running" | "success" | "failed";
  total: number;
  scanned: number;
  found: number;
  skipped: number;
  failed: number;
  months: number;
  concurrency: number;
  skip_existing: boolean;
  start_date?: string | null;
  end_date?: string | null;
  program_ids: number[];
  results: Array<{
    program_id: number;
    name: string;
    status: "ok" | "empty" | "skipped" | "failed";
    monthly_visits?: number;
    period_month?: string;
    created?: string | null;
    expires?: string | null;
    count?: number;         // kind="advertisers": số NQC
    has_more?: boolean;
    error?: string;
  }>;
  error?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  created_at?: string | null;
};

export const createTrafficScanJob = (
  ids: number[],
  skip_existing = true,
  months = 3,
  concurrency = 2,
) =>
  req<TrafficScanJob>(`/programs/bulk-scan-traffic-job`, {
    method: "POST",
    body: JSON.stringify({ ids, skip_existing, months, concurrency }),
  });

export const getTrafficScanJob = (job_id: number) =>
  req<TrafficScanJob>(`/programs/traffic-jobs/${job_id}`);

// Đếm số nhà quảng cáo (Google Ads) cho domain, theo khung ngày (YYYYMMDD). Dùng chung
// getTrafficScanJob để poll tiến độ.
export const createAdvertisersScanJob = (
  ids: number[],
  opts: { start_date?: string; end_date?: string; skip_existing?: boolean } = {},
) =>
  req<TrafficScanJob>(`/programs/bulk-scan-advertisers-job`, {
    method: "POST",
    body: JSON.stringify({
      ids,
      start_date: opts.start_date || "",
      end_date: opts.end_date || "",
      skip_existing: opts.skip_existing ?? false,
    }),
  });

// ---- SimilarWeb manual login (noVNC) ----
export type SimilarwebStatus = { logged_in: boolean; login_running: boolean; error: string | null; novnc_url: string };
export const getSimilarwebStatus = () =>
  req<SimilarwebStatus>(`/programs/similarweb/status`);
export const similarwebLogin = () =>
  req<{ status: string; novnc_url: string; message: string }>(`/programs/similarweb/login`, { method: "POST" });

// ---- Signup (auto-register) ----
export type SignupAttempt = {
  program_id: number;
  profile_id: string | null;
  status: string;
  message?: string;
  steps?: number;
  final_url?: string;
  screenshot?: string;
  duration_sec?: number;
  started_at?: string;
  finished_at?: string;
};
export type SignupJob = {
  id: number;
  user_id: number | null;
  program_ids: number[];
  profile_ids: string[];
  email_ids: string[];
  proxy_ids: string[];
  instruction_names: string[];
  instruction_name: string | null;
  extra_prompt: string | null;
  headless: boolean;
  status: string;
  total: number;
  succeeded: number;
  failed: number;
  results: SignupAttempt[];
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string | null;
  sms_profile_id?: string | null;
  gemini_key_index?: number | null;
  llm_provider?: string | null;
  llm_key_index?: number | null;
  run_mode?: string | null;
  playbook_id?: number | null;
  batch_id?: string | null;
  tier3_behavior?: string | null;
  tier3_status?: string | null;
  script_overrides?: Record<string, number> | null;
};
export const createSignupJob = (data: {
  program_ids: number[]; profile_ids: string[];
  email_ids?: string[]; proxy_ids?: string[]; instruction_names?: string[];
  instruction_name?: string; extra_prompt?: string; headless?: boolean;
  sms_profile_id?: string;
  llm_provider?: string;
  llm_key_index?: number;
  run_mode?: string;
  playbook_id?: number;
  tier3_behavior?: string;
  script_overrides?: Record<string, number>;
}) => req<SignupJob>("/signup/jobs", { method: "POST", body: JSON.stringify(data) });
export const createSignupJobBatch = async (data: {
  program_ids_list: number[][];
  profile_ids: string[];
  email_ids?: string[]; proxy_ids?: string[]; instruction_names?: string[];
  instruction_name?: string; extra_prompt?: string; headless?: boolean;
  sms_profile_id?: string;
  llm_provider?: string;
  llm_key_index?: number;
}): Promise<SignupJob[]> => {
  const batchId = crypto.randomUUID();
  const { program_ids_list, ...rest } = data;
  return Promise.all(
    program_ids_list.map((ids) =>
      req<SignupJob>("/signup/jobs", { method: "POST", body: JSON.stringify({ ...rest, program_ids: ids, batch_id: batchId }) })
    )
  );
};
export const listSignupJobs = (params?: { run_mode?: string }) => {
  const qs = params?.run_mode ? `?run_mode=${encodeURIComponent(params.run_mode)}` : "";
  return req<SignupJob[]>(`/signup/jobs${qs}`);
};
export const getSignupJob = (id: number) => req<SignupJob>(`/signup/jobs/${id}`);
export const cancelSignupJob = (id: number) => req<SignupJob>(`/signup/jobs/${id}/cancel`, { method: "PATCH" });
export const approveTier3 = (id: number) => req<SignupJob>(`/signup/jobs/${id}/approve-tier3`, { method: "POST" });
export const skipTier3 = (id: number) => req<SignupJob>(`/signup/jobs/${id}/skip-tier3`, { method: "POST" });

// ---- Signup History ----
export type ProfileSnapshot = {
  full_name?: string;
  ho?: string;
  ten?: string;
  email?: string;
  phone?: string;
  website?: string;
  country?: string;
  niche?: string[];
  company?: string;
  password?: string;
  notes?: string;
};
export type SignupHistoryItem = {
  job_id: number;
  job_created_at: string | null;
  program_id: number | null;
  program_name: string | null;
  program_url: string | null;
  program_signup_url: string | null;
  program_commission: string | null;
  program_source: string | null;
  program_logo_url: string | null;
  profile_id: string | null;
  profile_name: string | null;
  profile_email: string | null;
  profile_website: string | null;
  profile_niche: string[] | null;
  // Actual runtime data
  email_used: string | null;
  proxy_label: string | null;
  proxy_host: string | null;
  proxy_url_used: string | null;
  instruction_names_used: string[] | null;
  profile_snapshot: ProfileSnapshot | null;
  status: string;
  message: string | null;
  screenshot: string | null;
  final_url: string | null;
  duration_sec: number | null;
  steps: number | null;
  started_at: string | null;
  finished_at: string | null;
};
export type SignupHistoryStats = {
  total: number;
  success: number;
  failed: number;
  captcha: number;
  pending_verify: number;
  by_status: Record<string, number>;
  by_source: Record<string, number>;
  by_day: Array<{ date: string; success: number; failed: number; total: number }>;
};
export type SignupHistoryResponse = {
  stats: SignupHistoryStats;
  items: SignupHistoryItem[];
  total_count: number;
  page: number;
  limit: number;
};
export const getSignupHistory = (params?: {
  status?: string;
  search?: string;
  date_from?: string;
  date_to?: string;
  page?: number;
  limit?: number;
}) => {
  const qs = new URLSearchParams();
  if (params?.status) qs.set("status", params.status);
  if (params?.search) qs.set("search", params.search);
  if (params?.date_from) qs.set("date_from", params.date_from);
  if (params?.date_to) qs.set("date_to", params.date_to);
  if (params?.page != null) qs.set("page", String(params.page));
  if (params?.limit != null) qs.set("limit", String(params.limit));
  return req<SignupHistoryResponse>(`/signup/history?${qs.toString()}`);
};
export const exportSignupHistoryUrl = (params?: {
  status?: string;
  search?: string;
  date_from?: string;
  date_to?: string;
  job_ids?: string;
}) => {
  const qs = new URLSearchParams();
  if (params?.status) qs.set("status", params.status);
  if (params?.search) qs.set("search", params.search);
  if (params?.date_from) qs.set("date_from", params.date_from);
  if (params?.date_to) qs.set("date_to", params.date_to);
  if (params?.job_ids) qs.set("job_ids", params.job_ids);
  return `${process.env.NEXT_PUBLIC_API_URL || ""}/api/signup/history/export?${qs.toString()}`;
};


export type SystemSubsystem = {
  key: string;
  label: string;
  enabled: boolean;
  value: string;
  required: boolean;
  note: string;
};
export type SystemStatus = {
  app: string;
  ready: boolean;
  fully_configured: boolean;
  subsystems: SystemSubsystem[];
  missing_required: string[];
  missing_optional: string[];
  signup_max_steps: number;
};
export const getSystemStatus = () => req<SystemStatus>("/system/status");

// ---- LLM API keys (Gemini / OpenAI / DeepSeek) ----
export type LlmProvider = "gemini" | "openai" | "deepseek";

export type LlmKeyMeta = { index: number; masked: string; source: string };
export type LlmKeyList = { provider: string; items: LlmKeyMeta[]; total: number };
export type LlmKeyTestOut = {
  ok: boolean;
  provider: string;
  model?: string;
  key_index: number;
  masked: string;
  error?: string;
  elapsed_ms: number;
};

export const listLlmKeys = (provider: LlmProvider) =>
  req<LlmKeyList>(`/system/llm-keys/${provider}`);
export const createLlmKey = (provider: LlmProvider, key: string) =>
  req<LlmKeyList>(`/system/llm-keys/${provider}`, { method: "POST", body: JSON.stringify({ key }) });
export const deleteLlmKey = (provider: LlmProvider, index: number) =>
  req<LlmKeyList>(`/system/llm-keys/${provider}/${index}`, { method: "DELETE" });
export const testLlmKey = (provider: LlmProvider, index: number) =>
  req<LlmKeyTestOut>(`/system/llm-keys/${provider}/${index}/test`, { method: "POST" });

// Backward-compat aliases (gemini)
export type GeminiKeyMeta = LlmKeyMeta;
export type GeminiKeyList = LlmKeyList;
export type GeminiKeyTestOut = LlmKeyTestOut;
export const listGeminiKeys = () => listLlmKeys("gemini");
export const createGeminiKey = (key: string) => createLlmKey("gemini", key);
export const deleteGeminiKey = (index: number) => deleteLlmKey("gemini", index);
export const testGeminiKey = (index: number) => testLlmKey("gemini", index);

// ---- Connection tests (real ping) ----
export type ConnTestResult = {
  ok: boolean;
  error?: string;
  elapsed_ms?: number;
  // LLM
  provider?: string;
  model?: string;
  // CapSolver / SMS
  balance?: string;
  currency?: string;
  // IMAP
  email?: string;
  inbox_count?: number;
  // Proxy
  ip?: string;
  proxy_name?: string;
};
export type SystemTestAllOut = {
  results: {
    llm: ConnTestResult;
    capsolver: ConnTestResult;
    sms: ConnTestResult;
    imap: ConnTestResult;
    proxy: ConnTestResult;
  };
  total_ms: number;
};
export const testAllConnections = () =>
  req<SystemTestAllOut>("/system/test-all", { method: "POST" });
export type SystemTestOneOut = { key: string; result: ConnTestResult };
export const testOneConnection = (key: "llm" | "capsolver" | "sms" | "imap" | "proxy") =>
  req<SystemTestOneOut>(`/system/test-one?key=${encodeURIComponent(key)}`, { method: "POST" });

// ---- Google Ads Transparency Center (SerpAPI) ----
export type AdCreative = {
  advertiser_id: string;
  advertiser?: string;
  ad_creative_id: string;
  format?: string;             // text | image | video
  link?: string;
  target_domain?: string;
  image?: string;
  total_days_shown?: number;
  first_shown?: number;        // unix ts
  last_shown?: number;         // unix ts
  details_link?: string;
  serpapi_details_link?: string;
  [k: string]: any;
};
export type AdsSearchResult = {
  search_metadata?: any;
  search_parameters?: any;
  search_information?: { total_results?: number };
  ad_creatives?: AdCreative[];
  pagination?: { next?: string; next_page_token?: string };
  serpapi_pagination?: { next_page_token?: string };
  error?: string;
};
export type AdsSearchRequest = {
  text?: string;
  advertiser_id?: string;
  platform?: string;
  creative_format?: string;
  start_date?: string;
  end_date?: string;
  region?: string;
  political_ads?: boolean;
  num?: number;
  next_page_token?: string;
};
export const searchAdsTransparency = (body: AdsSearchRequest) =>
  req<AdsSearchResult>("/ads-transparency/search", { method: "POST", body: JSON.stringify(body) });
export const getAdDetails = (advertiser_id: string, creative_id: string, region = "") =>
  req<any>("/ads-transparency/ad-details", {
    method: "POST",
    body: JSON.stringify({ advertiser_id, creative_id, region }),
  });
export const exportAdsToDiscovery = (
  advertiser_id: string,
  advertiser_name: string,
  creatives: AdCreative[],
  opts: { start_date?: string; end_date?: string; all_time?: boolean } = {},
) =>
  req<{ started: number; all_time?: boolean }>("/ads-transparency/export-to-discovery", {
    method: "POST",
    body: JSON.stringify({
      advertiser_id, advertiser_name, creatives,
      start_date: opts.start_date || "", end_date: opts.end_date || "", all_time: !!opts.all_time,
    }),
  });

export type AdsHistoryItem = {
  id: number;
  text: string;
  advertiser_id: string;
  platform: string;
  creative_format: string;
  region: string;
  start_date: string;
  end_date: string;
  num: number;
  political_ads: boolean;
  result_count: number;
  results_json: string | null;
  created_at: string | null;
};
export const listAdsHistory = (limit = 30) =>
  req<AdsHistoryItem[]>(`/ads-transparency/history?limit=${limit}`);
export const deleteAdsHistory = (id: number) =>
  req<{ ok: boolean }>(`/ads-transparency/history/${id}`, { method: "DELETE" });
export const clearAdsHistory = () =>
  req<{ ok: boolean }>(`/ads-transparency/history`, { method: "DELETE" });

// ── Lovable homepage management ──────────────────────────────────────────────
export const discoverHomepages = (body: {
  source?: string;
  limit?: number;
  concurrency?: number;
  method?: string;
}) =>
  req<{ total: number; updated: number; failed: number }>(
    "/programs/discover-homepages",
    { method: "POST", body: JSON.stringify(body) }
  );

export const discoverProgramHomepage = (id: number, method = "auto") =>
  req<{ id: number; url: string | null; found: boolean }>(
    `/programs/${id}/discover-homepage?method=${method}`,
    { method: "POST" }
  );

export const updateProgramUrl = (id: number, url: string) =>
  req<{ id: number; url: string }>(
    `/programs/${id}/url`,
    { method: "PATCH", body: JSON.stringify({ url }) }
  );

// ── Experiment (Browser Agent) ────────────────────────────────────────────────
export type ExperimentJob = {
  id: number;
  task: string;
  model: string;
  max_steps: number;
  status: "running" | "done" | "error" | "cancelled";
  steps: number;
  result: string | null;
  screenshot: string | null;
  duration_sec: number | null;
  created_at: string;
};

export const runExperiment = (task: string, model: string, max_steps: number) =>
  req<{ job_id: number }>("/experiment/run", {
    method: "POST",
    body: JSON.stringify({ task, model, max_steps }),
  });

export const getExperimentJob = (id: number) =>
  req<ExperimentJob>(`/experiment/jobs/${id}`);

export const listExperimentJobs = (limit = 50) =>
  req<ExperimentJob[]>(`/experiment/jobs?limit=${limit}`);

export const cancelExperimentJob = (id: number) =>
  req<{ ok: boolean }>(`/experiment/jobs/${id}/cancel`, { method: "POST" });

// ---- Captcha Memory ----
export type CaptchaMemory = {
  version: number;
  user_notes: string;
  sites: Record<string, { type: string; sitekey?: string; tips?: string[]; successes?: number; last_success?: string }>;
};
export const getCaptchaMemory = () => req<CaptchaMemory>("/captcha-memory");
export const putCaptchaMemory = (body: CaptchaMemory) =>
  req<CaptchaMemory>("/captcha-memory", { method: "PUT", body: JSON.stringify(body) });

// ---- Playbooks ----
export type PlaybookStep = Record<string, any>;
export type PlaybookOut = {
  id: number;
  name: string;
  platform: string | null;
  category: string | null;
  program_id: number | null;
  program_name: string | null;
  signup_url: string | null;
  status: string; // "active" | "needs_llm" | "archived"
  pending_llm_approval: boolean;
  pending_review: boolean;
  source: string; // "manual" | "ai_generated" | "ai_updated"
  version: number;
  steps: PlaybookStep[];
  consecutive_fails: number;
  total_runs: number;
  success_runs: number;
  fail_runs: number;
  success_rate: number;
  created_from_job_id: number | null;
  last_used_at: string | null;
  created_at: string;
  updated_at: string;
};

export type PlaybookStatusEntry = {
  playbook_id: number;
  name: string;
  status: string;
  pending_llm_approval: boolean;
  pending_review: boolean;
  success_rate: number | null;
  consecutive_fails: number;
  match_type: "exact" | "platform_category" | "platform";
  is_fallback: boolean;
};
export type PlaybookStatusMap = Record<number, PlaybookStatusEntry>;

export const createPlaybook = (body: {
  name: string;
  platform?: string;
  category?: string;
  program_id?: number;
  program_name?: string;
  signup_url?: string;
  steps?: PlaybookStep[];
}) => req<PlaybookOut>("/playbooks", { method: "POST", body: JSON.stringify(body) });

export const listPlaybooks = (params?: { status?: string; platform?: string }) => {
  const qs = new URLSearchParams();
  if (params?.status) qs.set("status", params.status);
  if (params?.platform) qs.set("platform", params.platform);
  const q = qs.toString();
  return req<PlaybookOut[]>(`/playbooks${q ? "?" + q : ""}`);
};

export const getPlaybook = (id: number) => req<PlaybookOut>(`/playbooks/${id}`);

export const getPlaybookForProgram = (programId: number) =>
  req<PlaybookOut | null>(`/playbooks/for-program/${programId}`);

export const updatePlaybook = (id: number, body: { name?: string; steps?: PlaybookStep[] }) =>
  req<PlaybookOut>(`/playbooks/${id}`, { method: "PATCH", body: JSON.stringify(body) });

export const archivePlaybook = (id: number) =>
  req<PlaybookOut>(`/playbooks/${id}/archive`, { method: "POST" });

export const restorePlaybook = (id: number) =>
  req<PlaybookOut>(`/playbooks/${id}/restore`, { method: "POST" });

export const approvePlaybookLlm = (id: number) =>
  req<{ ok: boolean; message: string; rerecord_job_id: number; playbook: PlaybookOut }>(
    `/playbooks/${id}/approve-llm`, { method: "POST" }
  );

export const batchPlaybookStatus = (programIds: number[]) =>
  req<PlaybookStatusMap>("/playbooks/batch-status", {
    method: "POST",
    body: JSON.stringify(programIds),
  });

export const listPendingPlaybooks = () =>
  req<PlaybookOut[]>("/playbooks/pending");

export const listPlaybooksByPlatform = (platform: string) =>
  req<PlaybookOut[]>(`/playbooks/by-platform?platform=${encodeURIComponent(platform)}`);

export const approveReviewPlaybook = (id: number) =>
  req<PlaybookOut>(`/playbooks/${id}/approve-review`, { method: "POST" });

export const rejectReviewPlaybook = (id: number) =>
  req<PlaybookOut>(`/playbooks/${id}/reject-review`, { method: "POST" });

// ---- Q&A Library ----
export type QAEntryOut = {
  id: number;
  platform: string;
  category: string | null;
  program_id: number | null;
  question_pattern: string;
  answer_template: string;
  answer_type: string; // "profile_field" | "static" | "template" | "select_option"
  required: boolean;
  confidence: number;
  usage_count: number;
  success_count: number;
  failure_count: number;
  consecutive_failures: number;
  status: string; // "active" | "needs_review" | "archived"
  source: string; // "manual" | "ai_generated" | "seed"
  note: string | null;
  created_at: string;
  updated_at: string;
};
export type QAStatsOut = {
  platform: string;
  total: number;
  active: number;
  needs_review: number;
  archived: number;
};

export const listQA = (params?: { platform?: string; category?: string; status?: string; search?: string }) => {
  const qs = new URLSearchParams();
  if (params?.platform) qs.set("platform", params.platform);
  if (params?.category) qs.set("category", params.category);
  if (params?.status) qs.set("status", params.status);
  if (params?.search) qs.set("search", params.search);
  const q = qs.toString();
  return req<QAEntryOut[]>(`/qa-library${q ? "?" + q : ""}`);
};
export const getQAStats = () => req<QAStatsOut[]>("/qa-library/stats");
export const createQA = (body: {
  platform: string;
  category?: string;
  program_id?: number;
  question_pattern: string;
  answer_template: string;
  answer_type?: string;
  required?: boolean;
  note?: string;
}) => req<QAEntryOut>("/qa-library", { method: "POST", body: JSON.stringify(body) });
export const updateQA = (id: number, body: {
  answer_template?: string;
  category?: string;
  note?: string;
  status?: string;
  confidence?: number;
}) => req<QAEntryOut>(`/qa-library/${id}`, { method: "PATCH", body: JSON.stringify(body) });
export const archiveQA = (id: number) =>
  req<void>(`/qa-library/${id}`, { method: "DELETE" });
export const restoreQA = (id: number) =>
  req<QAEntryOut>(`/qa-library/${id}/restore`, { method: "POST" });
export const seedQA = (force = false) =>
  req<{ ok: boolean; seeded: number }>(`/qa-library/seed${force ? "?force=true" : ""}`, { method: "POST" });

// ---- Discovery ----
export type DiscoverySource = {
  id: number; url: string; name: string | null; category: string | null; field: string | null; status: string;
  is_crawling: boolean;
  last_crawled_at: string | null; total_candidates_found: number; created_at: string;
};
export type DiscoveryCandidate = {
  id: number; source_id: number; raw_url: string; domain: string;
  level: number; depth: number; is_primary: boolean;
  detection_method: string | null; suggested_name: string | null;
  source_page_url: string | null; source_page_title: string | null;
  homepage_url: string | null; is_redirect_resolved: boolean;
  traffic_monthly: number | null; traffic_status: string | null;
  affiliate_url: string | null; affiliate_detection_method: string | null;
  affiliate_url_status: string | null;
  ad_days_shown: number | null; ad_first_shown: string | null; ad_last_shown: string | null;
  status: string; promoted_program_id: number | null;
  created_at: string; updated_at: string;
};
export type DiscoveryBlacklist = { id: number; domain: string; category: string | null; created_at: string };
export type DiscoverySummary = {
  discovered: number; homepage_resolved: number; traffic_scanned: number;
  affiliate_found: number; promoted: number; no_affiliate_found: number; total: number;
};

/** Chủ sở hữu dữ liệu discovery — admin dùng cho tab "Dự án user khác". */
export type DiscoveryOwner = { id: number; email: string; source_count: number; candidate_count: number };
export const listDiscoveryOwners = () => req<DiscoveryOwner[]>("/discovery/owners");

/** owner_id: chỉ admin mới truyền được — xem dữ liệu của user khác (chỉ xem). */
export const listDiscoverySources = (owner_id?: number) =>
  req<DiscoverySource[]>(`/discovery/sources${owner_id != null ? `?owner_id=${owner_id}` : ""}`);
export const createDiscoverySource = (url: string, name?: string, category?: string, field?: string) =>
  req<DiscoverySource>("/discovery/sources", { method: "POST", body: JSON.stringify({ url, name, category, field }) });
export type ImportDomainsResult = {
  source_id: number; name: string; category: string | null;
  total_parsed: number; created: number; skipped_duplicate: number; skipped_invalid: number;
};
export const importDiscoveryDomains = async (opts: {
  name: string; category?: string; field?: string; domains?: string; file?: File | null;
}): Promise<ImportDomainsResult> => {
  const fd = new FormData();
  fd.append("name", opts.name);
  fd.append("category", opts.category || "");
  fd.append("field", opts.field || "");
  fd.append("domains", opts.domains || "");
  if (opts.file) fd.append("file", opts.file);
  const res = await fetch(`${BASE}/discovery/import-domains`, { method: "POST", credentials: "include", body: fd });
  if (!res.ok) {
    let msg = "Nhập domain thất bại";
    try { const j = await res.json(); msg = j.detail || msg; } catch {}
    throw new Error(msg);
  }
  return res.json();
};
export const updateDiscoverySource = (id: number, patch: { name?: string; category?: string }) =>
  req<DiscoverySource>(`/discovery/sources/${id}`, { method: "PATCH", body: JSON.stringify(patch) });
export const deleteDiscoverySource = (id: number) =>
  req<void>(`/discovery/sources/${id}`, { method: "DELETE" });
export const crawlDiscoverySource = (
  id: number,
  opts?: { proxy_ids?: string[]; max_pages?: number; incremental?: boolean },
) =>
  req<{ ok: boolean; message: string }>(`/discovery/sources/${id}/crawl`, {
    method: "POST",
    body: JSON.stringify(opts || {}),
  });
export const stopDiscoverySource = (id: number) =>
  req<{ ok: boolean; message: string }>(`/discovery/sources/${id}/stop`, { method: "POST" });

export const listDiscoveryCandidates = (params?: {
  source_id?: number; status?: string; is_primary?: boolean;
  detection_method?: string; min_traffic?: number;
  traffic_state?: string; affiliate_state?: string; q?: string;
  owner_id?: number;
  page?: number; page_size?: number;
}) => {
  const qs = new URLSearchParams();
  if (params?.owner_id != null) qs.set("owner_id", String(params.owner_id));
  if (params?.source_id != null) qs.set("source_id", String(params.source_id));
  if (params?.status) qs.set("status", params.status);
  if (params?.is_primary != null) qs.set("is_primary", String(params.is_primary));
  if (params?.detection_method) qs.set("detection_method", params.detection_method);
  if (params?.min_traffic != null) qs.set("min_traffic", String(params.min_traffic));
  if (params?.traffic_state) qs.set("traffic_state", params.traffic_state);
  if (params?.affiliate_state) qs.set("affiliate_state", params.affiliate_state);
  if (params?.q) qs.set("q", params.q);
  if (params?.page) qs.set("page", String(params.page));
  if (params?.page_size) qs.set("page_size", String(params.page_size));
  return req<DiscoveryCandidatePage>(`/discovery/candidates?${qs.toString()}`);
};
export type DiscoveryCandidatePage = {
  items: DiscoveryCandidate[]; total: number; page: number; page_size: number;
};
export const getDiscoverySummary = (source_id?: number, owner_id?: number) => {
  const p = new URLSearchParams();
  if (source_id) p.set("source_id", String(source_id));
  if (owner_id != null) p.set("owner_id", String(owner_id));
  const qs = p.toString();
  return req<DiscoverySummary>(`/discovery/candidates/summary${qs ? "?" + qs : ""}`);
};
export const resolveHomepage = (id: number) =>
  req<{ ok: boolean }>(`/discovery/candidates/${id}/resolve-homepage`, { method: "POST" });
export const scanCandidateTraffic = (id: number) =>
  req<{ ok: boolean; monthly_visits?: number; error?: string }>(`/discovery/candidates/${id}/scan-traffic`, { method: "POST" });
export const detectAffiliate = (id: number) =>
  req<{ ok: boolean; status: string; affiliate_url: string | null }>(`/discovery/candidates/${id}/detect-affiliate`, { method: "POST" });
export const runDiscoveryPipeline = (candidate_ids: number[], proxy_ids?: string[]) =>
  req<{ started: number }>(`/discovery/candidates/run-pipeline`, {
    method: "POST", body: JSON.stringify({ candidate_ids, proxy_ids }),
  });
export const scanDiscoveryTrafficBatch = (candidate_ids: number[]) =>
  req<{ started: number }>(`/discovery/candidates/scan-traffic-batch`, {
    method: "POST", body: JSON.stringify({ candidate_ids }),
  });
export const detectAffiliateBatch = (candidate_ids: number[], proxy_ids?: string[]) =>
  req<{ started: number }>(`/discovery/candidates/detect-affiliate-batch`, {
    method: "POST", body: JSON.stringify({ candidate_ids, proxy_ids }),
  });
export const checkAffiliateLinkBatch = (candidate_ids: number[], proxy_ids?: string[]) =>
  req<{ started: number }>(`/discovery/candidates/check-affiliate-link-batch`, {
    method: "POST", body: JSON.stringify({ candidate_ids, proxy_ids }),
  });
export const detectAffiliateSearchBatch = (candidate_ids: number[], proxy_ids?: string[]) =>
  req<{ started: number }>(`/discovery/candidates/detect-affiliate-search-batch`, {
    method: "POST", body: JSON.stringify({ candidate_ids, proxy_ids }),
  });

export const exportDiscoveryCandidatesCsvUrl = (ids: number[], owner_id?: number) => {
  const p = new URLSearchParams();
  if (ids.length) p.set("ids", ids.join(","));
  if (owner_id != null) p.set("owner_id", String(owner_id));
  const qs = p.toString();
  return `${BASE}/discovery/candidates/export.csv${qs ? "?" + qs : ""}`;
};
export const exportDiscoveryCandidatesByFilterCsvUrl = (f: {
  source_id?: number; status?: string; detection_method?: string;
  traffic_state?: string; affiliate_state?: string; min_traffic?: number; q?: string;
  owner_id?: number;
}) => {
  const p = new URLSearchParams();
  if (f.owner_id != null) p.set("owner_id", String(f.owner_id));
  if (f.source_id != null) p.set("source_id", String(f.source_id));
  if (f.status) p.set("status", f.status);
  if (f.detection_method) p.set("detection_method", f.detection_method);
  if (f.traffic_state) p.set("traffic_state", f.traffic_state);
  if (f.affiliate_state) p.set("affiliate_state", f.affiliate_state);
  if (f.min_traffic != null) p.set("min_traffic", String(f.min_traffic));
  if (f.q) p.set("q", f.q);
  const qs = p.toString();
  return `${BASE}/discovery/candidates/export.csv${qs ? "?" + qs : ""}`;
};
export const listDiscoveryBlacklist = () => req<DiscoveryBlacklist[]>("/discovery/blacklist");
export const addDiscoveryBlacklist = (domain: string, category = "custom") =>
  req<DiscoveryBlacklist>("/discovery/blacklist", { method: "POST", body: JSON.stringify({ domain, category }) });
export const deleteDiscoveryBlacklist = (id: number) =>
  req<void>(`/discovery/blacklist/${id}`, { method: "DELETE" });
