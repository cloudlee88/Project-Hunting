# Affiliate Hub - Product Requirements Document (PRD)

**Trạng thái:** Approved  
**Phiên bản:** 2.0  
**Người tạo (Product Owner):** MIC ACE Team  
**Team tham gia:** Backend (Python/FastAPI), Frontend (Next.js), QA  
**Ngày cập nhật cuối:** 11/06/2026

---

## 1. Tổng quan dự án

### 1.1 Vấn đề và Mục tiêu

**Vấn đề hiện tại:**

| Đối tượng | Muốn gì | Sợ gì |
|-----------|---------|-------|
| Affiliate Manager (MIC ACE) | Đăng ký nhiều chương trình affiliate nhanh, không bỏ sót program tiềm năng | Tốn thời gian thủ công, bị ban tài khoản, bỏ lỡ chương trình tốt |
| PO / PM | Theo dõi tiến độ, đảm bảo team đạt target số lượng affiliate | Không có visibility vào kết quả thực tế |

**Bối cảnh:**
- Quy trình thủ công hiện tại tốn **2–3 giờ/người/ngày** cho các tác vụ lặp đi lặp lại
- Tìm chương trình: phải vào từng directory website, copy thông tin tay
- Đánh giá: tra traffic thủ công trên SimilarWeb từng site
- Đăng ký: điền form một tay, tự giải CAPTCHA, chờ email verify
- Không có tool nào trên thị trường phục vụ đúng nhu cầu nội bộ này

**Hiện trạng nội bộ:**

| | Ưu điểm | Nhược điểm |
|--|---------|-----------|
| Nguồn lực | Có team tech in-house, có API key LLM | Chi phí API khi scale lớn |
| Quy trình | Team đã quen với affiliate workflow | Chưa có chuẩn hóa, kiến thức cá nhân hóa |

**Mục tiêu:** Tự động hoá toàn bộ pipeline: **Crawl → Evaluate → Shortlist → Auto-signup**, giảm thời gian thủ công từ 2-3 giờ/ngày xuống dưới 30 phút/ngày.

---

### 1.2 Giá trị mang lại

| Giá trị | Mô tả | Ước tính |
|---------|-------|---------|
| **Tiết kiệm thời gian** | Tự động hóa 80%+ tác vụ thủ công | ~2 giờ/ngày × 20 ngày = 40 giờ/tháng |
| **Tăng độ phủ** | Crawl tự động → không bỏ sót program | 1,142 programs vs ~50-100 thủ công |
| **Giảm chi phí API** | Hybrid Script+AI giảm 85-90% API cost | ~$0.012/signup vs $0.10-0.20 trước |
| **Chuẩn hóa quy trình** | Mọi người dùng cùng một workflow | Giảm phụ thuộc vào kinh nghiệm cá nhân |
| **Khả năng scale** | Chạy song song N jobs, không giới hạn profile | 5+ chương trình/phút thay vì 1 |

---

### 1.3 Tiêu chí Thành công (KPIs)

| Chỉ số | Baseline | Mục tiêu | Trạng thái |
|--------|---------|---------|-----------|
| Thời gian đăng ký 1 affiliate | 10-15 phút/tay | < 2 phút/site | ✅ ~10-30s (Script mode) |
| Tỷ lệ thành công signup | ~60% (tay) | > 80% | ✅ 100% test GoAffPro |
| Chi phí API/signup | $0.10-0.20 (full LLM) | < $0.02 (hybrid) | ✅ ~$0.012 TB |
| Số program trong DB | 0 | 1,000+ | ✅ 1,142 |
| Tỷ lệ URL đã resolved | 0% | > 75% | ✅ 77% (877/1,142) |

---

## 2. Đối tượng và Luồng người dùng

### 2.1 User Personas

**Persona 1 — Affiliate Manager**
- Vai trò: Vận hành toàn bộ quy trình hàng ngày
- Nhu cầu chính: Chạy signup hàng loạt, theo dõi kết quả, quản lý tài nguyên (profile, email, proxy)
- Tần suất: Hàng ngày, 1-3 giờ/ngày

**Persona 2 — PO / PM**
- Vai trò: Giám sát kết quả, quyết định danh sách program ưu tiên
- Nhu cầu chính: Xem dashboard tổng quan, review shortlist, theo dõi tỷ lệ thành công
- Tần suất: 2-3 lần/tuần

---

### 2.2 User Stories

| ID | Persona | Muốn | Để |
|----|---------|------|-----|
| US-01 | Affiliate Manager | Crawl affiliate programs mới từ nhiều nguồn | Cập nhật database không bỏ sót program mới |
| US-02 | Affiliate Manager | Tạo shortlist tự động theo điểm commission + traffic | Không mất thời gian chọn tay |
| US-03 | Affiliate Manager | Chạy signup hàng loạt, tự giải CAPTCHA, tự verify email | Không phải làm tay từng site |
| US-04 | Affiliate Manager | Xem screenshot và log từng bước đăng ký | Debug khi signup thất bại |
| US-05 | Affiliate Manager | Tái sử dụng script đã ghi thành công cho cùng nền tảng | Không tốn API cost cho site quen |
| US-06 | PM | Xem tổng quan số lượng chương trình, tỷ lệ thành công | Báo cáo tiến độ mà không cần hỏi team |

---

### 2.3 User Flow — Luồng chính

```
┌─────────────────────────────────────────────────────────────────────┐
│                    LUỒNG HÀNG NGÀY (Happy Path)                      │
└─────────────────────────────────────────────────────────────────────┘

[1] CRAWL                [2] DISCOVER URL           [3] ĐÁNH GIÁ
Chọn nguồn crawl    →   AI browser resolve      →   Quét traffic
(OpenAffiliate /        link trung gian              SimilarWeb hàng loạt
 Lovable / GoAffPro)    → domain thực                Xem commission, cookie
      ↓                        ↓                           ↓

[4] SHORTLIST            [5] CHUẨN BỊ              [6] SIGNUP
Đặt tiêu chí        →   Profile + Email        →   Chọn shortlist
Auto-fill top-N          Proxy + OTP                Chọn profile/email
Review & chỉnh sửa       API keys                   Bấm Bắt đầu
                         Hướng dẫn                  Chờ kết quả (~30s-3ph/site)
                                                           ↓
                                                    [7] KẾT QUẢ
                                                    Xem status: success/fail
                                                    Screenshot bằng chứng
                                                    Re-run nếu cần
```

---

## 3. Yêu cầu Chức năng

### Epic 1: Thu thập dữ liệu (Data Collection)

| ID | Tính năng | Mô tả | Acceptance Criteria | Ưu tiên |
|----|-----------|-------|---------------------|---------|
| FR-1.1 | Crawl OpenAffiliate | Crawl ~755 programs từ GitHub YAML | Đủ fields: name, URL, commission, cookie, category | P0 ✅ |
| FR-1.2 | Crawl Lovable Directory | Browser agent crawl ~387 programs theo platform | Import đúng platform tag (Rewardful, FirstPromoter…) | P0 ✅ |
| FR-1.3 | Crawl GoAffPro | Pull API Shopify stores dùng GoAffPro | Tùy chọn số lượng, dedup theo domain | P0 ✅ |
| FR-1.4 | Homepage Discovery | AI browser resolve link affiliate → domain thực | Tỷ lệ resolve > 75%, lưu lịch sử thay đổi | P0 ✅ |
| FR-1.5 | Traffic Scanner | Quét SimilarWeb hàng loạt | Lưu visits/month, lịch sử theo thời gian | P1 ✅ |

---

### Epic 2: Quản lý & Đánh giá chương trình

| ID | Tính năng | Mô tả | Acceptance Criteria | Ưu tiên |
|----|-----------|-------|---------------------|---------|
| FR-2.1 | Database chương trình | Xem, tìm kiếm, lọc 1,142 programs | Bộ lọc: category, platform, commission, traffic, cookie | P0 ✅ |
| FR-2.2 | Export CSV | Xuất danh sách đang lọc ra file CSV | File đúng format, tất cả cột | P1 ✅ |
| FR-2.3 | Shortlist Engine | Tạo danh sách ưu tiên theo điểm | Điểm = commission × traffic × cookie; Auto-fill top-N | P0 ✅ |
| FR-2.4 | Google Ads Transparency | Tra quảng cáo brand qua SerpAPI | Hiển thị số quảng cáo đang chạy, preview | P2 ✅ |

---

### Epic 3: Auto Signup

| ID | Tính năng | Mô tả | Acceptance Criteria | Ưu tiên |
|----|-----------|-------|---------------------|---------|
| FR-3.1 | Full AI Mode | Gemini/OpenAI/DeepSeek agent tự signup | Xử lý được form động, CAPTCHA, email verify, OTP | P0 ✅ |
| FR-3.2 | Script Mode | Playwright thuần chạy playbook | $0 API cost, hoàn thành trong ~10s/site | P0 ✅ |
| FR-3.3 | Script+AI Hybrid | 3-tier: Script → per-step recovery → Full AI | ~85-90% giảm cost vs Full AI; tỷ lệ thành công tương đương | P0 ✅ |
| FR-3.4 | CAPTCHA Auto-solve | CapSolver giải Turnstile/reCAPTCHA/hCaptcha | Pass Cloudflare Turnstile, kết quả inject vào page | P0 ✅ |
| FR-3.5 | Email Verification | Tự đọc IMAP inbox lấy link/code verify | Timeout 5 phút, retry 3 lần | P1 ✅ |
| FR-3.6 | SMS OTP | Mua số tạm qua SMSPool/5sim | Pre-flight check stock trước khi submit | P1 ✅ |
| FR-3.7 | Parallel Execution | N jobs chạy đồng thời | Configurable concurrency (default 2), isolate browser per job | P0 ✅ |
| FR-3.8 | Screenshot Evidence | Chụp màn hình sau khi signup xong | Lưu file PNG, hiển thị trong history | P1 ✅ |

---

### Epic 4: Playbook Manager

| ID | Tính năng | Mô tả | Acceptance Criteria | Ưu tiên |
|----|-----------|-------|---------------------|---------|
| FR-4.1 | Tạo playbook | Ghi lại steps signup cho 1 platform | Lưu selector, action, giá trị cho từng step | P0 ✅ |
| FR-4.2 | Auto-match 3 tầng | Tự tìm playbook phù hợp | Khớp: program_id → platform+category → platform | P0 ✅ |
| FR-4.3 | Per-step LLM Recovery | Khi selector sai, 1 Gemini Flash call tìm selector đúng | Tỷ lệ recovery > 70% lỗi selector; cost < $0.005/lần | P1 ✅ |
| FR-4.4 | Playbook stats | Theo dõi tỷ lệ thành công/thất bại | consecutive_fails tracking, tự chuyển needs_llm sau 2 lần | P1 ✅ |

---

### Epic 5: Thư viện tài nguyên (Library)

| ID | Tính năng | Mô tả | Acceptance Criteria | Ưu tiên |
|----|-----------|-------|---------------------|---------|
| FR-5.1 | Profile Manager | Quản lý danh tính ảo | CRUD, duplicate, tag, filter theo niche | P0 ✅ |
| FR-5.2 | Email Manager | Gmail + App Password + IMAP | Test login trực tiếp, TOTP support | P0 ✅ |
| FR-5.3 | Proxy Manager | Import/test proxy hàng loạt | Import format `ip:port:user:pass`, test kết nối | P1 ✅ |
| FR-5.4 | API Key Manager | Nhiều key Gemini/OpenAI/DeepSeek | Auto-rotate khi quota, skip 1 giờ khi hit limit | P0 ✅ |
| FR-5.5 | Instruction Editor | System prompt cho AI agent | Editor trong browser, đặt tên theo workflow | P1 ✅ |

---

## 4. Yêu cầu Phi chức năng

| Hạng mục | Yêu cầu | Trạng thái |
|----------|---------|-----------|
| **Hiệu suất** | Script mode: < 30s/site. Full AI: < 3 phút/site. API response: < 500ms | ✅ Script ~10-30s, AI ~2-3ph |
| **Bảo mật** | API keys không commit lên repo. `.env` file chứa secrets, đọc live không cache | ✅ |
| **Độ tin cậy** | Retry tự động khi key quota. Fallback LLM khi script fail | ✅ |
| **Khả năng mở rộng** | SQLite đủ cho 1-5 người. Cần migrate PostgreSQL nếu scale > 10 users | ⚠️ Giới hạn hiện tại |
| **Nền tảng** | Windows 10/11 (chính), Linux, macOS. Chrome stealth via CloakBrowser | ✅ |
| **Concurrency** | Signup: configurable N workers song song (default 2, test ổn với 5) | ✅ |

---

## 5. Yêu cầu Kỹ thuật & Tích hợp

### Kiến trúc tóm tắt

```
Frontend: Next.js 14 + TypeScript + Tailwind CSS  (localhost:3001)
    ↕ REST API
Backend:  FastAPI + Python 3.11                    (localhost:8088)
    ↕
Database: SQLite (app.db) + File store (JSON/PNG)

Browser Engine: Playwright + CloakBrowser (stealth Chromium)
AI Layer:       browser-use + Gemini 3.5 Flash / GPT-4o / DeepSeek
CAPTCHA:        CapSolver API
```

### Tích hợp bên thứ 3

| Service | Mục đích | Bắt buộc |
|---------|----------|---------|
| Google Gemini API | LLM agent + step recovery | ✅ |
| CapSolver | Giải CAPTCHA tự động | ✅ |
| OpenAI API | Thay thế Gemini | Tùy chọn |
| DeepSeek API | Thay thế Gemini (rẻ hơn) | Tùy chọn |
| SimilarWeb Pro | Traffic data | Tùy chọn |
| SMSPool / 5sim | SMS OTP | Tùy chọn |
| Gmail IMAP | Email verification | Tùy chọn |
| SerpAPI | Google Ads Transparency | Tùy chọn |

### Database chính (SQLite)

| Bảng | Mô tả | Số bản ghi |
|------|-------|-----------|
| `affiliate_programs` | Toàn bộ programs | 1,142 |
| `signup_playbooks` | Script steps theo platform | 3 active |
| `signup_jobs` | Lịch sử signup batch | 23 |
| `shortlists` | Danh sách tuyển chọn | — |
| `crawl_jobs` | Lịch sử crawl | — |

---

## 6. Kế hoạch Triển khai

### 6.1 Lộ trình đã hoàn thành

| Phase | Tính năng | Trạng thái |
|-------|-----------|-----------|
| **Phase 1 — MVP** | Crawl 3 nguồn, DB chương trình, Shortlist, Full AI signup | ✅ Done |
| **Phase 2 — Enrichment** | Traffic scanner, Homepage discovery, Library, Job history | ✅ Done |
| **Phase 3 — Script Engine** | Playbook system, Script mode, CAPTCHA trong script | ✅ Done |
| **Phase 4 — Hybrid AI** | Script+AI hybrid, Per-step LLM recovery, Multi-LLM provider | ✅ Done |

### 6.2 Roadmap tiếp theo

| Phase | Tính năng | Ưu tiên | Ghi chú |
|-------|-----------|---------|---------|
| **Phase 5** | Thêm nguồn crawl (ShareASale, CJ, Impact) | P1 | Tăng coverage thêm ~500+ programs |
| **Phase 5** | Self-healing playbooks (tự update selector khi LLM fix) | P1 | Giảm bảo trì manual |
| **Phase 6** | Scheduler re-crawl định kỳ (daily/weekly) | P2 | Tự động cập nhật database |
| **Phase 6** | Notification (email/Slack) khi job xong | P2 | Không cần ngồi chờ kết quả |
| **Phase 7** | Dashboard analytics (tỷ lệ thành công theo platform) | P2 | Visibility cho PM |
| **Phase 7** | Multi-user + phân quyền | P3 | Khi team > 2 người |
| **Phase 8** | Migrate PostgreSQL | P3 | Khi cần scale horizontal |

---

## 7. Đánh giá Rủi ro

| Rủi ro | Mức độ | Phương án xử lý |
|--------|--------|----------------|
| Affiliate platform thay đổi HTML form | Cao | Per-step LLM recovery tự fix selector; playbook tự cập nhật |
| Gemini quota hết giữa chừng | Trung bình | Multi-key auto-rotate; OpenAI/DeepSeek là fallback |
| IP bị ban khi signup nhiều | Trung bình | Proxy residential per-session; rate limiting có thể config |
| CapSolver không giải được CAPTCHA mới | Thấp | Mark job là cần xử lý tay; optional step trong playbook |
| SimilarWeb thay đổi login flow | Thấp | Traffic scanner là tính năng phụ, có thể tạm dừng |
| SQLite bottleneck khi scale | Thấp | Chỉ 1-5 users hiện tại; migrate PostgreSQL nếu cần |

---

## 8. Phụ lục

### Thuật ngữ

| Từ | Nghĩa |
|----|-------|
| **Affiliate Program** | Chương trình hoa hồng của một brand (VD: Visme trả 20% recurring) |
| **FirstPromoter / Everflow** | Nền tảng quản lý affiliate (như Shopify nhưng cho affiliate) |
| **Playbook** | Kịch bản bước-by-bước để signup tự động vào 1 platform |
| **Shortlist** | Danh sách program đã được chọn lọc để signup |
| **CAPTCHA** | Bài kiểm tra "bạn có phải robot không?" (Turnstile, reCAPTCHA…) |
| **CloakBrowser** | Chromium đã được patch để qua được bot-detection |
| **Script mode** | Chạy signup theo script cố định, không dùng AI, $0 API cost |
| **Hybrid mode** | Script trước, AI chỉ dùng khi script không xử lý được |
| **Per-step recovery** | Khi 1 bước trong script thất bại, gọi 1 lần AI để tìm cách fix |
| **CCU** | Concurrent Users — số người dùng cùng lúc |
| **OTP** | One-Time Password — mã SMS dùng 1 lần |

### Chi phí vận hành ước tính

| Hạng mục | Chi phí | Ghi chú |
|----------|---------|---------|
| Gemini API | ~$0.012/signup (hybrid) | Free tier có thể đủ cho < 100 signup/ngày |
| CapSolver | ~$0.001/CAPTCHA | Nạp $5 dùng được lâu |
| SMSPool | ~$0.05-0.20/OTP | Chỉ khi cần số điện thoại |
| Proxy | $10-50/tháng | Residential proxy, tùy lượng |
| SimilarWeb Pro | Theo gói | Tùy chọn, chỉ cần cho traffic data |

### Tài liệu liên quan

- `Aboutme.md` — Technical overview
- `backend/.env.example` — Cấu hình mẫu
- `http://localhost:8088/docs` — API documentation (Swagger)
- `backend/data/app.db` — SQLite database
