#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Running the tests:
# $ python3 -m unittest discover --start-directory ./tests/
# Checking the coverage of the tests:
# $ coverage run --include=./*.py --omit=tests/* -m unittest discover && \
#   rm -rf html_dev/coverage && coverage html --directory=html_dev/coverage \
#   --title="Code test coverage for SyncBoom"

import unittest
import sys
import os
import logging
import json
from unittest.mock import patch, call, MagicMock
import io
import contextlib
import inspect
import tempfile
from uuid import uuid4
from requests.exceptions import HTTPError, ConnectionError
from app import create_app, db
from config import Config

sys.path.append('.')
target = __import__("syncboom")

# Used to test manual entry
def setUpModule():
    def mock_raw_input(s):
        global mock_raw_input_counter
        global mock_raw_input_values
        print(s)
        mock_raw_input_counter += 1
        return mock_raw_input_values[mock_raw_input_counter - 1]
    target.input = mock_raw_input

# Ensure config and cached values are empty before each new test
def setUp(cls):
    target.config = None
    target.cache.clear()
# Use this for all the tests
unittest.TestCase.setUp = setUp


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite://'
    WTF_CSRF_ENABLED = False
    TRELLO_API_KEY = "ghi"


class FlaskTestCase(unittest.TestCase):
    def setUp(self):
        target.app = None
        target.app = create_app(TestConfig)
        target.app_context = target.app.app_context()
        target.app_context.push()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        target.app_context.pop()


class TestOutputSummary(FlaskTestCase):
    def test_output_summary_propagate(self):
        """
        Test the summary for --propagate
        """
        args = type("blabla", (object,), {
            "propagate": True,
            "cleanup": False,
            "dry_run": False})()
        summary = {"master_cards": 4,
            "active_master_cards": 2,
            "slave_card": 3,
            "new_slave_card": 1}
        with self.assertLogs(level='INFO') as cm:
            target.output_summary(args, summary)
        self.assertEqual(cm.output, [
            "INFO:root:================================================================",
            "INFO:root:Summary: processed 4 master cards (of which 2 active) that have 3 slave cards (of which 1 new)."])

    def test_output_summary_propagate_dry_run(self):
        """
        Test the summary for --propagate --dry-run
        """
        args = type("blabla", (object,), {
            "propagate": True,
            "cleanup": False,
            "dry_run": True})()
        summary = {"master_cards": 4,
            "active_master_cards": 2,
            "slave_card": 3,
            "new_slave_card": 1}
        with self.assertLogs(level='INFO') as cm:
            target.output_summary(args, summary)
        self.assertEqual(cm.output, [
            "INFO:root:================================================================",
            "INFO:root:Summary [DRY RUN]: processed 4 master cards (of which 2 active) that have 3 slave cards (of which 1 would have been new)."])

    def test_output_summary_cleanup(self):
        """
        Test the summary for --cleanup
        """
        args = type("blabla", (object,), {
            "propagate": False,
            "cleanup": True,
            "dry_run": False})()
        summary = {"cleaned_up_master_cards": 4,
            "deleted_slave_cards": 6,
            "erased_destination_boards": 2,
            "erased_destination_lists": 2}
        with self.assertLogs(level='INFO') as cm:
            target.output_summary(args, summary)
        self.assertEqual(cm.output, [
            "INFO:root:================================================================",
            "INFO:root:Summary: cleaned up 4 master cards and deleted 6 slave cards from 2 slave boards/2 slave lists."])

    def test_output_summary_cleanup_dry_run(self):
        """
        Test the summary for --cleanup --dry-run
        """
        args = type("blabla", (object,), {
            "propagate": False,
            "cleanup": True,
            "dry_run": True})()
        summary = {"cleaned_up_master_cards": 4,
            "deleted_slave_cards": 6,
            "erased_destination_boards": 2,
            "erased_destination_lists": 2}
        with self.assertLogs(level='INFO') as cm:
            target.output_summary(args, summary)
        self.assertEqual(cm.output, [
            "INFO:root:================================================================",
            "INFO:root:Summary [DRY RUN]: would have cleaned up 4 master cards and deleted 6 slave cards from 2 slave boards/2 slave lists."])

    def test_output_summary_new_config(self):
        """
        Test the summary for --new-config (no summary output)
        """
        args = type("blabla", (object,), {
            "new_config": True})()
        summary = None
        f = io.StringIO()
        with contextlib.redirect_stderr(f):
            target.output_summary(args, summary)
        # No output
        self.assertEqual(f.getvalue(), "")


class TestGetCardAttachments(FlaskTestCase):
    def test_get_card_attachments_none(self):
        """
        Test retrieving attachments from a card without attachments
        """
        card = {"badges": {"attachments": 0}}
        card_attachments = target.get_card_attachments(card)
        self.assertEqual(card_attachments, [])

    @patch("syncboom.perform_request")
    def test_get_card_attachments_non_trello(self, t_pr):
        """
        Test the logic retrieving attachments from a card without Trello attachments
        """
        t_pr.return_value = [{"url": "https://monip.org"}, {"url": "https://example.com"}]
        card = {"id": "1a2b3c", "badges": {"attachments": 2}}
        card_attachments = target.get_card_attachments(card)
        self.assertEqual(card_attachments, [])

    @patch("syncboom.perform_request")
    def test_get_card_attachments_one_trello(self, t_pr):
        """
        Test retrieving attachments from a card with one Trello attachment
        """
        shortLink = "eoK0Rngb"
        t_pr.return_value = [{"url": "https://trello.com/c/%s/blablabla" % shortLink}]
        card = {"id": "1a2b3c", "badges": {"attachments": 1}}
        card_attachments = target.get_card_attachments(card)
        self.assertEqual(len(card_attachments), 1)
        expected_card_attachments = [{"card_shortUrl": shortLink,
            "url": "https://trello.com/c/%s/blablabla" % shortLink}]
        self.assertEqual(card_attachments, expected_card_attachments)

    @patch("syncboom.perform_request")
    def test_get_card_attachments_various(self, t_pr):
        """
        Test retrieving attachments from a card with both Trello and non-Trello attachments
        """
        shortLink1 = "eoK0Rngb"
        shortLink2 = "abcd1234"
        t_pr.return_value = [{"url": "https://trello.com/c/%s/blablabla" % shortLink1},
            {"url": "https://monip.org"},
            {"url": "https://trello.com/c/%s/blablabla" % shortLink2}]
        card = {"id": "1a2b3c", "badges": {"attachments": 3}}
        card_attachments = target.get_card_attachments(card)
        self.assertEqual(len(card_attachments), 2)
        expected_card_attachments = [{"card_shortUrl": shortLink1,
            "url": "https://trello.com/c/%s/blablabla" % shortLink1},
            {"card_shortUrl": shortLink2,
            "url": "https://trello.com/c/%s/blablabla" % shortLink2}]
        self.assertEqual(card_attachments, expected_card_attachments)


class TestCleanupTestBoards(FlaskTestCase):
    @patch("syncboom.perform_request")
    def test_cleanup_test_boards_not_configured(self, t_pr):
        """
        Test cleaning when the config has no whitelisted boards that are allowed to be cleaned up
        """
        target.config = {}
        with self.assertRaises(SystemExit) as cm1, self.assertLogs(level='CRITICAL') as cm2:
            summary = target.cleanup_test_boards([])
        self.assertEqual(cm1.exception.code, 43)
        self.assertEqual(cm2.output, ["CRITICAL:root:This configuration has not been enabled to accept the --cleanup operation. See the `cleanup_boards` section in the config file. Exiting..."])

    @patch("syncboom.perform_request")
    def test_cleanup_test_boards_not_whitelisted(self, t_pr):
        """
        Test cleaning up a board that is not whitelisted for cleaning up
        """
        target.config = {
            "destination_lists": {
                "Label One": ["aaa"],
                "Label Two": ["ddd"],
                "All Teams": [
                  "aaa",
                  "ddd"
                ]
            },
            "cleanup_boards": ["r"*24]}
        master_cards = []
        t_pr.return_value = {"id": "q"*24}
        with self.assertRaises(SystemExit) as cm1, self.assertLogs(level='CRITICAL') as cm2:
            summary = target.cleanup_test_boards(master_cards)
        self.assertEqual(cm1.exception.code, 44)
        self.assertEqual(cm2.output, ["CRITICAL:root:This board qqqqqqqqqqqqqqqqqqqqqqqq is not whitelisted to be cleaned up. See the `cleanup_boards` section in the config file. Exiting..."])

    @patch("syncboom.perform_request")
    def test_cleanup_test_boards_none(self, t_pr):
        """
        Test cleaning up the test boards when there is no master card and no cards on the slave lists
        """
        target.config = {"token": "jkl",
            "destination_lists": {
                "Label One": ["aaa"],
                "Label Two": ["ddd"],
                "All Teams": [
                  "aaa",
                  "ddd"
                ]
            },
            "cleanup_boards": ["q"*24]}
        master_cards = []
        t_pr.side_effect = [
            {"id": "q"*24},
            [{"id": "aaa"}, {"id": "ddd"}],
            {"idBoard": "h"*24},
            {"name": "Destination board name 1"},
            {"name": "Destination list name 1"},
            [],
            {"idBoard": "y"*24},
            {"name": "Destination board name 2"},
            {"name": "Destination list name 2"},
            []]
        with self.assertLogs(level='DEBUG') as cm:
            summary = target.cleanup_test_boards(master_cards)
        self.assertEqual(summary, {"cleaned_up_master_cards": 0,
            "deleted_slave_cards": 0,
            "erased_destination_boards": 0,
            "erased_destination_lists": 0})
        expected = ["DEBUG:root:Removing slave cards attachments on the master cards",
            "DEBUG:root:Deleting slave cards",
            "DEBUG:root:================================================================",
            "DEBUG:root:Retrieve cards from list Destination board name 1|Destination list name 1 (list 1/2)",
            "DEBUG:root:[]",
            "DEBUG:root:List Destination board name 1/Destination list name 1 has 0 cards to delete",
            "DEBUG:root:================================================================",
            "DEBUG:root:Retrieve cards from list Destination board name 2|Destination list name 2 (list 2/2)",
            "DEBUG:root:[]",
            "DEBUG:root:List Destination board name 2/Destination list name 2 has 0 cards to delete"]
        self.assertEqual(cm.output, expected)

    @patch("syncboom.perform_request")
    def test_cleanup_test_boards_no_mc_yes_sc(self, t_pr):
        """
        Test cleaning up the test boards when there is no master card and cards on the slave lists
        """
        target.config = {"token": "jkl",
            "destination_lists": {
                "Label One": ["aaa"],
                "Label Two": ["ddd"],
                "All Teams": [
                  "aaa",
                  "ddd"
                ]
            },
            "cleanup_boards": ["q"*24]}
        master_cards = []
        t_pr.side_effect = [
            {"id": "q"*24},
            [{"id": "aaa"}, {"id": "ddd"}],
            {"idBoard": "h"*24},
            {"name": "Destination board name 1"},
            {"name": "Destination list name 1"},
            [{"id": "u"*24}],
            {},
            {"idBoard": "y"*24},
            {"name": "Destination board name 2"},
            {"name": "Destination list name 2"},
            [{"id": "j"*24}],
            {}]
        with self.assertLogs(level='DEBUG') as cm:
            summary = target.cleanup_test_boards(master_cards)
        self.assertEqual(summary, {"cleaned_up_master_cards": 0,
            "deleted_slave_cards": 2,
            "erased_destination_boards": 2,
            "erased_destination_lists": 2})
        expected = ["DEBUG:root:Removing slave cards attachments on the master cards",
            "DEBUG:root:Deleting slave cards",
            "DEBUG:root:================================================================",
            "DEBUG:root:Retrieve cards from list Destination board name 1|Destination list name 1 (list 1/2)",
            "DEBUG:root:[{'id': 'uuuuuuuuuuuuuuuuuuuuuuuu'}]",
            "DEBUG:root:List Destination board name 1/Destination list name 1 has 1 cards to delete",
            "DEBUG:root:Deleting slave card uuuuuuuuuuuuuuuuuuuuuuuu",
            "DEBUG:root:================================================================",
            "DEBUG:root:Retrieve cards from list Destination board name 2|Destination list name 2 (list 2/2)",
            "DEBUG:root:[{'id': 'jjjjjjjjjjjjjjjjjjjjjjjj'}]",
            "DEBUG:root:List Destination board name 2/Destination list name 2 has 1 cards to delete",
            "DEBUG:root:Deleting slave card jjjjjjjjjjjjjjjjjjjjjjjj"]
        self.assertEqual(cm.output, expected)

    @patch("syncboom.perform_request")
    def test_cleanup_test_boards_master_card_no_attach(self, t_pr):
        """
        Test cleaning up the test boards with a master card without attachment
        """
        target.config = {"token": "jkl",
            "destination_lists": {
                "Label One": ["aaa"],
                "Label Two": ["ddd"],
                "All Teams": [
                  "aaa",
                  "ddd"
                ]
            },
            "cleanup_boards": ["q"*24]}
        master_cards = [{"id": "t"*24, "desc": "abc", "name": "Card name",
            "badges": {"attachments": 0}}]
        t_pr.side_effect = [
            [],
            {"id": "q"*24},
            [{"id": "aaa"}, {"id": "ddd"}],
            {"idBoard": "h"*24},
            {"name": "Destination board name 1"},
            {"name": "Destination list name 1"},
            [],
            {"idBoard": "y"*24},
            {"name": "Destination board name 2"},
            {"name": "Destination list name 2"},
            []]
        with self.assertLogs(level='DEBUG') as cm:
            summary = target.cleanup_test_boards(master_cards)
        self.assertEqual(summary, {"cleaned_up_master_cards": 0,
            "deleted_slave_cards": 0,
            "erased_destination_boards": 0,
            "erased_destination_lists": 0})
        expected = ["DEBUG:root:Removing slave cards attachments on the master cards",
            "DEBUG:root:================================================================",
            "INFO:root:Cleaning up master card 1/1 - Card name",
            "DEBUG:root:Retrieving checklists from card tttttttttttttttttttttttt",
            "DEBUG:root:Deleting slave cards",
            "DEBUG:root:================================================================",
            "DEBUG:root:Retrieve cards from list Destination board name 1|Destination list name 1 (list 1/2)",
            "DEBUG:root:[]",
            "DEBUG:root:List Destination board name 1/Destination list name 1 has 0 cards to delete",
            "DEBUG:root:================================================================",
            "DEBUG:root:Retrieve cards from list Destination board name 2|Destination list name 2 (list 2/2)",
            "DEBUG:root:[]",
            "DEBUG:root:List Destination board name 2/Destination list name 2 has 0 cards to delete"]
        self.assertEqual(cm.output, expected)

    @patch("syncboom.perform_request")
    def test_cleanup_test_boards_master_card_attach(self, t_pr):
        """
        Test cleaning up the test boards with a master card with related attachment
        """
        target.config = {"token": "jkl",
            "destination_lists": {
                "Label One": ["aaa"],
                "Label Two": ["ddd"],
                "All Teams": [
                  "aaa",
                  "ddd"
                ]
            },
            "cleanup_boards": ["q"*24]}
        master_cards = [{"id": "t"*24, "desc": "abc", "name": "Card name",
            "badges": {"attachments": 1}}]
        t_pr.side_effect = [
            [{"id": "a"*24, "url": "https://trello.com/c/eoK0Rngb/blablabla"}],
            {},
            [{"id": "b"*24, "name": "Involved Teams"}],
            {},
            {"id": "q"*24},
            [{"id": "aaa"}, {"id": "ddd"}],
            {"idBoard": "h"*24},
            {"name": "Destination board name 1"},
            {"name": "Destination list name 1"},
            [],
            {"idBoard": "y"*24},
            {"name": "Destination board name 2"},
            {"name": "Destination list name 2"},
            []]
        with self.assertLogs(level='DEBUG') as cm:
            summary = target.cleanup_test_boards(master_cards)
        self.assertEqual(summary, {"cleaned_up_master_cards": 1,
            "deleted_slave_cards": 0,
            "erased_destination_boards": 0,
            "erased_destination_lists": 0})
        expected = ["DEBUG:root:Removing slave cards attachments on the master cards",
            "DEBUG:root:================================================================",
            "INFO:root:Cleaning up master card 1/1 - Card name",
            "DEBUG:root:Getting 1 attachments on master card tttttttttttttttttttttttt",
            "DEBUG:root:Deleting attachment aaaaaaaaaaaaaaaaaaaaaaaa from master card tttttttttttttttttttttttt",
            "DEBUG:root:Retrieving checklists from card tttttttttttttttttttttttt",
            "DEBUG:root:Deleting checklist Involved Teams (bbbbbbbbbbbbbbbbbbbbbbbb) from master card tttttttttttttttttttttttt",
            "DEBUG:root:Deleting slave cards",
            "DEBUG:root:================================================================",
            "DEBUG:root:Retrieve cards from list Destination board name 1|Destination list name 1 (list 1/2)",
            "DEBUG:root:[]",
            "DEBUG:root:List Destination board name 1/Destination list name 1 has 0 cards to delete",
            "DEBUG:root:================================================================",
            "DEBUG:root:Retrieve cards from list Destination board name 2|Destination list name 2 (list 2/2)",
            "DEBUG:root:[]",
            "DEBUG:root:List Destination board name 2/Destination list name 2 has 0 cards to delete"]
        self.assertEqual(cm.output, expected)


class TestUpdateMasterCardMetadata(FlaskTestCase):
    @patch("syncboom.perform_request")
    def test_update_master_card_metadata_both_none(self, t_pr):
        """
        Test updating a card that had no metadata with empty metadata
        """
        master_card = {"id": "1a2b3c", "desc": "abc"}
        target.update_master_card_metadata(master_card, "")
        # Confirm perform_request hasn't been called
        self.assertEqual(t_pr.mock_calls, [])

    @patch("syncboom.perform_request")
    def test_update_master_card_metadata(self, t_pr):
        """
        Test updating a card that had no metadata with new metadata
        """
        master_card = {"id": "1a2b3c", "desc": "abc"}
        metadata = "jsdofhzpeh\nldjfozije"
        target.update_master_card_metadata(master_card, metadata)
        expected = [call('PUT', 'cards/1a2b3c',
            {'desc': 'abc\n\n--------------------------------\n*== DO NOT EDIT BELOW THIS LINE ==*\njsdofhzpeh\nldjfozije'})]
        self.assertEqual(t_pr.mock_calls, expected)

    @patch("syncboom.perform_request")
    def test_update_master_card_metadata_empty(self, t_pr):
        """
        Test updating a card's that had metadata with empty metadata
        """
        main_desc = "abc"
        old_metadata = "old metadata"
        master_card = {"id": "1a2b3c", "desc": "%s%s%s" % (main_desc, target.METADATA_SEPARATOR, old_metadata) }
        target.update_master_card_metadata(master_card, "")
        expected = [call('PUT', 'cards/1a2b3c', {'desc': main_desc})]
        self.assertEqual(t_pr.mock_calls, expected)

    @patch("syncboom.perform_request")
    def test_update_master_card_metadata_both(self, t_pr):
        """
        Test updating a card's that had metadata with new metadata
        """
        main_desc = "abc"
        old_metadata = "old metadata"
        master_card = {"id": "1a2b3c", "desc": "%s%s%s" % (main_desc, target.METADATA_SEPARATOR, old_metadata) }
        new_metadata = "new metadata"
        target.update_master_card_metadata(master_card, new_metadata)
        expected = [call('PUT', 'cards/1a2b3c',
            {'desc': "%s%s%s" % (main_desc, target.METADATA_SEPARATOR, new_metadata)})]
        self.assertEqual(t_pr.mock_calls, expected)

    @patch("syncboom.perform_request")
    def test_update_master_card_metadata_same(self, t_pr):
        """
        Test updating a card's that had metadata with the same new metadata
        """
        main_desc = "abc"
        old_metadata = "old metadata"
        master_card = {"id": "1a2b3c", "desc": "%s%s%s" % (main_desc, target.METADATA_SEPARATOR, old_metadata) }
        target.update_master_card_metadata(master_card, old_metadata)
        # Confirm perform_request hasn't been called
        self.assertEqual(t_pr.mock_calls, [])


class TestSplitMasterCardMetadata(FlaskTestCase):
    def test_split_master_card_metadata_no_metadata(self):
        """
        Test splitting the master card description without metadata
        """
        full_desc = "ABC\nDEF"
        (main_desc, current_metadata) = target.split_master_card_metadata(full_desc)
        self.assertEqual(main_desc, full_desc)
        self.assertEqual(current_metadata, "")

    def test_split_master_card_metadata_partially_broken_metadata(self):
        """
        Test splitting the master card description with partially broken metadata
        """
        desc = "ABC\nDEFsldkjf"
        full_desc = "%s== DO NOT EDIT BELOW THIS LINEkhsfhizehf" % desc
        (main_desc, current_metadata) = target.split_master_card_metadata(full_desc)
        self.assertEqual(main_desc, desc + "== ")
        self.assertEqual(current_metadata, "")

    def test_split_master_card_metadata_fully_broken_metadata(self):
        """
        Test splitting the master card description with fully broken metadata
        """
        full_desc = "ABC\nDEFsldkjf\n== DO NOT EDIT BELOW THIS Lsfhizehf"
        (main_desc, current_metadata) = target.split_master_card_metadata(full_desc)
        self.assertEqual(main_desc, full_desc)
        self.assertEqual(current_metadata, "")

    def test_split_master_card_metadata_valid_metadata(self):
        """
        Test splitting the master card description with valid metadata
        """
        desc = "ABC\nDEFsldkjf"
        metadata = "jsdofhzpeh\nldjfozije"
        full_desc = "%s%s%s" % (desc, target.METADATA_SEPARATOR, metadata)
        (main_desc, current_metadata) = target.split_master_card_metadata(full_desc)
        self.assertEqual(main_desc, desc)
        self.assertEqual(current_metadata, metadata)


class TestGetName(FlaskTestCase):
    @patch("syncboom.perform_request")
    def test_get_name_board_uncached(self, t_pr):
        """
        Test getting a board's name, uncached
        """
        t_pr.return_value = {"name": "abc"}
        target.get_name("board", "a1b2c3")
        expected = call('GET', 'board/a1b2c3')
        self.assertEqual(t_pr.mock_calls[0], expected)

    @patch("syncboom.perform_request")
    def test_get_name_board_cached(self, t_pr):
        """
        Test getting a board's name, cached
        """
        expected_name = "Board name to be cached"
        # First call, expect network query and answer to be cached
        t_pr.side_effect = [{"name": expected_name}]
        cache_key = target.get_name.make_cache_key(target.get_name, "board", "a1b2c3")
        self.assertEqual(target.cache.get(cache_key), None)
        board_name = target.get_name("board", "a1b2c3")
        expected_call = call('GET', 'board/a1b2c3')
        self.assertEqual(len(t_pr.mock_calls), 1)
        self.assertEqual(t_pr.mock_calls[0], expected_call)
        self.assertEqual(board_name, expected_name)
        self.assertEqual(target.cache.get(cache_key), "Board name to be cached")
        # Second call, no new network call, value from the cache
        board_name = target.get_name("board", "a1b2c3")
        self.assertEqual(len(t_pr.mock_calls), 1)
        self.assertEqual(board_name, expected_name)

    @patch("syncboom.perform_request")
    def test_get_name_list_uncached(self, t_pr):
        """
        Test getting a list's name, uncached
        """
        t_pr.return_value = {"name": "abc"}
        target.get_name("list", "d4e5f6")
        expected = call('GET', 'list/d4e5f6')
        self.assertEqual(t_pr.mock_calls[0], expected)

    @patch("syncboom.perform_request")
    def test_get_name_list_cached(self, t_pr):
        """
        Test getting a list's name, cached
        """
        expected_name = "List name to be cached"
        # First call, expect network query and answer to be cached
        t_pr.side_effect = [{"name": expected_name}]
        cache_key = target.get_name.make_cache_key(target.get_name, "list", "d4e5f6")
        self.assertEqual(target.cache.get(cache_key), None)
        list_name = target.get_name("list", "d4e5f6")
        expected_call = call('GET', 'list/d4e5f6')
        self.assertEqual(len(t_pr.mock_calls), 1)
        self.assertEqual(t_pr.mock_calls[0], expected_call)
        self.assertEqual(list_name, expected_name)
        self.assertEqual(target.cache.get(cache_key), "List name to be cached")
        # Second call, no new network call, value from the cache
        list_name = target.get_name("list", "d4e5f6")
        self.assertEqual(len(t_pr.mock_calls), 1)
        self.assertEqual(list_name, expected_name)


class TestGetBoardNameFromList(FlaskTestCase):
    @patch("syncboom.perform_request")
    def test_get_board_name_from_list_uncached(self, t_pr):
        """
        Test getting a board's name from one of it's list ID, uncached
        """
        expected_name = "Board name"
        t_pr.side_effect = [{"idBoard": "x"*24}, {"name": expected_name}]
        board_name = target.get_board_name_from_list("z"*24)
        expected_calls =[call('GET', 'lists/zzzzzzzzzzzzzzzzzzzzzzzz'),
            call('GET', 'board/xxxxxxxxxxxxxxxxxxxxxxxx')]
        self.assertEqual(board_name, expected_name)
        self.assertEqual(t_pr.mock_calls, expected_calls)

    @patch("syncboom.perform_request")
    def test_get_board_name_from_list_cached(self, t_pr):
        """
        Test getting a board's name from one of it's list ID, cached
        """
        expected_name = "Board name to be cached"
        # First call, two expect network query and answer to be cached
        t_pr.side_effect = [{"idBoard": "x"*24}, {"name": expected_name}]
        board_name = target.get_board_name_from_list("z"*24)
        expected_calls =[call('GET', 'lists/zzzzzzzzzzzzzzzzzzzzzzzz'),
            call('GET', 'board/xxxxxxxxxxxxxxxxxxxxxxxx')]
        self.assertEqual(len(t_pr.mock_calls), 2)
        self.assertEqual(t_pr.mock_calls, expected_calls)
        self.assertEqual(board_name, expected_name)
        # Second call, no new network call, value from the cache
        board_name = target.get_board_name_from_list("z"*24)
        self.assertEqual(len(t_pr.mock_calls), 2)
        self.assertEqual(board_name, expected_name)


class TestGenerateMasterCardMetadata(FlaskTestCase):
    @patch("syncboom.perform_request")
    def test_generate_master_card_metadata_no_slave_cards(self, t_pr):
        """
        Test generating a master card's metadata that has no slave cards
        """
        slave_cards = []
        new_master_card_metadata = target.generate_master_card_metadata(slave_cards)
        # Confirm perform_request hasn't been called
        self.assertEqual(t_pr.mock_calls, [])
        self.assertEqual(new_master_card_metadata, "")

    @patch("syncboom.perform_request")
    def test_generate_master_card_metadata_no_slave_cards_uncached(self, t_pr):
        """
        Test generating a master card's metadata that has 3 slave cards, uncached
        """
        slave_cards = [{"name": "name1", "idBoard": "idBoard1", "idList": "idList1"},
                       {"name": "name2", "idBoard": "idBoard2", "idList": "idList2"},
                       {"name": "name3", "idBoard": "idBoard3", "idList": "idList3"}]
        t_pr.side_effect = [{"name": "record name1"},
            {"name": "record name2"},
            {"name": "record name3"},
            {"name": "record name4"},
            {"name": "record name5"},
            {"name": "record name6"}]
        new_master_card_metadata = target.generate_master_card_metadata(slave_cards)
        expected = [call('GET', 'board/idBoard1'),
            call('GET', 'list/idList1'),
            call('GET', 'board/idBoard2'),
            call('GET', 'list/idList2'),
            call('GET', 'board/idBoard3'),
            call('GET', 'list/idList3')]
        self.assertEqual(t_pr.mock_calls, expected)
        expected = "\n- 'name1' on list '**record name1|record name2**'\n- 'name2' on list '**record name3|record name4**'\n- 'name3' on list '**record name5|record name6**'"
        self.assertEqual(new_master_card_metadata, expected)


class TestPerformRequest(FlaskTestCase):
    def test_perform_request_invalid_http_method(self):
        """
        Test performing a request with an invalid HTTP method
        """
        with self.assertRaises(SystemExit) as cm1, self.assertLogs(level='CRITICAL') as cm2:
            target.perform_request("INVALID", "https://monip.org")
        self.assertEqual(cm1.exception.code, 30)
        self.assertEqual(cm2.output, ["CRITICAL:root:HTTP method 'INVALID' not supported. Exiting..."])

    @patch("requests.request")
    def test_perform_request_get(self, r_r):
        """
        Test performing a GET request
        """
        target.args = type(inspect.stack()[0][3], (object,), {"dry_run": False})()
        target.config = {"token": "jkl"}
        mock_response = MagicMock()
        mock_response.json.return_value = {}
        r_r.return_value = mock_response
        target.perform_request("GET", "cards/a1b2c3d4")
        expected = [call('GET', 'https://api.trello.com/1/cards/a1b2c3d4?key=ghi&token=jkl', params=None),
            call().raise_for_status(),
            call().json()]
        self.assertEqual(r_r.mock_calls, expected)
        target.args = None

    @patch("requests.request")
    def test_perform_request_get_dry_run(self, r_r):
        """
        Test performing a GET request with --dry-run
        """
        target.args = type(inspect.stack()[0][3], (object,), {"dry_run": True})()
        target.config = {"token": "jkl"}
        mock_response = MagicMock()
        mock_response.json.return_value = {}
        r_r.return_value = mock_response
        target.perform_request("GET", "cards/a1b2c3d4")
        expected = [call('GET', 'https://api.trello.com/1/cards/a1b2c3d4?key=ghi&token=jkl', params=None),
            call().raise_for_status(),
            call().json()]
        self.assertEqual(r_r.mock_calls, expected)
        target.args = None

    @patch("requests.request")
    def test_perform_request_post(self, r_r):
        """
        Test performing a POST request
        """
        target.args = type(inspect.stack()[0][3], (object,), {"dry_run": False})()
        target.config = {"token": "jkl"}
        target.perform_request("POST", "cards/a1b2c3d4", {"abc": "def"})
        expected = [call('POST', 'https://api.trello.com/1/cards/a1b2c3d4?key=ghi&token=jkl', params={'abc': 'def'}),
            call().raise_for_status(),
            call().json()]
        self.assertEqual(r_r.mock_calls, expected)
        target.args = None

    @patch("requests.request")
    def test_perform_request_post_dry_run(self, r_r):
        """
        Test performing a POST request with --dry-run
        """
        target.args = type(inspect.stack()[0][3], (object,), {"dry_run": True})()
        target.config = {"token": "jkl"}
        with self.assertLogs(level='DEBUG') as cm:
            target.perform_request("POST", "cards/a1b2c3d4", {"abc": "def"})
        self.assertEqual(cm.output, ["DEBUG:root:Skipping POST call to 'https://api.trello.com/1/cards/a1b2c3d4' due to --dry-run parameter"])
        # Confirm no actual network request went out
        self.assertEqual(r_r.mock_calls, [])
        target.args = None

    @patch("requests.request")
    def test_perform_request_put(self, r_r):
        """
        Test performing a PUT request
        """
        target.args = type(inspect.stack()[0][3], (object,), {"dry_run": False})()
        target.config = {"token": "jkl"}
        target.perform_request("PUT", "cards/a1b2c3d4", {"abc": "def"})
        expected = [call('PUT', 'https://api.trello.com/1/cards/a1b2c3d4?key=ghi&token=jkl', params={'abc': 'def'}),
            call().raise_for_status(),
            call().json()]
        self.assertEqual(r_r.mock_calls, expected)
        target.args = None

    @patch("requests.request")
    def test_perform_request_put_dry_run(self, r_r):
        """
        Test performing a PUT request with --dry-run
        """
        target.args = type(inspect.stack()[0][3], (object,), {"dry_run": True})()
        target.config = {"token": "jkl"}
        with self.assertLogs(level='DEBUG') as cm:
            target.perform_request("PUT", "cards/a1b2c3d4", {"abc": "def"})
        self.assertEqual(cm.output, ["DEBUG:root:Skipping PUT call to 'https://api.trello.com/1/cards/a1b2c3d4' due to --dry-run parameter"])
        # Confirm no actual network request went out
        self.assertEqual(r_r.mock_calls, [])
        target.args = None

    @patch("requests.request")
    def test_perform_request_delete(self, r_r):
        """
        Test performing a DELETE request
        """
        target.args = type(inspect.stack()[0][3], (object,), {"dry_run": False})()
        target.config = {"token": "jkl"}
        target.perform_request("DELETE", "cards/a1b2c3d4")
        expected = [call('DELETE', 'https://api.trello.com/1/cards/a1b2c3d4?key=ghi&token=jkl', params=None),
            call().raise_for_status(),
            call().json()]
        self.assertEqual(r_r.mock_calls, expected)
        target.args = None

    @patch("requests.request")
    def test_perform_request_delete_dry_run(self, r_r):
        """
        Test performing a DELETE request with --dry-run
        """
        target.args = type(inspect.stack()[0][3], (object,), {"dry_run": True})()
        target.config = {"token": "jkl"}
        with self.assertLogs(level='DEBUG') as cm:
            target.perform_request("DELETE", "cards/a1b2c3d4")
        self.assertEqual(cm.output, ["DEBUG:root:Skipping DELETE call to 'https://api.trello.com/1/cards/a1b2c3d4' due to --dry-run parameter"])
        # Confirm no actual network request went out
        self.assertEqual(r_r.mock_calls, [])
        target.args = None

    @patch("requests.request")
    def test_perform_request_connection_error(self, r_r):
        """
        Confirm a connection error to Trello raises TrelloConnectionError
        """
        target.args = type(inspect.stack()[0][3], (object,), {"dry_run": True})()
        target.config = {"token": "jkl"}
        r_r.side_effect = ConnectionError()
        with self.assertRaises(target.TrelloConnectionError) as cm:
            target.perform_request("GET", "cards/a1b2c3d4")

    @patch("requests.request")
    def test_perform_request_http_error_401(self, r_r):
        """
        Confirm a 401 response code from Trello raises TrelloAuthenticationError
        """
        target.args = type(inspect.stack()[0][3], (object,), {"dry_run": True})()
        target.config = {"token": "jkl"}
        mock_exception_response = MagicMock()
        mock_exception_response.status_code = 401
        mock_request = MagicMock()
        mock_request.raise_for_status.side_effect = HTTPError("", response=mock_exception_response)
        r_r.return_value = mock_request
        with self.assertRaises(target.TrelloAuthenticationError) as cm:
            target.perform_request("GET", "cards/a1b2c3d4")

    @patch("requests.request")
    def test_perform_request_http_error_other(self, r_r):
        """
        Confirm a 401 response code from Trello raises TrelloAuthenticationError
        """
        target.args = type(inspect.stack()[0][3], (object,), {"dry_run": True})()
        target.config = {"token": "jkl"}
        mock_exception_response = MagicMock()
        mock_exception_response.status_code = "OTHER"
        mock_request = MagicMock()
        mock_request.raise_for_status.side_effect = HTTPError("", response=mock_exception_response)
        r_r.return_value = mock_request
        with self.assertRaises(HTTPError) as cm1, self.assertLogs(level='CRITICAL') as cm2:
            target.perform_request("GET", "cards/a1b2c3d4")
        self.assertTrue("CRITICAL:root:Request failed with code OTHER and message '<MagicMock name='request().content' id='" in cm2.output[0])

    @patch("requests.request")
    def test_perform_request_cached(self, r_r):
        """
        Test performing twice the same GET request, the second call will return the cached content
        """
        target.args = type(inspect.stack()[0][3], (object,), {"dry_run": False})()
        target.config = {"token": "jkl"}
        mock_response = MagicMock()
        mock_response.json.return_value = {"key1": "value1", "key2": "value2"}
        r_r.return_value = mock_response
        output_first = target.perform_request("GET", "cards/a1b2c3d4")
        expected = [call('GET', 'https://api.trello.com/1/cards/a1b2c3d4?key=ghi&token=jkl', params=None),
            call().raise_for_status(),
            call().json()]
        self.assertEqual(r_r.mock_calls, expected)
        self.assertEqual(len(r_r.mock_calls), 3)
        # Second call, no additional external request, same output
        output_second = target.perform_request("GET", "cards/a1b2c3d4")
        self.assertEqual(r_r.mock_calls, expected)
        self.assertEqual(len(r_r.mock_calls), 3)
        self.assertEqual(output_first, output_second)
        target.args = None


class TestCreateNewSlaveCard(FlaskTestCase):
    @patch("syncboom.perform_request")
    def test_create_new_slave_card(self, t_pr):
        """
        Test creating a new card
        """
        target.config = {"token": "jkl"}
        master_card = {"id": "1a2b3c", "desc": "abc", "shortUrl": "https://trello.com/c/eoK0Rngb"}
        destination_list = "a"*24
        t_pr.return_value = {"id": "b"*24}
        card = target.create_new_slave_card(master_card, destination_list)
        expected = [call('POST', 'cards',
            {'idList': 'aaaaaaaaaaaaaaaaaaaaaaaa',
            'desc': 'abc\n\nCreated from master card https://trello.com/c/eoK0Rngb',
            'pos': 'bottom', 'idCardSource': '1a2b3c',
            'keepFromSource': 'attachments,checklists,comments,due,stickers'})]
        self.assertEqual(t_pr.mock_calls, expected)
        self.assertEqual(card, t_pr.return_value)


class TestGlobals(FlaskTestCase):
    def test_globals_metadata_phrase(self):
        """
        Test the METADATA_PHRASE global
        """
        self.assertEqual(target.METADATA_PHRASE, "DO NOT EDIT BELOW THIS LINE")

    def test_globals_metadata_separator(self):
        """
        Test the METADATA_SEPARATOR global
        """
        self.assertEqual(target.METADATA_SEPARATOR, "\n\n--------------------------------\n*== DO NOT EDIT BELOW THIS LINE ==*\n")


class TestIsProductionEnvironment(FlaskTestCase):
    def test_is_production_environment(self):
        self.assertEqual(target.is_production_environment(),
            os.environ.get('ON_HEROKU') == True)


class TestNewWebhook(FlaskTestCase):
    @patch("syncboom.perform_request")
    def test_new_webhook_devel_fresh(self, t_pr):
        """
        Test creating a new webhook, Dev mode with no temporary UUID defined
        """
        target.config = {"master_board": "cde"}
        # Generate a random UUID
        uuid = uuid4()
        t_pr.side_effect = [{"uuid": str(uuid)}, {}]
        # Generate a random UUID and save it in a temporary file
        (temp_fd, temp_webhook_file) = tempfile.mkstemp()
        target.new_webhook(temp_webhook_file)
        expected = [
            call('POST', 'token', base_url='https://webhook.site/%s'),
            call('POST', 'webhooks', {'callbackURL': 'https://webhook.site/%s?c=config' % uuid, 'idModel': 'cde'})
        ]
        self.assertEqual(t_pr.mock_calls, expected)
        self.assertTrue(os.path.isfile(temp_webhook_file))
        # Remove the temporary file
        os.close(temp_fd)
        os.remove(temp_webhook_file)

    @patch("syncboom.perform_request")
    def test_new_webhook_devel_temp_data_valid(self, t_pr):
        """
        Test creating a new webhook, Dev mode with a valid temporary UUID defined
        """
        target.config = {"master_board": "cde"}
        t_pr.return_value = {}
        # Generate a random UUID and save it in a temporary file
        uuid = uuid4()
        (temp_fd, temp_webhook_file) = tempfile.mkstemp()
        with open(temp_webhook_file, "w") as json_file:
            json.dump({"uuid": str(uuid)}, json_file, indent=2)
        target.new_webhook(temp_webhook_file)
        expected = [
            call('GET', 'token/%s' % uuid, base_url='https://webhook.site/%s'),
            call('POST', 'webhooks', {'callbackURL': 'https://webhook.site/%s?c=config' % uuid, 'idModel': 'cde'})
        ]
        self.assertEqual(t_pr.mock_calls, expected)
        # Remove the temporary file
        os.close(temp_fd)
        os.remove(temp_webhook_file)

    @patch("syncboom.is_production_environment")
    @patch("syncboom.perform_request")
    def test_new_webhook_prod(self, t_pr, s_ipe):
        """
        Test creating a new webhook, Prod mode
        """
        target.config = {"master_board": "cde"}
        s_ipe.return_value = True
        t_pr.return_value = {}
        target.new_webhook()
        expected = [
            call('POST', 'webhooks', {'callbackURL': 'https://syncboom.com/webhooks/1/?c=config', 'idModel': 'cde'})
        ]
        self.assertEqual(t_pr.mock_calls, expected)


class TestListWebhooks(FlaskTestCase):
    @patch("syncboom.perform_request")
    def test_list_webhooks(self, t_pr):
        """
        Test listing webhooks
        """
        target.config = {"token": "jkl"}
        t_pr.return_value = {"dummy": "list"}
        webhooks = target.list_webhooks()
        expected = [call('GET', 'tokens/jkl/webhooks')]
        self.assertEqual(t_pr.mock_calls, expected)
        self.assertEqual(webhooks, t_pr.return_value)


class TestDeleteWebhook(FlaskTestCase):
    @patch("syncboom.perform_request")
    def test_delete_webhook_none(self, t_pr):
        """
        Test deleting this board's webhook when no webhook exists
        """
        target.config = {"token": "jkl", "master_board": "cde"}
        t_pr.return_value = {}
        webhooks = target.delete_webhook()
        expected = [call('GET', 'tokens/jkl/webhooks')]
        self.assertEqual(t_pr.mock_calls, expected)

    @patch("syncboom.perform_request")
    def test_delete_webhook_one_ok(self, t_pr):
        """
        Test deleting this board's webhook when there is one webhook for that board
        """
        target.config = {"token": "jkl", "master_board": "cde"}
        t_pr.side_effect = [[{"id": "kdfg", "idModel": target.config["master_board"]}], {}]
        webhooks = target.delete_webhook()
        expected = [call('GET', 'tokens/jkl/webhooks'),
            call('DELETE', 'webhooks/kdfg')]
        self.assertEqual(t_pr.mock_calls, expected)

    @patch("syncboom.perform_request")
    def test_delete_webhook_one_nok(self, t_pr):
        """
        Test deleting this board's webhook when there is one webhook but not for that board
        """
        target.config = {"token": "jkl", "master_board": "this_board"}
        t_pr.return_value = [{"id": "kdfg", "idModel": "other_board"}]
        webhooks = target.delete_webhook()
        expected = [call('GET', 'tokens/jkl/webhooks')]
        self.assertEqual(t_pr.mock_calls, expected)

    @patch("syncboom.perform_request")
    def test_delete_webhook_multiple_ok(self, t_pr):
        """
        Test deleting this board's webhook when there are multiple webhook including for that board
        """
        target.config = {"token": "jkl", "master_board": "this_board"}
        t_pr.return_value = [{"id": "kdfg1", "idModel": "other_board"},
            {"id": "kdfg2", "idModel": "yet_another_board"},
            {"id": "kdfg3", "idModel": "this_board"}]
        webhooks = target.delete_webhook()
        expected = [call('GET', 'tokens/jkl/webhooks'),
            call('DELETE', 'webhooks/kdfg3')]
        self.assertEqual(t_pr.mock_calls, expected)

    @patch("syncboom.perform_request")
    def test_delete_webhook_multiple_ok(self, t_pr):
        """
        Test deleting this board's webhook when there are multiple webhook including for that board
        """
        target.config = {"token": "jkl", "master_board": "this_board"}
        t_pr.return_value = [{"id": "kdfg1", "idModel": "other_board"},
            {"id": "kdfg2", "idModel": "yet_another_board"}]
        webhooks = target.delete_webhook()
        expected = [call('GET', 'tokens/jkl/webhooks')]
        self.assertEqual(t_pr.mock_calls, expected)


class TestParseArgs(FlaskTestCase):
    def test_parse_args_no_arguments(self):
        """
        Test running the script without one of the required arguments --propagate, --cleanup, --new-config or --webhook
        """
        f = io.StringIO()
        with self.assertRaises(SystemExit) as cm, contextlib.redirect_stderr(f):
            parser = target.parse_args([])
        self.assertEqual(cm.exception.code, 2)
        self.assertTrue("error: one of the arguments -p/--propagate -cu/--cleanup -nc/--new-config -w/--webhook is required" in f.getvalue())

    def test_parse_args_propagate_cleanup(self):
        """
        Test running the script with both mutually exclusive arguments --propagate and --cleanup
        """
        f = io.StringIO()
        with self.assertRaises(SystemExit) as cm, contextlib.redirect_stderr(f):
            parser = target.parse_args(['--propagate', '--cleanup'])
        self.assertEqual(cm.exception.code, 2)
        self.assertTrue("error: argument -cu/--cleanup: not allowed with argument -p/--propagate" in f.getvalue())

    def test_parse_args_propagate_new_config(self):
        """
        Test running the script with both mutually exclusive arguments --propagate and --new-config
        """
        f = io.StringIO()
        with self.assertRaises(SystemExit) as cm, contextlib.redirect_stderr(f):
            parser = target.parse_args(['--propagate', '--new-config'])
        self.assertEqual(cm.exception.code, 2)
        self.assertTrue("error: argument -nc/--new-config: not allowed with argument -p/--propagate" in f.getvalue())

    def test_parse_args_propagate_webhook(self):
        """
        Test running the script with both mutually exclusive arguments --propagate and --webhook
        """
        f = io.StringIO()
        with self.assertRaises(SystemExit) as cm, contextlib.redirect_stderr(f):
            parser = target.parse_args(['--propagate', '--webhook', 'list'])
        self.assertEqual(cm.exception.code, 2)
        self.assertTrue("error: argument -w/--webhook: not allowed with argument -p/--propagate" in f.getvalue())

    def test_parse_args_debug(self):
        """
        Test the --debug argument
        """
        parser = target.parse_args(['--debug', '--propagate'])
        self.assertEqual(parser.loglevel, logging.DEBUG)
        self.assertEqual(parser.logging_level, "DEBUG")

    def test_parse_args_debug_shorthand(self):
        """
        Test the -d argument
        """
        parser = target.parse_args(['-d', '--propagate'])
        self.assertEqual(parser.loglevel, logging.DEBUG)
        self.assertEqual(parser.logging_level, "DEBUG")

    def test_parse_args_verbose(self):
        """
        Test the --verbose argument
        """
        parser = target.parse_args(['--verbose', '--propagate'])
        self.assertEqual(parser.loglevel, logging.INFO)
        self.assertEqual(parser.logging_level, "INFO")

    def test_parse_args_verbose_shorthand(self):
        """
        Test the -v argument
        """
        parser = target.parse_args(['-v', '--propagate'])
        self.assertEqual(parser.loglevel, logging.INFO)
        self.assertEqual(parser.logging_level, "INFO")

    def test_parse_args_propagate(self):
        """
        Test the --propagate argument
        """
        parser = target.parse_args(['--propagate'])
        self.assertTrue(parser.propagate)

    def test_parse_args_propagate_shorthand(self):
        """
        Test the -p argument
        """
        parser = target.parse_args(['-p'])
        self.assertTrue(parser.propagate)

    def test_parse_args_cleanup(self):
        """
        Test the --cleanup argument
        """
        parser = target.parse_args(['--cleanup', '--debug'])
        self.assertTrue(parser.cleanup)

    def test_parse_args_cleanup_shorthand(self):
        """
        Test the -cu argument
        """
        parser = target.parse_args(['-cu', '--debug'])
        self.assertTrue(parser.cleanup)

    def test_parse_args_valid_card8(self):
        """
        Test a valid 8-character --card parameter
        """
        shortLink = "eoK0Rngb"
        parser = target.parse_args(['--propagate', '--card', shortLink])
        self.assertEqual(parser.card, shortLink)

    def test_parse_args_valid_card24(self):
        """
        Test a valid 24-character --card parameter
        """
        card_id = "5ea946e30ea7437974b0ac9e"
        parser = target.parse_args(['--propagate', '--card', card_id])
        self.assertEqual(parser.card, card_id)

    def test_parse_args_valid_card_short_url(self):
        """
        Test a valid short card URL --card parameter
        """
        shortLink = "eoK0Rngb"
        url = "https://trello.com/c/%s" % shortLink
        parser = target.parse_args(['--propagate', '--card', url])
        self.assertEqual(parser.card, shortLink)

    def test_parse_args_valid_card_long_url(self):
        """
        Test a valid long card URL --card parameter
        """
        shortLink = "eoK0Rngb"
        url = "https://trello.com/c/%s/60-another-task-for-all-teams" % shortLink
        parser = target.parse_args(['--propagate', '--card', url])
        self.assertEqual(parser.card, shortLink)

    def test_parse_args_invalid_card8(self):
        """
        Test an invalid 8-character --card parameter
        """
        shortLink = "$oK0Rngb"
        with self.assertRaises(SystemExit) as cm1, self.assertLogs(level='CRITICAL') as cm2:
            parser = target.parse_args(['--propagate', '--card', shortLink])
        self.assertEqual(cm1.exception.code, 5)
        self.assertEqual(cm2.output, ["CRITICAL:root:The --card argument expects an 8 or 24-character card ID. Exiting..."])

    def test_parse_args_invalid_card24(self):
        """
        Test an invalid 24-character --card parameter
        """
        card_id = "5Ga946e30ea7437974b0ac9e"
        with self.assertRaises(SystemExit) as cm1, self.assertLogs(level='CRITICAL') as cm2:
            parser = target.parse_args(['--propagate', '--card', card_id])
        self.assertEqual(cm1.exception.code, 5)
        self.assertEqual(cm2.output, ["CRITICAL:root:The --card argument expects an 8 or 24-character card ID. Exiting..."])

    def test_parse_args_invalid_card_url(self):
        """
        Test an invalid card URL by passing a board URL as --card parameter
        """
        url = "https://trello.com/b/264nrPh8/test-scrum-of-scrums"
        with self.assertRaises(SystemExit) as cm1, self.assertLogs(level='CRITICAL') as cm2:
            parser = target.parse_args(['--propagate', '--card', url])
        self.assertEqual(cm1.exception.code, 5)
        self.assertEqual(cm2.output, ["CRITICAL:root:The --card argument expects an 8 or 24-character card ID. Exiting..."])

    def test_parse_args_card(self):
        """
        Test an invalid --card parameter
        """
        with self.assertRaises(SystemExit) as cm1, self.assertLogs(level='CRITICAL') as cm2:
            parser = target.parse_args(['--propagate', '--card', 'abc'])
        self.assertEqual(cm1.exception.code, 5)
        self.assertEqual(cm2.output, ["CRITICAL:root:The --card argument expects an 8 or 24-character card ID. Exiting..."])

    def test_parse_args_valid_list24(self):
        """
        Test a valid 24-character --list parameter
        """
        list_id = "5ea6f86a46d1b9096faf6a72"
        parser = target.parse_args(['--propagate', '--list', list_id])
        self.assertEqual(parser.list, list_id)

    def test_parse_args_invalid_list24(self):
        """
        Test an invalid 24-character --list parameter
        """
        list_id = "5Ga6f86a46d1b9096faf6a72"
        with self.assertRaises(SystemExit) as cm1, self.assertLogs(level='CRITICAL') as cm2:
            parser = target.parse_args(['--propagate', '--list', list_id])
        self.assertEqual(cm1.exception.code, 7)
        self.assertEqual(cm2.output, ["CRITICAL:root:The --list argument expects a 24-character list ID. Exiting..."])

    def test_parse_args_list(self):
        """
        Test an invalid --list parameter
        """
        with self.assertRaises(SystemExit) as cm1, self.assertLogs(level='CRITICAL') as cm2:
            parser = target.parse_args(['--propagate', '--list', 'abc'])
        self.assertEqual(cm1.exception.code, 7)
        self.assertEqual(cm2.output, ["CRITICAL:root:The --list argument expects a 24-character list ID. Exiting..."])

    def test_parse_args_cleanup_without_debug(self):
        """
        Test running the script with invalid arguments combination:
        --cleanup without --debug
        """
        with self.assertRaises(SystemExit) as cm1, self.assertLogs(level='CRITICAL') as cm2:
            parser = target.parse_args(['--cleanup'])
        self.assertEqual(cm1.exception.code, 3)
        self.assertEqual(cm2.output, ["CRITICAL:root:The --cleanup argument can only be used in conjunction with --debug. Exiting..."])

    def test_parse_args_card_cleanup(self):
        """
        Test running the script with invalid arguments combination:
        --card with --cleanup
        """
        card_id = "5ea946e30ea7437974b0ac9e"
        with self.assertRaises(SystemExit) as cm1, self.assertLogs(level='CRITICAL') as cm2:
            parser = target.parse_args(['--cleanup', '--debug', '--card', card_id])
        self.assertEqual(cm1.exception.code, 4)
        self.assertEqual(cm2.output, ["CRITICAL:root:The --card argument can only be used in conjunction with --propagate. Exiting..."])

    def test_parse_args_list_cleanup(self):
        """
        Test running the script with invalid arguments combination:
        --list with --cleanup
        """
        list_id = "5ea6f86a46d1b9096faf6a72"
        with self.assertRaises(SystemExit) as cm1, self.assertLogs(level='CRITICAL') as cm2:
            parser = target.parse_args(['--cleanup', '--debug', '--list', list_id])
        self.assertEqual(cm1.exception.code, 6)
        self.assertEqual(cm2.output, ["CRITICAL:root:The --list argument can only be used in conjunction with --propagate. Exiting..."])

    def test_parse_args_dry_run(self):
        """
        Test the --dry-run argument
        """
        parser = target.parse_args(['--propagate', '--dry-run'])
        self.assertTrue(parser.dry_run)

    def test_parse_args_config_valid(self):
        """
        Test the --config argument with a valid file path
        """
        parser = target.parse_args(['--propagate', '--config', "data/sample_config.json"])
        self.assertEqual(parser.config, "data/sample_config.json")

    def test_parse_args_config_invalid(self):
        """
        Test the --config argument with a valid file path
        """
        with self.assertRaises(SystemExit) as cm1, self.assertLogs(level='CRITICAL') as cm2:
            target.parse_args(['--propagate', '--config', "data/nonexisting_config.json"])
        self.assertEqual(cm1.exception.code, 8)
        self.assertEqual(cm2.output, ["CRITICAL:root:The value passed in the --path argument is not a valid file path. Exiting..."])

    def test_parse_args_webhook_no_arg(self):
        """
        Test running the script with --webhook but without its required argument
        """
        f = io.StringIO()
        with self.assertRaises(SystemExit) as cm, contextlib.redirect_stderr(f):
            parser = target.parse_args(["--webhook"])
        self.assertEqual(cm.exception.code, 2)
        self.assertTrue("error: argument -w/--webhook: expected one argument" in f.getvalue())

    def test_parse_args_webhook_invalid_arg(self):
        """
        Test running the script with --webhook with an invalid argument
        """
        f = io.StringIO()
        with self.assertRaises(SystemExit) as cm, contextlib.redirect_stderr(f):
            parser = target.parse_args(["--webhook", "abc"])
        self.assertEqual(cm.exception.code, 2)
        self.assertTrue("error: argument -w/--webhook: invalid choice: 'abc' (choose from 'new', 'list', 'delete')" in f.getvalue())

    def test_parse_args_webhook_without_debug(self):
        """
        Test running the script with --webhook without --debug
        """
        f = io.StringIO()
        with self.assertRaises(SystemExit) as cm1, contextlib.redirect_stderr(f), self.assertLogs(level='DEBUG') as cm2:
            parser = target.parse_args(["--webhook", "list"])
        self.assertEqual(cm1.exception.code, 9)
        self.assertTrue("CRITICAL:root:The --webhook argument can only be used in conjunction with --debug. Exiting..." in cm2.output)

    def test_parse_args_webhook_valid_args(self):
        """
        Test running the script with --webhook with all its valid arguments
        """
        for t in ('new', 'list', 'delete'):
            parser = target.parse_args(["--webhook", t, "--debug"])
            self.assertTrue(parser.webhook, t)


class TestInitMain(FlaskTestCase):
    @patch("syncboom.cleanup_test_boards")
    @patch("syncboom.perform_request")
    def test_init_cleanup(self, t_pr, t_ctb):
        """
        Test the initialization code with --cleanup parameter
        """
        global mock_raw_input_counter
        global mock_raw_input_values
        mock_raw_input_counter = 0
        mock_raw_input_values = ["No problemo"]
        target.__name__ = "__main__"
        target.sys.argv = ["scriptname.py", "--cleanup", "--debug", "--config", "data/sample_config.json"]
        t_pr.side_effect = [[{"id": "a"*24}, {"id": "b"*24}]]
        f = io.StringIO()
        with contextlib.redirect_stdout(f):
            target.init()
        self.assertEqual(f.getvalue(), "WARNING: this will delete all cards on the slave lists. Type 'YES' to confirm, or 'q' to quit:\u0020\n")
        self.assertEqual(t_ctb.mock_calls[0], call([{'id': 'aaaaaaaaaaaaaaaaaaaaaaaa'}, {'id': 'bbbbbbbbbbbbbbbbbbbbbbbb'}]))

    @patch("syncboom.perform_request")
    def test_init_cleanup_no(self, t_pr):
        """
        Test the initialization code with --cleanup parameter, don't accept warning message
        """
        global mock_raw_input_counter
        global mock_raw_input_values
        mock_raw_input_counter = 0
        mock_raw_input_values = ["No", "q"]
        target.__name__ = "__main__"
        target.sys.argv = ["scriptname.py", "--cleanup", "--debug", "--config", "data/sample_config.json"]
        f = io.StringIO()
        with self.assertRaises(SystemExit) as cm1, self.assertLogs(level='DEBUG') as cm2, contextlib.redirect_stdout(f):
            target.init()
        self.assertEqual(cm1.exception.code, 34)
        # Validate which config file was used and last output line (summary)
        self.assertTrue("DEBUG:root:Loading configuration data/sample_config.json" in cm2.output)
        self.assertTrue("DEBUG:root:{'name': 'Sample configuration'" in cm2.output[-1])
        expected_output = """WARNING: this will delete all cards on the slave lists. Type 'YES' to confirm, or 'q' to quit:\u0020
WARNING: this will delete all cards on the slave lists. Type 'YES' to confirm, or 'q' to quit:\u0020
Exiting...
"""
        self.assertEqual(f.getvalue(), expected_output)

    @patch("syncboom.cleanup_test_boards")
    @patch("syncboom.perform_request")
    def test_init_cleanup_dry_run_default_config(self, t_pr, t_ctb):
        """
        Test the initialization code with --cleanup parameter, --dry-run and default config file
        """
        target.__name__ = "__main__"
        target.sys.argv = ["scriptname.py", "--cleanup", "--debug", "--dry-run"]
        t_pr.side_effect = [[{"id": "a"*24}, {"id": "b"*24}]]
        # Handle cases where there is a default config file present (local dev) or not (remote CI)
        if os.path.isfile("data/config.json"):
            target.init()
            self.assertEqual(t_ctb.mock_calls[0], call([{'id': 'aaaaaaaaaaaaaaaaaaaaaaaa'}, {'id': 'bbbbbbbbbbbbbbbbbbbbbbbb'}]))
        else:
            with self.assertRaises(FileNotFoundError) as cm1, self.assertLogs(level='DEBUG') as cm2:
                target.init()
            self.assertEqual(str(cm1.exception), "[Errno 2] No such file or directory: 'data/config.json'")

    @patch("syncboom.perform_request")
    def test_init_new_config(self, t_pr):
        """
        Test the initialization code with --new-config
        """
        global mock_raw_input_counter
        global mock_raw_input_values
        mock_raw_input_counter = 0
        mock_raw_input_values = ["q"]
        target.__name__ = "__main__"
        target.sys.argv = ["scriptname.py", "--new-config", "--config", "data/sample_config.json"]
        t_pr.return_value = {}
        f = io.StringIO()
        with self.assertRaises(SystemExit) as cm1, self.assertLogs(level='DEBUG') as cm2, contextlib.redirect_stdout(f):
            target.init()
        self.assertEqual(cm1.exception.code, 35)

    @patch("syncboom.process_master_card")
    @patch("syncboom.perform_request")
    def test_init_propagate_empty(self, t_pr, t_pmc):
        """
        Test the initialization code with --propagate with a single non-relevant master card
        """
        target.__name__ = "__main__"
        target.sys.argv = ["scriptname.py", "--propagate", "--config", "data/sample_config.json"]
        t_pr.side_effect = [[{"id": "a"*24, "name": "Master card name", "labels": {}, "badges": {"attachments": 0}, "desc": "Desc"}]]
        t_pmc.return_value = (20, 30, 40)
        with self.assertLogs(level='DEBUG') as cm:
            target.init()
        self.assertEqual(len(t_pmc.mock_calls), 1)
        self.assertEqual(t_pmc.mock_calls[0], call({'id': 'aaaaaaaaaaaaaaaaaaaaaaaa', 'name': 'Master card name', 'labels': {}, 'badges': {'attachments': 0}, 'desc': 'Desc'}))
        self.assertTrue("INFO:root:Summary: processed 1 master cards (of which 20 active) that have 30 slave cards (of which 40 new)." in cm.output)

    @patch("syncboom.perform_request")
    def test_init_propagate_list_invalid(self, t_pr):
        """
        Test the initialization code with --propagate and --list that's not on the master board
        """
        target.__name__ = "__main__"
        target.sys.argv = ["scriptname.py", "--propagate", "--config", "data/sample_config.json", "--list", "b2"*12]
        with self.assertRaises(SystemExit) as cm1, self.assertLogs(level='DEBUG') as cm2:
            target.init()
        self.assertEqual(cm1.exception.code, 32)
        self.assertTrue("CRITICAL:root:List b2b2b2b2b2b2b2b2b2b2b2b2 is not on the master board ghi. Exiting..." in cm2.output)

    @patch("syncboom.process_master_card")
    @patch("syncboom.perform_request")
    def test_init_propagate_list(self, t_pr, t_pmc):
        """
        Test the initialization code with --propagate and --list that's on the master board, then a single non-relevant master card
        """
        target.__name__ = "__main__"
        target.sys.argv = ["scriptname.py", "--propagate", "--config", "data/sample_config.json", "--list", "c3"*12]
        t_pr.side_effect = [
            [{"id": "c3"*12}],
            [{"id": "a"*24, "name": "Master card name", "labels": {}, "badges": {"attachments": 0}, "desc": "Desc"}]
        ]
        t_pmc.return_value = (30, 40, 50)
        f = io.StringIO()
        with self.assertLogs(level='DEBUG') as cm:
            target.init()
        self.assertEqual(len(t_pmc.mock_calls), 1)
        self.assertEqual(t_pmc.mock_calls[0], call({'id': 'aaaaaaaaaaaaaaaaaaaaaaaa', 'name': 'Master card name', 'labels': {}, 'badges': {'attachments': 0}, 'desc': 'Desc'}))
        self.assertTrue("INFO:root:Summary: processed 1 master cards (of which 30 active) that have 40 slave cards (of which 50 new)." in cm.output)

    @patch("syncboom.perform_request")
    def test_init_propagate_card_invalid_404(self, t_pr):
        """
        Test the initialization code with --propagate and --card that doesn't exist (404 error)
        """
        # Make the script believe we ran it directly
        target.__name__ = "__main__"
        # Pass it the --cleanup and related arguments
        target.sys.argv = ["scriptname.py", "--propagate", "--config", "data/sample_config.json", "--card", "d4"*12]
        # All network requests return empty
        t_pr.side_effect = HTTPError()
        # Run the init(), will run the full --propagate branch
        with self.assertRaises(SystemExit) as cm1, self.assertLogs(level='DEBUG') as cm2:
            target.init()
        self.assertEqual(cm1.exception.code, 33)
        self.assertTrue("CRITICAL:root:Invalid card ID d4d4d4d4d4d4d4d4d4d4d4d4, card not found. Exiting..." in cm2.output)

    @patch("syncboom.perform_request")
    def test_init_propagate_card_invalid_not_master_board(self, t_pr):
        """
        Test the initialization code with --propagate and --card that's not on the master board
        """
        # Make the script believe we ran it directly
        target.__name__ = "__main__"
        # Pass it the --cleanup and related arguments
        target.sys.argv = ["scriptname.py", "--propagate", "--config", "data/sample_config.json", "--card", "d4"*12]
        # All network requests return empty
        t_pr.return_value = {"idBoard": "notonmasterboard"}
        # Run the init(), will run the full --propagate branch
        with self.assertRaises(SystemExit) as cm1, self.assertLogs(level='DEBUG') as cm2:
            target.init()
        self.assertEqual(cm1.exception.code, 31)
        self.assertTrue("CRITICAL:root:Card d4d4d4d4d4d4d4d4d4d4d4d4 is not located on the master board ghi. Exiting..." in cm2.output)

    @patch("syncboom.process_master_card")
    @patch("syncboom.perform_request")
    def test_init_propagate_card(self, t_pr, t_pmc):
        """
        Test the initialization code with --propagate and valid --card
        """
        target.__name__ = "__main__"
        target.sys.argv = ["scriptname.py", "--propagate", "--config", "data/sample_config.json", "--card", "d4"*12]
        t_pr.side_effect = [{"idBoard": "ghi", "id": "odn", "shortLink": "eoK0Rngb", "name": "Master card name", "labels": {}, "badges": {"attachments": 0}, "desc": "Desc"}]
        t_pmc.return_value = (40, 50, 60)
        f = io.StringIO()
        with self.assertLogs(level='DEBUG') as cm:
            target.init()
        self.assertEqual(len(t_pmc.mock_calls), 1)
        self.assertEqual(t_pmc.mock_calls[0], call({'idBoard': 'ghi', 'id': 'odn', 'shortLink': 'eoK0Rngb', 'name': 'Master card name', 'labels': {}, 'badges': {'attachments': 0}, 'desc': 'Desc'}))
        self.assertTrue("INFO:root:Summary: processed 1 master cards (of which 40 active) that have 50 slave cards (of which 60 new)." in cm.output)

    @patch("syncboom.new_webhook")
    def test_init_webhook_new(self, t_nw):
        """
        Test the initialization code with --webhook new
        """
        target.__name__ = "__main__"
        target.sys.argv = ["scriptname.py", "--debug", "--config", "data/sample_config.json", "--webhook", "new"]
        target.init()
        # Confirm we called new_webhook()
        self.assertEqual(t_nw.mock_calls, [call()])

    @patch("syncboom.list_webhooks")
    def test_init_webhook_list(self, t_lw):
        """
        Test the initialization code with --webhook list
        """
        target.__name__ = "__main__"
        target.sys.argv = ["scriptname.py", "--debug", "--config", "data/sample_config.json", "--webhook", "list"]
        target.init()
        # Confirm we called list_webhooks()
        self.assertEqual(t_lw.mock_calls, [call()])

    @patch("syncboom.delete_webhook")
    def test_init_webhook_delete(self, t_dw):
        """
        Test the initialization code with --webhook delete
        """
        target.__name__ = "__main__"
        target.sys.argv = ["scriptname.py", "--debug", "--config", "data/sample_config.json", "--webhook", "delete"]
        target.init()
        # Confirm we called delete_webhook()
        self.assertEqual(t_dw.mock_calls, [call()])


class TestLicense(FlaskTestCase):
    def test_license_file(self):
        """Validate that the project has a LICENSE file, check part of its content"""
        self.assertTrue(os.path.isfile("LICENSE"))
        with open('LICENSE') as f:
            s = f.read()
            # Confirm it is the MIT License
            self.assertTrue("MIT License" in s)
            self.assertTrue("Copyright (c) 2020 Emilien Klein" in s)
            # Confirm the statement about microblog is present in the LICENSE file
            self.assertTrue("The website is originally based on microblog" in s)
            self.assertTrue("Copyright (c) 2017 Miguel Grinberg" in s)
            self.assertTrue("https://github.com/miguelgrinberg/microblog" in s)

    def test_license_mention(self):
        """Validate that the script file contain a mention of the license"""
        with open('syncboom.py') as f:
            s = f.read()
            # Confirm it is the MIT License
            self.assertTrue("#    This file is part of SyncBoom and is MIT-licensed." in s)


if __name__ == '__main__':
    unittest.main()
