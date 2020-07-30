#!/usr/bin/env python
# -*- coding: utf-8 -*-

#    This file is part of SyncBoom and is MIT-licensed.
#    Originally based on microblog, licensed under the MIT License.

import json
import sys
import time
from datetime import datetime
from flask import render_template
from rq import get_current_job
from app import create_app, db
from app.models import Task, Mapping, User
from app.email import send_email
from syncboom import perform_request, process_main_card, output_summary

app = create_app()
app.app_context().push()


def run_mapping(mapping_id, run_type, elem_id):
    mapping = Mapping.query.filter_by(id=mapping_id).first()
    try:
        job = get_current_job()
        _set_task_progress(0)
        app.logger.info('Starting task for mapping %d, %s %s' %
            (mapping_id, run_type, elem_id))
        destination_lists = []
        if mapping:
            try:
                destination_lists = json.loads(mapping.destination_lists)
            except (TypeError, json.decoder.JSONDecodeError):
                app.logger.error("Mapping has invalid destination_lists")
            user = User.query.get(mapping.user_id)
        if destination_lists and run_type in ("card", "list", "board"):
            args_from_app = {
                "destination_lists": destination_lists,
                "key": app.config['TRELLO_API_KEY'],
                "token": user.trello_token
            }
            if run_type == "card":
                status_information = "Job running... Processing one single card."
                _set_task_progress(0, status_information)
                main_cards = [perform_request("GET", "cards/%s" % elem_id,
                    key=args_from_app["key"], token=args_from_app["token"])]
            elif run_type in ("list", "board"):
                main_cards = perform_request("GET", "%s/%s/cards" %
                    (run_type, elem_id),
                    key=args_from_app["key"], token=args_from_app["token"])
                status_information = "Job running... Processing %d cards." % \
                    len(main_cards)
                _set_task_progress(0, status_information)
            summary = {
                "main_cards": len(main_cards),
                "active_main_cards": 0,
                "subordinate_card": 0,
                "new_subordinate_card": 0}
            for idx, main_card in enumerate(main_cards):
                app.logger.info("Processing main card %d/%d - %s" %
                    (idx+1, len(main_cards), main_card["name"]))
                output = process_main_card(main_card, args_from_app)
                summary["active_main_cards"] += output[0]
                summary["subordinate_card"] += output[1]
                summary["new_subordinate_card"] += output[2]
                if idx < len(main_cards)-1:
                    _set_task_progress(int(100.0 * (idx+1) / len(main_cards)))
            status_information = "Run complete. %s" % output_summary(None, summary)
        else:
            app.logger.error("Invalid task, ignoring")
            status_information = "Invalid task, ignored."
        _set_task_progress(100, status_information)
        app.logger.info('Completed task for mapping %d, %s %s' %
            (mapping_id, run_type, elem_id))
    except:
        _set_task_progress(100, "The job errored out.")
        app.logger.error(
            'run_mapping: Unhandled exception while running task %d %s %s' %
            (mapping_id, run_type, elem_id), exc_info=sys.exc_info())


def _set_task_progress(progress, status_information=None):
    job = get_current_job()
    if job:
        job.meta['progress'] = progress
        job.save_meta()
        task = Task.query.get(job.get_id())
        task.user.add_notification('task_progress', {'task_id': job.get_id(),
                                                     'progress': progress})
        if progress >= 100:
            task.complete = True
            task.timestamp_end = datetime.utcnow()
        if status_information:
            task.status = status_information
        db.session.commit()
