# Báo cáo PBL3 — RIPv2 và DHCP trên GNS3

Thư mục này chứa báo cáo LaTeX và PDF của bài lab PBL3, tái triển khai trên GNS3 với
FRRouting trong Docker thay cho Cisco IOS.

## Tệp chính

- `report.tex` — nguồn LaTeX (XeLaTeX, polyglossia tiếng Việt).
- `report.pdf` — bản PDF đã build, 24 trang.
- `build.sh` — build bằng `latexmk -xelatex`.
- `images/` — sơ đồ, ảnh GNS3 và ảnh terminal được render từ log thật.
- `artifacts/` — log/config được dùng làm minh chứng trong báo cáo.
- `tools/render_terminal.py` — chuyển log terminal thành ảnh PNG.

## Build

Yêu cầu: TeX Live với XeLaTeX, `latexmk`, các gói `fontspec`, `polyglossia`,
`tocloft`, `tikz`, `listings`; font DejaVu Serif/Sans và Liberation Mono.

```bash
./build.sh
```

Kết quả: `report.pdf`. Nếu cần build lại sau khi sửa `report.tex`, chạy lại lệnh trên;
`latexmk` tự chạy đủ số pass cho mục lục và tham chiếu.

## Ghi chú

- Báo cáo dùng FRRouting 8.4 trong container, không dùng image Cisco IOSv.
- DHCP server dùng Alpine + BusyBox `udhcpd`; client dùng VPCS 0.8.5.
- Số liệu, bảng định tuyến, DHCP và ping trong báo cáo lấy từ các log trong
  `artifacts/` và `../logs/`, không phải số liệu mô phỏng.
