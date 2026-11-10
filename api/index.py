"""HTTP routes, session checks, CSRF and templates."""
import hmac
import os
import secrets
from pathlib import Path

from flask import Flask, flash, g, jsonify, redirect, render_template, request, session, url_for

if __package__:
    from . import db as store
    from .constants import NOTIFICATIONS_PER_PAGE, POSTS_PER_PAGE
else:
    import db as store
    from constants import NOTIFICATIONS_PER_PAGE, POSTS_PER_PAGE

BASE_DIR = Path(__file__).resolve().parent.parent
app = Flask(__name__, template_folder=str(BASE_DIR / "templates"))
app.secret_key = os.environ.get("SECRET_KEY")
if not app.secret_key:
    if os.environ.get("VERCEL"):
        raise RuntimeError("SECRET_KEY phải được cấu hình trên Vercel")
    app.secret_key = secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=bool(os.environ.get("VERCEL")),
    MAX_CONTENT_LENGTH=64 * 1024,
)


@app.context_processor
def inject_csrf():
    def token():
        return session.setdefault("_csrf", secrets.token_urlsafe(32))
    return {"csrf_token": token}


@app.before_request
def protect_and_load_user():
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        expected = session.get("_csrf", "")
        received = request.form.get("_csrf", "") or request.headers.get("X-CSRFToken", "")
        if not expected or not hmac.compare_digest(expected, received):
            raise store.InvalidInput("Phiên biểu mẫu không hợp lệ, hãy tải lại trang")
    g.user = None
    if session.get("user_id"):
        try:
            g.user = store.get_user(session["user_id"])
        except store.ForumError:
            session.clear()
    if not g.user and request.method == "GET" and request.endpoint not in {"login", "static"}:
        return redirect(url_for("login"))


@app.errorhandler(store.ForumError)
def handle_forum_error(error):
    status = {
        store.EntityNotFound: 404,
        store.PermissionDenied: 403,
        store.ConflictError: 409,
        store.InvalidInput: 400,
    }.get(type(error), 400)
    flash(str(error), "danger")
    return render_template("error.html", message=str(error), status=status), status


def require_user(*roles):
    if not g.user:
        raise store.PermissionDenied("Vui lòng đăng nhập")
    if roles and g.user["role"] not in roles:
        raise store.PermissionDenied("Bạn không có quyền thực hiện thao tác này")
    return g.user


def accessible_space(space_type, space_id, moderate=False):
    user = require_user()
    space = store.get_space(space_type, space_id)
    if moderate:
        if user["role"] != "TEACHER" or space["teacherId"] != user["_id"]:
            raise store.PermissionDenied("Bạn không phụ trách không gian này")
    elif space_type == "SUBJECT_CLASS" and user["role"] != "ADMIN":
        if user["role"] == "STUDENT" and user.get("class") != space["targetClass"]:
            raise store.PermissionDenied("Bạn không thuộc lớp này")
        if user["role"] == "TEACHER" and space["teacherId"] != user["_id"]:
            raise store.PermissionDenied("Bạn không phụ trách lớp này")
    return space


def accessible_post(post_id, moderate=False):
    post = store.get_post(post_id)
    accessible_space(post["spaceType"], post["spaceId"], moderate=moderate)
    return post


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("home"))
    if request.method == "POST":
        user = store.authenticate(
            request.form.get("username", "").strip(),
            request.form.get("password", ""),
        )
        session.clear()
        session["user_id"] = str(user["_id"])
        flash(f"Xin chào {user['fullName']}!", "success")
        return redirect(url_for("home"))
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("Bạn đã đăng xuất", "info")
    return redirect(url_for("login"))


@app.route("/password", methods=["GET", "POST"])
def password():
    user = require_user()
    if request.method == "POST":
        new_password = request.form.get("new_password", "")
        if new_password != request.form.get("confirm_password", ""):
            raise store.InvalidInput("Xác nhận mật khẩu không khớp")
        store.change_password(user["_id"], request.form.get("old_password", ""), new_password)
        session.clear()
        flash("Đã đổi mật khẩu, vui lòng đăng nhập lại", "success")
        return redirect(url_for("login"))
    return render_template("password.html")


@app.route("/")
def home():
    user = require_user()
    classes, topics = store.list_spaces(user)
    return render_template("index.html", subject_classes=classes, topics=topics)


@app.route("/space/<space_type>/<space_id>", methods=["GET", "POST"])
def space_detail(space_type, space_id):
    space = accessible_space(space_type, space_id)
    if request.method == "POST":
        require_user("STUDENT", "TEACHER")
        if request.files:
            raise store.InvalidInput("Chỉ chấp nhận nội dung văn bản")
        title = request.form.get("title", "").strip()
        content = request.form.get("content", "").strip()
        if not title or not content:
            raise store.InvalidInput("Vui lòng nhập tiêu đề và nội dung")
        store.create_post(space_type, space_id, g.user, title, content)
        flash("Đã đăng câu hỏi", "success")
        return redirect(url_for("space_detail", space_type=space_type, space_id=space_id))
    page = request.args.get("page", 1, type=int)
    if page < 1:
        raise store.InvalidInput("Trang không hợp lệ")
    search = request.args.get("q", "").strip()
    status = request.args.get("status", "all")
    rows = store.list_posts(space_type, space_id, search, status, g.user["_id"], page)
    space_name = (f"{space['subjectName']} - Lớp {space['targetClass']}"
                  if space_type == "SUBJECT_CLASS" else space["title"])
    return render_template("space.html", space=space, space_name=space_name,
                           space_type=space_type, space_id=space_id,
                           posts=rows[:POSTS_PER_PAGE],
                           has_next=len(rows) > POSTS_PER_PAGE,
                           page=page, search=search, status=status)


@app.route("/post/<post_id>", methods=["GET", "POST"])
def post_detail(post_id):
    post = accessible_post(post_id)
    if request.method == "POST":
        require_user("STUDENT", "TEACHER")
        if request.files:
            raise store.InvalidInput("Chỉ chấp nhận nội dung văn bản")
        content = request.form.get("content", "").strip()
        if not content:
            raise store.InvalidInput("Vui lòng nhập nội dung phản hồi")
        store.create_comment(post, g.user, content)
        flash("Đã gửi phản hồi", "success")
        return redirect(url_for("post_detail", post_id=post_id))
    space = store.get_space(post["spaceType"], post["spaceId"])
    can_moderate = g.user["role"] == "TEACHER" and space["teacherId"] == g.user["_id"]
    comments = store.list_comments(post_id)
    return render_template("post_detail.html", post=post, comments=comments,
                           can_moderate=can_moderate)


@app.route("/post/<post_id>/mark-correct/<comment_id>", methods=["POST"])
def mark_correct(post_id, comment_id):
    accessible_post(post_id, moderate=True)
    store.set_correct(post_id, comment_id, True, g.user["_id"])
    flash("Đã duyệt đáp án", "success")
    return redirect(url_for("post_detail", post_id=post_id))


@app.route("/post/<post_id>/unmark-correct/<comment_id>", methods=["POST"])
def unmark_correct(post_id, comment_id):
    accessible_post(post_id, moderate=True)
    store.set_correct(post_id, comment_id, False, g.user["_id"])
    flash("Đã hủy duyệt đáp án", "success")
    return redirect(url_for("post_detail", post_id=post_id))


@app.route("/notifications")
def notifications():
    user = require_user()
    page = request.args.get("page", 1, type=int)
    rows, unread = store.list_notifications(user["_id"], page)
    has_next = len(rows) > NOTIFICATIONS_PER_PAGE
    notices = [{
        "id": str(row["_id"]), "type": row["type"], "message": row["message"],
        "postId": str(row["postId"]), "commentId": str(row["commentId"]),
        "createdAt": row["createdAt"].isoformat(),
        "isRead": row["isRead"],
    } for row in rows[:NOTIFICATIONS_PER_PAGE]]
    if request.accept_mimetypes.best == "application/json" or request.args.get("format") == "json":
        return jsonify(notifications=notices, unreadCount=unread)
    return render_template("notifications.html", notifications=notices,
                           unread_count=unread, page=page, has_next=has_next)


@app.route("/notifications/<notification_id>/read", methods=["POST"])
def notification_read(notification_id):
    user = require_user()
    store.mark_notification_read(notification_id, user["_id"])
    return redirect(url_for("notifications"))


@app.route("/admin")
def admin_dashboard():
    require_user("ADMIN")
    classes, topics = store.list_spaces(g.user)
    return render_template("admin.html", summary=store.admin_summary(),
                           classes=classes, topics=topics, teachers=store.list_teachers())


@app.route("/admin/topics", methods=["POST"])
def admin_create_topic():
    require_user("ADMIN")
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    if not title or not description:
        raise store.InvalidInput("Vui lòng nhập tiêu đề và mô tả chủ đề")
    store.create_topic(title, description, request.form.get("teacher_id"))
    flash("Đã tạo chủ đề", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/assign/<space_type>/<space_id>", methods=["POST"])
def admin_assign(space_type, space_id):
    require_user("ADMIN")
    store.assign_teacher(space_type, space_id, request.form.get("teacher_id"))
    flash("Đã cập nhật giáo viên phụ trách", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/reports")
def reports():
    require_user("TEACHER")
    classes, topics = store.list_spaces(g.user)
    owned = [("SUBJECT_CLASS", item) for item in classes]
    owned += [("TOPIC", item) for item in topics if item["teacherId"] == g.user["_id"]]
    selected = request.args.get("space", "")
    space_type, _, space_id = selected.partition(":")
    rows = store.teacher_report(g.user["_id"], space_type, space_id) if space_type and space_id else []
    return render_template("reports.html", spaces=owned, rows=rows,
                           selected_type=space_type, selected_id=space_id)


if __name__ == "__main__":
    app.run(port=5000)
