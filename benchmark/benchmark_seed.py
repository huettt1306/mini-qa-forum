"""Incremental benchmark data generator for the disposable school_forum database."""
import random
from datetime import datetime, timedelta, timezone

import bcrypt
from bson import ObjectId

SCALES = {5000: (500, 100, 10, 890, 3500),
          50000: (2000, 250, 20, 9730, 38000),
          100000: (4000, 420, 30, 19550, 76000)}
SCALES_NAMES = ('users', 'subject_classes', 'topics', 'posts', 'comments')
SUBJECTS = ['Toán', 'Ngữ văn', 'Tiếng Anh', 'Lịch sử', 'Vật lí', 'Hóa học', 'Tin học']
HOT_TITLE = 'Hỏi đáp quy chế & ôn thi'
BATCH = 1000
BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def insert_batches(collection, rows):
    for start in range(0, len(rows), BATCH):
        collection.insert_many(rows[start:start + BATCH], ordered=False)


def post_created_at(index):
    return BASE_TIME + timedelta(seconds=index * 37)


def state_counts(total):
    resolved = round(total * .4)
    discussing = round(total * .4)
    return {'resolved': resolved, 'discussing': discussing,
            'unanswered': total - resolved - discussing}


def counts(db):
    return {name: db[name].count_documents({}) for name in SCALES_NAMES}


def validate_scale(db, scale):
    observed = counts(db)
    if tuple(observed.values()) != SCALES[scale]:
        raise RuntimeError(f'Sai số lượng bản ghi tại {scale}: {observed}')
    hot = db.topics.find_one({'title': HOT_TITLE})
    if not hot:
        raise RuntimeError('Thiếu chủ đề lớn')
    hot_count = round(SCALES[scale][3] * .27)
    if db.posts.count_documents({'spaceId': hot['_id']}) != hot_count:
        raise RuntimeError('Sai số bài của chủ đề lớn')
    for state, expected in state_counts(hot_count).items():
        query = {'spaceId': hot['_id']}
        query.update({'correctCommentCount': {'$gt': 0}} if state == 'resolved' else
                     {'commentCount': 0} if state == 'unanswered' else
                     {'commentCount': {'$gt': 0}, 'correctCommentCount': 0})
        if db.posts.count_documents(query) != expected:
            raise RuntimeError(f'Sai số bài {state} của chủ đề lớn')
    if len(list(db.users.aggregate([{'$group': {'_id': '$username', 'n': {'$sum': 1}}},
                                    {'$match': {'n': {'$gt': 1}}}, {'$limit': 1}]))) != 0:
        raise RuntimeError('Trùng username')
    if len(list(db.subject_classes.aggregate([{'$group': {'_id': {'targetClass': '$targetClass',
                        'subject': '$subject'}, 'n': {'$sum': 1}}},
                        {'$match': {'n': {'$gt': 1}}}, {'$limit': 1}]))) != 0:
        raise RuntimeError('Trùng lớp môn')
    return observed


def append_to_scale(db, scale):
    if scale not in SCALES:
        raise ValueError('Quy mô không hợp lệ')
    observed = counts(db)
    current = next((key for key, budget in SCALES.items()
                    if tuple(observed.values()) == budget), None)
    if current is None and any(observed.values()):
        raise RuntimeError(f'Database không ở mốc benchmark hợp lệ: {observed}')
    if current is not None and scale <= current:
        raise ValueError('Chỉ được nạp nối tiếp lên mốc lớn hơn')
    if current is None and scale != 5000:
        raise ValueError('Phải bắt đầu từ mốc 5000')
    previous = SCALES.get(current, (0, 0, 0, 0, 0))
    previous_latest = (db.posts.find_one(sort=[('createdAt', -1)], projection={'createdAt': 1})
                       if current is not None else None)
    target = SCALES[scale]
    rng = random.Random(20261115 + scale)
    password = bcrypt.hashpw(b'123456', bcrypt.gensalt(rounds=8)).decode()
    users = []
    if current is None:
        users.append({'_id': ObjectId(), 'username': 'admin',
                      'password': bcrypt.hashpw(b'admin', bcrypt.gensalt(rounds=8)).decode(),
                      'fullName': 'Quản trị viên', 'role': 'ADMIN', 'class': None})
        for i in range(1, 71):
            users.append({'_id': ObjectId(), 'username': f'gv{i:04d}',
                          'password': password, 'fullName': f'Giáo viên {i}',
                          'role': 'TEACHER', 'class': None,
                          'subject': SUBJECTS[(i - 1) % len(SUBJECTS)]})
    teachers = list(db.users.find({'role': 'TEACHER'})) if current else users[1:]
    # All 60 administrative classes are available from the first milestone.
    class_names = [f'{grade}A{number}' for grade in (10, 11, 12)
                   for number in range(1, 21)]
    first_student = max(1, previous[0] - 70)
    for i in range(first_student, target[0] - 70):
        group = class_names[(i - 1) % (16 if scale == 5000 else 36 if scale == 50000 else 60)]
        users.append({'_id': ObjectId(), 'username': f'hs{i:05d}',
                      'password': password, 'fullName': f'Học sinh {i}',
                      'role': 'STUDENT', 'class': group})
    insert_batches(db.users, users)
    all_students = list(db.users.find({'role': 'STUDENT'}))
    students_by_class = {name: [] for name in class_names}
    for student in all_students:
        students_by_class[student['class']].append(student)
    subject_classes = []
    for i in range(previous[1], target[1]):
        subject = SUBJECTS[i % 7]
        target_class = class_names[i // 7]
        options = [teacher for teacher in teachers if teacher['subject'] == subject]
        teacher = options[(i // 7) % len(options)]
        subject_classes.append({'_id': ObjectId(), 'subject': subject,
                                'targetClass': target_class,
                                'teacherId': teacher['_id'], 'teacherName': teacher['fullName']})
    insert_batches(db.subject_classes, subject_classes)
    all_classes = list(db.subject_classes.find())
    topics = []
    for i in range(previous[2], target[2]):
        teacher = teachers[i % len(teachers)]
        topics.append({'_id': ObjectId(),
                       'title': HOT_TITLE if i == 0 else f'Chủ đề mở {i}',
                       'description': 'Thảo luận học tập và ôn thi',
                       'teacherId': teacher['_id'], 'teacherName': teacher['fullName']})
    insert_batches(db.topics, topics)
    all_topics = list(db.topics.find())
    hot = next(topic for topic in all_topics if topic['title'] == HOT_TITLE)
    old_hot = round(previous[3] * .27)
    new_hot = round(target[3] * .27)
    hot_delta = new_hot - old_hot
    post_delta = target[3] - previous[3]
    total_states = state_counts(target[3])
    old_states = state_counts(previous[3])
    hot_states = state_counts(new_hot)
    old_hot_states = state_counts(old_hot)
    hot_delta_states = {key: hot_states[key] - old_hot_states[key] for key in hot_states}
    total_delta_states = {key: total_states[key] - old_states[key] for key in total_states}
    other_delta_states = {key: total_delta_states[key] - hot_delta_states[key]
                          for key in total_states}
    assert sum(hot_delta_states.values()) == hot_delta
    states = ([state for state, count in hot_delta_states.items() for _ in range(count)] +
              [state for state, count in other_delta_states.items() for _ in range(count)])
    hot_part = states[:hot_delta]
    other_part = states[hot_delta:]
    rng.shuffle(hot_part)
    rng.shuffle(other_part)
    states = hot_part + other_part
    discussing_indices = [i for i, state in enumerate(states) if state == 'discussing']
    remaining = (target[4] - previous[4]) - total_delta_states['resolved'] * 3
    assert len(discussing_indices) * 6 <= remaining <= len(discussing_indices) * 7
    seven_indices = set(rng.sample(discussing_indices, remaining - len(discussing_indices) * 6))
    regular = [('SUBJECT_CLASS', row) for row in all_classes]
    regular += [('TOPIC', row) for row in all_topics if row['_id'] != hot['_id']]
    rng.shuffle(regular)
    posts, comments = [], []
    for offset in range(post_delta):
        global_i = previous[3] + offset
        is_hot = offset < hot_delta
        state = states[offset]
        kind, space = ('TOPIC', hot) if is_hot else regular[(offset - hot_delta) % len(regular)]
        group = all_students if kind == 'TOPIC' else students_by_class[space['targetClass']]
        asker = rng.choice(group)
        replies = 0 if state == 'unanswered' else 3 if state == 'resolved' else 7 if offset in seven_indices else 6
        created = post_created_at(global_i)
        post_id = ObjectId()
        posts.append({'_id': post_id, 'title': f'Câu hỏi ôn thi {global_i}: phương pháp giải',
                      'content': 'Hỏi về quy chế, ôn thi và phương pháp giải bài tập ' * 4,
                      'author': {'userId': asker['_id'], 'fullName': asker['fullName'], 'role': 'STUDENT'},
                      'spaceType': kind, 'spaceId': space['_id'], 'commentCount': replies,
                      'correctCommentCount': int(state == 'resolved'), 'createdAt': created})
        options = [student for student in group if student['_id'] != asker['_id']]
        for j, answerer in enumerate(rng.sample(options, replies)):
            comments.append({'_id': ObjectId(), 'postId': post_id,
                             'author': {'userId': answerer['_id'],
                                        'fullName': answerer['fullName'], 'role': 'STUDENT'},
                             'content': f'Phản hồi {j} về cách giải bài tập',
                             'isCorrect': state == 'resolved' and j == 0,
                             'createdAt': created + timedelta(minutes=j + 1)})
    assert len(posts) == post_delta and len(comments) == target[4] - previous[4]
    if previous_latest:
        latest = previous_latest['createdAt']
        if latest.tzinfo is None:
            latest = latest.replace(tzinfo=timezone.utc)
        if not latest < posts[0]['createdAt']:
            raise RuntimeError('createdAt phải tăng liên tục qua các mốc')
    insert_batches(db.posts, posts)
    insert_batches(db.comments, comments)
    observed = validate_scale(db, scale)
    print(f'Append {current or 0} → {scale}: {observed}, hot={new_hot}', flush=True)
    return observed


def seed_scaled(db, scale):
    """Compatibility entry point: reset then grow to the requested milestone."""
    for name in db.list_collection_names():
        db.drop_collection(name)
    for milestone in SCALES:
        append_to_scale(db, milestone)
        if milestone == scale:
            return counts(db)
    raise ValueError('Quy mô không hợp lệ')
