"""Focused data-rule tests; all MongoDB objects here are local fakes."""
import os
import unittest
from unittest.mock import MagicMock, patch

from bson import ObjectId

os.environ["MONGO_URI"] = "mongodb://unit-test.invalid:27017"

from api import db as store
from ensure_indexes import ensure_indexes


class DataRulesTest(unittest.TestCase):
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

    def test_approve_rejects_comment_from_other_post(self):
        post = {"_id": ObjectId()}
        comment_id = ObjectId()
        fake_db = MagicMock()
        fake_db.comments.find_one_and_update.return_value = None
        with patch.object(store, "database", fake_db), patch.object(store, "client", MagicMock()), \
             patch.object(store, "get_post", return_value=post):
            with self.assertRaises(store.ConflictError):
                store.set_correct(post["_id"], comment_id, True, ObjectId())
        self.assertEqual(
            fake_db.comments.find_one_and_update.call_args.args[0]["postId"], post["_id"]
        )
        fake_db.posts.update_one.assert_not_called()

    def test_approval_creates_and_unmark_removes_only_approval_notice(self):
        post = {"_id": ObjectId(), "spaceId": ObjectId(), "spaceType": "TOPIC"}
        author_id, teacher_id, comment_id = ObjectId(), ObjectId(), ObjectId()
        comment = {"_id": comment_id, "author": {"userId": author_id}}
        fake_db = MagicMock()
        fake_db.comments.find_one_and_update.return_value = comment
        fake_db.posts.update_one.return_value.matched_count = 1
        with patch.object(store, "database", fake_db), patch.object(store, "client", MagicMock()), \
             patch.object(store, "get_post", return_value=post):
            store.set_correct(post["_id"], comment_id, True, teacher_id)
            store.set_correct(post["_id"], comment_id, False, teacher_id)
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
        self.assertEqual(fake_db.notifications.update_one.call_args.args[0]["recipientId"],
                         fake_db.notifications.find_one.call_args.args[0]["recipientId"])
        self.assertEqual(fake_db.notifications.update_one.call_args.args[0]["isRead"], False)
        self.assertEqual(fake_db.notifications.update_one.call_args.args[1], {"$set": {"isRead": True}})

    def test_unread_count_uses_boolean(self):
        fake_db = MagicMock()
        fake_db.notifications.find.return_value.sort.return_value.skip.return_value.limit.return_value = []
        fake_db.notifications.count_documents.return_value = 3
        with patch.object(store, "database", fake_db):
            _, count = store.list_notifications(ObjectId())
        self.assertEqual(count, 3)
        self.assertEqual(fake_db.notifications.count_documents.call_args.args[0]["isRead"], False)


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
