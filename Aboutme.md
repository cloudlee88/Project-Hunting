# Affiliate Hub — Product Overview

> **Internal document — MIC ACE**  
> Phiên bản: 2.1 · Cập nhật: 2026-07-03  
> Mục tiêu: Tài liệu tham chiếu kỹ thuật cho team hiểu toàn bộ hệ thống trong 15 phút.

---

## 1. Tổng quan

**Affiliate Hub** là nền tảng nội bộ tự động hoá toàn bộ quy trình gia nhập mạng lưới affiliate — từ tìm kiếm, đánh giá đến đăng ký hàng loạt.

| Bước thủ công trước đây | Giải pháp |
|------------------------|-----------|
| Tìm chương trình trên nhiều site | Crawl tự động từ 5 nguồn → 1 DB duy nhất (~1,346 programs) |
| Tìm brand ngoài directory | Module **Discovery**: thả URL site review / marketplace bất kỳ → tự bóc brand → resolve → traffic → affiliate → thêm vào DB |
| Tra traffic thủ công | Quét SimilarWeb hàng loạt, lưu lịch sử |
| Chọn program tốt mất thời gian | Shortlist tự động theo điểm (commission + traffic + cookie) |
| Đăng ký từng site, giải captcha tay | 3 chế độ signup: Script (free) → Script+AI (hybrid) → Full AI |
| Trả lời form đăng ký lặp đi lặp lại | **Q&A Library**: ngân hàng câu trả lời tái sử dụng theo platform |
| Quản lý email / proxy rải rác | Thư viện tập trung: profile, email, proxy, OTP, API key |

---

## 2. Kiến trúc hệ thống

```
┌──────────────────────────────────────────────────────────┐
│                 Frontend · Next.js 14                     │
│           localhost:3001  (TypeScript + Tailwind)         │
└────────────────────────┬─────────────────────────────────┘
                         │ REST API  /api/*
┌────────────────────────▼─────────────────────────────────┐
│                  Backend · FastAPI                         │
│            localhost:8088  (Python 3.11)                  │
│                                                            │
│  ┌──────────┐  ┌───────────────────────┐  ┌───────────┐  │
│  │ Crawlers │  │   Signup Engine        │  │  Traffic  │  │
│  │ 5 nguồn  │  │  ┌─────────────────┐  │  │ Scanner   │  │
│  │          │  │  │ Script Runner   │  │  │(SimilarWeb│  │
│  └──────────┘  │  │ (Playwright)    │  │  └───────────┘  │
│                │  ├─────────────────┤  │                  │
│  ┌──────────┐  │  │ LLM Recovery    │  │  ┌───────────┐  │
│  │Discovery │  │  │ (Gemini Flash)  │  │  │   Q&A     │  │
│  │Pipeline  │  │  ├─────────────────┤  │  │  Library  │  │
│  │(săn      │  │  │ Full AI Agent   │  │  └───────────┘  │
│  │ brand)   │  │  │ (browser-use)   │  │                  │
│  └──────────┘  │  └─────────────────┘  │                  │
│                └───────────────────────┘                  │
│                      SQLite · data/app.db                  │
└──────────────────────────────────────────────────────────┘
```

**Tích hợp bên thứ ba:**

| Service | Mục đích | Bắt buộc? |
|---------|----------|-----------|
| Google Gemini API | LLM agent + per-step recovery | ✅ |
| CapSolver | Giải CAPTCHA (Turnstile, reCAPTCHA, hCaptcha) | ✅ |
| OpenAI API | LLM agent (thay thế Gemini) | Tuỳ chọn |
| DeepSeek API | LLM agent (thay thế Gemini, rẻ hơn) | Tuỳ chọn |
| SimilarWeb Pro | Traffic/tháng theo domain | Tuỳ chọn |
| SMSPool / 5sim | Nhận OTP điện thoại | Tuỳ chọn |
| IMAP Gmail | Xác thực email | Tuỳ chọn |
| SerpAPI | Google Ads Transparency | Tuỳ chọn |
| Proxy residential | Tránh ban IP (crawl + Discovery + signup) | Tuỳ chọn |

---

## 3. Luồng sử dụng chính

```
[1] Crawl → [2] Discover URL → [3] Quét traffic → [4] Shortlist
                                                          ↓
[7] Xem kết quả ← [6] Đăng ký ← [5] Chuẩn bị (Library)

Hoặc:  [Discovery] thả URL bất kỳ → bóc brand → resolve → traffic → affiliate → vào [Chương trình]
```

---

## 4. Các màn hình

### 4.1 Dashboard  `/dashboard`
Tổng quan 4 chỉ số + lối tắt đến các hành động thường dùng.

### 4.2 Nguồn quét  `/sources`

| Nguồn | Cách crawl | Số lượng |
|-------|-----------|---------|
| **OpenAffiliate** | YAML từ GitHub | ~755 |
| **Lovable Directory** | Nodriver browser | ~387 |
| **GoAffPro** | Public API | tùy chọn |
| **Affiliate Program DB** | HTML scraper tĩnh (tự resolve homepage khi crawl) | ~48 |
| **GrowthHero Marketplace** | Public JSON API (~16k merchant) | ~28 |

### 4.3 Tìm kiếm dự án  `/discovery`  ← **Mới**
Khác với "Nguồn quét" (crawl các directory có cấu trúc cố định), Discovery cho phép **thả bất kỳ URL nào** (site review, marketplace, bài blog "top 10…") rồi tự động bóc ra brand tiềm năng và chạy pipeline đánh giá.

**Pipeline mỗi candidate:**
```
Crawl site → tách link brand → resolve trang chủ → quét traffic → dò link affiliate → promote vào Chương trình
 discovered → homepage_resolved → traffic_scanned → affiliate_found → promoted
                                                                    ↘ no_affiliate_found
```

- **Bóc link brand:** 3 cách nhận diện — `direct_outbound` (link ra ngoài trực tiếp), `label_match` (link cạnh nhãn "Visit / Website / Official"), `fallback_outbound`.
- **Chống chặn:** pool proxy xoay IP, delay + jitter kiểu người thật, tự lùi (backoff) khi gặp trang chặn, tự bỏ IP bị block, dừng crawl nếu tỉ lệ chặn quá cao.
- **Điều khiển:** crawl incremental (chỉ quét mới), dừng giữa chừng (giữ lại kết quả đã tìm), blacklist domain rác.
- **Batch:** quét traffic / dò affiliate / chạy full pipeline cho nhiều candidate cùng lúc, có banner + chuông thông báo tiến độ.

*Hiện có 5 nguồn discovery, 248 candidate (~127 đã promote thành program).*

### 4.4 Jobs crawl  `/jobs`
Lịch sử crawl: trạng thái, số bản ghi, thời gian. Có thể chạy lại.

### 4.5 Chương trình  `/programs`
~1,346 programs với bộ lọc đầy đủ: nguồn, category, nền tảng, commission, traffic, cookie, trạng thái. Export CSV. Bulk quét traffic. Thêm vào shortlist.

### 4.6 Quản lý Trang chủ  `/homepage`
Resolve link affiliate platform → domain thực. AI browser tự động hoặc nhập tay. Phần lớn program đã được resolve.

### 4.7 Tuyển chọn  `/shortlists`
Tạo shortlist theo điểm (commission × traffic × cookie). Auto-fill top-N. Làm đầu vào cho signup.

### 4.8 Đăng ký tự động  `/signup`
Full LLM Agent (Gemini / OpenAI / DeepSeek) điều khiển browser stealth, tự điền form, giải CAPTCHA, đọc email, nhận OTP. Chạy song song N jobs.

### 4.9 Đăng ký Script  `/script-signup`
Ba chế độ signup có thể chọn:

| Chế độ | Chi phí API | Cách hoạt động |
|--------|------------|----------------|
| **Script thuần** | $0 | Playwright chạy playbook steps cố định |
| **Script + AI** *(mặc định)* | ~$0.012/run TB | Script → per-step LLM recovery → Full AI fallback |
| *(Full AI qua trang /signup)* | ~$0.10-0.20/run | Browser-use agent từ đầu |

### 4.10 Playbooks  `/playbooks`
Quản lý các script đăng ký đã ghi. Mỗi playbook = một chuỗi steps cho 1 affiliate platform. Tự động match theo: (1) program_id → (2) platform + category → (3) platform. Ví dụ playbook đã ghi:

| Playbook | Platform | Category |
|---------|---------|---------|
| lovable - firstpromoter - Video & Audio | firstpromoter | Video & Audio |
| lovable - everflow - Health & Wellness | everflow | Health & Wellness |
| lovable - firstpromoter - Streamable | firstpromoter | Video & Audio (Streamable riêng) |

### 4.11 Q&A Library  `/qa-library`  ← **Mới**
Ngân hàng câu hỏi → câu trả lời tái sử dụng cho form đăng ký, gom theo affiliate platform (Everflow, PartnerStack, Post Affiliate Pro, GoAffPro, FirstPromoter). Signup engine tra thư viện này để tự trả lời câu hỏi lặp lại thay vì hỏi LLM mỗi lần.

- **Loại câu trả lời:** `profile_field` (lấy từ profile), `static`, `template`, `select_option`, `traffic_source_lookup`.
- **Tự học:** mỗi entry ghi số lần dùng / thành công / thất bại, độ tin cậy, và trạng thái (active / cần review / lưu trữ). Câu liên tục fail sẽ bị đánh dấu cần review.
- Seed sẵn khi khởi động (hiện ~100 entry).

### 4.12 Lịch sử  `/history`
Chi tiết từng batch: status, message, số bước, screenshot. Click sâu đến từng program → từng bước.

### 4.13 Thư viện  `/library`

| Tab | Nội dung |
|-----|---------|
| Profile | Danh tính ảo: họ tên, quốc gia, niche, website, password |
| Email | Gmail + App Password + TOTP. Test IMAP login |
| Proxy | Residential / datacenter. Test kết nối. Import hàng loạt |
| SMS OTP | SMSPool / 5sim / sms-activate. Pre-flight check stock |
| API Keys | Gemini, OpenAI, DeepSeek — nhiều key, auto-rotate khi quota |
| Hướng dẫn | System prompt cho AI agent. Editor trong browser |

### 4.14 Google Ads  `/ads-transparency`
Tra quảng cáo đang chạy qua SerpAPI. Đánh giá brand trước khi đăng ký.

### 4.15 Thử nghiệm  `/experiment`
Sandbox cho AI agent. Test prompt trước khi đưa vào production.

---

## 5. Signup Engine — 3 Tier

```
Tier 1: Script (Playwright)  →  $0, ~10s
  ↓ step thất bại
Tier 2: LLM Recovery  →  1 Gemini Flash call/step, ~$0.003
  ↓ vẫn thất bại
Tier 3: Full LLM Agent  →  browser-use, ~$0.10-0.20
```

**Tiết kiệm ~85-90% chi phí API** so với chạy Full LLM mọi lúc. **Q&A Library** cấp sẵn câu trả lời cho các câu hỏi form lặp lại → giảm thêm số lần gọi LLM.

---

## 6. Kỹ thuật nổi bật

### Stealth Browser
CloakBrowser + Playwright. Bypass fingerprinting, headless detection. Proxy per-session.

### CAPTCHA Solver (CapSolver)
Turnstile · reCAPTCHA v2/v3 · hCaptcha · FunCaptcha · AWS WAF · Cloudflare Interstitial

### Multi-LLM Rotation
Gemini (primary) → OpenAI (gpt-4o) → DeepSeek. Nhiều key mỗi provider. Tự skip key bị quota 1 giờ.

### Live Config Reload
Đọc `.env` trực tiếp mỗi lần gọi — không cần restart khi đổi cấu hình.

---

## 7. Database

**SQLite** `backend/data/app.db`

| Bảng | Mô tả |
|------|-------|
| `affiliate_programs` | ~1,346 programs (openaffiliate 755 · lovable 387 · discovery 128 · apdb 48 · growthhero 28) |
| `discovery_sources` / `discovery_candidates` | Nguồn Discovery + brand tìm được (chạy qua pipeline) |
| `domain_blacklist` | Domain rác loại khỏi Discovery |
| `platform_qa_library` | Ngân hàng Q&A cho form đăng ký (~100 entry) |
| `signup_playbooks` | Script đăng ký đã ghi |
| `signup_jobs` | Lịch sử job đăng ký |
| `shortlists` + `shortlist_items` | Danh sách tuyển chọn |
| `crawl_jobs` | Lịch sử crawl (gồm cả job traffic/affiliate của Discovery) |
| `traffic_scan_jobs` | Lịch sử quét traffic |
| `users` / `sessions` | Tài khoản + phiên (default admin/123456) |

**File-based:** `profiles/` · `emails/` · `proxies/` · `instructions/` · `signup_screenshots/`

---

## 8. Cấu hình `.env`

| Biến | Mô tả | Bắt buộc |
|------|-------|----------|
| `GEMINI_API_KEY` | 1+ key, mỗi dòng 1 key | ✅ |
| `CAPSOLVER_API_KEY` | CapSolver | ✅ |
| `OPENAI_API_KEY` | OpenAI thay thế Gemini | — |
| `DEEPSEEK_API_KEY` | DeepSeek thay thế Gemini | — |
| `HEADLESS` | `true`/`false` (default: true) | — |
| `SIGNUP_WORKER_CONCURRENCY` | Song song (default: 2) | — |
| `SIGNUP_MAX_STEPS` | Bước tối đa/site (default: 60) | — |
| `SIGNUP_LLM_MODEL` | Model LLM (default: gemini-3.5-flash) | — |
| `SIMILARWEB_EMAIL` / `_PASSWORD` | Tài khoản SimilarWeb Pro | Tuỳ chọn |
| `SMS_OTP_API_KEY` | SMSPool / 5sim | Tuỳ chọn |
| `SERPAPI_KEYS` | Comma-separated, auto-rotate | Tuỳ chọn |

---

## 9. Chạy ứng dụng

**macOS / Linux (nhanh nhất):**
```bash
./start.sh     # bật Backend (8088) + Frontend (3001) chạy nền
./stop.sh      # tắt cả hai
./restart.sh   # tắt rồi bật lại
```
Log: `backend/logs/backend.log` · `frontend/logs/frontend.log`

**Windows:** Double-click `Start-App.cmd`

**Thủ công:**
```powershell
# Terminal 1
cd backend && .\.venv\Scripts\uvicorn.exe app.main:app --port 8088 --loop asyncio

# Terminal 2
cd frontend && npm run dev
```

| Service | URL |
|---------|-----|
| Frontend | http://localhost:3001 |
| Backend API | http://localhost:8088 |
| API Docs | http://localhost:8088/docs |

Đăng nhập: `admin` / `123456`

---

## 10. Yêu cầu & Giới hạn

**Yêu cầu:** Python 3.11+ · Node.js 18+ · RAM ≥ 8GB · Windows/Linux/macOS

**Giới hạn đã biết:**
- SQLite — phù hợp 1-5 người dùng, không scale horizontal
- SimilarWeb cần Selenium Hub riêng để login
- Headless = true bắt buộc khi deploy server (không có màn hình)
- Backend chạy `--loop asyncio` (không dùng uvloop) để tránh bug Playwright trên Windows
- Discovery crawl site lớn cần proxy pool + kiên nhẫn (delay chống chặn); nếu tỉ lệ block cao sẽ tự dừng

---

*Internal use — MIC ACE · API keys và `.env` không được commit lên repo*
