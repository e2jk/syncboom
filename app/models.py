#!/usr/bin/env python
# -*- coding: utf-8 -*-

#    This file is part of SyncBoom and is MIT-licensed.
#    Originally based on microblog, licensed under the MIT License.

import base64
from datetime import datetime
from hashlib import md5
import json
import os
from time import time
from flask import current_app, url_for
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
import jwt
import redis
import rq
from app import db, login


mappings = db.Table(
    'mappings',
    db.Column('user_id', db.Integer, db.ForeignKey('user.id')),
    db.Column('mapping_id', db.Integer, db.ForeignKey('mapping.id'))
)


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), index=True, unique=True)
    email = db.Column(db.String(256), index=True, unique=True)
    password_hash = db.Column(db.String(128))
    trello_token = db.Column(db.String(128))
    trello_username = db.Column(db.String(128))
    last_seen = db.Column(db.DateTime, default=datetime.utcnow)
    token = db.Column(db.String(32), index=True, unique=True)
    token_expiration = db.Column(db.DateTime)
    notifications = db.relationship('Notification', backref='user',
                                    lazy='dynamic')
    tasks = db.relationship('Task', backref='user', lazy='dynamic')
    mappings = db.relationship(
        'Mapping', secondary=mappings,
        backref=db.backref('users', lazy='dynamic'), lazy='dynamic')

    def __repr__(self):
        return '<User {}>'.format(self.username)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def avatar(self, size):
        digest = md5(self.email.lower().encode('utf-8')).hexdigest()
        return 'https://www.gravatar.com/avatar/{}?d=identicon&s={}'.format(
            digest, size)

    def get_reset_password_token(self, expires_in=600):
        return jwt.encode(
            {'reset_password': self.id, 'exp': time() + expires_in},
            current_app.config['SECRET_KEY'],
            algorithm='HS256').decode('utf-8')

    @staticmethod
    def verify_reset_password_token(token):
        try:
            id = jwt.decode(token, current_app.config['SECRET_KEY'],
                            algorithms=['HS256'])['reset_password']
        except:
            return
        return User.query.get(id)

    def add_notification(self, name, data):
        self.notifications.filter_by(name=name).delete()
        n = Notification(name=name, payload_json=json.dumps(data), user=self)
        db.session.add(n)
        return n

    def launch_task(self, name, argument, description, *args, **kwargs):
        task = None
        rq_job = None
        if name == "run_mapping" and len(argument) == 3:
            rq_job = current_app.task_queue.enqueue('app.tasks.' + name,
                argument[0], argument[1], argument[2], *args, **kwargs)
        if rq_job:
            task = Task(id=rq_job.get_id(), name=name, description=description,
                        user=self)
            db.session.add(task)
        return task

    def get_tasks_in_progress(self):
        return Task.query.filter_by(user=self, complete=False).all()

    def get_task_in_progress(self, name):
        return Task.query.filter_by(name=name, user=self,
                                    complete=False).first()

    def get_mappings(self):
        return self.mappings.all()

    def get_recent_tasks(self):
        return self.tasks.order_by(Task.timestamp_start.desc()).limit(10).all()


@login.user_loader
def load_user(id):
    return User.query.get(int(id))


class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    timestamp = db.Column(db.Float, index=True, default=time)
    payload_json = db.Column(db.Text)

    def get_data(self):
        return json.loads(str(self.payload_json))


class Task(db.Model):
    id = db.Column(db.String(36), primary_key=True)
    name = db.Column(db.String(128), index=True)
    description = db.Column(db.String(128))
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    complete = db.Column(db.Boolean, default=False)
    timestamp_start = db.Column(db.DateTime, index=True, default=datetime.utcnow)
    timestamp_end = db.Column(db.DateTime)
    status = db.Column(db.String(128))

    def get_rq_job(self):
        try:
            rq_job = rq.job.Job.fetch(self.id, connection=current_app.redis)
        except (redis.exceptions.RedisError, rq.exceptions.NoSuchJobError):
            return None
        return rq_job

    def get_progress(self):
        job = self.get_rq_job()
        return job.meta.get('progress', 0) if job is not None else 100

    def get_duration(self):
        if not (self.timestamp_start and self.timestamp_end):
            return "unknown"
        difference = self.timestamp_end - self.timestamp_start
        seconds_in_day = 24 * 60 * 60
        minsec = divmod(difference.days * seconds_in_day + difference.seconds, 60)
        duration = "%ds" % minsec[1]
        if minsec[0] > 0:
            duration = "%dm " % minsec[0] + duration
        return duration


class Mapping(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), index=True)
    description = db.Column(db.String(128))
    m_type = db.Column(db.Enum("automatic", "manual", name="mappingtypes"))
    main_board = db.Column(db.String(128))
    destination_lists = db.Column(db.Text())
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))

    def __repr__(self):
        return '<Mapping {}>'.format(self.name)

    def get_num_labels(self):
        try:
            return len(json.loads(self.destination_lists))
        except (TypeError, json.decoder.JSONDecodeError):
            return 0

    def get_num_dest_lists(self):
        num_dest_lists = 0
        try:
            dest_lists = json.loads(self.destination_lists)
            for label in dest_lists:
                num_dest_lists += len(dest_lists[label])
        except (TypeError, json.decoder.JSONDecodeError):
            pass
        return num_dest_lists
