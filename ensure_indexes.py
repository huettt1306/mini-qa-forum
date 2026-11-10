"""Idempotent index setup. Run after seed.py; never deletes data or indexes."""
from pymongo import ASCENDING, DESCENDING

from api.db import database


INDEXES = {
    "users": [
        ("username_1", [("username", ASCENDING)], True),
    ],
    "subject_classes": [
        ("targetClass_1_subjectName_1", [("targetClass", ASCENDING), ("subjectName", ASCENDING)], True),
        ("teacherId_1", [("teacherId", ASCENDING)], False),
    ],
    "posts": [
        ("spaceId_1_spaceType_1_createdAt_-1__id_-1",
         [("spaceId", ASCENDING), ("spaceType", ASCENDING), ("createdAt", DESCENDING), ("_id", DESCENDING)], False),
        ("spaceId_1_author.userId_1", [("spaceId", ASCENDING), ("author.userId", ASCENDING)], False),
    ],
    "comments": [
        ("postId_1_isCorrect_-1_createdAt_1",
         [("postId", ASCENDING), ("isCorrect", DESCENDING), ("createdAt", ASCENDING)], False),
        ("spaceId_1_author.userId_1", [("spaceId", ASCENDING), ("author.userId", ASCENDING)], False),
    ],
    "notifications": [
        ("recipientId_1_createdAt_-1__id_-1",
         [("recipientId", ASCENDING), ("createdAt", DESCENDING), ("_id", DESCENDING)], False),
        ("recipientId_1_isRead_1", [("recipientId", ASCENDING), ("isRead", ASCENDING)], False),
        ("type_1_commentId_1_recipientId_1",
         [("type", ASCENDING), ("commentId", ASCENDING), ("recipientId", ASCENDING)], True),
    ],
}


def ensure_indexes(db=database):
    for collection_name, definitions in INDEXES.items():
        collection = db[collection_name]
        current = collection.index_information()
        for name, keys, unique in definitions:
            if name in current:
                existing = current[name]
                if list(existing["key"]) != keys or bool(existing.get("unique", False)) != unique:
                    raise RuntimeError(f"Index {collection_name}.{name} có cấu hình khác")
                continue
            collection.create_index(keys, name=name, unique=unique)
            print(f"Đã tạo {collection_name}.{name}")


if __name__ == "__main__":
    ensure_indexes()
