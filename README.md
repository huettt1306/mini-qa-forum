# Mini QA Forum

Diễn đàn hỏi đáp cho trường học, viết bằng Flask và MongoDB Atlas. Học sinh và giáo viên đăng nhập, xem lớp môn học hoặc chủ đề chung, đăng câu hỏi và phản hồi. Giáo viên có thể duyệt hoặc hủy duyệt câu trả lời đúng. `seed.py` tạo dữ liệu mẫu cho 1 quản trị viên, 70 giáo viên, 1.500 học sinh, 210 lớp môn học, 5 chủ đề chung, 1.000 bài viết, 4.000 phản hồi.

## Chạy ứng dụng

Cài thư viện trong `requirements.txt`, đặt `MONGO_URI` trong `.env` ở thư mục gốc (hoặc biến môi trường), đặt `SECRET_KEY` khi deploy, rồi chạy:

```bash
python3 api/index.py
```

Để nạp lại dữ liệu mẫu:

```bash
python3 seed.py
```

**Lưu ý:** `seed.py` xóa các collection cũ trước khi nạp dữ liệu.

Index được quản lý riêng. Sau khi seed, chạy `python3 ensure_indexes.py` để tạo các index còn thiếu. Script này không xóa dữ liệu. Có thể đo truy vấn trước và sau khi chạy để so sánh hiệu năng.

Ứng dụng hỗ trợ tìm và lọc bài theo không gian, dashboard admin, báo cáo giáo viên và đổi mật khẩu. Bài viết và phản hồi chỉ nhận văn bản; không lưu ảnh.

## Unit test

```bash
python3 -m unittest discover -s tests -v
```

Test dùng URI giả và mock các thao tác dữ liệu cần thiết. Test không đọc hoặc kết nối database trong `.env`.

## Thực nghiệm index

`benchmark/benchmark.py` reset `school_forum` một lần, nạp lũy tiến 5.000 → 50.000 → 100.000 document (giữ `_id` và cho `createdAt` tăng liên tục), đo trước/sau 9 index tại mỗi mốc, rồi reset về seed mặc định. Chỉ chạy khi dữ liệu trong database đều là giả lập.

```bash
venv/bin/python -u benchmark/benchmark.py
```

Mặc định mỗi truy vấn chạy 2 lượt làm nóng, 12 lượt `explain` và 12 lượt thật. Có thể chọn mốc và truy vấn: `--scales 100000 --queries regex_hot --server-repeats 10 --client-repeats 10`. Chọn riêng mốc 100.000 vẫn nạp lũy tiến qua 5.000 và 50.000 nhưng không đo ở hai mốc đó. Lượt chạy một phần được lưu riêng dưới `benchmark_results/runs/`. `--keep-data` giữ bộ benchmark sau khi chạy; khi đó `--reuse-existing --phase after --scales 100000 --queries regex_hot` chỉ đo trên dữ liệu có đủ index. Chế độ mặc định luôn khôi phục seed phát triển.

Bảng và biểu đồ dùng trung vị thời gian server từ nhiều lượt `explain`; thời gian `perf_counter` phía ứng dụng được ghi riêng. Kết quả gồm `results.json`, `benchmark_summary.md`, `benchmark_table.csv`, `benchmark_chart.svg` và `benchmark_report.md` trong `benchmark_results/`. Có thể tạo riêng một bộ dữ liệu bằng `venv/bin/python seed.py --scale 50000`; sau đó chạy `venv/bin/python ensure_indexes.py` nếu cần index.
