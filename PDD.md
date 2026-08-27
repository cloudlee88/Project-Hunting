# Affiliate Hub — Product Design Document (PDD)

> **Internal — MIC ACE** · Phiên bản: 1.0 · Ngày: 11/06/2026  
> Tài liệu mô tả chi tiết thiết kế từng màn hình — mục tiêu, tính năng, luồng hoạt động và liên kết với các màn khác.

---

## Bản đồ màn hình tổng quan

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                          LUỒNG CHÍNH                                          │
│                                                                                │
│  [Dashboard] ──► [Sources] ──► [Jobs] ──► [Programs] ──► [Homepage]           │
│       │                                       │                ↓               │
│       │                                  [Shortlists] ◄── [Programs]           │
│       │                                       │                                │
│       │                               ┌───────▼───────┐                       │
│       │                               │    SIGNUP      │                       │
│       │                               │  ┌──────────┐  │                       │
│       │                               │  │ Full AI  │  │                       │
│       │                               │  │ /signup  │  │                       │
│       │                               │  ├──────────┤  │                       │
│       │                               │  │Script+AI │  │                       │
│       │                               │  │/script-  │  │                       │
│       │                               │  │ signup   │  │                       │
│       │                               │  └──────────┘  │                       │
│       │                               └───────┬────────┘                       │
│       │                                       ▼                                │
│       └──────────────────────────────── [History]                              │
│                                                                                │
│  HỖTRỢ: [Library] · [Playbooks] · [Ads Transparency] · [Experiment]           │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## Màn 01 — Dashboard `/dashboard`

### Mục tiêu
Cung cấp tổng quan nhanh về trạng thái hệ thống và lối tắt đến các hành động thường dùng nhất. Người dùng mở app → biết ngay hệ thống đang ở đâu trong 10 giây.

### Tính năng

| # | Tính năng | Mô tả chi tiết |
|---|-----------|---------------|
| 1 | **Stats cards (4 chỉ số)** | Tổng programs, Số profiles, Số hướng dẫn, Tổng jobs crawl — có link click thẳng vào màn tương ứng |
| 2 | **Quick actions (3 lối tắt)** | Quét nguồn mới → `/sources`; Tạo profile → `/profiles/new`; Viết hướng dẫn → `/instructions` |
| 3 | **Jobs gần đây** | 5 crawl jobs mới nhất: ID, source, trạng thái, số records. Auto-refresh mỗi 3 giây |
| 4 | **Hero banner** | Tên thương hiệu MIC ACE + tagline |

### Luồng hoạt động

```
Mở app
  ↓
Dashboard load stats (4 queries song song)
  ↓
User đọc chỉ số → chọn hành động tiếp theo:
  ├─ Muốn crawl thêm program  → click "Quét nguồn mới" → /sources
  ├─ Muốn đăng ký affiliate   → click stats "Tổng jobs" → /jobs (hoặc sidebar → /signup)
  ├─ Muốn tạo danh tính ảo    → click "Tạo profile"     → /profiles/new
  └─ Muốn xem chi tiết        → click từng stat card    → màn tương ứng
```

### Liên kết giữa các màn

| Điểm đến | Trigger | Ghi chú |
|----------|---------|---------|
| `/sources` | Quick action "Quét nguồn mới" | Bước đầu trong pipeline |
| `/profiles/new` | Quick action "Tạo profile" | Tạo mới profile nhanh |
| `/instructions` | Quick action "Viết hướng dẫn" | Quản lý system prompt |
| `/programs` | Click stat "Tổng programs" | Xem toàn bộ DB |
| `/profiles` | Click stat "Profiles" | Quản lý profiles |
| `/jobs` | Click stat "Tổng jobs" | Xem lịch sử crawl |

---

## Màn 02 — Nguồn quét `/sources`

### Mục tiêu
Khởi chạy crawl dữ liệu affiliate program từ 3 nguồn khác nhau. Đây là điểm khởi đầu của toàn bộ pipeline — nếu không có dữ liệu thì không có gì để đăng ký.

### Tính năng

| # | Tính năng | Mô tả chi tiết |
|---|-----------|---------------|
| 1 | **3 nguồn crawl** | OpenAffiliate (~755), Lovable Directory (~387), GoAffPro (tùy chọn) — mỗi nguồn 1 card riêng với màu sắc phân biệt |
| 2 | **Cấu hình per-source** | GoAffPro: nhập số lượng; Lovable: chọn nền tảng (FirstPromoter, Rewardful, Tolt...); OpenAffiliate: chạy thẳng |
| 3 | **Tùy chọn sau crawl** | Checkbox "Tự tìm trang chủ" và "Quét traffic SimilarWeb" — tự động trigger sau khi crawl xong |
| 4 | **Start button** | Gọi API `POST /crawl/{source}` → nhận job_id → chuyển sang `/jobs` để theo dõi |
| 5 | **Trạng thái realtime** | Toast notification khi crawl bắt đầu, tìm homepage bắt đầu, scan traffic bắt đầu |

### Luồng hoạt động

```
Vào /sources
  ↓
Chọn nguồn (1 trong 3)
  ↓
Cấu hình tham số (nếu có)
  ↓
Tick "Tự tìm trang chủ" (khuyến nghị) và/hoặc "Quét traffic"
  ↓
Bấm "Bắt đầu crawl"
  ↓
API trả về job_id → toast "Đã thêm vào hàng đợi — Job #X"
  ↓ (nếu tick tìm trang chủ)
API discover homepage bắt đầu song song → toast
  ↓ (nếu tick quét traffic)
Lấy danh sách program_ids mới → tạo traffic scan job → toast
  ↓
User chuyển sang /jobs để theo dõi tiến độ
```

### Liên kết giữa các màn

| Điểm đến | Trigger | Ghi chú |
|----------|---------|---------|
| `/jobs` | Sau khi bấm Start → tự navigate hoặc user click | Xem tiến độ crawl |
| `/homepage` | Nếu tick "Tìm trang chủ" → job chạy background | Không navigate trực tiếp |
| `/programs` | Sau khi job hoàn thành | Xem dữ liệu vừa crawl |

---

## Màn 03 — Jobs crawl `/jobs`

### Mục tiêu
Theo dõi trạng thái tất cả các phiên crawl đã và đang chạy. Phát hiện lỗi sớm, chạy lại khi cần.

### Tính năng

| # | Tính năng | Mô tả chi tiết |
|---|-----------|---------------|
| 1 | **Bảng jobs** | Cột: ID, Source, Trạng thái, Found/Saved, Bắt đầu, Kết thúc, Error |
| 2 | **Auto-refresh** | Tự cập nhật mỗi 2 giây — thấy progress realtime |
| 3 | **Expand row** | Click vào job → xem chi tiết kết quả crawl (sub-table) |
| 4 | **Status badge** | running / done / failed với màu sắc trực quan |
| 5 | **Re-run** | Nút chạy lại job cũ với cùng cấu hình |
| 6 | **Làm mới thủ công** | Nút "Làm mới" trên header |

### Luồng hoạt động

```
Vào /jobs (từ /sources hoặc sidebar)
  ↓
Xem danh sách jobs — tự refresh 2s
  ↓
Job đang chạy (running) → chờ → status đổi thành done/failed
  ↓
Click expand row → xem Found: X / Saved: Y
  ├─ Saved > 0     → chuyển sang /programs để xem dữ liệu mới
  └─ Status failed → đọc error message → xem xét re-run
```

### Liên kết giữa các màn

| Điểm đến | Trigger | Ghi chú |
|----------|---------|---------|
| `/programs` | Sau khi job done | Xem programs vừa crawl, filter theo source |
| `/sources` | Sidebar | Tạo job crawl mới |

---

## Màn 04 — Chương trình `/programs`

### Mục tiêu
Là kho dữ liệu trung tâm của toàn bộ affiliate programs đã thu thập. User cần có thể tìm, lọc, đánh giá và hành động (thêm shortlist, quét traffic, export) trên bất kỳ program nào.

### Tính năng

| # | Tính năng | Mô tả chi tiết |
|---|-----------|---------------|
| 1 | **Bộ lọc đa chiều** | Source, Category, Platform (14 giá trị), Commission min/max, Traffic min, Cookie min, Trạng thái đăng ký, Có traffic / Có URL, Tiền tệ payout |
| 2 | **Sắp xếp** | 5 cột: Name, Source, Category, Commission, Ngày crawl (asc/desc) |
| 3 | **Phân trang** | Page size tùy chọn, hiển thị tổng records |
| 4 | **Xem chi tiết** | Click row → panel bên phải: commission, cookie, traffic chart, payout, tags, signup URL |
| 5 | **Export CSV** | Xuất danh sách đang lọc ra `.csv` |
| 6 | **Bulk quét traffic** | Chọn nhiều programs → "Quét traffic" → tạo traffic scan job |
| 7 | **Thêm vào shortlist** | Chọn programs → chọn shortlist → thêm vào |
| 8 | **Traffic chart** | Xem biểu đồ traffic theo thời gian trong panel chi tiết |
| 9 | **Platform detection** | Badge hiển thị platform (FirstPromoter, Everflow, Rewardful...) nhận diện từ signup_url |

### Luồng hoạt động

```
Vào /programs
  ↓
Apply bộ lọc (category + platform + commission thường dùng)
  ↓
Sắp xếp theo commission_value DESC
  ↓
┌─────────────────────────────────────────────────────┐
│  User muốn làm gì?                                   │
├─────────────────────────────────────────────────────┤
│  Xem chi tiết    → click row → panel chi tiết        │
│  Quét traffic    → check programs → "Quét traffic"  │
│  Thêm shortlist  → check programs → chọn shortlist  │
│  Export          → bấm "Export CSV"                  │
└─────────────────────────────────────────────────────┘
```

### Liên kết giữa các màn

| Điểm đến | Trigger | Ghi chú |
|----------|---------|---------|
| `/shortlists` | Thêm vào shortlist | Cần tạo shortlist trước |
| `/homepage` | Sidebar | Resolve URL cho các program chưa có domain thực |
| `/signup` | Sidebar → chọn program từ shortlist | Dùng shortlist để signup |
| `/programs/[id]` | Click row | Chi tiết 1 program |

---

## Màn 05 — Quản lý Trang chủ `/homepage`

### Mục tiêu
Chuyển đổi "link trung gian" (VD: `brand.getrewardful.com`) sang domain thực của brand (`brand.com`). AI agent cần domain thực để tìm form đăng ký đúng chỗ.

### Tính năng

| # | Tính năng | Mô tả chi tiết |
|---|-----------|---------------|
| 1 | **Bộ lọc 5 trạng thái** | Tất cả / Đã có URL thực / Còn link trung gian / Chưa có URL / Theo platform |
| 2 | **Bộ lọc platform** | Lọc theo 20+ affiliate platform (Rewardful, Tolt, FirstPromoter, Everflow...) |
| 3 | **Badge nhận diện** | Badge màu hiển thị platform detect từ URL (ví dụ "FirstPromoter" màu xanh) |
| 4 | **Discover tự động (batch)** | Chọn nhiều program → "Discover" → AI browser tự mở từng URL, tìm link homepage thực |
| 5 | **Sửa tay** | Click icon bút → nhập URL mới → lưu ngay |
| 6 | **Phân trang + sắp xếp** | Duyệt qua 1,142 programs dễ dàng |
| 7 | **Thống kê đầu trang** | Tổng / Đã có URL thực (877) / Còn trung gian (265) |
| 8 | **Bulk select** | Checkbox all / none / partial — select nhiều để discover hàng loạt |

### Luồng hoạt động

```
Vào /homepage
  ↓
Filter "Còn link trung gian" → thấy 265 programs chưa resolve
  ↓
Chọn batch (VD: theo platform = Rewardful, 50 programs)
  ↓
Bấm "Discover" → AI browser chạy background (concurrency=3)
  ↓
Programs từng cái được cập nhật URL thực → badge đổi từ "Trung gian" → "URL thực"
  ↓
Programs không resolve được → sửa tay (click bút, nhập URL)
  ↓
Khi đã đủ URL thực → sang /programs → filter → thêm shortlist
```

### Liên kết giữa các màn

| Điểm đến | Trigger | Ghi chú |
|----------|---------|---------|
| `/programs` | Sau khi resolve xong | Xem programs đã có URL thực |
| `/sources` | Sidebar | Crawl thêm program mới |

---

## Màn 06 — Tuyển chọn `/shortlists`

### Mục tiêu
Tạo và quản lý danh sách các affiliate program ưu tiên để đăng ký. Shortlist là "đầu vào" trực tiếp cho luồng Auto Signup — không có shortlist thì không chạy signup được.

### Tính năng

| # | Tính năng | Mô tả chi tiết |
|---|-----------|---------------|
| 1 | **Tạo shortlist mới** | Modal: đặt tên, mô tả, cài tiêu chí (criteria editor) |
| 2 | **Criteria Editor** | Điều chỉnh trọng số: Commission weight, Traffic weight, Cookie weight; filter min commission, min traffic, platform filter |
| 3 | **Auto-fill top-N** | Nhập số lượng → hệ thống tự chọn N programs phù hợp nhất theo tiêu chí |
| 4 | **Quản lý items** | Trong `/shortlists/[id]`: xem danh sách, xóa từng item, thêm program tay |
| 5 | **Nhiều shortlist** | Có thể có nhiều shortlist song song (theo category, theo chiến dịch...) |
| 6 | **Xóa shortlist** | Xóa toàn bộ shortlist + items |
| 7 | **Hiển thị tóm tắt** | Card mỗi shortlist: tên, mô tả, số lượng items, ngày tạo |

### Luồng hoạt động

```
Vào /shortlists
  ↓
Bấm "Tạo shortlist"
  ↓
Điền tên + tiêu chí (commission weight, traffic weight, cookie weight)
  → Bấm "Tạo"
  ↓
Vào shortlist vừa tạo (/shortlists/[id])
  ↓
Bấm "Auto-fill" → nhập số lượng (VD: 20)
  → Hệ thống tự chọn top 20 programs theo criteria
  ↓
Review danh sách:
  ├─ OK → chuyển sang /signup hoặc /script-signup
  └─ Cần điều chỉnh → xóa từng item / thêm tay từ /programs
  ↓
Dùng shortlist này làm input cho signup
```

### Liên kết giữa các màn

| Điểm đến | Trigger | Ghi chú |
|----------|---------|---------|
| `/signup` | Chọn shortlist khi tạo job signup | Input cho Full AI mode |
| `/script-signup` | Chọn shortlist khi tạo script job | Input cho Script mode |
| `/programs` | Thêm program tay vào shortlist | Tìm program trong DB |
| `/shortlists/[id]` | Click vào shortlist card | Xem và quản lý items |

---

## Màn 07 — Đăng ký tự động (Full AI) `/signup`

### Mục tiêu
Chạy đăng ký affiliate tự động hoàn toàn bằng AI agent — không cần playbook, không cần script. AI tự đọc form, điền thông tin, giải CAPTCHA, xác thực email, nhận OTP. Dùng khi chưa có playbook hoặc form quá phức tạp.

### Tính năng

| # | Tính năng | Mô tả chi tiết |
|---|-----------|---------------|
| 1 | **Chọn shortlist** | Dropdown chọn shortlist làm input programs |
| 2 | **Chọn profile** | Multi-select từ Library profiles |
| 3 | **Chọn email** | Multi-select từ Library emails (Gmail IMAP) |
| 4 | **Chọn proxy** | Multi-select từ Library proxies |
| 5 | **Chọn hướng dẫn** | Multi-select instructions (system prompt) |
| 6 | **Chọn LLM provider** | Gemini / OpenAI / DeepSeek + key index |
| 7 | **Headless toggle** | Chạy ẩn (server) hoặc hiện Chrome (debug) |
| 8 | **Batch ID** | Gom nhiều job vào 1 batch để theo dõi chung |
| 9 | **Connection test** | Test kết nối API key, email, proxy trước khi chạy |
| 10 | **CAPTCHA memory** | Xem/xóa kinh nghiệm CAPTCHA đã học theo domain |
| 11 | **System status banner** | Cảnh báo nếu API key chưa cấu hình, CloakBrowser chưa cài |
| 12 | **Realtime job list** | Xem các job signup đang chạy, tự refresh 3s |

### Luồng hoạt động

```
Vào /signup
  ↓
(Kiểm tra System Status Banner — phải green mới chạy được)
  ↓
Chọn shortlist → các programs tự load
  ↓
Chọn profile(s) + email(s) + proxy (nếu có) + hướng dẫn
  ↓
Cài LLM provider + chọn headless
  ↓
Bấm "Bắt đầu"
  ↓
API tạo job → job_id hiện trong danh sách
  ↓
Job chạy background:
  Mỗi program → 1 browser session (CloakBrowser stealth)
  AI agent điền form → gặp CAPTCHA → CapSolver giải
  Cần verify email → đọc IMAP → lấy link → click
  Cần OTP → SMSPool mua số → nhận code
  → Lưu kết quả + screenshot
  ↓
Xem kết quả realtime trong list hoặc chuyển sang /history
```

### Liên kết giữa các màn

| Điểm đến | Trigger | Ghi chú |
|----------|---------|---------|
| `/shortlists` | "Tạo shortlist mới" link | Cần có shortlist trước |
| `/library` | "Thêm profile/email/proxy" link | Cần có tài nguyên trước |
| `/history` | Sau khi job xong / click "Xem lịch sử" | Xem kết quả chi tiết |
| `/script-signup` | Sidebar | Chuyển sang chế độ Script |
| `/playbooks` | Sidebar | Quản lý playbooks |

---

## Màn 08 — Đăng ký Script `/script-signup`

### Mục tiêu
Chạy signup theo kịch bản (playbook) đã ghi sẵn — không cần AI đọc form từ đầu mỗi lần. Có 3 chế độ linh hoạt: thuần script (free), script+AI hybrid (tiết kiệm 85-90% API cost), hoặc full AI fallback. **Đây là chế độ mặc định và khuyến nghị khi đã có playbook.**

### Tính năng

| # | Tính năng | Mô tả chi tiết |
|---|-----------|---------------|
| 1 | **Chọn shortlist** | Dropdown chọn shortlist; chỉ programs có playbook matching mới được check |
| 2 | **Chế độ chạy (run_mode)** | **Script+AI** (mặc định): 3-tier cascade; **Script thuần**: Playwright $0, không fallback |
| 3 | **Màu trạng thái script** | Mỗi program hiển thị badge: Khớp chính xác (xanh) / Tương thích (xanh nhạt) / Cần LLM (vàng) / Chưa có script (xám) |
| 4 | **Chọn profile/email/proxy** | Multi-select tương tự /signup |
| 5 | **Headless toggle** | Ẩn/hiện Chrome |
| 6 | **Select runnable** | Nút "Chọn X có script" — tự chọn tất cả programs có playbook hợp lệ |
| 7 | **Stats tóm tắt** | Khớp chính xác X / Tương thích X / Cần LLM X / Chưa có script X |
| 8 | **Job history (script)** | Phần dưới: danh sách job script đã chạy (filter theo run_mode=script|script_llm) |
| 9 | **Screenshot proof** | Mỗi kết quả có screenshot bằng chứng thành công |

### 3 Tier hoạt động (Script+AI mode)

| Tier | Trigger | Hành động | Chi phí |
|------|---------|----------|---------|
| **Tier 1** — Script | Luôn thử trước | Playwright chạy playbook steps | $0 |
| **Tier 2** — LLM Recovery | Khi 1 step selector thất bại | 1 Gemini Flash call → tìm selector đúng | ~$0.003 |
| **Tier 3** — Full AI | Khi cả 2 lần script đều fail | Full browser-use agent từ URL hiện tại | ~$0.10-0.20 |

### Luồng hoạt động

```
Vào /script-signup
  ↓
Chọn shortlist → programs load kèm trạng thái playbook
  ↓
Xem stats: X khớp chính xác, Y tương thích, Z chưa có script
  ↓
Click "Chọn X có script" → tự check các programs có thể chạy
  ↓
Chọn chế độ: Script+AI (mặc định) hoặc Script thuần
  ↓
Chọn profile + email + proxy
  ↓
Bấm "Chạy Script"
  ↓
Mỗi program chạy qua 3 Tier:
  Tier 1: Playwright chạy playbook → OK → Done ($0)
      ↓ fail
  Tier 2: Gemini Flash tìm selector → OK → tiếp tục ($0.003)
      ↓ fail
  Tier 3: Full AI agent xử lý (~$0.10)
  ↓
Kết quả lưu vào history + screenshot
```

### Liên kết giữa các màn

| Điểm đến | Trigger | Ghi chú |
|----------|---------|---------|
| `/playbooks` | "Xem playbooks" / Sidebar | Quản lý và review scripts |
| `/signup` | Sidebar | Chuyển sang Full AI mode |
| `/shortlists` | "Tạo shortlist" link | Cần có shortlist trước |
| `/library` | "Thêm tài nguyên" link | Cần profile/email/proxy |
| `/history` | Sau khi job xong | Xem kết quả chi tiết |

---

## Màn 09 — Playbooks `/playbooks`

### Mục tiêu
Quản lý kho script (playbooks) cho từng affiliate platform. Xem cấu trúc steps, theo dõi tỷ lệ thành công, phê duyệt playbook được LLM tạo ra, lưu trữ playbook không dùng nữa.

### Tính năng

| # | Tính năng | Mô tả chi tiết |
|---|-----------|---------------|
| 1 | **Danh sách playbooks** | Hiển thị card: tên, platform, category, trạng thái, stats (total/success/fail runs) |
| 2 | **Status badge** | active (xanh) / needs_llm (vàng — cần review) / archived (xám) |
| 3 | **Xem steps** | Expand card → bảng steps: action, selector/url, value, label, optional |
| 4 | **Đổi tên** | Inline edit tên playbook |
| 5 | **Lưu trữ (archive)** | Đưa playbook vào trạng thái archived — không dùng nhưng vẫn giữ lịch sử |
| 6 | **Khôi phục (restore)** | Đưa playbook archived → active |
| 7 | **Phê duyệt LLM** | Khi playbook ở trạng thái `needs_llm`: bấm "Approve" → tạo LLM re-record job để học lại script |
| 8 | **Stats trực quan** | Consecutive fails, tỷ lệ thành công |

### Trạng thái playbook

```
active ──(2 consecutive fails)──► needs_llm ──(approve + LLM re-record)──► active
  │                                                                           ▲
  └──(archive button)──► archived ──(restore button)─────────────────────────┘
```

### Luồng hoạt động

```
Vào /playbooks
  ↓
Xem danh sách:
  ├─ active   → playbook đang dùng tốt → không cần làm gì
  ├─ needs_llm → playbook thất bại 2+ lần
  │               → click Expand → xem step nào đang lỗi
  │               → click "Approve LLM" → trigger re-record job
  │               → job LLM chạy lại signup trên site thật → học steps mới
  │               → playbook cập nhật selector mới → trở về active
  └─ archived → không dùng → có thể restore nếu cần
```

### Liên kết giữa các màn

| Điểm đến | Trigger | Ghi chú |
|----------|---------|---------|
| `/script-signup` | Sidebar | Chạy signup dùng playbook |
| `/signup` | LLM re-record job | AI agent chạy để học lại script |
| `/history` | Sidebar | Xem kết quả các lần chạy |

---

## Màn 10 — Lịch sử đăng ký `/history`

### Mục tiêu
Xem toàn bộ kết quả các lần đăng ký affiliate — cả Full AI lẫn Script mode. Tra cứu, debug khi thất bại, xuất báo cáo.

### Tính năng

| # | Tính năng | Mô tả chi tiết |
|---|-----------|---------------|
| 1 | **Stats tổng quan** | Tổng, Thành công, Thất bại, Đang chờ verify email — có thể filter theo khoảng ngày |
| 2 | **Danh sách kết quả phân trang** | Mỗi row: Program, Profile, Status, Message, Steps, Duration, Ngày chạy |
| 3 | **Filter đa chiều** | Theo trạng thái (success/failed/pending_verify/captcha), theo ngày, theo batch |
| 4 | **Xem screenshot** | Click biểu tượng ảnh → modal hiện screenshot màn hình lúc kết thúc |
| 5 | **Xem URL cuối** | Link "URL" → mở trang affiliate site đã đăng ký |
| 6 | **Export CSV** | Xuất lịch sử đang filter ra file CSV |
| 7 | **Expand row** | Click row → xem message đầy đủ, email dùng, proxy dùng, snapshot profile |
| 8 | **Infinite scroll / pagination** | Duyệt qua lịch sử nhiều trang |

### Status map

| Status | Ý nghĩa | Action cần làm |
|--------|---------|---------------|
| `success` | Đăng ký thành công, chờ phê duyệt từ affiliate | Theo dõi email approve |
| `pending_verify` | Cần xác thực email | Xem email inbox xác nhận |
| `failed` | Thất bại — xem message | Debug, thử lại với LLM |
| `captcha` | Bị chặn bởi CAPTCHA không giải được | Kiểm tra CapSolver balance |
| `error` | Lỗi hệ thống | Xem log |

### Luồng hoạt động

```
Vào /history
  ↓
Filter theo ngày hôm nay + status = failed → xem các job thất bại
  ↓
Click expand row → đọc message lỗi
  ├─ "Selector thất bại" → sang /playbooks xem step đó → sửa
  ├─ "CAPTCHA không giải được" → kiểm tra CapSolver balance
  ├─ "Max steps" → LLM không xong trong 60 bước → thêm instruction
  └─ "pending_verify" → check email inbox
  ↓
Click screenshot icon → xem browser đang ở đâu khi kết thúc
  ↓
Quyết định: re-run thủ công hay bỏ qua
```

### Liên kết giữa các màn

| Điểm đến | Trigger | Ghi chú |
|----------|---------|---------|
| `/signup` | Sidebar → re-run | Chạy lại program thất bại |
| `/script-signup` | Sidebar → re-run script | Chạy lại bằng script |
| `/playbooks` | Debug selector thất bại | Xem/sửa script |
| `/library` | Kiểm tra API key / proxy | Debug lỗi kỹ thuật |

---

## Màn 11 — Thư viện `/library`

### Mục tiêu
Kho tập trung quản lý **tất cả tài nguyên** cần thiết để chạy auto-signup: danh tính (profile), email (Gmail), IP (proxy), số điện thoại (SMS OTP), API key LLM, và system prompt (instructions). Không có library → không chạy signup được.

### Tính năng theo tab

#### Tab Profile

| # | Tính năng | Mô tả |
|---|-----------|-------|
| 1 | CRUD profile | Tạo/sửa/xóa danh tính ảo: họ tên (VN), quốc gia, niche, website, company, password, notes |
| 2 | Duplicate | Nhân đôi profile để tạo biến thể nhanh |
| 3 | Filter + search | Tìm theo tên, lọc theo niche/quốc gia |
| 4 | Tag niche | Gán nhiều niche cho 1 profile (vd: SaaS, Health, Finance) |

#### Tab Email

| # | Tính năng | Mô tả |
|---|-----------|-------|
| 1 | Thêm Gmail | Nhập address, App Password, TOTP secret, recovery email |
| 2 | Test IMAP | Kết nối IMAP thực tế → hiển thị số email trong inbox |
| 3 | IMAP status | Badge OK/fail theo kết quả test |

#### Tab Proxy

| # | Tính năng | Mô tả |
|---|-----------|-------|
| 1 | Thêm proxy | Form: host, port, username, password, label |
| 2 | Import hàng loạt | Paste định dạng `ip:port:user:pass` nhiều dòng |
| 3 | Test kết nối | Kiểm tra proxy hoạt động → hiển thị IP thực |

#### Tab SMS OTP

| # | Tính năng | Mô tả |
|---|-----------|-------|
| 1 | Thêm SMS profile | Combo: Provider (SMSPool/5sim/sms-activate) + Quốc gia + Service |
| 2 | Pre-flight check | Tự kiểm tra stock số điện thoại trước khi lưu |

#### Tab API Keys

| # | Tính năng | Mô tả |
|---|-----------|-------|
| 1 | Gemini keys | Thêm/xóa nhiều Gemini API key — masked display (4 đầu + 4 cuối) |
| 2 | OpenAI keys | Thêm/xóa GPT-4o keys |
| 3 | DeepSeek keys | Thêm/xóa DeepSeek keys |
| 4 | Test ping | Gọi API test xem key có valid không |
| 5 | Auto-rotate | Khi 1 key bị quota → tự dùng key tiếp theo trong danh sách |

#### Tab Hướng dẫn (Instruction)

| # | Tính năng | Mô tả |
|---|-----------|-------|
| 1 | Tạo instruction | Tên file + nội dung text (system prompt) |
| 2 | Editor inline | Soạn thảo trực tiếp trong browser |
| 3 | Xóa | Xóa instruction không dùng |
| 4 | Dùng nhiều | Chọn nhiều instruction → ghép nội dung theo thứ tự |

### Luồng hoạt động

```
Vào /library
  ↓
Tab Profile:
  → Tạo profile với thông tin đầy đủ (họ tên, niche, website, password)
  → Duplicate để tạo nhiều profile biến thể
Tab Email:
  → Thêm Gmail → Test IMAP → Badge OK
Tab Proxy (nếu có):
  → Import hàng loạt → Test kết nối
Tab API Keys:
  → Thêm Gemini/OpenAI key → Test ping → Badge OK
Tab SMS OTP (nếu cần số điện thoại):
  → Thêm profile SMSPool
  ↓
Khi đủ resources → sang /signup hoặc /script-signup
```

### Liên kết giữa các màn

| Điểm đến | Trigger | Ghi chú |
|----------|---------|---------|
| `/signup` | Sau khi setup xong | Dùng resources vừa tạo |
| `/script-signup` | Sau khi setup xong | Dùng resources vừa tạo |
| `/profiles/new` | Quick action từ Dashboard | Shortcut tạo profile |
| `/instructions` | Link từ sidebar | Shortcut quản lý instructions |

---

## Màn 12 — Google Ads Transparency `/ads-transparency`

### Mục tiêu
Tra cứu quảng cáo Google đang chạy của một brand trước khi đăng ký affiliate. Brand đang chạy nhiều ads = có ngân sách marketing = có khả năng trả hoa hồng → ưu tiên đăng ký.

### Tính năng

| # | Tính năng | Mô tả chi tiết |
|---|-----------|---------------|
| 1 | **Tìm kiếm** | Nhập tên brand hoặc domain → gọi SerpAPI → lấy ads đang chạy |
| 2 | **Danh sách ads** | Hiển thị: advertiser, format (text/image/video), platform, ngày chạy gần nhất, khu vực |
| 3 | **Lịch sử tìm kiếm** | Lưu các lần tra cứu để xem lại, không cần tìm lại |
| 4 | **Link xem gốc** | Click → mở Google Ads Transparency Center trực tiếp |

### Luồng hoạt động

```
Vào /ads-transparency
  ↓
Nhập tên brand (VD: "Streamable" hoặc "streamable.com")
  ↓
SerpAPI gọi Google Ads Transparency → trả danh sách ads
  ↓
Xem: brand có bao nhiêu ads? Gần đây nhất khi nào?
  ├─ Nhiều ads, gần đây → brand active → nên đăng ký affiliate
  └─ Ít/không có ads → brand ít marketing → cân nhắc ưu tiên thấp hơn
  ↓
Lưu kết quả trong lịch sử → không cần tra lại
```

### Liên kết giữa các màn

| Điểm đến | Trigger | Ghi chú |
|----------|---------|---------|
| `/programs` | Sau khi đánh giá → tìm program trong DB | Thêm vào shortlist nếu tiềm năng |
| `/shortlists` | Sau khi đánh giá | Thêm trực tiếp vào shortlist |

---

## Màn 13 — Thử nghiệm `/experiment`

### Mục tiêu
Sandbox chạy thử AI agent với bất kỳ tác vụ browser nào. Dùng để test prompt, debug hành vi agent, thử nghiệm cách xử lý form đặc biệt trước khi đưa vào production signup.

### Tính năng

| # | Tính năng | Mô tả chi tiết |
|---|-----------|---------------|
| 1 | **Free-form task** | Nhập bất kỳ task text nào → AI agent thực thi |
| 2 | **Chọn URL** | Chỉ định URL bắt đầu (hoặc để AI tự tìm) |
| 3 | **Xem kết quả** | HTML output hiển thị log từng bước của agent |
| 4 | **Screenshot** | Xem ảnh màn hình tại thời điểm kết thúc |

### Luồng hoạt động

```
Vào /experiment
  ↓
Nhập task (VD: "Mở streamable.firstpromoter.com, điền email test@test.com vào trường email")
  ↓
Bấm chạy → AI agent mở Chrome → thực thi
  ↓
Xem log realtime → debug prompt nếu agent đi sai hướng
  ↓
Khi OK → copy logic sang system prompt trong /library → instruction
```

### Liên kết giữa các màn

| Điểm đến | Trigger | Ghi chú |
|----------|---------|---------|
| `/library` | Sau khi tìm ra prompt tốt | Lưu instruction |
| `/signup` | Sau khi validate → chạy thật | Dùng instruction vừa test |

---

## Sơ đồ liên kết đầy đủ

```
                    ┌─────────────────────────────────────────────────────┐
                    │                   SIDEBAR NAV                        │
                    │  Tất cả màn đều có sidebar để navigate trực tiếp    │
                    └─────────────────────────────────────────────────────┘

LUỒNG DỮ LIỆU:

/sources ──────────► /jobs ──────────► /programs ──────────┐
                                           │                │
                                      /homepage             │
                                           │                ▼
                                      /programs ──────► /shortlists
                                                             │
                              ┌──────────────────────────────┤
                              │                              │
                              ▼                              ▼
                         /signup                    /script-signup
                    (Full AI Mode)                  (Script+AI Mode)
                              │                              │
                              └──────────┬───────────────────┘
                                         │
                                         ▼
                                    /history
                                         │
                              ┌──────────┴──────────┐
                              │                     │
                              ▼                     ▼
                        /playbooks            /signup (re-run)


LUỒNG HỖ TRỢ:

/library ──────────────────────────────────► /signup, /script-signup
  (profile, email, proxy,                    (cung cấp tất cả tài nguyên)
   api keys, instructions)

/ads-transparency ─────────────────────────► /programs, /shortlists
  (đánh giá brand)                           (thêm vào danh sách nếu tiềm năng)

/experiment ────────────────────────────────► /library
  (test prompt)                               (lưu instruction)

/playbooks ─────────────────────────────────► /script-signup
  (quản lý scripts)                           (script engine dùng playbooks)
```

---

## Ma trận phụ thuộc (Dependency Matrix)

> Màn A **cần** màn B = không thể hoàn thành flow của A nếu không có dữ liệu từ B

| Màn hình | Cần từ màn khác | Cung cấp cho màn khác |
|----------|----------------|----------------------|
| `/sources` | — | `/jobs`, `/programs` |
| `/jobs` | `/sources` | `/programs` |
| `/programs` | `/jobs` | `/shortlists`, `/homepage` |
| `/homepage` | `/programs` | `/programs` (cập nhật URL) |
| `/shortlists` | `/programs` | `/signup`, `/script-signup` |
| `/signup` | `/shortlists` + `/library` | `/history` |
| `/script-signup` | `/shortlists` + `/library` + `/playbooks` | `/history` |
| `/playbooks` | (tự tạo từ job LLM hoặc tay) | `/script-signup` |
| `/history` | `/signup` hoặc `/script-signup` | — |
| `/library` | — | `/signup`, `/script-signup` |
| `/ads-transparency` | — | `/shortlists` (gián tiếp) |
| `/experiment` | — | `/library` (gián tiếp) |
| `/dashboard` | Tất cả | — (read-only tổng hợp) |

---

*Internal — MIC ACE · v1.0 · 11/06/2026*
