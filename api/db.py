"""MongoDB access and forum data rules. No Flask request or session state here."""
import os
from datetime import datetime, timezone
from pathlib import Path

import bcrypt
from bson import ObjectId
from bson.errors import InvalidId
from pymongo import MongoClient, ReturnDocument

if __package__:
    from .constants import NOTIFICATIONS_PER_PAGE, POSTS_PER_PAGE
else:
    from constants import NOTIFICATIONS_PER_PAGE, POSTS_PER_PAGE


class ForumError(Exception):
    pass


class EntityNotFound(ForumError):
    pass


class PermissionDenied(ForumError):
    pass


class ConflictError(ForumError):
    pass


class InvalidInput(ForumError):
    pass


NOTIFICATION_MESSAGES = {
    "NEW_COMMENT": "Có phản hồi mới trong bài viết của bạn",
    "ANSWER_APPROVED": "Câu trả lời của bạn đã được giáo viên duyệt",
}


def load_mongo_uri():
    uri = os.environ.get("MONGODB_URI") or os.environ.get("MONGO_URI")
    if uri:
        return uri
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator and key.strip() in ("MONGODB_URI", "MONGO_URI"):
                uri = value.strip().strip("\"'")
                if uri:
                    return uri
    raise RuntimeError("Chưa cấu hình MONGODB_URI hoặc MONGO_URI")


client = MongoClient(load_mongo_uri(), serverSelectionTimeoutMS=5000, maxPoolSize=10)
database = client["school_forum"]


def object_id(value):
    try:
        return ObjectId(str(value))
    except (InvalidId, TypeError, ValueError):
        raise InvalidInput("ID không hợp lệ") from None


def get_user(user_id):
    user = database.users.find_one({"_id": object_id(user_id)})
    if not user:
        raise EntityNotFound("Tài khoản không tồn tại")
    return user


def authenticate(username, password):
    user = database.users.find_one({"username": username})
    if not user or not bcrypt.checkpw(password.encode(), user["password"].encode()):
        raise PermissionDenied("Tên đăng nhập hoặc mật khẩu không chính xác")
    return user


def change_password(user_id, old_password, new_password):
    if len(new_password) < 8:
        raise InvalidInput("Mật khẩu mới cần ít nhất 8 ký tự")
    user = get_user(user_id)
    if not bcrypt.checkpw(old_password.encode(), user["password"].encode()):
        raise PermissionDenied("Mật khẩu hiện tại không đúng")
    new_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
    result = database.users.update_one(
        {"_id": user["_id"], "password": user["password"]},
        {"$set": {"password": new_hash}},
    )
    if not result.modified_count:
        raise ConflictError("Mật khẩu đã được thay đổi trong một phiên khác")


def list_spaces(user):
    if user["role"] == "STUDENT":
        query = {"targetClass": user["class"]}
    elif user["role"] == "TEACHER":
        query = {"teacherId": user["_id"]}
    else:
        query = {}
    return list(database.subject_classes.find(query)), list(database.topics.find())


def get_space(space_type, space_id):
    if space_type == "SUBJECT_CLASS":
        collection = database.subject_classes
    elif space_type == "TOPIC":
        collection = database.topics
    else:
        raise EntityNotFound("Không gian không tồn tại")
    space = collection.find_one({"_id": object_id(space_id)})
    if not space:
        raise EntityNotFound("Không gian không tồn tại")
    return space


def get_post(post_id):
    post = database.posts.find_one({"_id": object_id(post_id)})
    if not post:
        raise EntityNotFound("Bài viết không tồn tại")
    return post


def list_posts(space_type, space_id, search="", status="all", mine=None, page=1):
    if status not in {"all", "unanswered", "resolved", "mine"}:
        raise InvalidInput("Bộ lọc không hợp lệ")
    query = {"spaceType": space_type, "spaceId": object_id(space_id)}
    if search:
        # Escaped regex is safe for the current small data set; index strategy can evolve.
        import re
        query["$or"] = [
            {"title": {"$regex": re.escape(search[:100]), "$options": "i"}},
            {"content": {"$regex": re.escape(search[:100]), "$options": "i"}},
        ]
    if status == "unanswered":
        query["correctCommentIds"] = {"$size": 0}
    elif status == "resolved":
        query["correctCommentIds.0"] = {"$exists": True}
    elif status == "mine":
        query["author.userId"] = object_id(mine)
    cursor = database.posts.find(query).sort([("createdAt", -1), ("_id", -1)])
    return list(cursor.skip((page - 1) * POSTS_PER_PAGE).limit(POSTS_PER_PAGE + 1))


def list_comments(post_id):
    return list(database.comments.find({"postId": object_id(post_id)})
                .sort([("isCorrect", -1), ("createdAt", 1), ("_id", 1)]))


def create_post(space_type, space_id, user, title, content):
    space = get_space(space_type, space_id)
    post_id = ObjectId()
    doc = {
        "_id": post_id, "title": title, "content": content,
        "author": {"userId": user["_id"], "fullName": user["fullName"], "role": user["role"]},
        "spaceType": space_type, "spaceId": space["_id"],
        "commentCount": 0, "correctCommentIds": [],
        "createdAt": datetime.now(timezone.utc),
    }
    with client.start_session() as session:
        with session.start_transaction():
            database.posts.insert_one(doc, session=session)
    return post_id


def create_comment(post, user, content):
    comment_id = ObjectId()
    doc = {
        "_id": comment_id, "postId": post["_id"], "spaceId": post["spaceId"],
        "author": {"userId": user["_id"], "fullName": user["fullName"], "role": user["role"]},
        "content": content, "isCorrect": False, "createdAt": datetime.now(timezone.utc),
    }
    with client.start_session() as session:
        with session.start_transaction():
            database.comments.insert_one(doc, session=session)
            result = database.posts.update_one(
                {"_id": post["_id"]}, {"$inc": {"commentCount": 1}}, session=session)
            if not result.matched_count:
                raise EntityNotFound("Bài viết không còn tồn tại")
            recipient_id = post["author"]["userId"]
            if recipient_id != user["_id"]:
                database.notifications.insert_one({
                    "recipientId": recipient_id, "actorId": user["_id"],
                    "type": "NEW_COMMENT", "message": NOTIFICATION_MESSAGES["NEW_COMMENT"],
                    "postId": post["_id"],
                    "commentId": comment_id, "spaceId": post["spaceId"],
                    "spaceType": post["spaceType"], "createdAt": doc["createdAt"],
                    "isRead": False,
                }, session=session)
    return comment_id


def set_correct(post_id, comment_id, correct, actor_id):
    post = get_post(post_id)
    comment_id = object_id(comment_id)
    with client.start_session() as session:
        with session.start_transaction():
            comment = database.comments.find_one_and_update(
                {"_id": comment_id, "postId": post["_id"], "isCorrect": not correct},
                {"$set": {"isCorrect": correct}}, session=session,
                return_document=ReturnDocument.AFTER)
            if not comment:
                raise ConflictError("Phản hồi không thuộc bài hoặc đã ở trạng thái này")
            update = {"$addToSet": {"correctCommentIds": comment_id}} if correct else {
                "$pull": {"correctCommentIds": comment_id}}
            result = database.posts.update_one({"_id": post["_id"]}, update, session=session)
            if not result.matched_count:
                raise EntityNotFound("Bài viết không còn tồn tại")
            notice_query = {"type": "ANSWER_APPROVED", "commentId": comment_id,
                            "recipientId": comment["author"]["userId"]}
            if correct and comment["author"]["userId"] != object_id(actor_id):
                database.notifications.insert_one({
                    **notice_query, "message": NOTIFICATION_MESSAGES["ANSWER_APPROVED"],
                    "actorId": object_id(actor_id),
                    "postId": post["_id"], "spaceId": post["spaceId"],
                    "spaceType": post["spaceType"],
                    "createdAt": datetime.now(timezone.utc), "isRead": False,
                }, session=session)
            elif not correct:
                database.notifications.delete_one(notice_query, session=session)


def list_notifications(user_id, page=1):
    if page < 1:
        raise InvalidInput("Trang không hợp lệ")
    recipient_id = object_id(user_id)
    rows = list(database.notifications.find({"recipientId": recipient_id})
                .sort([("createdAt", -1), ("_id", -1)])
                .skip((page - 1) * NOTIFICATIONS_PER_PAGE).limit(NOTIFICATIONS_PER_PAGE + 1))
    unread = database.notifications.count_documents({"recipientId": recipient_id, "isRead": False})
    return rows, unread


def mark_notification_read(notification_id, user_id):
    query = {"_id": object_id(notification_id), "recipientId": object_id(user_id)}
    result = database.notifications.update_one(
        {**query, "isRead": False}, {"$set": {"isRead": True}})
    if not result.matched_count and not database.notifications.find_one(query, {"_id": 1}):
        raise EntityNotFound("Thông báo không tồn tại")


def create_topic(title, description, teacher_id):
    teacher = get_user(teacher_id)
    if teacher["role"] != "TEACHER":
        raise InvalidInput("Người phụ trách phải là giáo viên")
    result = database.topics.insert_one({
        "title": title, "description": description,
        "teacherId": teacher["_id"], "teacherName": teacher["fullName"],
        "createdAt": datetime.now(timezone.utc),
    })
    return result.inserted_id


def assign_teacher(space_type, space_id, teacher_id):
    space = get_space(space_type, space_id)
    teacher = get_user(teacher_id)
    if teacher["role"] != "TEACHER":
        raise InvalidInput("Người được gán phải là giáo viên")
    if space_type == "SUBJECT_CLASS" and teacher.get("subject") != space["subjectName"].rsplit(" ", 1)[0]:
        raise InvalidInput("Giáo viên không dạy môn của lớp này")
    collection = database.subject_classes if space_type == "SUBJECT_CLASS" else database.topics
    collection.update_one({"_id": space["_id"]}, {"$set": {
        "teacherId": teacher["_id"], "teacherName": teacher["fullName"]}})


def list_teachers(subject=None):
    query = {"role": "TEACHER"}
    if subject:
        query["subject"] = subject
    return list(database.users.find(query, {"password": 0}).sort("username", 1))


def admin_summary():
    return {
        "users": database.users.count_documents({}),
        "teachers": database.users.count_documents({"role": "TEACHER"}),
        "students": database.users.count_documents({"role": "STUDENT"}),
        "classes": database.subject_classes.count_documents({}),
        "topics": database.topics.count_documents({}),
        "posts": database.posts.count_documents({}),
        "comments": database.comments.count_documents({}),
    }


def teacher_report(teacher_id, space_type, space_id):
    space = get_space(space_type, space_id)
    if space["teacherId"] != object_id(teacher_id):
        raise PermissionDenied("Bạn không phụ trách không gian này")
    match = {"spaceId": space["_id"]}
    posts = list(database.posts.aggregate([
        {"$match": match},
        {"$group": {"_id": "$author.userId", "posts": {"$sum": 1},
                    "resolvedPosts": {"$sum": {"$cond": [
                        {"$gt": [{"$size": {"$ifNull": ["$correctCommentIds", []]}}, 0]}, 1, 0]}}}},
    ]))
    comments = list(database.comments.aggregate([
        {"$match": match},
        {"$group": {"_id": "$author.userId", "comments": {"$sum": 1},
                    "correctComments": {"$sum": {"$cond": ["$isCorrect", 1, 0]}}}},
    ]))
    by_student = {}
    for row in posts + comments:
        entry = by_student.setdefault(row["_id"], {"posts": 0, "resolvedPosts": 0,
                                                     "comments": 0, "correctComments": 0})
        entry.update({key: value for key, value in row.items() if key != "_id"})
    users = {user["_id"]: user for user in database.users.find(
        {"_id": {"$in": list(by_student)}, "role": "STUDENT"},
        {"fullName": 1, "username": 1})}
    rows = [{"name": users[user_id]["fullName"], "username": users[user_id]["username"], **counts}
            for user_id, counts in by_student.items() if user_id in users]
    return sorted(rows, key=lambda row: row["username"])
