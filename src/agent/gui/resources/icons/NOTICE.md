# Nguồn gốc icon

SVG trong thư mục này lấy nguyên văn từ package `lucide-static@0.462.0`
(https://lucide.dev), pin đúng version để tái lập được — không sửa path/stroke
bên trong file gốc, chỉ đổi `stroke` thành `currentColor` là hành vi mặc định
sẵn có của Lucide (không phải do dự án chỉnh sửa).

Giấy phép: ISC (xem `LICENSE` cùng thư mục) — cho phép dùng/sao chép/sửa/phân
phối tự do miễn giữ nguyên notice bản quyền + giấy phép, đúng lý do file
`LICENSE` này được đóng gói cùng SVG trong wheel/sdist.

## Đổi tên so với plan gốc

`docs/gui_implementation_plan.md` (Phase 2) và `docs/ui_ux_spec.md` (mục 6.2)
liệt kê file `alert-triangle.svg`. Lucide đã đổi tên icon này thành
`triangle-alert` kể từ một bản phát hành gần đây (0.462.0 không còn
`alert-triangle.svg`, chỉ còn `triangle-alert.svg` — hình dạng SVG giống hệt,
chỉ tên file khác). Repo này lưu icon với đúng tên `alert-triangle.svg` như
tài liệu yêu cầu (để `IconManager`/call site khớp tài liệu), nhưng nội dung
lấy từ `triangle-alert.svg` phía upstream.
