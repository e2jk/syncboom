#!/usr/bin/env python
# -*- coding: utf-8 -*-

#    This file is part of SyncBoom and is MIT-licensed.

from flask_wtf import FlaskForm
from wtforms import StringField, SubmitField, TextAreaField, SelectField, \
    SelectMultipleField, RadioField
from wtforms import widgets
from wtforms.validators import DataRequired, Regexp, ValidationError
from flask_babel import _, lazy_gettext as _l


class MultiCheckboxField(SelectMultipleField):
    """
    A multiple-select, except displays a list of checkboxes.

    Iterating the field will produce subfields, allowing custom rendering of
    the enclosed checkbox fields.
    """
    widget = widgets.ListWidget(prefix_label=False)
    option_widget = widgets.CheckboxInput()


def makeNewMappingForm(obj, num_map_labelN_lists):
    class NewMappingForm(FlaskForm):
        name = StringField(_l('Mapping name'), validators=[DataRequired()])
        description = TextAreaField(_l('Mapping description (optional)'))
        m_type = RadioField(_l('Type of mapping'),
            description=_l('An "Automatic" mapping will run each time any card ' \
                'on the main board is modified, while a "Manual" mapping ' \
                'will need to be run each time from the home page.'),
            choices=[("automatic", "Automatic"), ("manual", "Manual")],
            default='automatic',
            validators=[DataRequired()])
        main_board = SelectField(_l('Main board'), coerce=str)
        labels = MultiCheckboxField(_l('Which labels need mapping?'), \
            coerce=str, render_kw={'style':'height: auto; list-style: none;'})

        def validate_main_board(form, field):
            if not form.labels.choices:
                raise ValidationError(_l('None of the labels on this board have '
                    'names. Only named labels can be selected for mapping.'))

    # Add fields to map labels to lists
    for i in range(num_map_labelN_lists):
        setattr(NewMappingForm, "map_label%d_lists" % i,
            MultiCheckboxField(_l('Map label XX to which Trello lists?'),
                coerce=str, choices = [],
                render_kw={'style':'height: auto; list-style: none;'}))
    # Add the Submit button after all the other fields
    setattr(NewMappingForm, "submit", SubmitField(_l('Submit')))

    return NewMappingForm(obj=obj)


class DeleteMappingForm(FlaskForm):
    submit = SubmitField(_l('Delete mapping'))


class RunMappingForm(FlaskForm):
    submit_board = SubmitField(_l('Process the entire main board'))
    lists = SelectField(_l('List'), coerce=str,
        validators=[Regexp("^[0-9a-fA-F]{24}$",
        message=_l('Invalid Trello list ID format, it must be a 24 character string.'))])
    submit_list = SubmitField(_l('Process all cards on this list'))
    cards = SelectField(_l('Card'), coerce=str,
        validators=[Regexp("^[0-9a-fA-F]{24}$",
        message=_l('Invalid Trello card ID format, it must be a 24 character string.'))])
    submit_card = SubmitField(_l('Process only this specific card'))
