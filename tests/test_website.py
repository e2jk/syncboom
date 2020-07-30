#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Running the tests:
# $ python3 -m unittest discover --start-directory ./tests/
# Checking the coverage of the tests:
# $ coverage run --include=./*.py --omit=tests/* -m unittest discover && \
#   rm -rf html_dev/coverage && coverage html --directory=html_dev/coverage \
#   --title="Code test coverage for SyncBoom"

from datetime import datetime, timedelta
import unittest
from app import create_app, db
from app.models import User, load_user, Task, Mapping
from app.email import send_email
from config import Config, basedir
import sys
import io
import contextlib
import os
from unittest.mock import patch, call, MagicMock
from sqlalchemy.exc import IntegrityError
from redis.exceptions import RedisError
from rq.exceptions import NoSuchJobError
from datetime import datetime, timedelta
import json
from urllib.parse import quote

if not os.environ.get("FLASK_DEBUG"):
    # Suppress output when starting up app from website.py or app/tasks.py
    with contextlib.redirect_stderr(io.StringIO()):
        import app.tasks
        from app.tasks import _set_task_progress, run_mapping
        from website import make_shell_context
else:
    import app.tasks
    from app.tasks import _set_task_progress, run_mapping
    from website import make_shell_context


class TestMakeShellContext(unittest.TestCase):
    def test_make_shell_context(self):
        return_value = make_shell_context()
        for i in ("db", "User", "Notification", "Task", "Mapping"):
            self.assertTrue(i in return_value)


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite://'
    WTF_CSRF_ENABLED = False
    TRELLO_API_KEY = "a1"*16


class WebsiteTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        self.client = self.app.test_client()
        app.tasks.app = self.app

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def create_user(self, username, password, email=None, trello_token=None,
        trello_username=None):
        u = User(username=username, email=email, trello_token=trello_token,
        trello_username=trello_username)
        u.set_password(password)
        db.session.add(u)
        db.session.commit()
        return u

    def login(self, username, password, follow_redirects=True):
        return self.client.post(
            '/auth/login',
            data=dict(username=username, password=password),
            follow_redirects=follow_redirects
        )


class UserModelCase(WebsiteTestCase):
    def test_password_hashing(self):
        u = User(username='susan')
        u.set_password('cat')
        self.assertFalse(u.check_password('dog'))
        self.assertTrue(u.check_password('cat'))

    def test_avatar(self):
        u = User(username='john', email='john@example.com')
        self.assertEqual(u.avatar(128), ('https://www.gravatar.com/avatar/'
                                         'd4c74594d841139328695756648b6bd6'
                                         '?d=identicon&s=128'))

    def test_repr(self):
        u = User(username='john', email='john@example.com')
        self.assertEqual(str(u), "<User john>")

    def test_password_reset_token_valid(self):
        u1 = User(username='john', email='john@example.com')
        db.session.add(u1)
        db.session.commit()
        token = u1.get_reset_password_token()
        u2 = User.verify_reset_password_token(token)
        self.assertEqual(u2, u1)

    def test_password_reset_token_invalid(self):
        u = User.verify_reset_password_token("Invalid token")
        self.assertEqual(u, None)

    def test_add_notification(self):
        u = User(username='john', email='john@example.com')
        db.session.add(u)
        n1 = u.add_notification("First notification", {"aa": "abc", "bb": "def"})
        n2 = u.add_notification("Second notification", {"cc": "ghi"})
        self.assertEqual(u.notifications.all(), [n1, n2])

    def test_run_mapping_valid_task(self):
        u = User(username='john', email='john@example.com')
        mock = MagicMock()
        mock.get_id.side_effect = ['foobarbaz', 'other_id']
        with patch.object(self.app.task_queue, 'enqueue', return_value=mock) \
            as mock_enqueue_method:
            t1 = u.launch_task("run_mapping", (123, "board", "abc"), "Description")
            t1.complete = True
            t2 = u.launch_task("run_mapping", (123, "board", "abc"), "Desc 2")
        self.assertEqual(t1.id, 'foobarbaz')
        self.assertEqual(t1.name, 'run_mapping')
        self.assertEqual(t1.description, 'Description')
        self.assertEqual(t1.user, u)
        self.assertEqual(u.tasks.all(), [t1, t2])
        expected_calls = [
            call('app.tasks.run_mapping', 123, 'board', 'abc'),
            call('app.tasks.run_mapping', 123, 'board', 'abc')
        ]
        self.assertEqual(mock_enqueue_method.mock_calls, expected_calls)
        self.assertEqual(u.get_tasks_in_progress(), [t2])
        self.assertEqual(u.get_task_in_progress("run_mapping"), t2)
        self.assertEqual(u.get_recent_tasks(), [t2, t1])

    def test_launch_task_invalid(self):
        u = User(username='john', email='john@example.com')
        t1 = u.launch_task("name", (), "description")
        t2 = u.launch_task("run_mapping", (None, None), "description")
        self.assertEqual(t1, None)
        self.assertEqual(t2, None)

    def test_same_username(self):
        u1 = User(username='john', email='john1@example.com')
        u2 = User(username='john', email='john2@example.com')
        db.session.add(u1)
        db.session.add(u2)
        with self.assertRaises(IntegrityError) as cm:
            db.session.commit()
        self.assertEqual(str(cm.exception.orig),
            'UNIQUE constraint failed: user.username')

    def test_same_email(self):
        u1 = User(username='john1', email='john@example.com')
        u2 = User(username='john2', email='john@example.com')
        db.session.add(u1)
        db.session.add(u2)
        with self.assertRaises(IntegrityError) as cm:
            db.session.commit()
        self.assertEqual(str(cm.exception.orig),
            'UNIQUE constraint failed: user.email')

    def test_load_user(self):
        u = User(username='john', email='john1@example.com')
        db.session.add(u)
        self.assertEqual(load_user(1), u)

    def test_notification_get_data(self):
        u = User(username='john', email='john@example.com')
        db.session.add(u)
        n1 = u.add_notification("First notification", {"aa": "abc", "bb": "def"})
        data = n1.get_data()
        self.assertEqual(data["aa"], "abc")
        self.assertEqual(data["bb"], "def")


class TaskModelCase(WebsiteTestCase):
    @patch("rq.job.Job.fetch")
    def test_task_get_rq_job(self, rjjf):
        t = Task()
        # First test Redis/rq errors, then mock a running Redis server
        rjjf.side_effect = [RedisError(), NoSuchJobError(), "abc"]
        self.assertEqual(t.get_rq_job(), None)
        self.assertEqual(t.get_rq_job(), None)
        self.assertEqual(t.get_rq_job(), "abc")

    @patch("app.models.Task.get_rq_job")
    def test_task_get_progress(self, tgrj):
        t = Task()
        mock_job = MagicMock()
        mock_job.meta.get.return_value = 27
        tgrj.side_effect = [None, mock_job]
        # Non-existing job returns 100% done
        self.assertEqual(t.get_progress(), 100)
        self.assertEqual(t.get_progress(), 27)

    def test_task_get_duration(self):
        now = datetime.utcnow()
        data = [
            [now - timedelta(seconds=3),   now, "3s"],
            [now - timedelta(seconds=60),  now, "1m 0s"],
            [now - timedelta(seconds=61),  now, "1m 1s"],
            [now - timedelta(seconds=627), now, "10m 27s"]
        ]
        for d in data:
            t = Task(timestamp_start=d[0], timestamp_end=d[1])
            self.assertEqual(t.get_duration(), d[2])
        # No end timestamp
        t = Task(timestamp_start=now)
        self.assertEqual(t.get_duration(), "unknown")


class MappingModelCase(WebsiteTestCase):
    def test_mapping(self):
        u = User(username='john', email='john@example.com')
        destination_lists = {
            "Label One": ["a1a1a1a1a1a1a1a1a1a1a1a1"],
            "Label Two": ["ddd"],
            "All Teams": [
                "a1a1a1a1a1a1a1a1a1a1a1a1",
                "ddd"
            ]
        }
        dl = json.dumps(destination_lists)
        m1 = Mapping(name="abc", destination_lists=dl)
        self.assertEqual(str(m1), "<Mapping abc>")
        self.assertEqual(m1.get_num_labels(), 3)
        self.assertEqual(m1.get_num_dest_lists(), 4)
        u.mappings.append(m1)
        m2 = Mapping(name="def")
        u.mappings.append(m2)
        self.assertEqual(u.get_mappings(), [m1, m2])

    def test_mapping_invalid(self):
        m2 = Mapping(name="def")
        self.assertEqual(m2.get_num_labels(), 0)
        self.assertEqual(m2.get_num_dest_lists(), 0)


class ConfigCase(unittest.TestCase):
    def test_config_values(self):
        self.assertEqual(Config.SECRET_KEY, os.environ.get('SECRET_KEY') or \
            'you-will-never-guess')
        self.assertEqual(Config.SQLALCHEMY_DATABASE_URI, os.environ.get('DATABASE_URL') or \
            'sqlite:///' + os.path.join(basedir, 'data', 'app.db'))
        self.assertEqual(Config.SQLALCHEMY_TRACK_MODIFICATIONS, False)
        self.assertEqual(Config.LOG_TO_STDOUT, os.environ.get('LOG_TO_STDOUT'))
        self.assertEqual(Config.MAIL_SERVER, os.environ.get('MAIL_SERVER'))
        self.assertEqual(Config.MAIL_PORT, int(os.environ.get('MAIL_PORT') or 25))
        self.assertEqual(Config.MAIL_USE_TLS, os.environ.get('MAIL_USE_TLS') is not None)
        self.assertEqual(Config.MAIL_USERNAME, os.environ.get('MAIL_USERNAME'))
        self.assertEqual(Config.MAIL_PASSWORD, os.environ.get('MAIL_PASSWORD'))
        self.assertEqual(Config.ADMINS, ['SyncBoom <hello@syncboom.com>'])
        self.assertEqual(Config.LANGUAGES, ['en'])
        self.assertEqual(Config.REDIS_URL, os.environ.get('REDIS_URL') or 'redis://')
        self.assertEqual(Config.TRELLO_API_KEY, os.environ.get('TRELLO_API_KEY'))


class MiscTests(WebsiteTestCase):
    @patch("app.email.mail")
    def test_send_email(self, aem):
        send_email("Nice subject", "sender@domain.tld", "recipient@domain.tld",
            "This is the body of the email", "<h1>Nice body</h1>",
            attachments=[("image.png", "image/png", b"abc")], sync=True)
        self.assertRegex(str(aem.mock_calls), "\[call.send\(<flask_mail\." \
            "Message object at 0x([a-zA-Z0-9]{8,12})>\)\]")
        # TODO: test content of the email in g.outbox
        # See https://pythonhosted.org/flask-mail/#unit-tests

    @patch("app.email.mail")
    def test_create_app(self, aem):
        tc = TestConfig()
        # WARNING, testing without the TESTING or DEBUG flag
        tc.TESTING = False
        tc.DEBUG = False
        f = io.StringIO()
        with contextlib.redirect_stderr(f):
            # The logs folder will likely already exist on the local dev machine
            # but not when running from CI
            tc.LOG_TO_STDOUT = False
            app = create_app(tc)
            tc.LOG_TO_STDOUT = True
            app = create_app(tc)
            tc.MAIL_SERVER = "domain.tld"
            tc.MAIL_USERNAME = "username"
            tc.MAIL_PASSWORD = "password"
            tc.MAIL_USE_TLS = True
            tc.MAIL_PORT = 9999
            app = create_app(tc)
            for h in ("<StreamHandler (INFO)>", "<RotatingFileHandler ",
                "<SMTPHandler (ERROR)>",):
                self.assertIn(h, str(app.logger.handlers))
            self.assertEqual(aem.mock_calls, [])

    def test_production_redirects(self):
        # No redirects when testing
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)

        tc = TestConfig()
        # WARNING, testing without the TESTING or DEBUG flag
        tc.TESTING = False
        tc.DEBUG = False
        app = create_app(tc)
        client = app.test_client()
        # http://localhost/ -> https://localhost/
        response = client.get('/')
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.headers["Location"], "https://localhost/")
        # CloudFlare HTTPS -> Heroku HTTP, no redirect
        response = client.get('/', headers={"Cf-Visitor": '{"scheme":"https"}'})
        self.assertEqual(response.status_code, 200)
        # CloudFlare HTTPS -> Heroku HTTP, no redirect
        response = client.get('/', headers={"Cf-Visitor": '{"scheme":"http"}'})
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.headers["Location"], "https://localhost/")
        # Invalid CloudFlare header
        response = client.get('/', headers={"Cf-Visitor": 'INVALID'})
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.headers["Location"], "https://localhost/")
        # Heroku subdomain HTTP (faking CF header to test only the Heroku subdomain)
        response = client.get('http://syncboom.herokuapp.com/',
            headers={"Cf-Visitor": '{"scheme":"https"}'})
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.headers["Location"], "https://syncboom.com/")
        # Heroku subdomain HTTPS (faking CF header to test only the Heroku subdomain)
        response = client.get('https://syncboom.herokuapp.com/',
            headers={"Cf-Visitor": '{"scheme":"https"}'})
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.headers["Location"], "https://syncboom.com/")
        # www subdomain HTTP (faking CF header to test only the www subdomain)
        response = client.get('http://www.syncboom.com/',
            headers={"Cf-Visitor": '{"scheme":"https"}'})
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.headers["Location"], "https://syncboom.com/")
        # www subdomain HTTPS (faking CF header to test only the www subdomain)
        response = client.get('https://www.syncboom.com/',
            headers={"Cf-Visitor": '{"scheme":"https"}'})
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.headers["Location"], "https://syncboom.com/")
        # No redirect (faking CF header to test only the www subdomain)
        response = client.get('https://syncboom.com/',
            headers={"Cf-Visitor": '{"scheme":"https"}'})
        self.assertEqual(response.status_code, 200)

    def test_static_files(self):
        response = self.client.get('/robots.txt')
        self.assertEqual(response.status_code, 200)
        expected_content = [
            "User-agent: *",
            "Disallow: /auth/"
        ]
        for ec in expected_content:
            self.assertIn(str.encode(ec), response.data)
        response.close()

        response = self.client.get('/sitemap.txt')
        self.assertEqual(response.status_code, 200)
        expected_content = [
            "https://syncboom.com/",
            "https://syncboom.com/contact",
            "https://syncboom.com/pricing",
            "https://syncboom.com/privacy",
            "https://syncboom.com/legal",
        ]
        for ec in expected_content:
            self.assertIn(str.encode(ec), response.data)
        response.close()


class TaskCase(WebsiteTestCase):
    @patch("app.tasks.get_current_job")
    def test_run_task(self, atgcj):
        mock_job = MagicMock()
        mock_job.get_id.return_value = "foobarbaz"
        atgcj.return_value = mock_job
        u = User(username='john', email='john@example.com')
        db.session.add(u)
        mock = MagicMock()
        mock.get_id.return_value = 'foobarbaz'
        with patch.object(self.app.task_queue, 'enqueue', return_value=mock) \
            as mock_enqueue_method:
            t = u.launch_task("run_mapping", (123, "board", "abc"), "Description")
        db.session.add(t)
        _set_task_progress(33)
        self.assertEqual(atgcj.mock_calls[2], call().meta.__setitem__('progress', 33))
        self.assertEqual(t.status, None)
        _set_task_progress(66, "Nice message")
        self.assertEqual(t.status, "Nice message")
        self.assertFalse(t.complete)
        _set_task_progress(100)
        self.assertTrue(t.complete)

    def test_run_mapping_nonexistent_mapping(self):
        f = io.StringIO()
        with self.assertLogs(level='INFO') as cm, contextlib.redirect_stderr(f):
            run_mapping(0, "", "")
        expected_logging = ['INFO:app:Starting task for mapping 0,  ',
            'ERROR:app:Invalid task, ignoring',
            'INFO:app:Completed task for mapping 0,  ']
        self.assertEqual(cm.output, expected_logging)
        if not self.app.debug:
            for l in expected_logging:
                self.assertTrue(l.split(":app:")[1] in f.getvalue())

    def test_run_mapping_vm_invalid_args(self):
        destination_lists = {
            "Label One": ["a1a1a1a1a1a1a1a1a1a1a1a1"],
            "Label Two": ["ddd"],
            "All Teams": [
                "a1a1a1a1a1a1a1a1a1a1a1a1",
                "ddd"
            ]
        }
        dl = json.dumps(destination_lists)
        m = Mapping(name="abc", destination_lists=dl)
        db.session.add(m)
        db.session.commit()
        f = io.StringIO()
        with self.assertLogs(level='INFO') as cm, contextlib.redirect_stderr(f):
            run_mapping(m.id, "", "")
        expected_logging = ['INFO:app:Starting task for mapping 1,  ',
            'ERROR:app:Invalid task, ignoring',
            'INFO:app:Completed task for mapping 1,  ']
        self.assertEqual(cm.output, expected_logging)
        if not self.app.debug:
            for l in expected_logging:
                self.assertTrue(l.split(":app:")[1] in f.getvalue())

    def test_run_mapping_invalid_mapping(self):
        # Create an incomplete mapping
        m = Mapping(name="def")
        db.session.add(m)
        db.session.commit()
        f = io.StringIO()
        with self.assertLogs(level='INFO') as cm, contextlib.redirect_stderr(f):
            run_mapping(m.id, "card", "abc")
        expected_logging = ['INFO:app:Starting task for mapping 1, card abc',
            'ERROR:app:Mapping has invalid destination_lists',
            'ERROR:app:Invalid task, ignoring',
            'INFO:app:Completed task for mapping 1, card abc']
        self.assertEqual(cm.output, expected_logging)

    @patch("app.tasks._set_task_progress")
    @patch("app.tasks.process_main_card")
    @patch("app.tasks.perform_request")
    def test_run_mapping_vm_valid_args_card(self, atpr, atpmc, atstp):
        u = User(username='john', email='john@example.com', trello_token="b2"*16)
        db.session.add(u)
        db.session.commit()
        destination_lists = {
            "Label One": ["a1a1a1a1a1a1a1a1a1a1a1a1"],
            "Label Two": ["ddd"],
            "All Teams": [
                "a1a1a1a1a1a1a1a1a1a1a1a1",
                "ddd"
            ]
        }
        dl = json.dumps(destination_lists)
        m = Mapping(name="abc", destination_lists=dl, user_id=u.id)
        db.session.add(m)
        db.session.commit()
        atpr.return_value = {"name": "Card name"}
        atpmc.return_value = (1, 2, 3)
        f = io.StringIO()
        with self.assertLogs(level='INFO') as cm, contextlib.redirect_stderr(f):
            run_mapping(m.id, "card", "abc")
        expected_calls = [call('GET', 'cards/abc', key="a1"*16, token="b2"*16)]
        self.assertEqual(atpr.mock_calls, expected_calls)
        expected_logging = "INFO:app:Processing main card 1/1 - Card name"
        self.assertEqual(cm.output[1], expected_logging)
        self.assertTrue(list(atpmc.call_args[0][1].keys()),
            ['destination_lists', 'key', 'token'])
        expected_call = call(100, 'Run complete. Processed 1 main cards (' \
            'of which 1 active) that have 2 subordinate cards (of which 3 new).')
        self.assertEqual(atstp.mock_calls[-1], expected_call)

    @patch("app.tasks._set_task_progress")
    @patch("app.tasks.process_main_card")
    @patch("app.tasks.perform_request")
    def test_run_mapping_vm_valid_args_list(self, atpr, atpmc, atstp):
        u = User(username='john', email='john@example.com', trello_token="b2"*16)
        db.session.add(u)
        db.session.commit()
        destination_lists = {
            "Label One": ["a1a1a1a1a1a1a1a1a1a1a1a1"],
            "Label Two": ["ddd"],
            "All Teams": [
                "a1a1a1a1a1a1a1a1a1a1a1a1",
                "ddd"
            ]
        }
        dl = json.dumps(destination_lists)
        m = Mapping(name="abc", destination_lists=dl, user_id=u.id)
        db.session.add(m)
        db.session.commit()
        atpr.return_value = [{"name": "Card name"}, {"name": "Second card"}]
        atpmc.side_effect = [(4, 5, 6), (7, 8, 9)]
        f = io.StringIO()
        with self.assertLogs(level='INFO') as cm, contextlib.redirect_stderr(f):
            run_mapping(m.id, "list", "def")
        expected_calls = [call('GET', 'list/def/cards', key="a1"*16, token="b2"*16)]
        self.assertEqual(atpr.mock_calls, expected_calls)
        expected_logging = ['INFO:app:Starting task for mapping 1, list def',
            'INFO:app:Processing main card 1/2 - Card name',
            'INFO:app:Processing main card 2/2 - Second card',
            'INFO:app:Completed task for mapping 1, list def']
        self.assertEqual(cm.output, expected_logging)
        expected_calls = [call(0),
            call(0, 'Job running... Processing 2 cards.'),
            call(50),
            call(100, 'Run complete. Processed 2 main cards (of which 11 ' \
                'active) that have 13 subordinate cards (of which 15 new).')]
        self.assertEqual(atstp.mock_calls, expected_calls)

    @patch("app.tasks._set_task_progress")
    @patch("app.tasks.get_current_job")
    def test_run_mapping_unhandled_exception(self, atgcj, atstp):
        destination_lists = {
            "Label One": ["a1a1a1a1a1a1a1a1a1a1a1a1"],
            "Label Two": ["ddd"],
            "All Teams": [
                "a1a1a1a1a1a1a1a1a1a1a1a1",
                "ddd"
            ]
        }
        dl = json.dumps(destination_lists)
        m = Mapping(name="abc", destination_lists=dl)
        db.session.add(m)
        db.session.commit()
        f = io.StringIO()
        atgcj.side_effect = Exception()
        with self.assertLogs(level='INFO') as cm, contextlib.redirect_stderr(f):
            run_mapping(m.id, "list", "def")
        expected_logging = "ERROR:app:run_mapping: Unhandled exception while " \
            "running task 1 list def"
        self.assertIn(expected_logging, cm.output[0])


class AuthCase(WebsiteTestCase):
    def register(self, username, email, password, password2, accept_terms):
        data_dict = dict(username=username, email=email,
            password=password, password2=password2)
        if accept_terms:
            data_dict["accept_terms"] = "y"
        return self.client.post(
            '/auth/register',
            data=data_dict,
            follow_redirects=True
        )

    def test_register_invalid(self):
        response = self.register(None, None, None, None, None)
        self.assertEqual(response.status_code, 200)
        expected_content = [
            '<input class="form-control is-invalid" id="username" ' \
                'name="username" required type="text" value="">',
            '<div class="invalid-feedback">This field is required.</div>',
            '<input class="form-control is-invalid" id="email" name="email" ' \
                'required type="text" value="">',
            '<input class="form-control is-invalid" id="password" ' \
                'name="password" required type="password" value="">',
            '<input class="form-control is-invalid" id="password2" ' \
                'name="password2" required type="password" value="">',
            '<input class="form-check-input is-invalid" id="accept_terms" ' \
                'name="accept_terms" required type="checkbox" value="y">']
        for ec in expected_content:
            self.assertIn(str.encode(ec), response.data)

        # Test with an invalid email address (invalid format)
        response = self.register("john", "invalid", "abc"*3, "abc"*3, True)
        self.assertEqual(response.status_code, 200)
        ec = '<div class="invalid-feedback">Invalid email address.</div>'
        self.assertIn(str.encode(ec), response.data)

        # Test with an invalid email address (too long)
        response = self.register("john", "%s@example.com" % ("john"*20),
            "abc"*3, "abc"*3, True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(str.encode(ec), response.data)

        # Test with an invalid password (too short)
        response = self.register("john", "john@example.com", "abc", "abc", True)
        self.assertEqual(response.status_code, 200)
        ec = '<div class="invalid-feedback">Field must be between 8 and 128 ' \
            'characters long.</div>'
        self.assertIn(str.encode(ec), response.data)

        # Test with an invalid password (too long)
        response = self.register("john", "john@example.com", "a"*129, "a"*129, True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(str.encode(ec), response.data)

        # Test with an invalid username (too long)
        ec = '<div class="invalid-feedback">Field cannot be longer than 63 characters.</div>'
        response = self.register("john"*20, "john@example.com",
            "abc"*3, "abc"*3, True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(str.encode(ec), response.data)

        # Test without accepting the terms
        response = self.register("john", "john@example.com", "abc"*3, "abc"*3, False)
        self.assertEqual(response.status_code, 200)
        expected_content = [
            '<div class="invalid-feedback" style="display: block;">This field is required.</div',
            '<input class="form-check-input is-invalid" id="accept_terms" ' \
                'name="accept_terms" required type="checkbox" value="y">']
        for ec in expected_content:
            self.assertIn(str.encode(ec), response.data)

    def test_register(self):
        # First registration is succesful
        response = self.register("john", "john@example.com", "abc"*3, "abc"*3, True)
        self.assertEqual(response.status_code, 200)
        ec = '<div class="alert alert-info" role="alert">Congratulations, ' \
            'you are now a registered user!</div>'
        self.assertIn(str.encode(ec), response.data)

    def test_register_double_fails(self):
        # First registration is succesful
        response = self.register("john", "john@example.com", "abc"*3, "abc"*3, True)
        self.assertEqual(response.status_code, 200)
        ec = '<div class="alert alert-info" role="alert">Congratulations, ' \
            'you are now a registered user!</div>'
        self.assertIn(str.encode(ec), response.data)
        # Double registration fails
        response = self.register("john", "john@example.com", "abc"*3, "abc"*3, True)
        self.assertEqual(response.status_code, 200)
        expected_content = [
            '<div class="invalid-feedback">Please use a different username.</div>',
            '<div class="invalid-feedback">Please use a different email address.</div>']
        for ec in expected_content:
            self.assertIn(str.encode(ec), response.data)

    def test_register_case_insensitive_username(self):
        # First registration is succesful
        response = self.register("john", "john@example.com", "abc"*3, "abc"*3, True)
        self.assertEqual(response.status_code, 200)
        ec = '<div class="alert alert-info" role="alert">Congratulations, ' \
            'you are now a registered user!</div>'
        self.assertIn(str.encode(ec), response.data)
        # Username is case-insensitive, fails if entering with a different case
        response = self.register("John", "john2@example.com", "def"*3, "def"*3, True)
        self.assertEqual(response.status_code, 200)
        ec = '<div class="invalid-feedback">Please use a different username.</div>'
        self.assertIn(str.encode(ec), response.data)

    def test_login_invalid(self):
        self.create_user("john", "abc")
        # Invalid username
        response = self.login("johnny", "abc")
        self.assertEqual(response.status_code, 200)
        ec = '<div class="alert alert-info" role="alert">Invalid username or ' \
            'password</div>'
        self.assertIn(str.encode(ec), response.data)
        # Invalid password, same error message
        response = self.login("john", "def")
        self.assertEqual(response.status_code, 200)
        self.assertIn(str.encode(ec), response.data)

    def test_login_valid(self):
        self.create_user("john", "abc")
        response = self.login("john", "abc")
        self.assertEqual(response.status_code, 200)
        expected_content = [
            '<li class="nav-item"><a class="nav-link" href="/account">' \
                'Account</a></li>',
            '<li class="nav-item"><a class="nav-link" href="/auth/logout">' \
                'Logout</a></li>',
            '<title>Home - SyncBoom</title>',
            '<h1>Hi, john!</h1>',
            '<h2>Connect to Trello</h2>',
            '<a class="btn btn-info" href="https://trello.com/1/authorize?name=' \
                'SyncBoom&amp;scope=read,write&amp;expiration=never' \
                '&amp;return_url=http://localhost/auth/validate_trello_token' \
                '&amp;key=a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1&amp;' \
                'callback_method=fragment" role="button">Connect to Trello</a>']
        for ec in expected_content:
            self.assertIn(str.encode(ec), response.data)

    def test_login_double(self):
        self.create_user("john", "abc")
        response = self.login("john", "abc")
        self.assertEqual(response.status_code, 200)
        # Logging in while already logged in redirects to home
        response = self.login("john", "abc", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "http://localhost/")

    def test_login_register(self):
        self.create_user("john", "abc")
        response = self.login("john", "abc")
        self.assertEqual(response.status_code, 200)
        # GETting the registration page while logged in redirects to home
        response = self.client.get('/auth/register')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "http://localhost/")

    def test_logout(self):
        self.create_user("john", "abc")
        response = self.login("john", "abc")
        self.assertEqual(response.status_code, 200)
        response = self.client.get('/auth/logout')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "http://localhost/")

    @patch("app.email.mail")
    def test_reset_password_request(self, aem):
        self.create_user("john", "abc", "a@a.com")

        # GETting the password reset form page while not logged in shows reset form
        response = self.client.get('/auth/reset_password_request')
        self.assertEqual(response.status_code, 200)
        expected_content = [
            '<h1>Reset Password</h1>',
            '<input class="btn btn-secondary btn-md" id="submit" ' \
                'name="submit" type="submit" value="Request Password Reset">']
        for ec in expected_content:
            self.assertIn(str.encode(ec), response.data)

        # Valid form submission redirects to login page
        response = self.client.post('/auth/reset_password_request',
            data=dict(email="a@a.com"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "http://localhost/auth/login")
        self.assertRegex(str(aem.mock_calls), "\[call.send\(<flask_mail\." \
            "Message object at 0x([a-zA-Z0-9]{8,12})>\)\]")

        # GETting the password reset form page while logged in redirects to home
        response = self.login("john", "abc")
        self.assertEqual(response.status_code, 200)
        response = self.client.get('/auth/reset_password_request')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "http://localhost/")

    def test_reset_password(self):
        u = self.create_user("john", "abc", "a@a.com")

        # GETting the password reset page with an invalid token redirects to home
        response = self.client.get('/auth/reset_password/abc')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "http://localhost/")

        # GETting the password reset page with a valid token shows reset form
        token = u.get_reset_password_token()
        response = self.client.get('/auth/reset_password/%s' % token)
        self.assertEqual(response.status_code, 200)
        expected_content = [
            '<h1>Reset Your Password</h1>',
            '<input class="btn btn-secondary btn-md" id="submit" ' \
                'name="submit" type="submit" value="Reset Password">']
        for ec in expected_content:
            self.assertIn(str.encode(ec), response.data)

        # Valid form submission redirects to login page
        self.assertTrue(u.check_password("abc"))
        response = self.client.post('/auth/reset_password/%s' % token,
            data=dict(password="def", password2="def"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "http://localhost/auth/login")
        self.assertTrue(u.check_password("def"))

        # GETting the password reset page while logged in redirects to home
        response = self.login("john", "def")
        self.assertEqual(response.status_code, 200)
        response = self.client.get('/auth/reset_password/abc')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "http://localhost/")

    def test_validate_trello_token_redirects(self):
        # User needs to be logged in
        response = self.client.get('/auth/validate_trello_token')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "http://localhost/")
        # User that already has his Trello token defined
        self.create_user("john", "abc", trello_token="abc")
        response = self.login("john", "abc")
        self.assertEqual(response.status_code, 200)
        response = self.client.get('/auth/validate_trello_token')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "http://localhost/")

    def test_validate_trello_token_get(self):
        self.create_user("john", "abc")
        response = self.login("john", "abc")
        self.assertEqual(response.status_code, 200)
        response = self.client.get('/auth/validate_trello_token')
        self.assertEqual(response.status_code, 200)
        expected_content = [
            '<title>Validating Trello token - SyncBoom</title>',
            '<h1>Redirecting</h1>',
            '<input class="form-control" id="trello_token" name="trello_token" ' \
                'required type="text" value="">',
            '<input class="btn btn-secondary btn-md" id="submit_trello_token" ' \
                'name="submit_trello_token" type="submit" value="Validate ' \
                'Trello token">']
        for ec in expected_content:
            self.assertIn(str.encode(ec), response.data)

    def test_validate_trello_token_post(self):
        self.create_user("john", "abc")
        response = self.login("john", "abc")
        self.assertEqual(response.status_code, 200)
        # Invalid token format
        response = self.client.post('/auth/validate_trello_token',
            data=dict(trello_token="abc"))
        self.assertEqual(response.status_code, 200)
        expected_content = [
            '<title>Validating Trello token - SyncBoom</title>',
            '<h1>Redirecting</h1>',
            '<input class="form-control is-invalid" id="trello_token" name="' \
                'trello_token" required type="text" value="abc">',
            '<div class="invalid-feedback">Invalid Trello token format, it ' \
                'must be a 64 character string.</div>',
            '<input class="btn btn-secondary btn-md" id="submit_trello_token" ' \
                'name="submit_trello_token" type="submit" value="Validate ' \
                'Trello token">']
        for ec in expected_content:
            self.assertIn(str.encode(ec), response.data)
        # Valid token format
        response = self.client.post('/auth/validate_trello_token',
            data=dict(trello_token="b2"*32))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "http://localhost/")


class MainCase(WebsiteTestCase):
    def test_main_routes_not_logged_in_redirects(self):
        # GETting these pages without being logged in redirects to login page
        for url in (
            "/account",
            "/account/edit/username",
            "/account/edit/email",
            "/account/edit/trello",
            "/notifications",
            "/mapping/999/edit",
            "/mapping/new",
            "/mapping/999/delete",
            "/mapping/999",
            ):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.headers["Location"],
                "http://localhost/auth/login?next=%s" % quote(url, safe=''))

    def test_main_routes_home_not_logged_in(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        expected_content = [
        '<title>Welcome - SyncBoom</title>',
        '<h1 class="display-4">Welcome to SyncBoom!</h1>',
        'SyncBoom enables you to "push" cards from one Main Trello board '\
            'onto one or multiple destination lists.']
        for ec in expected_content:
            self.assertIn(str.encode(ec), response.data)

    def test_main_routes_home_logged_in(self):
        u = self.create_user("john", "abc")
        response = self.login("john", "abc")
        self.assertEqual(response.status_code, 200)
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        expected_content = [
            '<title>Home - SyncBoom</title>',
            '<li class="nav-item"><a class="nav-link" href="/auth/logout">' \
                'Logout</a></li>',
            '<h1>Hi, john!</h1>',
            '<h2>Connect to Trello</h2>',
            '<a class="btn btn-info" href="https://trello.com/1/authorize?name=' \
                'SyncBoom&amp;scope=read,write&amp;expiration=never' \
                '&amp;return_url=http://localhost/auth/validate_trello_token' \
                '&amp;key=a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1&amp;' \
                'callback_method=fragment" role="button">Connect to Trello</a>']
        for ec in expected_content:
            self.assertIn(str.encode(ec), response.data)

    def test_main_routes_contact(self):
        response = self.client.get('/contact')
        self.assertEqual(response.status_code, 200)
        expected_content = [
            '<title>Contact Us - SyncBoom</title>',
            '<h1>Contact Us</h1>',
            'You can drop us an email at: <strong>hello AT syncboom DOT com' \
                '</strong>',
            '(replace AT by "@" and DOT by ".", without the quotes)']
        for ec in expected_content:
            self.assertIn(str.encode(ec), response.data)

    def test_main_routes_pricing(self):
        response = self.client.get('/pricing')
        self.assertEqual(response.status_code, 200)
        expected_content = [
            '<title>Pricing - SyncBoom</title>',
            '<h1>Pricing</h1>',
            'As SyncBoom is just being launched, the current features are '\
                'all free of cost.']
        for ec in expected_content:
            self.assertIn(str.encode(ec), response.data)

    def test_main_routes_privacy(self):
        response = self.client.get('/privacy')
        self.assertEqual(response.status_code, 200)
        expected_content = [
            '<title>Privacy Policy - SyncBoom</title>',
            '<h1>Privacy Policy for SyncBoom</h1>',
            'This Privacy Policy describes how your personal information is ' \
                'collected, used, and shared when you',
            'Latest update: 2020-06-08']
        for ec in expected_content:
            self.assertIn(str.encode(ec), response.data)

    def test_main_routes_legal(self):
        response = self.client.get('/legal')
        self.assertEqual(response.status_code, 200)
        expected_content = [
            '<title>Terms and Conditions - SyncBoom</title>',
            '<h1>Terms and Conditions for SyncBoom</h1>',
            'By visiting our site and/ or purchasing from us, you engage in ' \
                'our “Service” and agree to be bound by the following terms ' \
                'and conditions',
            'Latest update: 2020-06-08']
        for ec in expected_content:
            self.assertIn(str.encode(ec), response.data)

    def test_main_routes_notifications(self):
        u = self.create_user("john", "abc")
        response = self.login("john", "abc")
        self.assertEqual(response.status_code, 200)
        response = self.client.get('/notifications')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b'[]\n', response.data)

        n1 = u.add_notification("First notification", {"aa": "abc", "bb": "def"})
        n2 = u.add_notification("Second notification", {"cc": "ghi"})
        response = self.client.get('/notifications')
        self.assertEqual(response.status_code, 200)
        if not self.app.debug:
            expected_content = [
                '[{"data":{"aa":"abc","bb":"def"},"name":"First notification","timestamp":',
                '},{"data":{"cc":"ghi"},"name":"Second notification","timestamp":']
        else:
            expected_content = '[\n  {\n    "data": {\n      "aa": "abc", \n      "bb": "def"\n    }, \n    "name": "First notification", \n    "timestamp":',
            '}, \n  {\n    "data": {\n      "cc": "ghi"\n    }, \n    "name": "Second notification", \n    "timestamp":'
        for ec in expected_content:
            self.assertIn(str.encode(ec), response.data)

    def test_main_routes_account(self):
        u = self.create_user("john", "abc", email='john@example.com',
            trello_username="trello_username")
        response = self.login("john", "abc")
        self.assertEqual(response.status_code, 200)
        response = self.client.get('/account')
        self.assertEqual(response.status_code, 200)
        expected_content = [
            '<title>Account - SyncBoom</title>',
            '<h1>Account</h1>',
            '<div class="list-group" id="account_list">',
            'Username: john\n            <span class="badge badge-pill">edit',
            'Email address: john@example.com',
            'Account type: Free',
            'Trello username: trello_username',
        ]
        for ec in expected_content:
            self.assertIn(str.encode(ec), response.data)

    def test_main_routes_account_edit_username_get(self):
        u = self.create_user("john", "abc")
        response = self.login("john", "abc")
        self.assertEqual(response.status_code, 200)
        response = self.client.get('/account/edit/username')
        self.assertEqual(response.status_code, 200)
        expected_content = [
            '<title>Edit username - SyncBoom</title>',
            '<h1>Edit username</h1>',
            '<input class="form-control" id="username" name="username" ' \
                'required type="text" value="john">']
        for ec in expected_content:
            self.assertIn(str.encode(ec), response.data)

    def test_main_routes_account_edit_username_post(self):
        u = self.create_user("john", "abc")
        self.assertEqual(u.username, "john")
        response = self.login("john", "abc")
        self.assertEqual(response.status_code, 200)
        # Empty username
        response = self.client.post('/account/edit/username')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(u.username, "john")
        ec = '<div class="invalid-feedback">This field is required.</div>'
        self.assertIn(str.encode(ec), response.data)
        # Username too long
        response = self.client.post('/account/edit/username',
            data=dict(username="a"*64))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(u.username, "john")
        self.assertNotEqual(u.username, "a"*64)
        ec = '<div class="invalid-feedback">Field cannot be longer than 63 ' \
            'characters.</div>'
        self.assertIn(str.encode(ec), response.data)
        # Valid username
        response = self.client.post('/account/edit/username',
            data=dict(username="j2"), follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(u.username, "j2")
        ec = 'Your username has been updated.'
        self.assertIn(str.encode(ec), response.data)

    def test_main_routes_account_edit_username_post_collision(self):
        u1 = self.create_user("john", "abc")
        u2 = self.create_user("j2", "def")
        self.assertEqual(u1.username, "john")
        response = self.login("john", "abc")
        self.assertEqual(response.status_code, 200)
        response = self.client.post('/account/edit/username',
            data=dict(username="j2"))
        self.assertEqual(response.status_code, 200)
        expected_content = [
            '<title>Edit username - SyncBoom</title>',
            '<h1>Edit username</h1>',
            '<div class="invalid-feedback">Please use a different username.</div>']
        for ec in expected_content:
            self.assertIn(str.encode(ec), response.data)
        self.assertEqual(u1.username, "john")

    def test_main_routes_account_edit_email_get(self):
        u = self.create_user("john", "abc", email='john@example.com')
        response = self.login("john", "abc")
        self.assertEqual(response.status_code, 200)
        response = self.client.get('/account/edit/email')
        self.assertEqual(response.status_code, 200)
        expected_content = [
            '<title>Edit email - SyncBoom</title>',
            '<h1>Edit email</h1>',
            '<input class="form-control" id="email" name="email" ' \
                'required type="text" value="john@example.com">']
        for ec in expected_content:
            self.assertIn(str.encode(ec), response.data)

    def test_main_routes_account_edit_email_post(self):
        u = self.create_user("john", "abc", email='john@example.com')
        self.assertEqual(u.email, "john@example.com")
        response = self.login("john", "abc")
        self.assertEqual(response.status_code, 200)
        # Empty email
        response = self.client.post('/account/edit/email')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(u.email, "john@example.com")
        ec = '<div class="invalid-feedback">This field is required.</div>'
        self.assertIn(str.encode(ec), response.data)
        # Short email
        response = self.client.post('/account/edit/email',
            data=dict(email="abc"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(u.email, "john@example.com")
        ec = '<div class="invalid-feedback">Field must be between 5 and 255 ' \
            'characters long.</div>'
        self.assertIn(str.encode(ec), response.data)
        # Invalid email
        response = self.client.post('/account/edit/email',
            data=dict(email="abcdef"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(u.email, "john@example.com")
        ec = '<div class="invalid-feedback">Invalid email address.</div>'
        self.assertIn(str.encode(ec), response.data)
        # Valid email
        response = self.client.post('/account/edit/email',
            data=dict(email="j2@example.com"), follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(u.email, "j2@example.com")
        ec = 'Your email has been updated.'
        self.assertIn(str.encode(ec), response.data)

    def test_main_routes_account_edit_email_post_collision(self):
        u1 = self.create_user("john", "abc", email='john@example.com')
        u2 = self.create_user("j2", "def", email='j2@example.com')
        self.assertEqual(u1.email, "john@example.com")
        response = self.login("john", "abc")
        self.assertEqual(response.status_code, 200)
        response = self.client.post('/account/edit/email',
            data=dict(email="j2@example.com"))
        self.assertEqual(response.status_code, 200)
        expected_content = [
            '<title>Edit email - SyncBoom</title>',
            '<h1>Edit email</h1>',
            '<div class="invalid-feedback">Please use a different email ' \
                'address.</div>']
        for ec in expected_content:
            self.assertIn(str.encode(ec), response.data)
        self.assertEqual(u1.email, "john@example.com")

    def test_main_routes_account_edit_password_get(self):
        u = self.create_user("john", "abc"*3)
        response = self.login("john", "abc"*3)
        self.assertEqual(response.status_code, 200)
        response = self.client.get('/account/edit/password')
        self.assertEqual(response.status_code, 200)
        expected_content = [
            '<title>Edit password - SyncBoom</title>',
            '<h1>Edit password</h1>',
            '<input class="form-control" id="password" name="password" ' \
                'required type="password" value="">',
            '<input class="form-control" id="password2" name="password2" ' \
                'required type="password" value="">']
        for ec in expected_content:
            self.assertIn(str.encode(ec), response.data)

    def test_main_routes_account_edit_password_post(self):
        u = self.create_user("john", "abc"*3)
        self.assertTrue(u.check_password("abc"*3))
        response = self.login("john", "abc"*3)
        self.assertEqual(response.status_code, 200)
        # Empty passwords
        response = self.client.post('/account/edit/password')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(u.check_password("def"*3))
        self.assertTrue(u.check_password("abc"*3))
        ec = '<div class="invalid-feedback">This field is required.</div>'
        self.assertIn(str.encode(ec), response.data)
        # Different second password
        response = self.client.post('/account/edit/password',
            data=dict(password="def"*3, password2="abc"*3))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(u.check_password("def"*3))
        self.assertTrue(u.check_password("abc"*3))
        ec = '<div class="invalid-feedback">Field must be equal to password.' \
            '</div>'
        self.assertIn(str.encode(ec), response.data)
        # Password is too short
        response = self.client.post('/account/edit/password',
            data=dict(password="abc", password2="abc"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(u.check_password("abc"))
        self.assertTrue(u.check_password("abc"*3))
        ec = '<div class="invalid-feedback">Field must be between 8 and 128 ' \
            'characters long.</div>'
        self.assertIn(str.encode(ec), response.data)
        # Valid password
        response = self.client.post('/account/edit/password',
            data=dict(password="def"*3, password2="def"*3), follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(u.check_password("def"*3))
        ec = 'Your password has been updated.'
        self.assertIn(str.encode(ec), response.data)

    def test_main_routes_account_edit_trello_get(self):
        u = self.create_user("john", "abc"*3)
        u.mappings.append(Mapping(name="abc"))
        response = self.login("john", "abc"*3)
        self.assertEqual(response.status_code, 200)
        response = self.client.get('/account/edit/trello')
        self.assertEqual(response.status_code, 200)
        expected_content = [
            '<title>Edit trello - SyncBoom</title>',
            '<h1>Edit trello</h1>',
            '<p>Warning: changing your Trello username will delete the ' \
                'mapping you currently have configured.</p>',
            '<p><strong>Are you sure you want to proceed?</strong></p>',
            '<input class="btn btn-secondary btn-md" id="submit" name="submit" ' \
                'type="submit" value="Unlink and continue to Trello">']
        for ec in expected_content:
            self.assertIn(str.encode(ec), response.data)

    def test_main_routes_account_edit_trello_post(self):
        u = self.create_user("john", "abc"*3, trello_token="b2"*16)
        u.mappings.append(Mapping(name="abc"))
        response = self.login("john", "abc"*3)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(u.trello_token, "b2"*16)
        self.assertEqual(len(u.get_mappings()), 1)
        # Checkbox not checked
        response = self.client.post('/account/edit/trello')
        self.assertEqual(response.status_code, 200)
        ec = '<div class="invalid-feedback" style="display: block;">This ' \
            'field is required.</div>'
        self.assertIn(str.encode(ec), response.data)
        # Checkbox checked
        response = self.client.post('/account/edit/trello',
            data=dict(trello="y"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], 'https://trello.com/1/' \
            'authorize?name=SyncBoom&scope=read,write&expiration=never&' \
            'return_url=http://localhost/auth/validate_trello_token&' \
            'key=a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1&callback_method=fragment')
        self.assertEqual(u.trello_token, None)
        self.assertEqual(len(u.get_mappings()), 0)

    def test_error_404(self):
        response = self.client.get('/non_existent_route')
        self.assertEqual(response.status_code, 404)
        expected_content = [
            '<title>Page Not Found - SyncBoom</title>',
            '<h1>Page Not Found</h1>']
        for ec in expected_content:
            self.assertIn(str.encode(ec), response.data)


class MappingCase(WebsiteTestCase):
    def create_user_mapping_and_login(self, secondary_user=False):
        username = "john" if not secondary_user else "john2"
        password = "abc" if not secondary_user else "def"
        u = self.create_user(username, password)
        destination_lists = {
            "bbbbbbbbbbbbbbbbbbbbbbbb": ["eeeeeeeeeeeeeeeeeeeeeeee"]}
        dl = json.dumps(destination_lists)
        mapping_name = "abc" if not secondary_user else "def"
        m = Mapping(name=mapping_name,
            description = "Mapping description for %s" % mapping_name,
            m_type = "automatic",
            main_board = "a"*24,
            destination_lists=dl
        )
        u.mappings.append(m)
        db.session.commit()
        self.assertEqual(u.get_mappings(), [m])
        if not secondary_user:
            response = self.login("john", "abc")
            self.assertEqual(response.status_code, 200)
        return (u, m)

    def test_mapping_delete(self):
        (u, m) = self.create_user_mapping_and_login()
        # GET
        response = self.client.get('/mapping/%d/delete' % m.id)
        self.assertEqual(response.status_code, 200)
        expected_content = [
            '<title>Delete mapping &#34;abc&#34; ? - SyncBoom</title>',
            '<h1>Delete mapping &#34;abc&#34; ?</h1>',
            '<input class="btn btn-danger btn-md" id="submit" name="submit" ' \
                'type="submit" value="Delete mapping">']
        for ec in expected_content:
            self.assertIn(str.encode(ec), response.data)

        # POST
        response = self.client.post('/mapping/%d/delete' % m.id)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "http://localhost/")
        self.assertEqual(u.get_mappings(), [])

    def test_mapping_no_access(self):
        (u1, m1) = self.create_user_mapping_and_login()
        (u2, m2) = self.create_user_mapping_and_login(secondary_user=True)

        expected_content = [
        '<title>Invalid mapping - SyncBoom</title>',
        '<h1>Invalid mapping</h1>',
        '<div class="alert alert-danger">\n      <strong>Error!</strong> ' \
        'You don\'t have access to this mapping.\n    </div>',
        '<a href="/">Back to Home</a>']

        # GETting these pages with a non-existing mapping ID or
        # another user's mapping ID shows an error page
        for base_url in ("", "/delete", "/edit"):
            for id in (999, m2.id):
                url = "/mapping/%d%s" % (id, base_url)
                response = self.client.get(url)
                self.assertEqual(response.status_code, 403)
                for ec in expected_content:
                    self.assertIn(str.encode(ec), response.data)

    @patch("app.mapping.routes.current_user")
    @patch("app.mapping.routes.perform_request")
    def test_mapping_run(self, amrpr, amrcu):
        (u, m) = self.create_user_mapping_and_login()
        # GET
        pr_return = [
            [
                {"id": "123", "name": "hij"},
                {"id": "a"*24, "name": "klm"}
            ],
            [
                {"id": "456", "name": "opq"},
                {"id": "789", "name": "yza"}
            ],
            [
                {"id": "357", "name": "stu"},
                {"id": "b"*24, "name": "vwx"},
                {"id": "579", "name": "efg"}
            ]
        ]
        # Five groups of these requests are going to be made
        amrpr.side_effect = pr_return * 5
        amrcu.id = 1
        response = self.client.get("/mapping/%d" % m.id)
        self.assertEqual(response.status_code, 200)
        expected_content = [
            '<title>Run mapping &#34;abc&#34; - SyncBoom</title>',
            '<h1>Run mapping &#34;abc&#34;</h1>',
            'Would you like to:<br/><br/>',
            '<input class="btn btn-secondary btn-md" id="submit_board" name="' \
                'submit_board" type="submit" value="Process the entire main board">',
            '<select class="form-control" id="lists" name="lists"><option ' \
                'value="123">hij</option><option value="%s">klm</option>' \
                '</select>' % ("a"*24),
            '<input class="btn btn-secondary btn-md" id="submit_list" name="' \
                'submit_list" type="submit" value="Process all cards on this list">',
            '<select class="form-control" id="cards" name="cards"><option ' \
                'value="456">hij | opq</option><option value="789">hij | yza' \
                '</option><option value="357">klm | stu</option><option ' \
                'value="%s">klm | vwx</option><option value="579">klm | efg' \
                '</option></select>' % ("b"*24),
            '<input class="btn btn-secondary btn-md" id="submit_card" name="' \
                'submit_card" type="submit" value="Process only this specific card">'
            ]
        for ec in expected_content:
            self.assertIn(str.encode(ec), response.data)

        # POST while task in progress
        amrcu.get_task_in_progress.return_value = True
        response = self.client.post("/mapping/%d" % m.id, follow_redirects = True)
        self.assertEqual(response.status_code, 200)
        ec = '<div class="alert alert-info" role="alert">A run is currently ' \
            'in progress, please wait...</div>'
        self.assertIn(str.encode(ec), response.data)

        # POST entire main board
        amrcu.get_task_in_progress.return_value = False
        response = self.client.post("/mapping/%d" % m.id,
            data=dict(submit_board="submit_board"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "http://localhost/")
        expected_call = call.launch_task('run_mapping', (1, 'board', "a"*24),
            'Processing the full "abc" main board...')
        self.assertEqual(amrcu.mock_calls[-1], expected_call)

        # POST list
        response = self.client.post("/mapping/%d" % m.id,
            data=dict(submit_list="submit_list", lists="a"*24))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "http://localhost/")
        expected_call = call.launch_task('run_mapping', (1, 'list',
            "a"*24), 'Processing all cards on list "klm"...')
        self.assertEqual(amrcu.mock_calls[-1], expected_call)

        # POST card
        response = self.client.post("/mapping/%d" % m.id,
            data=dict(submit_card="submit_card", cards="b"*24))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "http://localhost/")
        expected_call = call.launch_task('run_mapping', (1, 'card',
            "b"*24), 'Processing card "klm | vwx"...')
        self.assertEqual(amrcu.mock_calls[-1], expected_call)

    def retrieve_and_check(self, method, url, expected_status_code,
        expected_content, unexpected_content, data=None, redirect_url=None, display=None):
        if method == 'GET':
            response = self.client.get(url)
        elif method == 'POST':
            response = self.client.post(url, data=data)
        if display:
            print("\n\n", response.data, "\n\n")
            print("expected_content", expected_content)
            print("unexpected_content", unexpected_content)
        self.assertEqual(response.status_code, expected_status_code)
        if redirect_url:
            self.assertEqual(response.headers["Location"], redirect_url)
        # Confirm that the elements from the this step are present
        if expected_content:
            for ec in expected_content:
                self.assertIn(str.encode(ec), response.data)
        # Confirm that the elements from the next step are not yet present
        if unexpected_content:
            for uec in unexpected_content:
                self.assertNotIn(str.encode(uec), response.data)

    def get_data_step_valid(self):
        ds1ok = dict(name="Mapping name",
            description="Nice description")
        ds2ok = dict(ds1ok, main_board="a"*24)
        ds3ok = dict(ds2ok, labels="b"*24)
        ds4ok = dict(ds3ok, map_label0_lists="e"*24)
        return ds1ok, ds2ok, ds3ok, ds4ok

    def get_sample_values(self):
        t_boards = [
            {"id": "123", "name": "hij", "closed": False},
            {"id": "456", "name": "nop", "closed": True},
            {"id": "a"*24, "name": "klm", "closed": False}
        ]
        t_labels = [
            {"id": "label_id_1", "name": "Label Name One"},
            {"id": "b"*24, "name": "Label Name Two"},
            {"id": "label_id_3", "name": "Label Name Three"},
            {"id": "label_id_4", "name": "Label Name Four"}
        ]
        t_lists1 = [
            {"id": "list_id_1", "name": "List Name One"},
            {"id": "c"*24, "name": "List Name Two"},
            {"id": "d"*24, "name": "List Name Three"},
            {"id": "list_id_4", "name": "List Name Four"}
        ]
        t_lists2 = [
            {"id": "list_id_5", "name": "List Name Five"},
            {"id": "e"*24, "name": "List Name Six"},
            {"id": "f"*24, "name": "List Name Seven"},
            {"id": "list_id_8", "name": "List Name Eight"}
        ]
        return t_boards, t_labels, t_lists1, t_lists2

    @patch("app.mapping.routes.perform_request")
    def test_mapping_no_boards(self, amrpr):
        (u, m) = self.create_user_mapping_and_login()
        self.assertEqual(m.id, 1)
        ds1ok, ds2ok, ds3ok, ds4ok = self.get_data_step_valid()
        amrpr.side_effect = [
            # No boards at all
            [],
            # Only closed boards
            [
                {"id": "123", "name": "hij", "closed": True},
                {"id": "456", "name": "nop", "closed": True},
                {"id": "a"*24, "name": "klm", "closed": True}
            ]
        ]

        # GET step 1
        expected_content = [
            '<title>No boards available in Trello - SyncBoom</title>',
            '<h1>No boards available in Trello</h1>',
            'You don\'t have any active board available in Trello.',
            'Go create some boards, lists and cards in <a href="https://' \
                'trello.com/">Trello</a> and come back for some syncing fun!'
        ]
        unexpected_content = [
            '<title>New mapping, Step 1/4 - SyncBoom</title>',
            '<h1>New mapping, Step 1/4</h1>',
            '<input class="form-control" id="name" name="name" required ' \
                'type="text" value="">',
            '<div class="form-group "><label class="form-control-label" ' \
                'for="m_type">Type of mapping</label>',
            '<input checked class="form-check-input" id="m_type-0" ' \
                'name="m_type" type="radio" value="automatic"> Automatic',
            '<input class="form-check-input" id="m_type-1" name="m_type" ' \
                'type="radio" value="manual"> Manual',
            '<textarea class="form-control" id="description" name="description">'
            '<select class="form-control" id="main_board" ' \
                'name="main_board"><option',
        ]
        # Test no boards at all
        self.retrieve_and_check("GET", "/mapping/new", 200, expected_content,
            unexpected_content)
        # Test only closed boards
        self.retrieve_and_check("GET", "/mapping/new", 200, expected_content,
            unexpected_content)

    @patch("app.mapping.routes.flash")
    @patch("app.mapping.routes.current_user")
    @patch("app.mapping.routes.perform_request")
    def test_mapping_new(self, amrpr, amrcu, amrf):
        (u, m) = self.create_user_mapping_and_login()
        self.assertEqual(m.id, 1)
        ds1ok, ds2ok, ds3ok, ds4ok = self.get_data_step_valid()
        t_boards, t_labels, t_lists1, t_lists2 = self.get_sample_values()
        amrpr.side_effect = [
            t_boards,
            t_boards, t_labels,
            t_boards, t_labels,
            t_boards, t_labels,
            t_boards, [],
            t_boards, t_labels,
            t_boards, t_labels,
            t_boards, t_labels, t_lists1, t_lists2,
            t_boards, t_labels, t_lists1, t_lists2,
            t_boards, t_labels
        ]
        amrcu.id = 1

        # GET step 1
        expected_content = [
            '<title>New mapping, Step 1/4 - SyncBoom</title>',
            '<h1>New mapping, Step 1/4</h1>',
            '<input class="form-control" id="name" name="name" required ' \
                'type="text" value="">',
            '<textarea class="form-control" id="description" name="description">',
            '<div class="form-group "><label class="form-control-label" ' \
                'for="m_type">Type of mapping</label>',
            '<input checked class="form-check-input" id="m_type-0" ' \
                'name="m_type" type="radio" value="automatic"> Automatic',
            '<input class="form-check-input" id="m_type-1" name="m_type" ' \
                'type="radio" value="manual"> Manual',
        ]
        unexpected_content = [
            '<title>New mapping, Step 2/4 - SyncBoom</title>',
            '<h1>New mapping, Step 2/4</h1>',
            '<select class="form-control" id="main_board" ' \
                'name="main_board"><option',
        ]
        self.retrieve_and_check("GET", "/mapping/new", 200, expected_content,
            unexpected_content)

        # POST step 1, invalid name
        expected_content = [
            '<h1>New mapping, Step 1/4</h1>',
            '<input class="form-control is-invalid" id="name" name="name" ' \
                'required type="text" value="">',
            '<div class="invalid-feedback">This field is required.</div>',
        ]
        self.retrieve_and_check("POST", "/mapping/new", 200, expected_content,
            unexpected_content, data=dict(name=""))

        # POST step 1, valid data
        expected_content = unexpected_content
        unexpected_content = [
            '<title>New mapping, Step 3/4 - SyncBoom</title>',
            '<h1>New mapping, Step 3/4</h1>',
            '<label class="form-control-label" for="labels">Which labels need ' \
                'mapping?</label>',
            '<ul class="form-control" id="labels" style="height: auto; ' \
                'list-style: none;"><li><input id="labels-0" name="labels" ' \
                'type="checkbox" value="label_id_1"> <label for="labels-0">' \
                'Label Name One</label></li>',
        ]
        self.retrieve_and_check("POST", "/mapping/new", 200, expected_content,
            unexpected_content, data=ds1ok)

        # POST step 2, invalid main_board, no specific error message
        expected_content = ['<title>New mapping, Step 2/4 - SyncBoom' \
                '</title>',
            '<h1>New mapping, Step 2/4</h1>',
        ]
        self.retrieve_and_check("POST", "/mapping/new", 200, expected_content,
            unexpected_content, data=dict(ds1ok, main_board="c"))

        # POST step 2, valid main_board, which has no named labels
        expected_content = ['<title>New mapping, Step 2/4 - SyncBoom' \
                '</title>',
            '<div class="invalid-feedback">None of the labels on this board have ' \
                'names. Only named labels can be selected for mapping.</div>',
        ]
        self.retrieve_and_check("POST", "/mapping/new", 200, expected_content,
            unexpected_content, data=dict(ds1ok, main_board="c"))

        # POST step 2, valid data
        expected_content = unexpected_content
        unexpected_content = [
            '<title>New mapping, Step 4/4 - SyncBoom</title>',
            '<h1>New mapping, Step 4/4</h1>',
            '<label class="form-control-label" for="map_label0_lists">Map label ' \
                '&#34;Label Name Two&#34; to which Trello lists?</label>',
            '<ul class="form-control" id="map_label0_lists" style="height: auto;' \
                ' list-style: none;"><li><input id="map_label0_lists-0" name="' \
                'map_label0_lists" type="checkbox" value="list_id_1"> <label ' \
                'for="map_label0_lists-0">hij | List Name One</label></li>',
        ]
        self.retrieve_and_check("POST", "/mapping/new", 200, expected_content,
            unexpected_content, data=ds2ok)

        # POST step 3, invalid label
        expected_content = [
            '<h1>New mapping, Step 3/4</h1>',
            '<ul class="form-control is-invalid" id="labels" style="height: ' \
                'auto; list-style: none;"><li><input id="labels-0" name=' \
                '"labels" type="checkbox" value="label_id_1"> <label for=' \
                '"labels-0">Label Name One</label></li>',
        ]
        self.retrieve_and_check("POST", "/mapping/new", 200, expected_content,
            unexpected_content,
            data=dict(ds2ok, labels="invalid_label"))

        # POST step 3, valid label
        expected_content = unexpected_content
        unexpected_content = None
        self.retrieve_and_check("POST", "/mapping/new", 200, expected_content,
            unexpected_content, data=ds3ok)

        # POST step 4, invalid selected list
        expected_content = [
            '<h1>New mapping, Step 4/4</h1>',
            '<ul class="form-control is-invalid" id="map_label0_lists" style=' \
                '"height: auto; list-style: none;"><li><input id="' \
                'map_label0_lists-0" name="map_label0_lists" type="checkbox" ' \
                'value="list_id_1"> <label for="map_label0_lists-0">hij | ' \
                'List Name One</label></li>',
        ]
        self.retrieve_and_check("POST", "/mapping/new", 200, expected_content,
            unexpected_content,
            data=dict(ds3ok, map_label0_lists="invalid_list"))

        # POST step 4, valid list, all good on step 4
        expected_content = None
        self.retrieve_and_check("POST", "/mapping/new", 302, expected_content,
            unexpected_content,
            data=ds4ok,
            redirect_url="http://localhost/")
        self.assertEqual(amrf.mock_calls,[call('Your new mapping "Mapping ' \
            'name" has been created.')])
        m2 = Mapping.query.filter_by(id=m.id+1).first()
        self.assertEqual(m2.id, 2)

    @patch("app.mapping.routes.flash")
    @patch("app.mapping.routes.current_user")
    @patch("app.mapping.routes.perform_request")
    def test_mapping_edit(self, amrpr, amrcu, amrf):
        (u, m) = self.create_user_mapping_and_login()
        self.assertEqual(m.id, 1)
        ds1ok, ds2ok, ds3ok, ds4ok = self.get_data_step_valid()
        t_boards, t_labels, t_lists1, t_lists2 = self.get_sample_values()
        amrpr.side_effect = [
            t_boards, t_labels, t_lists1, t_lists2,
            t_boards, t_labels, t_lists1, t_lists2,
            t_boards, t_labels, t_lists1, t_lists2,
            t_boards, t_labels, t_lists1, t_lists2,
            t_boards, t_labels, t_lists1, t_lists2,
        ]
        amrcu.id = 1

        # GET step 1
        expected_content_step_1 = [
            '<title>Edit mapping 1 - SyncBoom</title>',
            '<h1>Edit mapping 1</h1>',
            # Elements from step 1
            '<input class="form-control" id="name" name="name" required ' \
                'type="text" value="abc">',
            '<textarea class="form-control" id="description" name="description">',
            'Mapping description for abc</textarea>',
            '<div class="form-group "><label class="form-control-label" ' \
                'for="m_type">Type of mapping</label>',
            '<input checked class="form-check-input" id="m_type-0" ' \
                'name="m_type" type="radio" value="automatic"> Automatic',
            '<input class="form-check-input" id="m_type-1" name="m_type" ' \
                'type="radio" value="manual"> Manual',
            # Elements from step 2
            '<select class="form-control" id="main_board" ' \
                'name="main_board"><option',
            # Elements from step 3
            '<label class="form-control-label" for="labels">Which labels need ' \
                'mapping?</label>',
            '<ul class="form-control" id="labels" style="height: auto; ' \
                'list-style: none;"><li><input id="labels-0" name="labels" ' \
                'type="checkbox" value="label_id_1"> <label for="labels-0">' \
                'Label Name One</label></li>',
            # Elements from step 4
            '<label class="form-control-label" for="map_label0_lists">Map label ' \
                '&#34;Label Name Two&#34; to which Trello lists?</label>',
            '<ul class="form-control" id="map_label0_lists" style="height: auto;' \
                ' list-style: none;"><li><input id="map_label0_lists-0" name="' \
                'map_label0_lists" type="checkbox" value="list_id_1"> <label ' \
                'for="map_label0_lists-0">hij | List Name One</label></li>'
        ]
        self.retrieve_and_check("GET", "/mapping/1/edit", 200,
            expected_content_step_1, None)

        # POST, error on step 4
        expected_content = [
            '<title>Edit mapping 1, Step 4/4 - SyncBoom</title>',
            '<h1>Edit mapping 1, Step 4/4</h1>',
            '<label class="form-control-label" for="map_label0_lists">Map label ' \
                '&#34;Label Name Two&#34; to which Trello lists?</label>',
            '<ul class="form-control is-invalid" id="map_label0_lists" style=' \
                '"height: auto; list-style: none;"><li><input id="' \
                'map_label0_lists-0" name="map_label0_lists" type="checkbox" ' \
                'value="list_id_1"> <label for="map_label0_lists-0">hij | ' \
                'List Name One</label></li>',
        ]
        self.retrieve_and_check("POST", "/mapping/1/edit", 200, expected_content,
            None, data=dict(ds3ok, map_label0_lists="invalid_list"))

        # GET with invalid destination_lists
        # Expect all elements until step 3 (included)
        expected_content = expected_content_step_1[:8]
        saved_destination_lists = m.destination_lists
        m.destination_lists = ""
        self.retrieve_and_check("GET", "/mapping/1/edit", 200,
            expected_content, None)
        m.destination_lists = None
        self.retrieve_and_check("GET", "/mapping/1/edit", 200,
            expected_content, None)

        # POST, all good on step 4
        expected_content = None
        m.destination_lists = saved_destination_lists
        self.retrieve_and_check("POST", "/mapping/1/edit", 302, expected_content,
            None, data=ds4ok, redirect_url="http://localhost/")
        self.assertEqual(amrf.mock_calls,[call('Your mapping "Mapping name" ' \
            'has been updated.')])

    @patch("app.mapping.routes.flash")
    @patch("app.mapping.routes.current_user")
    @patch("app.mapping.routes.perform_request")
    def test_mapping_large_map_labelN_lists(self, amrpr, amrcu, amrf):
        (u, m) = self.create_user_mapping_and_login()
        self.assertEqual(m.id, 1)
        ds1ok, ds2ok, ds3ok, ds4ok = self.get_data_step_valid()
        t_boards, t_labels, t_lists1, t_lists2 = self.get_sample_values()
        t_lists = t_lists1 * 5
        amrpr.side_effect = [
            # 9 boards, of which only 6 are not closed
            t_boards * 3,
            # 120 labels
            t_labels * 30,
            # 6 sets of 20 lists each
            t_lists, t_lists, t_lists, t_lists, t_lists, t_lists
        ]
        amrcu.id = 1

        # POST step 3, valid label
        expected_content = ['<li><input id="map_label109_lists-119" name="map_label109_lists" type="checkbox" value="list_id_4"> <label for="map_label109_lists-119">klm | List Name Four</label></li></ul>']
        unexpected_content = None
        # Select 110 labels
        selected_labels = ["b"*24]*110
        self.retrieve_and_check("POST", "/mapping/new", 200, expected_content,
            unexpected_content, data=dict(ds2ok, labels=selected_labels), display=False)


if __name__ == '__main__':
    unittest.main()
