import os
import random
from datetime import datetime, timedelta
from pathlib import Path

import bcrypt
from bson.objectid import ObjectId
from pymongo import MongoClient


# --- 1. HÀM CẤU HÌNH KẾT NỐI MONGODB ---
def load_mongo_uri():
    uri = os.environ.get("MONGODB_URI") or os.environ.get("MONGO_URI")
    if uri:
        return uri

    env_file = Path(__file__).resolve().parent / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, separator, value = line.partition("=")
            if separator and key.strip() in ("MONGODB_URI", "MONGO_URI"):
                uri = value.strip().strip("\"'")
                if uri:
                    return uri

    raise RuntimeError(
        "LỖI: Chưa cấu hình MONGODB_URI/MONGO_URI trong biến môi trường hoặc file .env!"
    )


MONGO_URI = load_mongo_uri()
client = MongoClient(MONGO_URI)
db = client["school_forum"]

# --- 2. KHO TỪ ĐIỂN TÊN TIẾNG VIỆT ---
HO_LIST = [
    "Nguyễn", "Trần", "Lê", "Phạm", "Hoàng", "Huỳnh", "Phan",
    "Vũ", "Võ", "Đặng", "Bùi", "Đỗ", "Hồ", "Ngô", "Dương", "Lý"
]
DEM_NAM = ["Văn", "Đức", "Đình", "Minh", "Quang", "Hải", "Tuấn", "Hữu", "Thành", "Bảo", "Quốc", "Tiến"]
TEN_NAM = ["Anh", "Dũng", "Cường", "Huy", "Hùng", "Hoàng", "Nam", "Long", "Quân", "Sơn", "Thắng", "Tùng", "Tuấn", "Phong", "Kiên", "Khôi", "Minh", "Phúc", "Thịnh", "Đạt", "Khoa"]

DEM_NU = ["Thị", "Ngọc", "Thu", "Thùy", "Kim", "Thanh", "Phương", "Khánh", "Mai", "Như"]
TEN_NU = ["Anh", "Châu", "Diệp", "Dung", "Duyên", "Giang", "Hà", "Hạnh", "Hoa", "Hương", "Huyền", "Lan", "Linh", "Mai", "Nga", "Ngân", "Ngọc", "Phương", "Quỳnh", "Thảo", "Trang", "Vân", "Yến"]

def tao_ho_ten():
    if random.choice(["NAM", "NU"]) == "NAM":
        return f"{random.choice(HO_LIST)} {random.choice(DEM_NAM)} {random.choice(TEN_NAM)}"
    return f"{random.choice(HO_LIST)} {random.choice(DEM_NU)} {random.choice(TEN_NU)}"

# --- 3. DỌN SẠCH CSDL CŨ ---
print("=" * 60)
print("1. Đang dọn dẹp sạch toàn bộ collections cũ...")
for col in db.list_collection_names():
    db.drop_collection(col)

# --- 4. TẠO HASH MẬT KHẨU ---
print("2. Đang sinh mã băm mật khẩu bảo mật (bcrypt)...")
admin_pw_hash = bcrypt.hashpw(b"admin", bcrypt.gensalt(rounds=8)).decode("utf-8")
user_pw_hash = bcrypt.hashpw(b"123456", bcrypt.gensalt(rounds=8)).decode("utf-8")

# --- 5. TẠO TÀI KHOẢN ADMIN ---
print("3. Đang khởi tạo tài khoản Quản trị viên (ADMIN)...")
admin_doc = {
    "_id": ObjectId("000000000000000000000001"),
    "username": "admin",
    "password": admin_pw_hash,
    "fullName": "Quản trị viên Hệ thống",
    "role": "ADMIN",
    "class": None,
    "createdAt": datetime.utcnow()
}
db.users.insert_one(admin_doc)

# --- 6. TẠO 70 GIÁO VIÊN (7 MÔN CỐT LÕI x 10 GV/MÔN) ---
CORE_SUBJECTS = ["Toán", "Ngữ văn", "Tiếng Anh", "Lịch sử", "Vật lí", "Hóa học", "Tin học"]
print("4. Đang tạo 70 Giáo viên phụ trách 7 môn cốt lõi...")

teachers = []
teachers_by_subject = {mon: [] for mon in CORE_SUBJECTS}
gv_counter = 1

for mon in CORE_SUBJECTS:
    for _ in range(10):
        t_id = ObjectId(f"{gv_counter + 1:024x}")
        t_name = tao_ho_ten()
        t_doc = {
            "_id": t_id,
            "username": f"gv{gv_counter:04d}",
            "password": user_pw_hash,
            "fullName": t_name,
            "role": "TEACHER",
            "class": None,
            "subject": mon,
            "createdAt": datetime.utcnow()
        }
        teachers.append(t_doc)
        teachers_by_subject[mon].append(t_doc)
        gv_counter += 1

db.users.insert_many(teachers)
print(f"   -> Đã lưu 70 Giáo viên (gv0001 -> gv0070).")

# --- 7. TẠO 30 LỚP HÀNH CHÍNH VÀ 1.500 HỌC SINH ---
print("5. Đang tạo 30 lớp hành chính và 1.500 Học sinh (50 em/lớp)...")
classes = [f"{grade}A{room}" for grade in [10, 11, 12] for room in range(1, 11)]

students = []
students_by_class = {c: [] for c in classes}
hs_counter = 1

for class_name in classes:
    for _ in range(50):
        s_id = ObjectId(f"{hs_counter + 1000:024x}")
        s_name = tao_ho_ten()
        s_doc = {
            "_id": s_id,
            "username": f"hs{hs_counter:05d}",
            "password": user_pw_hash,
            "fullName": s_name,
            "role": "STUDENT",
            "class": class_name,
            "createdAt": datetime.utcnow()
        }
        students.append(s_doc)
        students_by_class[class_name].append(s_doc)
        hs_counter += 1

db.users.insert_many(students)
print(f"   -> Đã lưu 1.500 Học sinh (hs00001 -> hs01500).")

# --- 8. PHÂN CÔNG GIẢNG DẠY (210 LỚP MÔN HỌC) ---
# Quy tắc: Mỗi giáo viên dạy đúng 1 lớp 10, 1 lớp 11 và 1 lớp 12 tương ứng môn mình
print("6. Đang phân công giáo viên: Mỗi GV phụ trách đúng 1 lớp 10, 1 lớp 11, 1 lớp 12...")
subject_classes = []

classes_k10 = [f"10A{i}" for i in range(1, 11)]
classes_k11 = [f"11A{i}" for i in range(1, 11)]
classes_k12 = [f"12A{i}" for i in range(1, 11)]

for mon in CORE_SUBJECTS:
    mon_teachers = teachers_by_subject[mon]
    # Trộn ngẫu nhiên danh sách lớp để gán 1-1 với 10 giáo viên
    rand_k10 = random.sample(classes_k10, 10)
    rand_k11 = random.sample(classes_k11, 10)
    rand_k12 = random.sample(classes_k12, 10)

    for i in range(10):
        t = mon_teachers[i]
        # Lớp 10
        subject_classes.append({
            "subjectName": f"{mon} 10",
            "targetClass": rand_k10[i],
            "teacherId": t["_id"],
            "teacherName": t["fullName"]
        })
        # Lớp 11
        subject_classes.append({
            "subjectName": f"{mon} 11",
            "targetClass": rand_k11[i],
            "teacherId": t["_id"],
            "teacherName": t["fullName"]
        })
        # Lớp 12
        subject_classes.append({
            "subjectName": f"{mon} 12",
            "targetClass": rand_k12[i],
            "teacherId": t["_id"],
            "teacherName": t["fullName"]
        })

db.subject_classes.insert_many(subject_classes)
print(f"   -> Đã khởi tạo {len(subject_classes)} lớp môn học (210 lớp môn).")

# --- 9. TẠO CÁC CHỦ ĐỀ MỞ (TOPICS) VÀ CHỈ ĐỊNH GIÁO VIÊN PHỤ TRÁCH ---
print("7. Đang tạo các Chủ đề mở toàn trường...")
open_topics_def = [
    {
        "title": "Góc Ôn thi Học sinh Giỏi cấp Tỉnh",
        "description": "Nơi trao đổi tài liệu, bài toán và phương pháp giải nâng cao cho các đội tuyển chuyên đề.",
        "mon": "Toán"
    },
    {
        "title": "CLB Tiếng Anh (English Speaking & Grammar)",
        "description": "Thảo luận ngữ pháp, luyện bài đọc hiểu THPTQG và chia sẻ mẹo làm đề thi.",
        "mon": "Tiếng Anh"
    },
    {
        "title": "Góc Sáng tạo Khoa học Kỹ thuật & STEM",
        "description": "Diễn đàn trao đổi thuật toán C++, lập trình vi điều khiển và mô hình thực nghiệm.",
        "mon": "Tin học"
    },
    {
        "title": "Không gian Cảm thụ Văn học & Đọc sách",
        "description": "Chia sẻ phương pháp làm bài nghị luận văn học, nghị luận xã hội và giới thiệu sách hay.",
        "mon": "Ngữ văn"
    },
    {
        "title": "Góc Vật lí Ứng dụng & Bài tập Đồ thị",
        "description": "Trao đổi các bài toán đồ thị dao động cơ, sóng ánh sáng và thí nghiệm ảo.",
        "mon": "Vật lí"
    }
]

topics = []
for t_def in open_topics_def:
    lead_teacher = random.choice(teachers_by_subject[t_def["mon"]])
    topics.append({
        "title": t_def["title"],
        "description": t_def["description"],
        "teacherId": lead_teacher["_id"],
        "teacherName": lead_teacher["fullName"],
        "createdAt": datetime.utcnow() - timedelta(days=10)
    })

db.topics.insert_many(topics)
print(f"   -> Đã tạo {len(topics)} chủ đề mở toàn trường.")

# --- 10. TẠO DỮ LIỆU BÀI VIẾT VÀ BÌNH LUẬN MẪU (TEST USE CASES) ---
print("8. Đang tạo bài viết mẫu và lời giải (ghép chuẩn học sinh theo lớp)...")

KHO_CAU_HOI = {
    "Toán": [
        ("Cho em hỏi bài toán tìm m để hàm số bậc hai đồng biến ạ", "Tìm tất cả các giá trị thực của tham số m để parabol y = x^2 - 2(m+1)x + 3 đồng biến trên khoảng (2; +vô cùng)...", "Hoành độ đỉnh parabol là x = m + 1. Để hàm số đồng biến trên (2; +vô cùng) thì đỉnh phải nằm bên trái khoảng, tức là m + 1 <= 2 <=> m <= 1 nhé em."),
        ("Cách giải phương trình lượng giác sin2x + cosx = 0", "Có ai có cách giải nhanh phương trình này không ạ, em chuyển đổi mãi mà vướng nghiệm âm...", "Em biến đổi sin2x = 2sinx*cosx, sau đó đặt cosx làm nhân tử chung: cosx*(2sinx + 1) = 0 là ra ngay hai họ nghiệm."),
        ("Hỏi về định lý Cosin trong tam giác bất kỳ", "Khi biết độ dài 3 cạnh a=5, b=7, c=8 thì tính góc A như thế nào cho chính xác nhất ạ?", "Dùng công thức hệ quả: cosA = (b^2 + c^2 - a^2)/(2bc) = (49 + 64 - 25)/(2*7*8) = 88/112 = 11/14, sau đó bấm Shift Cos nhé.")
    ],
    "Ngữ văn": [
        ("Xin dàn ý bài nghị luận xã hội về đức tính khiêm tốn", "Mọi người có thể gợi ý cho em các luận điểm chính và dẫn chứng thực tế cho bài này không?", "Mở bài: Khái niệm khiêm tốn. Thân bài: Biểu hiện (không tự cao, biết lắng nghe), Dẫn chứng (Bác Hồ, các nhà khoa học lớn), Phản đề (thói tự kiêu), Bài học hành động."),
        ("Cảm nhận về hình tượng sông Hương vùng thượng nguồn", "Trong tác phẩm Ai đã đặt tên cho dòng sông, đoạn thượng lưu miêu tả tính cách gì đặc trưng ạ?", "Tác giả miêu tả sông Hương thượng nguồn như 'bản trường ca của rừng già', vừa rầm rộ dữ dội vừa dịu dàng say đắm, mang vẻ đẹp của một cô gái Di-gan phóng khoáng và man dại.")
    ],
    "Tiếng Anh": [
        ("Phân biệt thì Present Perfect và Past Simple", "Khi nào thì dùng thì Hiện tại hoàn thành, khi nào dùng Quá khứ đơn vậy mọi người?", "Past Simple dùng khi hành động đã chấm dứt hoàn toàn trong quá khứ và có mốc thời gian cụ thể (yesterday, in 2020). Present Perfect dùng khi hành động xảy ra không rõ thời gian hoặc kết quả còn ảnh hưởng đến hiện tại."),
        ("Hỏi về cấu trúc đảo ngữ với 'Not only... but also'", "Câu này viết lại đảo ngữ thế nào: 'He is not only rich but he is also kind'?", "Đưa Not only lên đầu câu rồi đảo trợ động từ: 'Not only is he rich, but he is also kind.'")
    ],
    "Tin học": [
        ("Lỗi mảng vượt quá chỉ số trong C++ (Segmentation Fault)", "Code của em chạy n <= 100 thì đúng nhưng n = 100000 thì báo lỗi runtime, ai giúp em với ạ!", "Mảng khai báo cục bộ bên trong hàm main() dùng bộ nhớ Stack bị tràn đấy. Em hãy khai báo mảng tĩnh ra ngoài biến toàn cục (global) hoặc cấp phát động bằng vector nhé."),
        ("Thuật toán tìm kiếm nhị phân (Binary Search)", "Điều kiện tiên quyết để áp dụng tìm kiếm nhị phân trên mảng là gì ạ?", "Mảng hoặc danh sách bắt buộc phải được sắp xếp theo thứ tự tăng dần hoặc giảm dần trước khi áp dụng thuật toán.")
    ],
    "Vật lí": [
        ("Cách tính chu kỳ con lắc đơn khi thay đổi chiều dài", "Nếu tăng chiều dài dây treo lên 4 lần thì chu kỳ dao động tăng hay giảm mấy lần ạ?", "Công thức T = 2*pi*sqrt(l/g). Chiều dài l tăng 4 lần thì căn bậc hai của l tăng 2 lần, do đó chu kỳ T tăng 2 lần em nhé."),
        ("Định luật bảo toàn cơ năng trong rơi tự do", "Bỏ qua sức cản không khí thì đại lượng nào không đổi trong suốt quá trình rơi?", "Cơ năng (tổng động năng và thế năng) là đại lượng được bảo toàn không đổi.")
    ],
    "Hóa học": [
        ("Cân bằng phương trình phản ứng", "Làm sao cân bằng phản ứng Fe + O2 -> Fe2O3?", "Phương trình cân bằng là 4Fe + 3O2 -> 2Fe2O3.")
    ],
    "Lịch sử": [
        ("Ý nghĩa của Cách mạng tháng Tám", "Vì sao Cách mạng tháng Tám năm 1945 được xem là bước ngoặt của lịch sử Việt Nam?", "Cách mạng đã lật đổ ách thống trị thực dân, phong kiến và mở ra thời kỳ độc lập dân tộc.")
    ]
}

# Phản hồi chưa được duyệt dùng lời trao đổi chung, không giả làm lời giải đúng.
KHO_PHAN_HOI_CHUA_DUYET = [
    "Mình cũng đang tắc ở chỗ này.",
    "Mình đọc đề mấy lần mà vẫn chưa biết bắt đầu từ đâu.",
    "Bạn nào hiểu đoạn này có thể giải thích thêm không?",
    "Mình cũng đang chờ thầy cô hướng dẫn phần này.",
    "Có bạn nào tìm được tài liệu liên quan chưa?",
    "Mình thử làm rồi nhưng chưa chắc cách hiểu của mình đúng.",
    "Câu này khó hơn mình tưởng, có ai có gợi ý không?",
    "Mình cũng gặp đúng vướng mắc này.",
    "Bạn có thể nói rõ hơn chỗ bạn đang vướng không?",
    "Mình muốn xem thêm một cách tiếp cận khác.",
    "Có ai biết nên tra phần nào trong sách không?",
    "Mình đang xem lại kiến thức nền trước khi trả lời.",
    "Cảm ơn bạn đã đặt câu hỏi, mình cũng muốn tìm hiểu.",
    "Mình chưa có câu trả lời chắc chắn, để tìm hiểu thêm nhé.",
    "Có bạn nào đã trao đổi câu này với giáo viên chưa?",
    "Mình nghĩ cần làm rõ yêu cầu của đề trước.",
]

# --- A. Tạo đúng 1.000 bài viết và 4.000 phản hồi ---
# 250 bài chưa có phản hồi; 350 bài đang thảo luận x 8 phản hồi;
# 400 bài đã giải quyết x 3 phản hồi = 4.000 phản hồi.
states = ["unanswered"] * 250 + ["discussing"] * 350 + ["resolved"] * 400
random.shuffle(states)

# Phân bố bài viết đều trên 210 lớp môn và 5 chủ đề chung.
spaces = [("SUBJECT_CLASS", space) for space in subject_classes]
spaces += [("TOPIC", topic) for topic in topics]
random.shuffle(spaces)
teachers_by_id = {teacher["_id"]: teacher for teacher in teachers}

posts_to_insert = []
comments_to_insert = []
notifications_to_insert = []
for number, state in enumerate(states, start=1):
    space_type, space = spaces[(number - 1) % len(spaces)]
    teacher = teachers_by_id[space["teacherId"]]
    subject = teacher["subject"]
    question_title, question_content, correct_answer = random.choice(KHO_CAU_HOI[subject])
    candidates = (
        students_by_class[space["targetClass"]]
        if space_type == "SUBJECT_CLASS" else students
    )
    asker = random.choice(candidates)
    post_id = ObjectId()
    post_time = datetime.utcnow() - timedelta(days=random.randint(1, 30), hours=random.randint(0, 12))
    comment_count = {"unanswered": 0, "discussing": 8, "resolved": 3}[state]
    correct_ids = []

    # Chọn người trả lời khác người hỏi; với lớp môn, họ cùng lớp hành chính.
    answerers = random.sample(
        [student for student in candidates if student["_id"] != asker["_id"]],
        comment_count,
    )
    replies = iter(random.sample(
        KHO_PHAN_HOI_CHUA_DUYET,
        comment_count - int(state == "resolved"),
    ))
    for index, answerer in enumerate(answerers):
        comment_id = ObjectId()
        is_correct = state == "resolved" and index == 0
        if is_correct:
            correct_ids.append(comment_id)
        comment_time = post_time + timedelta(minutes=30 * (index + 1))
        comments_to_insert.append({
            "_id": comment_id,
            "postId": post_id,
            "spaceId": space["_id"],
            "author": {
                "userId": answerer["_id"],
                "fullName": answerer["fullName"],
                "role": "STUDENT",
            },
            "content": correct_answer if is_correct else next(replies),
            "isCorrect": is_correct,
            "createdAt": comment_time,
        })
        notifications_to_insert.append({
            "recipientId": asker["_id"], "actorId": answerer["_id"],
            "type": "NEW_COMMENT", "message": "Có phản hồi mới trong bài viết của bạn",
            "postId": post_id, "commentId": comment_id,
            "spaceId": space["_id"], "spaceType": space_type,
            "createdAt": comment_time, "isRead": False,
        })
        if is_correct:
            notifications_to_insert.append({
                "recipientId": answerer["_id"], "actorId": teacher["_id"],
                "type": "ANSWER_APPROVED", "message": "Câu trả lời của bạn đã được giáo viên duyệt",
                "postId": post_id, "commentId": comment_id,
                "spaceId": space["_id"], "spaceType": space_type,
                "createdAt": comment_time + timedelta(minutes=5), "isRead": False,
            })

    posts_to_insert.append({
        "_id": post_id,
        "title": f"{question_title} [#{number:04d}]",
        "content": question_content,
        "author": {
            "userId": asker["_id"],
            "fullName": asker["fullName"],
            "role": "STUDENT",
        },
        "spaceType": space_type,
        "spaceId": space["_id"],
        "commentCount": comment_count,
        "correctCommentIds": correct_ids,
        "createdAt": post_time,
    })

assert len(posts_to_insert) == 1000
assert len(comments_to_insert) == 4000
assert len(notifications_to_insert) == 4400
db.posts.insert_many(posts_to_insert)
db.comments.insert_many(comments_to_insert)
db.notifications.insert_many(notifications_to_insert)

print(f"   -> Đã tạo {len(posts_to_insert)} bài viết, {len(comments_to_insert)} phản hồi và {len(notifications_to_insert)} thông báo.")
print("   -> 250 bài chưa có phản hồi, 350 bài đang thảo luận, 400 bài đã giải quyết.")

print("\n" + "=" * 60)
print("=> KHỞI TẠO DỮ LIỆU SEED THÀNH CÔNG!")
print("   - Quản trị viên (ADMIN) : 1 tài khoản")
print("   - Giáo viên (TEACHER)   : 70 tài khoản")
print("   - Học sinh (STUDENT)    : 1.500 tài khoản")
print("   - Không gian thảo luận  : 210 lớp môn học + 5 chủ đề mở toàn trường")
print("   - Bài viết / phản hồi   : 1.000 / 4.000")
print("=" * 60)
