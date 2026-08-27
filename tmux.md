Đây là một tài liệu ngắn gọn về cách quản lý session trong **tmux**:

---

## 📌 Tạo session mới
- Tạo session có tên:
  ```bash
  tmux new -s mysession
  ```
  → Session tên `mysession` sẽ được tạo, bạn có thể chạy lệnh bên trong.

- Tạo session mặc định (không tên):
  ```bash
  tmux
  ```
  → tmux sẽ tự đặt số thứ tự (0, 1, 2…).

---

## 📌 Detach và xem lại session
- **Detach** khỏi session nhưng giữ tiến trình chạy:
  - Nhấn `Ctrl + b`, sau đó nhấn `d`.

- **Liệt kê tất cả session**:
  ```bash
  tmux ls
  ```

- **Attach lại một session**:
  ```bash
  tmux attach -t mysession
  ```
  hoặc nếu là session số:
  ```bash
  tmux attach -t 2
  ```

---

## 📌 Xoá session
- Xoá một session cụ thể:
  ```bash
  tmux kill-session -t mysession
  ```
  hoặc
  ```bash
  tmux kill-session -t 2
  ```

- Xoá tất cả session trừ session hiện tại:
  ```bash
  tmux kill-session -a
  ```

- Xoá toàn bộ tmux server (mọi session):
  ```bash
  tmux kill-server
  ```

---

👉 Tóm lại: **tạo session bằng `tmux new -s <tên>`, detach bằng `Ctrl+b d`, xem lại bằng `tmux ls` + `tmux attach`, và xoá bằng `tmux kill-session`.**