"""MongoDB access and forum data rules. No Flask request or session state here."""
import os
from datetime import datetime, timezone
from pathlib import Path

import bcrypt
from bson import ObjectId
from bson.errors import InvalidId
from pymongo import MongoClient, ReturnDocument

POSTS_PER_PAGE = 10


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
    if not isinstance(username, str) or not isinstance(password, str):
        raise PermissionDenied("Tên đăng nhập hoặc mật khẩu không chính xác")
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
    topics = database.topics.find().sort("_id", -1)
    return list(database.subject_classes.find(query)), list(topics)


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
    if page < 1:
        raise InvalidInput("Trang không hợp lệ")
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
        query["correctCommentCount"] = 0
    elif status == "resolved":
        query["correctCommentCount"] = {"$gt": 0}
    elif status == "mine":
        if not mine:
            raise InvalidInput("Thiếu thông tin người dùng")
        query["author.userId"] = object_id(mine)
    cursor = database.posts.find(query).sort([("createdAt", -1), ("_id", -1)])
    return list(cursor.skip((page - 1) * POSTS_PER_PAGE).limit(POSTS_PER_PAGE + 1))


def list_comments(post_id):
    return list(database.comments.find({"postId": object_id(post_id)})
                .sort([("isCorrect", -1), ("createdAt", 1), ("_id", 1)]))


def create_post(space_type, space_id, user, title, content):
    space = get_space(space_type, space_id)
    doc = {
        "title": title, "content": content,
        "author": {"userId": user["_id"], "fullName": user["fullName"], "role": user["role"]},
        "spaceType": space_type, "spaceId": space["_id"],
        "commentCount": 0, "correctCommentCount": 0,
        "createdAt": datetime.now(timezone.utc),
    }
    return database.posts.insert_one(doc).inserted_id


def create_comment(post, user, content):
    comment_id = ObjectId()
    doc = {
        "_id": comment_id, "postId": post["_id"],
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
    return comment_id


def _can_moderate_post(post, actor, session=None):
    if actor["role"] == "ADMIN":
        return True
    if actor["role"] != "TEACHER":
        return False
    if post["spaceType"] == "SUBJECT_CLASS":
        collection = database.subject_classes
    elif post["spaceType"] == "TOPIC":
        collection = database.topics
    else:
        return False
    return collection.find_one(
        {"_id": post["spaceId"], "teacherId": actor["_id"]},
        {"_id": 1},
        session=session,
    ) is not None


def delete_comment(post_id, comment_id, actor):
    post_id = object_id(post_id)
    comment_id = object_id(comment_id)
    with client.start_session() as session:
        with session.start_transaction():
            post = database.posts.find_one({"_id": post_id}, session=session)
            if not post:
                raise EntityNotFound("Bài viết không tồn tại")
            comment = database.comments.find_one(
                {"_id": comment_id, "postId": post_id}, session=session)
            if not comment:
                raise EntityNotFound("Phản hồi không tồn tại")
            is_author = comment["author"]["userId"] == actor["_id"]
            if not is_author and not _can_moderate_post(post, actor, session):
                raise PermissionDenied("Bạn không có quyền xóa phản hồi này")

            deleted = database.comments.delete_one(
                {"_id": comment_id, "postId": post_id}, session=session)
            if not deleted.deleted_count:
                raise ConflictError("Phản hồi đã được xóa trong một phiên khác")
            post_filter = {"_id": post_id, "commentCount": {"$gt": 0}}
            decrements = {"commentCount": -1}
            if comment.get("isCorrect", False):
                post_filter["correctCommentCount"] = {"$gt": 0}
                decrements["correctCommentCount"] = -1
            result = database.posts.update_one(
                post_filter, {"$inc": decrements}, session=session)
            if not result.matched_count:
                raise ConflictError("Số lượng phản hồi không còn nhất quán")
    return post


def delete_post(post_id, actor):
    post_id = object_id(post_id)
    with client.start_session() as session:
        with session.start_transaction():
            post = database.posts.find_one({"_id": post_id}, session=session)
            if not post:
                raise EntityNotFound("Bài viết không tồn tại")
            is_author = post["author"]["userId"] == actor["_id"]
            if not is_author and not _can_moderate_post(post, actor, session):
                raise PermissionDenied("Bạn không có quyền xóa bài viết này")

            deleted = database.posts.delete_one({"_id": post_id}, session=session)
            if not deleted.deleted_count:
                raise ConflictError("Bài viết đã được xóa trong một phiên khác")
            database.comments.delete_many({"postId": post_id}, session=session)
    return post


def set_correct(post_id, comment_id, correct, actor):
    post_id = object_id(post_id)
    comment_id = object_id(comment_id)
    with client.start_session() as session:
        with session.start_transaction():
            post = database.posts.find_one({"_id": post_id}, session=session)
            if not post:
                raise EntityNotFound("Bài viết không tồn tại")
            if actor["role"] != "TEACHER" or not _can_moderate_post(post, actor, session):
                raise PermissionDenied("Bạn không phụ trách không gian này")
            comment = database.comments.find_one_and_update(
                {"_id": comment_id, "postId": post["_id"], "isCorrect": not correct},
                {"$set": {"isCorrect": correct}}, session=session,
                return_document=ReturnDocument.AFTER)
            if not comment:
                raise ConflictError("Phản hồi không thuộc bài hoặc đã ở trạng thái này")
            post_filter = {"_id": post["_id"]}
            if not correct:
                post_filter["correctCommentCount"] = {"$gt": 0}
            increment = 1 if correct else -1
            result = database.posts.update_one(
                post_filter, {"$inc": {"correctCommentCount": increment}}, session=session)
            if not result.matched_count:
                raise ConflictError("Số đáp án đúng không còn nhất quán")


def create_topic(title, description, teacher_id):
    teacher = get_user(teacher_id)
    if teacher["role"] != "TEACHER":
        raise InvalidInput("Người phụ trách phải là giáo viên")
    result = database.topics.insert_one({
        "title": title, "description": description,
        "teacherId": teacher["_id"], "teacherName": teacher["fullName"],
    })
    return result.inserted_id


def assign_teacher(space_type, space_id, teacher_id):
    space = get_space(space_type, space_id)
    teacher = get_user(teacher_id)
    if teacher["role"] != "TEACHER":
        raise InvalidInput("Người được gán phải là giáo viên")
    if space_type == "SUBJECT_CLASS" and teacher.get("subject") != space["subject"]:
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
    if space_type != "SUBJECT_CLASS":
        raise InvalidInput("Chỉ hỗ trợ báo cáo cho lớp học phần")
    space = get_space(space_type, space_id)
    if space["teacherId"] != object_id(teacher_id):
        raise PermissionDenied("Bạn không phụ trách không gian này")
    return list(database.users.aggregate(teacher_report_pipeline(space)))


def teacher_report_pipeline(space):
    pipeline = [
        {"$match": {"role": "STUDENT", "class": space["targetClass"]}},
        {"$sort": {"username": 1}},
        {"$lookup": {
            "from": "posts",
            "let": {"uid": "$_id"},
            "pipeline": [
                {"$match": {"$expr": {"$and": [
                    {"$eq": ["$spaceId", space["_id"]]},
                    {"$eq": ["$spaceType", "SUBJECT_CLASS"]},
                    {"$eq": ["$author.userId", "$$uid"]},
                ]}}},
                {"$group": {
                    "_id": None,
                    "posts": {"$sum": 1},
                    "resolvedPosts": {"$sum": {"$cond": [
                        {"$gt": ["$correctCommentCount", 0]},
                        1,
                        0,
                    ]}},
                }},
            ],
            "as": "postStats",
        }},
        {"$lookup": {
            "from": "comments",
            "let": {"uid": "$_id"},
            "pipeline": [
                {"$match": {"$expr": {"$eq": ["$author.userId", "$$uid"]}}},
                {"$lookup": {
                    "from": "posts", "localField": "postId", "foreignField": "_id",
                    "pipeline": [
                        {"$match": {"spaceId": space["_id"],
                                    "spaceType": "SUBJECT_CLASS"}},
                        {"$project": {"_id": 1}},
                    ],
                    "as": "parentPost",
                }},
                {"$match": {"parentPost.0": {"$exists": True}}},
                {"$group": {
                    "_id": None,
                    "comments": {"$sum": 1},
                    "correctComments": {"$sum": {"$cond": ["$isCorrect", 1, 0]}},
                }},
            ],
            "as": "commentStats",
        }},
        {"$project": {
            "_id": 0,
            "name": "$fullName",
            "username": 1,
            "posts": {"$ifNull": [{"$first": "$postStats.posts"}, 0]},
            "resolvedPosts": {"$ifNull": [{"$first": "$postStats.resolvedPosts"}, 0]},
            "comments": {"$ifNull": [{"$first": "$commentStats.comments"}, 0]},
            "correctComments": {"$ifNull": [{"$first": "$commentStats.correctComments"}, 0]},
        }},
    ]
    return pipeline
