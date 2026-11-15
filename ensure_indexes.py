"""Idempotent index setup. Run after seed.py; never deletes data or indexes."""
from pymongo import ASCENDING, DESCENDING

from api.db import database


INDEXES = {
    "users": [
        ("username_1", [("username", ASCENDING)], True),
        ("role_1_class_1", [("role", ASCENDING), ("class", ASCENDING)], False),
    ],
    "subject_classes": [
        ("targetClass_1_subject_1",
         [("targetClass", ASCENDING), ("subject", ASCENDING)], True),
        ("teacherId_1", [("teacherId", ASCENDING)], False),
    ],
    "topics": [
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
        ("author.userId_1_postId_1", [("author.userId", ASCENDING), ("postId", ASCENDING)], False),
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
