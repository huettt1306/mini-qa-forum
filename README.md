# Mini QA Forum

Diễn đàn hỏi đáp cho trường học, viết bằng Flask và MongoDB Atlas. Học sinh và giáo viên đăng nhập, xem lớp môn học hoặc chủ đề chung, đăng câu hỏi và phản hồi. Giáo viên có thể duyệt hoặc hủy duyệt câu trả lời đúng. Tác giả bài viết nhận thông báo khi có phản hồi; người trả lời nhận thông báo khi được duyệt. Trang tự cập nhật số thông báo chưa đọc khi tải và khi quay lại tab. `seed.py` tạo dữ liệu mẫu cho 1 quản trị viên, 70 giáo viên, 1.500 học sinh, 210 lớp môn học, 5 chủ đề chung, 1.000 bài viết, 4.000 phản hồi và 4.400 thông báo.

## Chạy ứng dụng

Cài thư viện trong `requirements.txt`, đặt `MONGO_URI` trong `.env` ở thư mục gốc (hoặc biến môi trường), đặt `SECRET_KEY` khi deploy, rồi chạy:

```bash
python api/index.py
```

Để nạp lại dữ liệu mẫu:

```bash
python seed.py
```

**Lưu ý:** `seed.py` xóa các collection cũ trước khi nạp dữ liệu.

Index được quản lý riêng. Sau khi seed, chạy `python ensure_indexes.py` để tạo các index còn thiếu. Script này không xóa dữ liệu. Có thể đo truy vấn trước và sau khi chạy để so sánh hiệu năng.

Ứng dụng hỗ trợ tìm và lọc bài theo không gian, dashboard admin, báo cáo giáo viên và đổi mật khẩu. Bài viết và phản hồi chỉ nhận văn bản; không lưu ảnh.

## Unit test

```bash
python -m unittest discover -s tests -v
```

Test dùng URI giả và mock các thao tác dữ liệu cần thiết. Test không đọc hoặc kết nối database trong `.env`.
