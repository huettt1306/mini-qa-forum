"""Focused data-rule tests; all MongoDB objects here are local fakes."""
import os
import unittest
from unittest.mock import MagicMock, patch

from bson import ObjectId

os.environ["MONGO_URI"] = "mongodb://unit-test.invalid:27017"

from api import db as store
from ensure_indexes import ensure_indexes


class DataRulesTest(unittest.TestCase):
    def test_create_post_is_single_atomic_insert_without_transaction(self):
        space = {"_id": ObjectId()}
        user = {"_id": ObjectId(), "fullName": "Học sinh", "role": "STUDENT"}
        inserted_id = ObjectId()
        fake_db = MagicMock()
        fake_db.posts.insert_one.return_value.inserted_id = inserted_id
        fake_client = MagicMock()
        with patch.object(store, "database", fake_db), \
             patch.object(store, "client", fake_client), \
             patch.object(store, "get_space", return_value=space):
            result = store.create_post("TOPIC", space["_id"], user, "Tiêu đề", "Nội dung")
        self.assertEqual(result, inserted_id)
        doc = fake_db.posts.insert_one.call_args.args[0]
        self.assertEqual(doc["commentCount"], 0)
        self.assertEqual(doc["correctCommentCount"], 0)
        fake_client.start_session.assert_not_called()

    def test_comment_insert_increments_parent_in_same_transaction(self):
        post = {"_id": ObjectId(), "spaceId": ObjectId(), "spaceType": "TOPIC",
                "author": {"userId": ObjectId()}}
        user = {"_id": ObjectId(), "fullName": "Học sinh", "role": "STUDENT"}
        fake_db = MagicMock()
        fake_db.posts.update_one.return_value.matched_count = 1
        fake_client = MagicMock()
        with patch.object(store, "database", fake_db), patch.object(store, "client", fake_client):
            store.create_comment(post, user, "Lời giải")
        comment = fake_db.comments.insert_one.call_args.args[0]
        self.assertEqual(comment["spaceId"], post["spaceId"])
        self.assertEqual(comment["postId"], post["_id"])
        self.assertEqual(fake_db.posts.update_one.call_args.args[1], {"$inc": {"commentCount": 1}})
        self.assertIs(
            fake_db.comments.insert_one.call_args.kwargs["session"],
            fake_db.posts.update_one.call_args.kwargs["session"],
        )
        notice = fake_db.notifications.insert_one.call_args.args[0]
        self.assertEqual(notice["recipientId"], post["author"]["userId"])
        self.assertEqual(notice["commentId"], comment["_id"])
        self.assertEqual(notice["message"], store.NOTIFICATION_MESSAGES["NEW_COMMENT"])
        self.assertIs(notice["isRead"], False)
        self.assertIs(fake_db.notifications.insert_one.call_args.kwargs["session"],
                      fake_db.comments.insert_one.call_args.kwargs["session"])

    def test_self_comment_has_no_notification(self):
        user = {"_id": ObjectId(), "fullName": "Học sinh", "role": "STUDENT"}
        post = {"_id": ObjectId(), "spaceId": ObjectId(), "spaceType": "TOPIC",
                "author": {"userId": user["_id"]}}
        fake_db = MagicMock()
        fake_db.posts.update_one.return_value.matched_count = 1
        with patch.object(store, "database", fake_db), patch.object(store, "client", MagicMock()):
            store.create_comment(post, user, "Bổ sung")
        fake_db.notifications.insert_one.assert_not_called()

    def test_delete_comment_twice_decrements_count_only_once(self):
        post_id, comment_id, author_id = ObjectId(), ObjectId(), ObjectId()
        post = {"_id": post_id, "spaceId": ObjectId(), "spaceType": "TOPIC",
                "author": {"userId": ObjectId()}}
        comment = {"_id": comment_id, "postId": post_id,
                   "author": {"userId": author_id}, "isCorrect": False}
        actor = {"_id": author_id, "role": "STUDENT"}
        fake_db = MagicMock()
        fake_db.posts.find_one.return_value = post
        fake_db.comments.find_one.side_effect = [comment, None]
        fake_db.comments.delete_one.return_value.deleted_count = 1
        fake_db.posts.update_one.return_value.matched_count = 1

        with patch.object(store, "database", fake_db), \
             patch.object(store, "client", MagicMock()):
            store.delete_comment(post_id, comment_id, actor)
            with self.assertRaises(store.EntityNotFound):
                store.delete_comment(post_id, comment_id, actor)

        fake_db.comments.delete_one.assert_called_once_with(
            {"_id": comment_id, "postId": post_id}, session=fake_db.comments.delete_one.call_args.kwargs["session"])
        fake_db.posts.update_one.assert_called_once()
        self.assertEqual(fake_db.posts.update_one.call_args.args, (
            {"_id": post_id, "commentCount": {"$gt": 0}},
            {"$inc": {"commentCount": -1}},
        ))
        fake_db.notifications.delete_many.assert_called_once_with(
            {"commentId": comment_id}, session=fake_db.notifications.delete_many.call_args.kwargs["session"])

    def test_delete_correct_comment_decrements_both_counts(self):
        post_id, comment_id, author_id = ObjectId(), ObjectId(), ObjectId()
        post = {"_id": post_id, "spaceId": ObjectId(), "spaceType": "TOPIC",
                "author": {"userId": ObjectId()}}
        comment = {"_id": comment_id, "postId": post_id, "isCorrect": True,
                   "author": {"userId": author_id}}
        fake_db = MagicMock()
        fake_db.posts.find_one.return_value = post
        fake_db.comments.find_one.return_value = comment
        fake_db.comments.delete_one.return_value.deleted_count = 1
        fake_db.posts.update_one.return_value.matched_count = 1
        with patch.object(store, "database", fake_db), \
             patch.object(store, "client", MagicMock()):
            store.delete_comment(post_id, comment_id,
                                 {"_id": author_id, "role": "STUDENT"})
        self.assertEqual(fake_db.posts.update_one.call_args.args, (
            {"_id": post_id, "commentCount": {"$gt": 0},
             "correctCommentCount": {"$gt": 0}},
            {"$inc": {"commentCount": -1, "correctCommentCount": -1}},
        ))

    def test_delete_comment_rejects_comment_from_another_post(self):
        post_id, comment_id, actor_id = ObjectId(), ObjectId(), ObjectId()
        post = {"_id": post_id, "spaceId": ObjectId(), "spaceType": "TOPIC",
                "author": {"userId": actor_id}}
        fake_db = MagicMock()
        fake_db.posts.find_one.return_value = post
        fake_db.comments.find_one.return_value = None
        with patch.object(store, "database", fake_db), \
             patch.object(store, "client", MagicMock()):
            with self.assertRaises(store.EntityNotFound):
                store.delete_comment(post_id, comment_id, {"_id": actor_id, "role": "STUDENT"})
        self.assertEqual(fake_db.comments.find_one.call_args.args[0],
                         {"_id": comment_id, "postId": post_id})
        fake_db.posts.update_one.assert_not_called()
        fake_db.notifications.delete_many.assert_not_called()

    def test_delete_post_cascades_in_same_transaction(self):
        post_id, author_id = ObjectId(), ObjectId()
        post = {"_id": post_id, "spaceId": ObjectId(), "spaceType": "TOPIC",
                "author": {"userId": author_id}}
        fake_db = MagicMock()
        fake_db.posts.find_one.return_value = post
        fake_db.posts.delete_one.return_value.deleted_count = 1
        with patch.object(store, "database", fake_db), \
             patch.object(store, "client", MagicMock()):
            deleted = store.delete_post(post_id, {"_id": author_id, "role": "STUDENT"})
        self.assertEqual(deleted, post)
        session = fake_db.posts.delete_one.call_args.kwargs["session"]
        fake_db.comments.delete_many.assert_called_once_with({"postId": post_id}, session=session)
        fake_db.notifications.delete_many.assert_called_once_with({"postId": post_id}, session=session)

    def test_delete_comment_requires_author_moderator_or_admin(self):
        post_id, comment_id = ObjectId(), ObjectId()
        post = {"_id": post_id, "spaceId": ObjectId(), "spaceType": "SUBJECT_CLASS",
                "author": {"userId": ObjectId()}}
        comment = {"_id": comment_id, "postId": post_id,
                   "author": {"userId": ObjectId()}}
        fake_db = MagicMock()
        fake_db.posts.find_one.return_value = post
        fake_db.comments.find_one.return_value = comment
        with patch.object(store, "database", fake_db), \
             patch.object(store, "client", MagicMock()):
            with self.assertRaises(store.PermissionDenied):
                store.delete_comment(post_id, comment_id,
                                     {"_id": ObjectId(), "role": "STUDENT"})
        fake_db.comments.delete_one.assert_not_called()
        fake_db.posts.update_one.assert_not_called()

    def test_teacher_can_delete_comment_in_managed_space(self):
        post_id, comment_id, teacher_id = ObjectId(), ObjectId(), ObjectId()
        post = {"_id": post_id, "spaceId": ObjectId(), "spaceType": "SUBJECT_CLASS",
                "author": {"userId": ObjectId()}}
        comment = {"_id": comment_id, "postId": post_id,
                   "author": {"userId": ObjectId()}}
        fake_db = MagicMock()
        fake_db.posts.find_one.return_value = post
        fake_db.comments.find_one.return_value = comment
        fake_db.subject_classes.find_one.return_value = {"_id": post["spaceId"]}
        fake_db.comments.delete_one.return_value.deleted_count = 1
        fake_db.posts.update_one.return_value.matched_count = 1
        with patch.object(store, "database", fake_db), \
             patch.object(store, "client", MagicMock()):
            store.delete_comment(post_id, comment_id,
                                 {"_id": teacher_id, "role": "TEACHER"})
        self.assertEqual(fake_db.subject_classes.find_one.call_args.args[0],
                         {"_id": post["spaceId"], "teacherId": teacher_id})
        fake_db.comments.delete_one.assert_called_once()

    def test_admin_delete_bypasses_space_lookup(self):
        post_id, admin_id = ObjectId(), ObjectId()
        post = {"_id": post_id, "spaceId": ObjectId(), "spaceType": "TOPIC",
                "author": {"userId": ObjectId()}}
        fake_db = MagicMock()
        fake_db.posts.find_one.return_value = post
        fake_db.posts.delete_one.return_value.deleted_count = 1
        with patch.object(store, "database", fake_db), \
             patch.object(store, "client", MagicMock()):
            store.delete_post(post_id, {"_id": admin_id, "role": "ADMIN"})
        fake_db.topics.find_one.assert_not_called()
        fake_db.posts.delete_one.assert_called_once()

    def test_approve_rejects_comment_from_other_post(self):
        post = {"_id": ObjectId(), "spaceId": ObjectId(), "spaceType": "TOPIC"}
        comment_id = ObjectId()
        actor = {"_id": ObjectId(), "role": "TEACHER"}
        fake_db = MagicMock()
        fake_db.posts.find_one.return_value = post
        fake_db.comments.find_one_and_update.return_value = None
        with patch.object(store, "database", fake_db), patch.object(store, "client", MagicMock()), \
             patch.object(store, "_can_moderate_post", return_value=True):
            with self.assertRaises(store.ConflictError):
                store.set_correct(post["_id"], comment_id, True, actor)
        self.assertEqual(
            fake_db.comments.find_one_and_update.call_args.args[0]["postId"], post["_id"]
        )
        fake_db.posts.update_one.assert_not_called()

    def test_approval_creates_and_unmark_removes_only_approval_notice(self):
        post = {"_id": ObjectId(), "spaceId": ObjectId(), "spaceType": "TOPIC"}
        author_id, teacher_id, comment_id = ObjectId(), ObjectId(), ObjectId()
        actor = {"_id": teacher_id, "role": "TEACHER"}
        comment = {"_id": comment_id, "author": {"userId": author_id}}
        fake_db = MagicMock()
        fake_db.posts.find_one.return_value = post
        fake_db.comments.find_one_and_update.return_value = comment
        fake_db.posts.update_one.return_value.matched_count = 1
        with patch.object(store, "database", fake_db), patch.object(store, "client", MagicMock()), \
             patch.object(store, "_can_moderate_post", return_value=True):
            store.set_correct(post["_id"], comment_id, True, actor)
            store.set_correct(post["_id"], comment_id, False, actor)
        self.assertEqual(fake_db.posts.update_one.call_args_list[0].args, (
            {"_id": post["_id"]}, {"$inc": {"correctCommentCount": 1}}))
        self.assertEqual(fake_db.posts.update_one.call_args_list[1].args, (
            {"_id": post["_id"], "correctCommentCount": {"$gt": 0}},
            {"$inc": {"correctCommentCount": -1}}))
        self.assertEqual(fake_db.notifications.insert_one.call_args.args[0]["type"], "ANSWER_APPROVED")
        self.assertEqual(fake_db.notifications.insert_one.call_args.args[0]["message"],
                         store.NOTIFICATION_MESSAGES["ANSWER_APPROVED"])
        self.assertIs(fake_db.notifications.insert_one.call_args.args[0]["isRead"], False)
        self.assertEqual(fake_db.notifications.delete_one.call_args.args[0], {
            "type": "ANSWER_APPROVED", "commentId": comment_id, "recipientId": author_id})
        self.assertIs(fake_db.notifications.insert_one.call_args.kwargs["session"],
                      fake_db.posts.update_one.call_args.kwargs["session"])

    def test_mark_read_is_scoped_to_recipient(self):
        fake_db = MagicMock()
        fake_db.notifications.update_one.return_value.matched_count = 0
        fake_db.notifications.find_one.return_value = None
        with patch.object(store, "database", fake_db):
            with self.assertRaises(store.EntityNotFound):
                store.mark_notification_read(ObjectId(), ObjectId())
        self.assertIn("recipientId", fake_db.notifications.update_one.call_args.args[0])
        self.assertEqual(fake_db.notifications.update_one.call_args.args[1], {"$set": {"isRead": True}})
        fake_db.notifications.find_one.assert_not_called()

    def test_mark_read_is_idempotent_with_one_update(self):
        fake_db = MagicMock()
        fake_db.notifications.update_one.return_value.matched_count = 1
        with patch.object(store, "database", fake_db):
            store.mark_notification_read(ObjectId(), ObjectId())
        fake_db.notifications.update_one.assert_called_once()
        fake_db.notifications.find_one.assert_not_called()

    def test_set_correct_requires_assigned_teacher_before_writes(self):
        post = {"_id": ObjectId(), "spaceId": ObjectId(), "spaceType": "TOPIC"}
        fake_db = MagicMock()
        fake_db.posts.find_one.return_value = post
        actors = [
            {"_id": ObjectId(), "role": "STUDENT"},
            {"_id": ObjectId(), "role": "ADMIN"},
            {"_id": ObjectId(), "role": "TEACHER"},
        ]
        with patch.object(store, "database", fake_db), \
             patch.object(store, "client", MagicMock()), \
             patch.object(store, "_can_moderate_post", return_value=False):
            for actor in actors:
                with self.subTest(role=actor["role"]), self.assertRaises(store.PermissionDenied):
                    store.set_correct(post["_id"], ObjectId(), True, actor)
        fake_db.comments.find_one_and_update.assert_not_called()
        fake_db.posts.update_one.assert_not_called()

    def test_unread_count_uses_boolean(self):
        fake_db = MagicMock()
        fake_db.notifications.find.return_value.sort.return_value.skip.return_value.limit.return_value = []
        fake_db.notifications.count_documents.return_value = 3
        with patch.object(store, "database", fake_db):
            _, count = store.list_notifications(ObjectId())
        self.assertEqual(count, 3)
        self.assertEqual(fake_db.notifications.count_documents.call_args.args[0]["isRead"], False)

    def test_teacher_report_aggregates_from_all_students_in_class(self):
        teacher_id = ObjectId()
        space_id = ObjectId()
        space = {"_id": space_id, "teacherId": teacher_id, "targetClass": "10A1"}
        expected = [{"name": "Học sinh", "username": "hs00001", "posts": 0,
                     "resolvedPosts": 0, "comments": 0, "correctComments": 0}]
        fake_db = MagicMock()
        fake_db.users.aggregate.return_value = expected

        with patch.object(store, "database", fake_db), \
             patch.object(store, "get_space", return_value=space):
            rows = store.teacher_report(teacher_id, "SUBJECT_CLASS", space_id)

        self.assertEqual(rows, expected)
        pipeline = fake_db.users.aggregate.call_args.args[0]
        self.assertEqual(pipeline[0], {"$match": {"role": "STUDENT", "class": "10A1"}})
        self.assertEqual([stage["$lookup"]["from"] for stage in pipeline if "$lookup" in stage],
                         ["posts", "comments"])
        fake_db.posts.aggregate.assert_not_called()
        fake_db.comments.aggregate.assert_not_called()
        post_group = pipeline[2]["$lookup"]["pipeline"][1]["$group"]
        self.assertEqual(post_group["resolvedPosts"]["$sum"]["$cond"][0],
                         {"$gt": ["$correctCommentCount", 0]})

    def test_assign_teacher_uses_explicit_subject(self):
        space = {"_id": ObjectId(), "subject": "Tiếng Anh",
                 "subjectName": "Tên hiển thị không dùng để phân quyền"}
        teacher = {"_id": ObjectId(), "role": "TEACHER", "subject": "Tiếng Anh",
                   "fullName": "Giáo viên"}
        fake_db = MagicMock()
        with patch.object(store, "database", fake_db), \
             patch.object(store, "get_space", return_value=space), \
             patch.object(store, "get_user", return_value=teacher):
            store.assign_teacher("SUBJECT_CLASS", space["_id"], teacher["_id"])
        fake_db.subject_classes.update_one.assert_called_once()

    def test_post_filters_use_correct_comment_count(self):
        fake_db = MagicMock()
        fake_db.posts.find.return_value.sort.return_value.skip.return_value.limit.return_value = []
        with patch.object(store, "database", fake_db):
            store.list_posts("TOPIC", ObjectId(), status="unanswered")
            unanswered = fake_db.posts.find.call_args.args[0]
            store.list_posts("TOPIC", ObjectId(), status="resolved")
            resolved = fake_db.posts.find.call_args.args[0]
        self.assertEqual(unanswered["correctCommentCount"], 0)
        self.assertEqual(resolved["correctCommentCount"], {"$gt": 0})

    def test_list_posts_rejects_invalid_page_and_missing_mine_before_query(self):
        fake_db = MagicMock()
        with patch.object(store, "database", fake_db):
            for page in (0, -1):
                with self.subTest(page=page), self.assertRaises(store.InvalidInput):
                    store.list_posts("TOPIC", ObjectId(), page=page)
            for mine in (None, ""):
                with self.subTest(mine=mine), self.assertRaises(store.InvalidInput):
                    store.list_posts("TOPIC", ObjectId(), status="mine", mine=mine)
        fake_db.posts.find.assert_not_called()

    def test_teacher_report_rejects_topic_before_query(self):
        teacher_id = ObjectId()
        fake_db = MagicMock()
        with patch.object(store, "database", fake_db), \
             patch.object(store, "get_space") as get_space:
            with self.assertRaises(store.InvalidInput):
                store.teacher_report(teacher_id, "TOPIC", ObjectId())
        get_space.assert_not_called()
        fake_db.users.aggregate.assert_not_called()


    def test_ensure_indexes_skips_existing_definitions(self):
        fake_db = MagicMock()
        fake_db.__getitem__.side_effect = lambda name: getattr(fake_db, name)
        with patch("ensure_indexes.INDEXES", {"users": [
            ("username_1", [("username", 1)], True)
        ]}):
            fake_db.users.index_information.return_value = {
                "username_1": {"key": [("username", 1)], "unique": True}
            }
            ensure_indexes(fake_db)
        fake_db.users.create_index.assert_not_called()


if __name__ == "__main__":
    unittest.main()
