"""Integration smoke test for the student device approval workflow.

Run after installing requirements-local.txt:
    python -m unittest discover -s tests -v
The test uses an isolated SQLite database under /tmp and does not touch user data.
"""
from __future__ import annotations

import os
import tempfile
import unittest
import base64
from pathlib import Path

_db_dir = tempfile.mkdtemp(prefix="infinite-academy-test-")
_db_file = Path(_db_dir) / "test.db"
_db_url = f"sqlite:///{_db_file}"
os.environ["DATABASE_URL_MAIN"] = _db_url
os.environ["DATABASE_URL_COURSES"] = _db_url
os.environ["DATABASE_URL_AUDIT"] = _db_url
os.environ["SECRET_KEY"] = "test-key-only-do-not-use-in-production"
os.environ["WTF_CSRF_ENABLED"] = "false"
os.environ["DEVICE_LIMIT"] = "1"
os.environ["SINGLE_SESSION"] = "true"

from werkzeug.security import generate_password_hash  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
import app as app_module  # noqa: E402
from app import app, db, Course, Device, Lesson, SecurityEvent, User  # noqa: E402


class DevicePolicyTest(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        with app.app_context():
            db.drop_all()
            db.create_all()
            self.admin = User(
                email="admin@test.local", username="Admin",
                password_hash=generate_password_hash("Admin-password-123!"),
                role="admin", status="active",
            )
            self.student = User(
                email="student@test.local", username="Student",
                password_hash=generate_password_hash("Student-password-123!"),
                role="student", status="active", whatsapp_number="+447700900123",
            )
            db.session.add_all([self.admin, self.student])
            db.session.commit()
            self.admin_id = self.admin.id
            self.student_id = self.student.id
            self.course = Course(title="Test course", description="Test course", is_published=True)
            db.session.add(self.course)
            db.session.commit()
            self.lesson = Lesson(
                course_id=self.course.id, title="Test lesson", description="Test lesson",
                vdocipher_video_id="test-video-id", sort_order=1, is_published=True,
            )
            db.session.add(self.lesson)
            db.session.commit()
            self.course_id = self.course.id
            self.lesson_id = self.lesson.id
        self.admin_client = app.test_client()
        self.student_client_1 = app.test_client()
        self.student_client_2 = app.test_client()

    @staticmethod
    def _b64url(data):
        return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")

    @staticmethod
    def _b64url_decode(data):
        return base64.urlsafe_b64decode(data + ("=" * (-len(data) % 4)))

    def native_login(self, client, installation_id, private_key=None, *, signature_key=None, challenge_override=None):
        private_key = private_key or ec.generate_private_key(ec.SECP256R1())
        public_key = private_key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        public_key_b64 = self._b64url(public_key)
        challenge_response = client.post("/api/device/challenge", json={
            "email": "student@test.local",
            "installation_id": installation_id,
            "public_key": public_key_b64,
        })
        self.assertEqual(challenge_response.status_code, 200, challenge_response.get_data(as_text=True))
        challenge_json = challenge_response.get_json()
        nonce_text = challenge_override or challenge_json["challenge"]
        nonce = self._b64url_decode(nonce_text)
        signer = signature_key or private_key
        signature = signer.sign(nonce, ec.ECDSA(hashes.SHA256()))
        data = {
            "email": "student@test.local",
            "password": "Student-password-123!",
            "app_installation_id": installation_id,
            "app_public_key": public_key_b64,
            "app_challenge_id": challenge_json["challenge_id"],
            "app_challenge": nonce_text,
            "app_signature": self._b64url(signature),
        }
        return client.post("/login", data=data, follow_redirects=False), private_key, data

    def test_admin_bypass_and_student_device_replacement(self):
        response = self.admin_client.post(
            "/login",
            data={"email": "admin@test.local", "password": "Admin-password-123!"},
        )
        self.assertEqual(response.status_code, 302)
        with app.app_context():
            self.assertEqual(Device.query.filter_by(user_id=self.admin_id).count(), 0)

        first_login = self.student_client_1.post(
            "/login",
            data={"email": "student@test.local", "password": "Student-password-123!"},
        )
        self.assertEqual(first_login.status_code, 302)
        with app.app_context():
            records = Device.query.filter_by(user_id=self.student_id).order_by(Device.id).all()
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].status, "approved")
            original_device_id = records[0].id

        second_login = self.student_client_2.post(
            "/login",
            data={"email": "student@test.local", "password": "Student-password-123!"},
        )
        self.assertEqual(second_login.status_code, 200)
        self.assertIn("بانتظار المراجعة", second_login.get_data(as_text=True))
        with app.app_context():
            records = Device.query.filter_by(user_id=self.student_id).order_by(Device.id).all()
            self.assertEqual([record.status for record in records], ["approved", "pending"])
            replacement_device_id = records[1].id

        approval = self.admin_client.post(f"/admin/devices/{replacement_device_id}/approve")
        self.assertEqual(approval.status_code, 302)
        with app.app_context():
            statuses = {
                record.id: record.status
                for record in Device.query.filter_by(user_id=self.student_id).all()
            }
            self.assertEqual(statuses[original_device_id], "blocked")
            self.assertEqual(statuses[replacement_device_id], "approved")
            replacement = db.session.get(Device, replacement_device_id)
            self.assertIsNone(replacement.last_active_at)

        from unittest.mock import patch
        with patch.object(app_module.requests, "post") as vdocipher_post:
            revoked_playback = self.student_client_1.post(f"/lessons/{self.lesson_id}/playback", json={})
        self.assertEqual(revoked_playback.status_code, 401)
        self.assertEqual(revoked_playback.get_json()["error"], "session_revoked")
        vdocipher_post.assert_not_called()

        old_session = self.student_client_1.get("/dashboard")
        self.assertEqual(old_session.status_code, 302)
        self.assertIn("/login", old_session.headers["Location"])
        dashboard = self.admin_client.get("/admin")
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn("تحقّق عبر واتساب", dashboard.get_data(as_text=True))


    def test_native_installation_id_reuses_the_same_device_record_after_cookie_loss(self):
        installation_id = "123e4567-e89b-42d3-a456-426614174000"
        first_login, key_pair, _ = self.native_login(self.student_client_1, installation_id)
        self.assertEqual(first_login.status_code, 302)
        with app.app_context():
            records = Device.query.filter_by(user_id=self.student_id).all()
            self.assertEqual(len(records), 1)
            record = records[0]
            self.assertEqual(record.status, "approved")
            self.assertIsNotNone(record.app_installation_hash)
            self.assertIsNotNone(record.app_public_key_hash)
            self.assertNotEqual(record.app_installation_hash, installation_id)
            original_device_id = record.id

        # A new WebView/browser cookie but the same native key + install ID resolves the same row.
        second_client = app.test_client()
        second_login, _, _ = self.native_login(second_client, installation_id, private_key=key_pair)
        self.assertEqual(second_login.status_code, 302)
        with app.app_context():
            records = Device.query.filter_by(user_id=self.student_id).all()
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].id, original_device_id)
            self.assertEqual(records[0].status, "approved")
            self.assertEqual(records[0].label, "تطبيق Infinite Academy (Android)")

    def test_different_native_installation_waits_for_admin_approval(self):
        first_id = "123e4567-e89b-42d3-a456-426614174000"
        second_id = "123e4567-e89b-42d3-a456-426614174001"
        first, _, _ = self.native_login(self.student_client_1, first_id)
        self.assertEqual(first.status_code, 302)
        second, _, _ = self.native_login(self.student_client_2, second_id)
        self.assertIn(second.status_code, (200, 401))
        body = second.get_data(as_text=True)
        self.assertTrue("بانتظار المراجعة" in body or "تعذّر اعتماد هذا الجهاز" in body)
        with app.app_context():
            records = Device.query.filter_by(user_id=self.student_id).order_by(Device.id).all()
            self.assertEqual([record.status for record in records], ["approved", "pending"])
            self.assertTrue(all(record.app_installation_hash for record in records))
            self.assertTrue(all(record.app_public_key_hash for record in records))

    def test_native_challenge_cannot_be_replayed(self):
        installation_id = "123e4567-e89b-42d3-a456-426614174000"
        response, _, submitted_data = self.native_login(self.student_client_1, installation_id)
        self.assertEqual(response.status_code, 302)
        replay_client = app.test_client()
        replay = replay_client.post("/login", data=submitted_data, follow_redirects=False)
        self.assertEqual(replay.status_code, 401)
        with app.app_context():
            self.assertEqual(Device.query.filter_by(user_id=self.student_id).count(), 1)

    def test_native_device_rejects_invalid_signature(self):
        private_key = ec.generate_private_key(ec.SECP256R1())
        wrong_key = ec.generate_private_key(ec.SECP256R1())
        response, _, _ = self.native_login(
            self.student_client_1,
            "123e4567-e89b-42d3-a456-426614174000",
            private_key=private_key,
            signature_key=wrong_key,
        )
        self.assertEqual(response.status_code, 401)
        with app.app_context():
            self.assertEqual(Device.query.filter_by(user_id=self.student_id).count(), 0)

    def test_native_device_key_change_cannot_inherit_existing_approval(self):
        installation_id = "123e4567-e89b-42d3-a456-426614174000"
        first, _, _ = self.native_login(self.student_client_1, installation_id)
        self.assertEqual(first.status_code, 302)
        second_client = app.test_client()
        second, _, _ = self.native_login(second_client, installation_id)
        self.assertIn(second.status_code, (200, 401))
        with app.app_context():
            records = Device.query.filter_by(user_id=self.student_id).all()
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].status, "approved")
            self.assertIsNotNone(records[0].app_public_key_hash)

    def test_native_keyless_legacy_device_requires_admin_review(self):
        # Simulate a previously-approved v9 device that only had a UUID and no Keystore key.
        installation_id = "123e4567-e89b-42d3-a456-426614174000"
        _, _, _ = self.native_login(self.student_client_1, installation_id)
        with app.app_context():
            record = Device.query.filter_by(user_id=self.student_id).first()
            record.app_public_key = None
            record.app_public_key_hash = None
            db.session.commit()
        second_client = app.test_client()
        response, _, _ = self.native_login(second_client, installation_id)
        self.assertEqual(response.status_code, 200)
        with app.app_context():
            record = Device.query.filter_by(user_id=self.student_id).first()
            self.assertEqual(record.status, "pending")
            self.assertIsNotNone(record.app_public_key_hash)

    def test_suspended_account_session_is_revoked(self):
        admin_login = self.admin_client.post(
            "/login",
            data={"email": "admin@test.local", "password": "Admin-password-123!"},
        )
        self.assertEqual(admin_login.status_code, 302)
        login = self.student_client_1.post(
            "/login",
            data={"email": "student@test.local", "password": "Student-password-123!"},
        )
        self.assertEqual(login.status_code, 302)
        old_setting = app_module.SINGLE_SESSION
        app_module.SINGLE_SESSION = False
        try:
            suspended = self.admin_client.post(f"/admin/users/{self.student_id}/suspend")
            self.assertEqual(suspended.status_code, 302)
            dashboard = self.student_client_1.get("/dashboard")
            self.assertEqual(dashboard.status_code, 302)
            self.assertIn("/login", dashboard.headers["Location"])
        finally:
            app_module.SINGLE_SESSION = old_setting

    def test_change_password_requires_current_password_and_revokes_session(self):
        login = self.student_client_1.post(
            "/login",
            data={"email": "student@test.local", "password": "Student-password-123!"},
        )
        self.assertEqual(login.status_code, 302)
        changed = self.student_client_1.post(
            "/change-password",
            data={
                "current_password": "Student-password-123!",
                "password": "Student-password-789!",
                "confirm_password": "Student-password-789!",
            },
        )
        self.assertEqual(changed.status_code, 302)
        self.assertIn("/login", changed.headers["Location"])
        with app.app_context():
            user = db.session.get(User, self.student_id)
            self.assertTrue(user.check_password("Student-password-789!"))
            self.assertIsNone(user.session_hash)

    def test_registration_rejects_weak_password(self):
        response = self.student_client_1.post(
            "/register",
            data={
                "username": "New Student",
                "email": "new.student@test.local",
                "whatsapp_number": "+447700900124",
                "password": "too-short-1",
            },
        )
        self.assertEqual(response.status_code, 200)
        with app.app_context():
            self.assertIsNone(User.query.filter_by(email="new.student@test.local").first())

    def test_login_attempts_are_rate_limited(self):
        last = None
        for _ in range(11):
            last = self.student_client_1.post(
                "/login",
                data={"email": "student@test.local", "password": "wrong-password"},
            )
        self.assertIsNotNone(last)
        self.assertEqual(last.status_code, 429)
        with app.app_context():
            self.assertGreaterEqual(SecurityEvent.query.filter_by(event_type="login_failed").count(), 10)

    def test_student_cannot_admin_reset_password_and_email_routes_are_removed(self):
        login = self.student_client_1.post(
            "/login",
            data={"email": "student@test.local", "password": "Student-password-123!"},
        )
        self.assertEqual(login.status_code, 302)
        denied = self.student_client_1.post(
            f"/admin/users/{self.student_id}/reset-password",
            data={"new_password": "New-temporary-password-123!", "confirm_password": "New-temporary-password-123!"},
        )
        self.assertEqual(denied.status_code, 403)
        for path in ("/forgot-password", "/reset-password/example-token", "/verify-email/example-token", "/resend-verification"):
            response = self.student_client_2.get(path)
            self.assertEqual(response.status_code, 404, path)

    def test_admin_can_reset_student_password_and_revoke_sessions(self):
        student_login = self.student_client_1.post(
            "/login",
            data={"email": "student@test.local", "password": "Student-password-123!"},
        )
        self.assertEqual(student_login.status_code, 302)
        admin_login = self.admin_client.post(
            "/login",
            data={"email": "admin@test.local", "password": "Admin-password-123!"},
        )
        self.assertEqual(admin_login.status_code, 302)
        with app.app_context():
            user = db.session.get(User, self.student_id)
            user.session_hash = "active-session-hash"
            old_version = user.session_version
            db.session.commit()
        reset = self.admin_client.post(
            f"/admin/users/{self.student_id}/reset-password",
            data={"new_password": "Admin-set-password-456!", "confirm_password": "Admin-set-password-456!"},
        )
        self.assertEqual(reset.status_code, 302)
        with app.app_context():
            user = db.session.get(User, self.student_id)
            self.assertTrue(user.check_password("Admin-set-password-456!"))
            self.assertIsNone(user.session_hash)
            self.assertEqual(user.session_version, old_version + 1)
            event = SecurityEvent.query.filter_by(event_type="admin_reset_student_password", user_id=self.student_id).first()
            self.assertIsNotNone(event)
            self.assertNotIn("Admin-set-password-456!", event.details or "")

    def test_admin_reset_rejects_mismatched_password_confirmation(self):
        admin_login = self.admin_client.post(
            "/login",
            data={"email": "admin@test.local", "password": "Admin-password-123!"},
        )
        self.assertEqual(admin_login.status_code, 302)
        response = self.admin_client.post(
            f"/admin/users/{self.student_id}/reset-password",
            data={"new_password": "Admin-set-password-456!", "confirm_password": "Not-the-same-password!"},
        )
        self.assertEqual(response.status_code, 302)
        with app.app_context():
            student = db.session.get(User, self.student_id)
            self.assertTrue(student.check_password("Student-password-123!"))
            self.assertFalse(student.check_password("Admin-set-password-456!"))


    def test_admin_can_activate_pending_account_directly(self):
        admin_login = self.admin_client.post(
            "/login",
            data={"email": "admin@test.local", "password": "Admin-password-123!"},
        )
        self.assertEqual(admin_login.status_code, 302)
        with app.app_context():
            student = db.session.get(User, self.student_id)
            student.status = "pending"
            student.email_verified = False  # compatibility field must not gate manual activation
            db.session.commit()
        response = self.admin_client.post(f"/admin/users/{self.student_id}/activate")
        self.assertEqual(response.status_code, 302)
        with app.app_context():
            student = db.session.get(User, self.student_id)
            self.assertEqual(student.status, "active")


    def test_course_lesson_and_playback_are_checked_on_server(self):
        from unittest.mock import Mock, patch

        login = self.student_client_1.post(
            "/login",
            data={"email": "student@test.local", "password": "Student-password-123!"},
        )
        self.assertEqual(login.status_code, 302)

        fake_response = Mock()
        fake_response.raise_for_status.return_value = None
        fake_response.json.return_value = {"otp": "test-otp", "playbackInfo": "test-playback-info"}
        with patch.dict(os.environ, {"VDOCIPHER_API_SECRET": "unit-test-secret"}), \
                patch.object(app_module.requests, "post", return_value=fake_response) as vdocipher_post:
            page = self.student_client_1.get(f"/lessons/{self.lesson_id}")
            self.assertEqual(page.status_code, 200)
            self.assertNotIn("test-otp", page.get_data(as_text=True))
            self.assertIn("lesson-player.js", page.get_data(as_text=True))
            vdocipher_post.assert_not_called()

            with app.app_context():
                student = db.session.get(User, self.student_id)
                student.status = "pending"
                db.session.commit()
            inactive = self.student_client_1.post(f"/lessons/{self.lesson_id}/playback", json={})
            self.assertEqual(inactive.status_code, 403)
            self.assertEqual(inactive.get_json()["error"], "account_inactive")
            vdocipher_post.assert_not_called()

            with app.app_context():
                student = db.session.get(User, self.student_id)
                student.status = "active"
                db.session.commit()
            allowed = self.student_client_1.post(f"/lessons/{self.lesson_id}/playback", json={})
            self.assertEqual(allowed.status_code, 200)
            self.assertEqual(allowed.get_json(), {"otp": "test-otp", "playback_info": "test-playback-info"})
            vdocipher_post.assert_called_once()
            self.assertEqual(vdocipher_post.call_args.kwargs["headers"]["Authorization"], "Apisecret unit-test-secret")

        # Protected course pages also require an approved device, not merely a valid login cookie.
        first_device_login = self.student_client_2.post(
            "/login",
            data={"email": "student@test.local", "password": "Student-password-123!"},
        )
        self.assertEqual(first_device_login.status_code, 200)
        old_single_session = app_module.SINGLE_SESSION
        app_module.SINGLE_SESSION = False
        try:
            with self.student_client_2.session_transaction() as student_session:
                student_session["_user_id"] = str(self.student_id)
                student_session["_fresh"] = True
                student_session["session_version"] = 0
            with patch.dict(os.environ, {"VDOCIPHER_API_SECRET": "unit-test-secret"}), \
                    patch.object(app_module.requests, "post") as vdocipher_post:
                denied_playback = self.student_client_2.post(f"/lessons/{self.lesson_id}/playback", json={})
                self.assertEqual(denied_playback.status_code, 403)
                self.assertEqual(denied_playback.get_json()["error"], "device_not_approved")
                vdocipher_post.assert_not_called()
                denied_course = self.student_client_2.get(f"/courses/{self.course_id}")
                self.assertEqual(denied_course.status_code, 302)
                self.assertIn("/dashboard", denied_course.headers["Location"])
        finally:
            app_module.SINGLE_SESSION = old_single_session

    def test_admin_can_request_lesson_playback_without_student_device(self):
        from unittest.mock import Mock, patch

        login = self.admin_client.post(
            "/login",
            data={"email": "admin@test.local", "password": "Admin-password-123!"},
        )
        self.assertEqual(login.status_code, 302)
        fake_response = Mock()
        fake_response.raise_for_status.return_value = None
        fake_response.json.return_value = {"otp": "admin-test-otp", "playbackInfo": "admin-test-playback"}
        with patch.dict(os.environ, {"VDOCIPHER_API_SECRET": "unit-test-secret"}), \
                patch.object(app_module.requests, "post", return_value=fake_response) as vdocipher_post:
            response = self.admin_client.post(f"/lessons/{self.lesson_id}/playback", json={})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["otp"], "admin-test-otp")
        vdocipher_post.assert_called_once()

    def test_health_endpoint_returns_json_for_all_database_binds(self):
        # Regression: the default bind key is None, which made Flask's sorted JSON dump raise TypeError (HTTP 500).
        response = app.test_client().get("/health")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        payload = response.get_json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(set(payload["databases"]), {"main", "courses", "audit"})

    def test_admin_dashboard_renders_with_lessons_table(self):
        self.admin_client.post("/login", data={"email": "admin@test.local", "password": "Admin-password-123!"})
        response = self.admin_client.get("/admin")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Test lesson", response.get_data(as_text=True))

    def test_admin_lesson_rejects_unsafe_video_id(self):
        self.admin_client.post("/login", data={"email": "admin@test.local", "password": "Admin-password-123!"})
        response = self.admin_client.post("/admin/lessons/create", data={
            "course_id": str(self.course_id), "title": "Unsafe", "sort_order": "2",
            "vdocipher_video_id": "../../admin/secrets",
        })
        self.assertEqual(response.status_code, 302)
        with app.app_context():
            self.assertEqual(Lesson.query.count(), 1)

    def test_csrf_failure_returns_friendly_json_error(self):
        app.config["WTF_CSRF_ENABLED"] = True
        try:
            response = app.test_client().post("/device/heartbeat", headers={"Accept": "application/json"})
        finally:
            app.config["WTF_CSRF_ENABLED"] = False
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "csrf_failed")


if __name__ == "__main__":
    unittest.main()
