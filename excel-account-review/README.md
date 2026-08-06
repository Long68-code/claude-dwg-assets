# Rà soát Account theo file chuẩn — Excel Review (app HTML một-file)

Ứng dụng chạy **hoàn toàn trong trình duyệt** (không cần server, không cài đặt).
Nhận **2 file Excel** — file cần rà soát + file chuẩn (Knowledge) — đối chiếu
**Description → Account**, tô màu cảnh báo và xuất ra file mới `*_reviewed.xlsx`.
Dữ liệu **không rời khỏi máy**.

## Dùng thế nào
1. Mở `excel-account-review.html` bằng trình duyệt (double-click).
2. Thả **① file cần rà soát** và **② file chuẩn**.
3. Bấm **“Rà soát & tải file kết quả”** → file `*_reviewed.xlsx` tự tải về,
   đồng thời bảng cảnh báo hiện ngay trên trang.

## Cách chấm màu
File gốc được **giữ nguyên 100%** (công thức, định dạng số/ngày, độ rộng cột,
font, merge). Tác động duy nhất là **tô nền**, theo 2 tầng:

| | Cả dòng (nền nhạt) | Đúng ô Account (nền đậm) |
|---|---|---|
| **ĐỎ** – sai rõ so với chuẩn CONFIRMED | `FBE4E6` | `FFC7CE` |
| **VÀNG** – nghi ngờ | `FFF8E1` | `FFEB9C` |

Dòng đúng: **không tô gì**. Dòng vừa đỏ vừa vàng → ưu tiên **đỏ**.

**Khi nào ĐỎ:** description khớp một quy tắc chuẩn `CONFIRMED` nhưng Account trong
file **khác** Account chuẩn.

**Khi nào VÀNG:** (nguyên tắc “không chắc thì vàng, không bao giờ đoán rồi tô đỏ”)
- Không có quy tắc chuẩn nào khớp description.
- Quy tắc khớp đang ở trạng thái **`TO CONFIRM`** (chờ Director duyệt).
- Description khớp **nhiều quy tắc trỏ Account khác nhau** (chuẩn mâu thuẫn).
- Ô Account **trống**.

**Bỏ qua (không tô):** dòng có Account nằm trong sheet **“No Mapping Value”** của
file chuẩn (vd. *Sales of Product Income, Services, Inventory Asset* — description
ở đây chỉ là ghi chú giao hàng/mã bưu chính, cố ý không map); dòng header nhóm /
subtotal / footer; dòng thiếu cả Description lẫn Account.

## Sheet “ĐIỀU CHỈNH” (đặt đầu file kết quả)
- Đầu sheet: tổng ô ĐỎ, tổng ô VÀNG, phạm vi đã quét, số quy tắc chuẩn, chú giải màu.
- Bảng phẳng, mỗi ô có vấn đề = 1 dòng:
  `Mức độ | Sheet | Dòng | Ô | Description | Account hiện tại | Account đề xuất | Lý do | Recommend`.
- Sắp xếp **ĐỎ trước VÀNG**, gom theo cụm lỗi giống nhau (cùng merchant/quy tắc).
- Bật **freeze panes** ở dòng tiêu đề và **autofilter** cho cả bảng.
- “Account đề xuất” lấy **nguyên văn từ file chuẩn**; không có căn cứ → `(chưa có chuẩn)`.

## Tự nhận diện cấu trúc file
- **File cần rà soát:** tự dò dòng header (chứa cột `Account` + `Description`/`Memo`),
  xác định cột khóa (Description) và cột giá trị (Account).
- **File chuẩn:** đọc các sheet có cột `... to match` + `Account` (+ `Status`);
  đọc sheet `No Mapping Value` để lấy danh sách Account cần bỏ qua.

## Tùy chọn
- ☑ *Tô vàng cả những dòng thiếu Description* — mặc định **tắt** (không có key để dò).

## Phát triển / build lại
Mã nguồn tách trong `src/` để dễ sửa:
- `src/part1.html` — giao diện + CSS.
- `src/review-core.js` — logic đối chiếu & tô màu (dùng chung Node + trình duyệt).
- `src/app.js` — glue UI (đọc file, gọi core, xuất & tải).

Build lại file một-file (nhúng ExcelJS):
```bash
bash build.sh      # cần npm để lấy exceljs@4.4.0
```

## Ghi chú kỹ thuật
- Thư viện: **ExcelJS 4.4.0** (bản browser UMD, nhúng sẵn — chạy offline).
- ExcelJS chia sẻ chung một object style giữa các ô cùng định dạng; gán `.fill`
  trực tiếp sẽ **lem màu ra toàn bộ**. `review-core.js` khắc phục bằng cách cấp
  **style mới cho từng ô** trước khi tô (`setFill`).
- Giới hạn: nếu file gốc có biểu đồ, hình ảnh nhúng, pivot table hoặc macro
  (`.xlsm`), ExcelJS có thể không giữ lại được các thành phần đó khi ghi lại.
  Với bảng dữ liệu kế toán thuần thì an toàn.
