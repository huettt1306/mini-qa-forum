"""Small route checks with a fake URI; no Atlas connection or test writes."""
import io
import os
import unittest
from unittest.mock import patch
from bson import ObjectId

os.environ["MONGO_URI"] = "mongodb://unit-test.invalid:27017"
os.environ["SECRET_KEY"] = "unit-test-secret"

from api import db as store
from api.index import app


class ForumTest(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()
        self.user_id = ObjectId()
        self.space_id = ObjectId()
        self.post_id = ObjectId()
        self.user = {"_id": self.user_id, "username": "hs00001", "fullName": "Học sinh",
                     "role": "STUDENT", "class": "10A1"}
        self.user_patch = patch.object(store, "get_user", return_value=self.user)
        self.user_patch.start()
        self.addCleanup(self.user_patch.stop)

    def login_as(self, user=None):
        if user is not None:
            self.user = user
        with self.client.session_transaction() as current:
            current["user_id"] = str(self.user_id)
            current["_csrf"] = "test-token"

    def test_login_form_and_csrf(self):
        response = self.client.get("/login")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'name="_csrf"', response.data)
        with patch.object(store, "authenticate") as authenticate:
            response = self.client.post("/login", data={"username": "hs00001", "password": "123456"})
            self.assertEqual(response.status_code, 400)
            authenticate.assert_not_called()

    def test_login_creates_session(self):
        with self.client.session_transaction() as current:
            current["_csrf"] = "test-token"
        with patch.object(store, "authenticate", return_value=self.user):
            response = self.client.post("/login", data={
                "_csrf": "test-token", "username": "hs00001", "password": "123456"})
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as current:
            self.assertEqual(current["user_id"], str(self.user_id))

    def test_student_cannot_open_other_class(self):
        self.login_as()
        other = {"_id": self.space_id, "targetClass": "11A1", "teacherId": ObjectId(),
                 "subjectName": "Toán 11"}
        with patch.object(store, "get_space", return_value=other):
            response = self.client.get(f"/space/SUBJECT_CLASS/{self.space_id}")
        self.assertEqual(response.status_code, 403)

    def test_teacher_cannot_approve_another_teachers_post(self):
        teacher = {**self.user, "role": "TEACHER", "class": None}
        self.login_as(teacher)
        post = {"_id": self.post_id, "spaceType": "SUBJECT_CLASS", "spaceId": self.space_id}
        space = {"_id": self.space_id, "teacherId": ObjectId(), "targetClass": "10A1"}
        with patch.object(store, "get_post", return_value=post), \
             patch.object(store, "get_space", return_value=space), \
             patch.object(store, "set_correct") as approve:
            response = self.client.post(
                f"/post/{self.post_id}/mark-correct/{ObjectId()}", data={"_csrf": "test-token"})
        self.assertEqual(response.status_code, 403)
        approve.assert_not_called()

    def test_post_form_passes_text_to_db(self):
        self.login_as()
        space = {"_id": self.space_id, "targetClass": "10A1", "teacherId": ObjectId(),
                 "subjectName": "Toán 10"}
        with patch.object(store, "get_space", return_value=space), \
             patch.object(store, "create_post") as create:
            response = self.client.post(
                f"/space/SUBJECT_CLASS/{self.space_id}",
                data={"_csrf": "test-token", "title": "Câu hỏi", "content": "Nội dung"},
            )
        self.assertEqual(response.status_code, 302)
        create.assert_called_once_with("SUBJECT_CLASS", str(self.space_id), self.user,
                                       "Câu hỏi", "Nội dung")

    def test_file_upload_rejected(self):
        self.login_as()
        space = {"_id": self.space_id, "targetClass": "10A1", "teacherId": ObjectId(),
                 "subjectName": "Toán 10"}
        with patch.object(store, "get_space", return_value=space), \
             patch.object(store, "create_post") as create:
            response = self.client.post(
                f"/space/SUBJECT_CLASS/{self.space_id}",
                data={"_csrf": "test-token", "title": "Câu hỏi", "content": "Nội dung",
                      "image": (io.BytesIO(b"not-an-image"), "bad.png")},
                content_type="multipart/form-data",
            )
        self.assertEqual(response.status_code, 400)
        create.assert_not_called()

    def test_invalid_id_is_handled(self):
        with self.assertRaises(store.InvalidInput):
            store.object_id("not-an-id")

    def test_notifications_use_logged_in_recipient(self):
        self.login_as()
        with patch.object(store, "list_notifications", return_value=([], 2)) as listing:
            response = self.client.get("/notifications?format=json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["unreadCount"], 2)
        listing.assert_called_once_with(self.user_id, 1)

    def test_mark_notification_read_requires_csrf_and_recipient(self):
        self.login_as()
        notice_id = ObjectId()
        with patch.object(store, "mark_notification_read") as mark:
            self.assertEqual(self.client.post(f"/notifications/{notice_id}/read").status_code, 400)
            mark.assert_not_called()
            response = self.client.post(f"/notifications/{notice_id}/read",
                                        data={"_csrf": "test-token"})
        self.assertEqual(response.status_code, 302)
        mark.assert_called_once_with(str(notice_id), self.user_id)


if __name__ == "__main__":
    unittest.main()
