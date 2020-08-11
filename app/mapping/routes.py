#!/usr/bin/env python
# -*- coding: utf-8 -*-

#    This file is part of SyncBoom and is MIT-licensed.

from datetime import datetime
from flask import render_template, flash, redirect, url_for, request, g, \
    jsonify, current_app
from flask_login import current_user, login_required
from flask_babel import _, get_locale
# from guess_language import guess_language
from app import db
from app.mapping.forms import makeNewMappingForm, DeleteMappingForm, \
    RunMappingForm
from app.models import Mapping, mappings as users_mappings_links
from app.mapping import bp
import requests
import re
from wtforms import BooleanField
import json
from syncboom import perform_request, new_webhook, delete_webhook


@bp.before_app_request
def before_request():
    if current_user.is_authenticated:
        current_user.last_seen = datetime.utcnow()
        db.session.commit()
    g.locale = str(get_locale())


@bp.route('/<int:mapping_id>/edit', methods=['GET', 'POST'])
@bp.route('/new', methods=['GET', 'POST'])
@login_required
def new_or_edit(mapping_id=None):
    mapping = None
    num_map_labelN_lists = 0
    if mapping_id:
        # Check if this user has access to this mapping
        (valid_mapping, val1, val2) = check_mapping_ownership(mapping_id)
        if not valid_mapping:
            return val1, val2
        else:
            this_users_mappings = val1
            mapping = val2
        # Set the items on the mapping object like they would come from the form
        try:
            destination_lists = json.loads(mapping.destination_lists)
            mapping.labels = list(destination_lists.keys())
        except (TypeError, json.decoder.JSONDecodeError):
            destination_lists = []
        i = 0
        for label_id in destination_lists:
            label_lists = []
            for list_id in destination_lists[label_id]:
                label_lists.append(list_id)
            setattr(mapping, "map_label%d_lists" % i, label_lists)
            i += 1
        num_map_labelN_lists = i
    else:
        num_map_labelN_lists = len(request.form.getlist('labels'))

    # Get the list of boards for this user
    all_boards = perform_request("GET", "members/me/boards",
        key=current_app.config['TRELLO_API_KEY'],
        token=current_user.trello_token)
    boards = []
    for b in all_boards:
        if not b["closed"]:
            boards.append(b)
    if not boards:
        title = _('No boards available in Trello')
        return render_template('mapping/new.html', title=title)

    form = makeNewMappingForm(obj=mapping,
        num_map_labelN_lists=num_map_labelN_lists)

    # Check if the fields from each fields are valid
    step = 1
    selected_labels = []
    if request.method == 'POST' or mapping_id:
        use_default_master_board = False
        form.master_board.choices = [(b["id"], b["name"]) for b in boards]
        # TODO (#74): show error message if form.master_board.choices is empty
        if not form.master_board.data:
            use_default_master_board = True
            form.master_board.data = form.master_board.choices[0][0]
        labels_names = {}
        labels = perform_request("GET", "boards/%s/labels" % \
            form.master_board.data,
            key=current_app.config['TRELLO_API_KEY'],
            token=current_user.trello_token)
        form.labels.choices = [(l["id"], l["name"]) for l in labels if l["name"]]
        for l in labels:
            if l["name"]:
                labels_names[l["id"]] = l["name"]

        form.validate_on_submit()

        # Check elements from the first step
        if form.name.validate(form) and \
            form.description.validate(form) and \
            form.m_type.validate(form):
            # Go to next step
            step = 2
        # If the first step cleared, go on to check elements from the second step
        if step == 2:
            if not use_default_master_board and \
                re.match("^[0-9a-fA-F]{24}$", form.master_board.data):
                # Go to next step
                step = 3
        # If the second step cleared, go on to check elements from the third step
        if step == 3:
            step_3_ok = len(form.labels.data) > 0
            for l in form.labels.data:
                if not re.match("^[0-9a-fA-F]{24}$", l):
                    step_3_ok = False
                    break
                selected_labels.append(l)
            if step_3_ok:
                # Go to next step
                step = 4
        # If the third step cleared, go on to check elements from the fourth step
        if step == 4:
            step_4_ok = len(form.labels.data) > 0
            destination_lists = {}
            i = 0
            for label_id in selected_labels:
                if not hasattr(form, "map_label%d_lists" % i) or \
                    len(getattr(form, "map_label%d_lists" % i).data) == 0:
                    step_4_ok = False
                    break
                destination_lists[label_id] = []
                for list_id in getattr(form, "map_label%d_lists" % i).data:
                    if not re.match("^[0-9a-fA-F]{24}$", list_id):
                        step_4_ok = False
                        break
                    destination_lists[label_id].append(list_id)
                i += 1
            if step_4_ok:
                # Go to next step
                step = 5

        # All the steps have valid information, add this mapping to the database!
        if step == 5 and request.method == 'POST':
            mapping_type_changed = True
            deactivate_previous_webhook = False
            if mapping_id:
                # Update the existing mapping
                if mapping.m_type == "automatic" and \
                    form.m_type.data == "manual":
                    # Need to deactivate the previous webhook
                    deactivate_previous_webhook = True
                if mapping.m_type == form.m_type.data:
                    mapping_type_changed = False
                mapping.name = form.name.data
                mapping.description=form.description.data
                mapping.m_type=form.m_type.data
                mapping.master_board=form.master_board.data
                mapping.destination_lists = json.dumps(destination_lists)
                mapping.user_id = current_user.id
                flash(_('Your mapping "%(name)s" has been updated.',
                    name=mapping.name))
            else:
                # Create a new mapping
                mapping = Mapping(
                    name=form.name.data,
                    description=form.description.data,
                    m_type=form.m_type.data,
                    master_board=form.master_board.data,
                    destination_lists = json.dumps(destination_lists),
                    user_id = current_user.id)
                current_user.mappings.append(mapping)
                flash(_('Your new mapping "%(name)s" has been created.',
                    name=mapping.name))
            db.session.add(mapping)
            db.session.commit()

            # Deactivate previous webhook or create a new webhook
            if deactivate_previous_webhook:
                #TODO: make sure only the webhook for this mapping is deleted,
                # not all webhooks for this master board (could be present in
                # different mappings)
                delete_webhook(mapping.master_board,
                    key=current_app.config['TRELLO_API_KEY'],
                    token=current_user.trello_token)
            elif mapping_type_changed and mapping.m_type == "automatic":
                new_webhook(mapping.master_board,
                    key=current_app.config['TRELLO_API_KEY'],
                    token=current_user.trello_token)
            else:
                # No change to webhook
                pass

            return redirect(url_for('main.index'))

    # Populate conditional form elements
    if step > 1:
        pass
    if step > 2:
        pass
    if step > 3:
        lists_on_boards = []
        for b in boards:
            boards_lists = perform_request("GET", "boards/%s/lists" % b["id"],
                key=current_app.config['TRELLO_API_KEY'],
                token=current_user.trello_token)
            for l in boards_lists:
                lists_on_boards.append((l["id"], "%s | %s" % (b["name"], l["name"])))
        i = 0
        for l in selected_labels:
            if hasattr(form, "map_label%d_lists" % i):
                field = getattr(form, "map_label%d_lists" % i)
                field.choices = [(l[0], l[1]) for l in lists_on_boards]
                field.label.text = field.label.text.replace("XX", \
                    '"%s"'% labels_names[selected_labels[i]])
                i+= 1
            else:
                break

    # Remove fields from later steps
    if step < 2:
        del form.master_board
    if step < 3:
        del form.labels
    if step < 4:
        # Remove all the "map_labelN_lists" fields
        map_label_lists = [mll for mll in dir(form)
            if mll.startswith("map_label")]
        for i in range(len(map_label_lists)):
            delattr(form, "map_label%d_lists" % i)

    if mapping_id:
        if request.method == 'GET':
            title = _('Edit mapping %(mapping_id)s', mapping_id=mapping_id)
        else:
            # We're still on the edit page while POSTing, show the step number
            title = _('Edit mapping %(mapping_id)s, Step %(step_nr)s/4',
                mapping_id=mapping_id, step_nr=step)
    else:
        title = _('New mapping, Step %(step_nr)s/4', step_nr=step)
    if step < 5:
        form.submit.label.text = _('Continue')

    return render_template('mapping/new.html', title=title, form=form)

def check_mapping_ownership(mapping_id):
    this_users_mappings = db.session.query(users_mappings_links).\
        filter_by(user_id=current_user.id, mapping_id=mapping_id).all()
    if this_users_mappings:
        mapping = Mapping.query.filter_by(id=mapping_id).first()
    if not this_users_mappings or not mapping:
        # Return a 403 error if this mapping is not related to this user
        # Return a 403 error even if the ID doesn't exist (should technically
        # have been a 404 error, but no need to expose that info to users)
        return (False, render_template('mapping/invalid.html',
            title=_("Invalid mapping")), 403)
    return (True, this_users_mappings, mapping)


@bp.route('/<int:mapping_id>/delete', methods=['GET', 'POST'])
@login_required
def delete(mapping_id):
    # Check if this user has access to this mapping
    (valid_mapping, val1, val2) = check_mapping_ownership(mapping_id)
    if not valid_mapping:
        return val1, val2
    else:
        this_users_mappings = val1
        mapping = val2
    if request.method == 'POST':
        # This also deletes the many-to-many user-mapping relationship
        db.session.delete(mapping)
        db.session.commit()
        return redirect(url_for('main.index'))

    form = DeleteMappingForm()
    title = _('Delete mapping "%(name)s" ?', name=mapping.name)
    return render_template('mapping/delete.html', title=title, form=form)


@bp.route('/<int:mapping_id>', methods=['GET', 'POST'])
@login_required
def run(mapping_id):
    # Check if this user has access to this mapping
    (valid_mapping, val1, val2) = check_mapping_ownership(mapping_id)
    if not valid_mapping:
        return val1, val2
    else:
        this_users_mappings = val1
        mapping = val2

    if request.method == 'POST' and current_user.get_task_in_progress('run_mapping'):
        flash(_('A run is currently in progress, please wait...'))
        return redirect(url_for('mapping.run', mapping_id=mapping_id))

    rmf = RunMappingForm()
    lists = perform_request("GET", "boards/%s/lists" % mapping.master_board,
        key=current_app.config['TRELLO_API_KEY'],
        token=current_user.trello_token)
    rmf.lists.choices = [(l["id"], l["name"]) for l in lists]
    list_names = {}
    card_names = {}
    for l in lists:
        list_names[l["id"]] = l["name"]
        cards = perform_request("GET", "lists/%s/cards" % l["id"],
            key=current_app.config['TRELLO_API_KEY'],
            token=current_user.trello_token)
        cards_choices = [(c["id"], "%s | %s" % (l["name"], c["name"])) for c in cards]
        if not rmf.cards.choices:
            rmf.cards.choices = cards_choices
        else:
            rmf.cards.choices = rmf.cards.choices + cards_choices
        for c in cards:
            card_names[c["id"]] = "%s | %s" % (l["name"], c["name"])

    if request.method == 'POST':
        rmf.validate_on_submit()
        if rmf.submit_board.data:
            current_user.launch_task('run_mapping',
                (mapping.id, "board", mapping.master_board),
                _('Processing the full "%(mapping_name)s" master board...',
                    mapping_name=mapping.name))
        if rmf.submit_list.data and rmf.lists.validate(rmf):
            current_user.launch_task('run_mapping',
                (mapping.id, "list", rmf.lists.data),
                _('Processing all cards on list "%(list_name)s"...',
                    list_name=list_names[rmf.lists.data]))
        if rmf.submit_card.data and rmf.cards.validate(rmf):
            current_user.launch_task('run_mapping',
                (mapping.id, "card", rmf.cards.data),
                _('Processing card "%(card_name)s"...',
                    card_name=card_names[rmf.cards.data]))
        db.session.commit()
        return redirect(url_for('main.index'))

    title = _('Run mapping "%(name)s"', name=mapping.name)
    return render_template('mapping/run.html', title=title, mapping=mapping,
        rmf=rmf)
