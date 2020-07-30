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
import json
from unittest.mock import patch
import io
import contextlib
from pathlib import Path
import time

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


def run_test_create_new_config(self, vals, expected_output, expected_exception_code, print_output=False):
    global mock_raw_input_counter
    global mock_raw_input_values
    mock_raw_input_counter = 0
    mock_raw_input_values = vals
    f = io.StringIO()
    config_file = ""
    if expected_exception_code:
        with self.assertRaises(SystemExit) as cm, contextlib.redirect_stdout(f):
            config_file = target.create_new_config()
        self.assertEqual(cm.exception.code, expected_exception_code)
    else:
        with contextlib.redirect_stdout(f):
            config_file = target.create_new_config()
    if print_output:
        print(f.getvalue())
    if expected_output:
        self.assertTrue(expected_output in f.getvalue())
    return config_file

class TestCreateNewConfig(unittest.TestCase):
    def remove_test_config_files(self):
        if os.path.isfile("data/config_config-name.json"):
            time.sleep(0.5)
            try:
                os.remove("data/config_config-name.json")
            except (PermissionError, FileNotFoundError):
                pass
        for p in Path("./data").glob("*.nxt"):
            try:
                p.unlink()
            except (PermissionError, FileNotFoundError):
                pass

    def setUp(self):
        self.remove_test_config_files()

    def tearDown(self):
        self.remove_test_config_files()

    def test_create_new_config_q(self):
        """
        Test creating a new config file, invalid key then quit
        """
        vals = ["q"]
        expected_output = """Welcome to the new configuration assistant.
Trello key and token can be created at https://trello.com/app-key
Please:
Enter your Trello key ('q' to quit):\u0020
Exiting...
"""
        expected_exception_code = 35
        run_test_create_new_config(self, vals, expected_output, expected_exception_code)

    def test_create_new_config_ik(self):
        """
        Test creating a new config file, invalid key then quit
        """
        vals = ["abc", "q"]
        expected_output = """Enter your Trello key ('q' to quit):\u0020
Invalid Trello key, must be 32 characters. Enter your Trello key ('q' to quit):\u0020
Exiting...
"""
        expected_exception_code = 35
        run_test_create_new_config(self, vals, expected_output, expected_exception_code)

    def test_create_new_config_k(self):
        """
        Test creating a new config file, valid key then quit
        """
        vals = ["a"*32, "q"]
        expected_output = """Enter your Trello key ('q' to quit):\u0020
Enter your Trello token ('q' to quit):\u0020
Exiting...
"""
        expected_exception_code = 36
        run_test_create_new_config(self, vals, expected_output, expected_exception_code)

    def test_create_new_config_it(self):
        """
        Test creating a new config file, invalid token then quit
        """
        vals = ["a"*32, "abc", "q"]
        expected_output = """Enter your Trello key ('q' to quit):\u0020
Enter your Trello token ('q' to quit):\u0020
Invalid Trello token, must be 64 characters. Enter your Trello token ('q' to quit):\u0020
Exiting...
"""
        expected_exception_code = 36
        run_test_create_new_config(self, vals, expected_output, expected_exception_code)

    @patch("syncboom.perform_request")
    def test_create_new_config_t(self, t_pr):
        """
        Test creating a new config file, valid token then quit
        """
        t_pr.return_value = [{"name": "Board One", "id": "m"*24}, {"name": "Board Two", "id": "c"*24}]
        vals = ["a"*32, "b"*64, "q"]
        expected_output = """These are your boards and their associated IDs:
           ID             |  Name
mmmmmmmmmmmmmmmmmmmmmmmm  |  Board One
cccccccccccccccccccccccc  |  Board Two
Enter your main board ID ('q' to quit):\u0020
Exiting...
"""
        expected_exception_code = 37
        run_test_create_new_config(self, vals, expected_output, expected_exception_code)

    @patch("syncboom.perform_request")
    def test_create_new_config_ibf(self, t_pr):
        """
        Test creating a new config file, invalid board ID format then quit
        """
        t_pr.return_value = [{"name": "Board One", "id": "m"*24}, {"name": "Board Two", "id": "c"*24}]
        vals = ["a"*32, "b"*64, "abc", "q"]
        expected_output = """These are your boards and their associated IDs:
           ID             |  Name
mmmmmmmmmmmmmmmmmmmmmmmm  |  Board One
cccccccccccccccccccccccc  |  Board Two
Enter your main board ID ('q' to quit):\u0020
Invalid board ID, must be 24 characters. Enter your main board ID ('q' to quit):\u0020
Exiting...
"""
        expected_exception_code = 37
        run_test_create_new_config(self, vals, expected_output, expected_exception_code)

    @patch("syncboom.perform_request")
    def test_create_new_config_vb_not_own(self, t_pr):
        """
        Test creating a new config file, valid board ID format but not in list of own boards, then quit
        """
        t_pr.return_value = [{"name": "Board One", "id": "m"*24}, {"name": "Board Two", "id": "c"*24}]
        vals = ["a"*32, "b"*64, "d"*24, "q"]
        expected_output = """These are your boards and their associated IDs:
           ID             |  Name
mmmmmmmmmmmmmmmmmmmmmmmm  |  Board One
cccccccccccccccccccccccc  |  Board Two
Enter your main board ID ('q' to quit):\u0020
This is not the ID of one of the boards you have access to. Enter your main board ID ('q' to quit):\u0020
Exiting...
"""
        expected_exception_code = 37
        run_test_create_new_config(self, vals, expected_output, expected_exception_code)

    @patch("syncboom.perform_request")
    def test_create_new_config_b(self, t_pr):
        """
        Test creating a new config file, valid board then quit
        """
        t_pr.return_value = [{"name": "Board One", "id": "m"*24}, {"name": "Board Two", "id": "c"*24}]
        vals = ["a"*32, "b"*64, "c"*24, "q"]
        expected_output = """These are your boards and their associated IDs:
           ID             |  Name
mmmmmmmmmmmmmmmmmmmmmmmm  |  Board One
cccccccccccccccccccccccc  |  Board Two
Enter your main board ID ('q' to quit):\u0020
Enter a name for this new configuration ('q' to quit):\u0020
Exiting...
"""
        expected_exception_code = 38
        run_test_create_new_config(self, vals, expected_output, expected_exception_code)

    @patch("syncboom.perform_request")
    def test_create_new_config_il(self, t_pr):
        """
        Test creating a new config file, invalid label then quit
        """
        t_pr.side_effect = [
        [{"name": "Board One", "id": "m"*24}, {"name": "Board Two", "id": "c"*24}],
        [{"name": "List One", "id": "d"*24}, {"name": "List Two", "id": "e"*24}],
        [{"name": "Label One", "id": "g"*24, "color": "color1"}, {"name": "", "id": "h"*24, "color": "color2"}, {"name": "Label Three", "id": "i"*24, "color": "color3"}]
        ]
        vals = ["a"*32, "b"*64, "c"*24, "Config name", "Invalid label", "q"]
        expected_output = """These are the labels from the selected board and their associated IDs:
           ID             |  Label
gggggggggggggggggggggggg  |  'Label One' (color1)
iiiiiiiiiiiiiiiiiiiiiiii  |  'Label Three' (color3)
Enter a label name ('q' to quit):\u0020
This is not a valid label name for the selected board. Enter a label name ('q' to quit):\u0020
Exiting...
"""
        expected_exception_code = 39
        run_test_create_new_config(self, vals, expected_output, expected_exception_code)

    @patch("syncboom.perform_request")
    def test_create_new_config_l(self, t_pr):
        """
        Test creating a new config file, valid label then quit
        """
        t_pr.side_effect = [
            [{"name": "Board One", "id": "m"*24}, {"name": "Board Two", "id": "c"*24}, {"name": "Board Three", "id": "u"*24}],
            [{"name": "List One", "id": "d"*24}, {"name": "List Two", "id": "e"*24}],
            [{"name": "List Three", "id": "f"*24}, {"name": "List Four", "id": "q"*24}],
            [{"name": "Label One", "id": "g"*24, "color": "color1"}, {"name": "", "id": "h"*24, "color": "color2"}, {"name": "Label Three", "id": "i"*24, "color": "color3"}]
        ]
        vals = ["a"*32, "b"*64, "c"*24, "Config name", "Label Three", "q"]
        expected_output = """These are the labels from the selected board and their associated IDs:
           ID             |  Label
gggggggggggggggggggggggg  |  'Label One' (color1)
iiiiiiiiiiiiiiiiiiiiiiii  |  'Label Three' (color3)
Enter a label name ('q' to quit):\u0020
These are the lists associated to the other boards:


Lists from board 'Board One':
           ID             |  Name
dddddddddddddddddddddddd  |  'List One' (from board 'Board One')
eeeeeeeeeeeeeeeeeeeeeeee  |  'List Two' (from board 'Board One')

Lists from board 'Board Three':
           ID             |  Name
ffffffffffffffffffffffff  |  'List Three' (from board 'Board Three')
qqqqqqqqqqqqqqqqqqqqqqqq  |  'List Four' (from board 'Board Three')
Enter the list ID you want to associate with label 'Label Three' ('q' to quit):\u0020
Exiting...
"""
        expected_exception_code = 40
        run_test_create_new_config(self, vals, expected_output, expected_exception_code)

    @patch("syncboom.perform_request")
    def test_create_new_config_vl_il(self, t_pr):
        """
        Test creating a new config file, valid label, invalid list ID then quit
        """
        t_pr.side_effect = [
            [{"name": "Board One", "id": "m"*24}, {"name": "Board Two", "id": "c"*24}],
            [{"name": "List One", "id": "d"*24}, {"name": "List Two", "id": "e"*24}],
            [{"name": "Label One", "id": "g"*24, "color": "color1"}, {"name": "", "id": "h"*24, "color": "color2"}, {"name": "Label Three", "id": "i"*24, "color": "color3"}]
        ]
        vals = ["a"*32, "b"*64, "c"*24, "Config name", "Label Three", "abc", "q"]
        expected_output = """Enter the list ID you want to associate with label 'Label Three' ('q' to quit):\u0020
Invalid list ID, must be 24 characters. Enter the list ID you want to associate with label 'Label Three' ('q' to quit):\u0020
Exiting...
"""
        expected_exception_code = 40
        run_test_create_new_config(self, vals, expected_output, expected_exception_code)

    @patch("syncboom.perform_request")
    def test_create_new_config_vl_vl_q(self, t_pr):
        """
        Test creating a new config file, valid label and list ID then quit
        """
        t_pr.side_effect = [
            [{"name": "Board One", "id": "m"*24}, {"name": "Board Two", "id": "c"*24}],
            [{"name": "List One", "id": "d"*24}, {"name": "List Two", "id": "e"*24}],
            [{"name": "Label One", "id": "g"*24, "color": "color1"}, {"name": "", "id": "h"*24, "color": "color2"}, {"name": "Label Three", "id": "i"*24, "color": "color3"}]
        ]
        vals = ["a"*32, "b"*64, "c"*24, "Config name", "Label Three", "d"*24, "q"]
        expected_output = """Enter a label name ('q' to quit):\u0020
These are the lists associated to the other boards:


Lists from board 'Board One':
           ID             |  Name
dddddddddddddddddddddddd  |  'List One' (from board 'Board One')
eeeeeeeeeeeeeeeeeeeeeeee  |  'List Two' (from board 'Board One')
Enter the list ID you want to associate with label 'Label Three' ('q' to quit):\u0020
Do you want to associate another list to this label? ('Yes', 'No' or 'q' to quit):\u0020
Exiting...
"""
        expected_exception_code = 42
        run_test_create_new_config(self, vals, expected_output, expected_exception_code)

    @patch("syncboom.perform_request")
    def test_create_new_config_vl_vl_no(self, t_pr):
        """
        Test creating a new config file, valid label and list ID then no and quit
        """
        t_pr.side_effect = [
            [{"name": "Board One", "id": "m"*24}, {"name": "Board Two", "id": "c"*24}],
            [{"name": "List One", "id": "d"*24}, {"name": "List Two", "id": "e"*24}],
            [{"name": "Label One", "id": "g"*24, "color": "color1"}, {"name": "", "id": "h"*24, "color": "color2"}, {"name": "Label Three", "id": "i"*24, "color": "color3"}]
        ]
        vals = ["a"*32, "b"*64, "c"*24, "Config name", "Label Three", "d"*24, "No", "q"]
        expected_output = """Enter a label name ('q' to quit):\u0020
These are the lists associated to the other boards:


Lists from board 'Board One':
           ID             |  Name
dddddddddddddddddddddddd  |  'List One' (from board 'Board One')
eeeeeeeeeeeeeeeeeeeeeeee  |  'List Two' (from board 'Board One')
Enter the list ID you want to associate with label 'Label Three' ('q' to quit):\u0020
Do you want to associate another list to this label? ('Yes', 'No' or 'q' to quit):\u0020
Do you want to add a new label? (Enter 'yes' or 'no', 'q' to quit):\u0020
Exiting...
"""
        expected_exception_code = 41
        run_test_create_new_config(self, vals, expected_output, expected_exception_code)

    @patch("syncboom.perform_request")
    def test_create_new_config_one_label_list_error_q(self, t_pr):
        """
        Test creating a new config file, one valid label/list ID then error and quit
        """
        t_pr.side_effect = [
            [{"name": "Board One", "id": "m"*24}, {"name": "Board Two", "id": "c"*24}],
            [{"name": "List One", "id": "d"*24}, {"name": "List Two", "id": "e"*24}],
            [{"name": "Label One", "id": "g"*24, "color": "color1"}, {"name": "", "id": "h"*24, "color": "color2"}, {"name": "Label Three", "id": "i"*24, "color": "color3"}]
        ]
        vals = ["a"*32, "b"*64, "c"*24, "Config name", "Label Three", "d"*24, "abc", "q"]
        expected_output = """Enter a label name ('q' to quit):\u0020
These are the lists associated to the other boards:


Lists from board 'Board One':
           ID             |  Name
dddddddddddddddddddddddd  |  'List One' (from board 'Board One')
eeeeeeeeeeeeeeeeeeeeeeee  |  'List Two' (from board 'Board One')
Enter the list ID you want to associate with label 'Label Three' ('q' to quit):\u0020
Do you want to associate another list to this label? ('Yes', 'No' or 'q' to quit):\u0020
Invalid entry. Do you want to associate another list to this label? ('Yes', 'No' or 'q' to quit):\u0020
Exiting...
"""
        expected_exception_code = 42
        run_test_create_new_config(self, vals, expected_output, expected_exception_code)

    @patch("syncboom.perform_request")
    def test_create_new_config_one_label_list_error_no(self, t_pr):
        """
        Test creating a new config file, one valid label/list ID then error and continue
        """
        t_pr.side_effect = [
            [{"name": "Board One", "id": "m"*24}, {"name": "Board Two", "id": "c"*24}],
            [{"name": "List One", "id": "d"*24}, {"name": "List Two", "id": "e"*24}],
            [{"name": "Label One", "id": "g"*24, "color": "color1"}, {"name": "", "id": "h"*24, "color": "color2"}, {"name": "Label Three", "id": "i"*24, "color": "color3"}]
        ]
        vals = ["a"*32, "b"*64, "c"*24, "Config name", "Label Three", "d"*24, "abc", "no", "no"]
        expected_output = """Enter the list ID you want to associate with label 'Label Three' ('q' to quit):\u0020
Do you want to associate another list to this label? ('Yes', 'No' or 'q' to quit):\u0020
Invalid entry. Do you want to associate another list to this label? ('Yes', 'No' or 'q' to quit):\u0020
Do you want to add a new label? (Enter 'yes' or 'no', 'q' to quit):\u0020
New configuration saved to file 'data/config_config-name.json'
"""
        expected_exception_code = None
        config_file = run_test_create_new_config(self, vals, expected_output, expected_exception_code)
        self.assertTrue(os.path.isfile(config_file))
        # Delete the temporary file
        os.remove(config_file)

    @patch("syncboom.perform_request")
    def test_create_new_config_one_label_list(self, t_pr):
        """
        Test creating a new config file, only one valid label/list ID
        """
        t_pr.side_effect = [
            [{"name": "Board One", "id": "m"*24}, {"name": "Board Two", "id": "c"*24}],
            [{"name": "List One", "id": "d"*24}, {"name": "List Two", "id": "e"*24}],
            [{"name": "Label One", "id": "g"*24, "color": "color1"}, {"name": "", "id": "h"*24, "color": "color2"}, {"name": "Label Three", "id": "i"*24, "color": "color3"}]
        ]
        vals = ["a"*32, "b"*64, "c"*24, "Config name", "Label Three", "d"*24, "no", "no"]
        expected_output = """Enter a label name ('q' to quit):\u0020
These are the lists associated to the other boards:


Lists from board 'Board One':
           ID             |  Name
dddddddddddddddddddddddd  |  'List One' (from board 'Board One')
eeeeeeeeeeeeeeeeeeeeeeee  |  'List Two' (from board 'Board One')
Enter the list ID you want to associate with label 'Label Three' ('q' to quit):\u0020
Do you want to associate another list to this label? ('Yes', 'No' or 'q' to quit):\u0020
Do you want to add a new label? (Enter 'yes' or 'no', 'q' to quit):\u0020
New configuration saved to file 'data/config_config-name.json'
"""
        expected_exception_code = None
        config_file = run_test_create_new_config(self, vals, expected_output, expected_exception_code)
        self.assertTrue(os.path.isfile(config_file))
        # Delete the temporary file
        os.remove(config_file)

    @patch("syncboom.perform_request")
    def test_create_new_config_one_label_list_error_new_label(self, t_pr):
        """
        Test creating a new config file, only one valid label/list ID, error on question about new label
        """
        t_pr.side_effect = [
            [{"name": "Board One", "id": "m"*24}, {"name": "Board Two", "id": "c"*24}],
            [{"name": "List One", "id": "d"*24}, {"name": "List Two", "id": "e"*24}],
            [{"name": "Label One", "id": "g"*24, "color": "color1"}, {"name": "", "id": "h"*24, "color": "color2"}, {"name": "Label Three", "id": "i"*24, "color": "color3"}]
        ]
        vals = ["a"*32, "b"*64, "c"*24, "Config name", "Label Three", "d"*24, "no", "abc", "no"]
        expected_output = """Enter a label name ('q' to quit):\u0020
These are the lists associated to the other boards:


Lists from board 'Board One':
           ID             |  Name
dddddddddddddddddddddddd  |  'List One' (from board 'Board One')
eeeeeeeeeeeeeeeeeeeeeeee  |  'List Two' (from board 'Board One')
Enter the list ID you want to associate with label 'Label Three' ('q' to quit):\u0020
Do you want to associate another list to this label? ('Yes', 'No' or 'q' to quit):\u0020
Do you want to add a new label? (Enter 'yes' or 'no', 'q' to quit):\u0020
Invalid entry. Do you want to add a new label? (Enter 'yes' or 'no', 'q' to quit):\u0020
New configuration saved to file 'data/config_config-name.json'
"""
        expected_exception_code = None
        config_file = run_test_create_new_config(self, vals, expected_output, expected_exception_code)
        self.assertTrue(os.path.isfile(config_file))
        # Delete the temporary file
        os.remove(config_file)

    @patch("syncboom.perform_request")
    def test_create_new_config_existing_filename(self, t_pr):
        """
        Test creating a new config file when there was already a config file with the same filename
        """
        t_pr.side_effect = [
            [{"name": "Board One", "id": "m"*24}, {"name": "Board Two", "id": "c"*24}],
            [{"name": "List One", "id": "d"*24}, {"name": "List Two", "id": "e"*24}],
            [{"name": "Label One", "id": "g"*24, "color": "color1"}, {"name": "", "id": "h"*24, "color": "color2"}, {"name": "Label Three", "id": "i"*24, "color": "color3"}]
        ]
        vals = ["a"*32, "b"*64, "c"*24, "Config name", "Label Three", "d"*24, "no", "no"]
        existing_config_file = "data/config_config-name.json"
        Path(existing_config_file).touch()
        expected_output = "New configuration saved to file 'data/config_config-name.json.nxt'"
        expected_exception_code = None
        config_file = run_test_create_new_config(self, vals, expected_output, expected_exception_code)
        self.assertTrue(os.path.isfile(config_file))
        # Delete the temporary files
        os.remove(config_file)
        os.remove(existing_config_file)

    @patch("syncboom.perform_request")
    def test_create_new_config_two_label_list(self, t_pr):
        """
        Test creating a new config file, two valid labels/list IDs
        """
        t_pr.side_effect = [
            [{"name": "Board One", "id": "m"*24}, {"name": "Board Two", "id": "c"*24}],
            [{"name": "List One", "id": "d"*24}, {"name": "List Two", "id": "e"*24}],
            [{"name": "Label One", "id": "g"*24, "color": "color1"}, {"name": "", "id": "h"*24, "color": "color2"}, {"name": "Label Three", "id": "i"*24, "color": "color3"}]
        ]
        vals = ["a"*32, "b"*64, "c"*24, "Config name", "Label One", "d"*24, "no", "yes", "Label Three", "e"*24, "no", "no"]
        config_file = run_test_create_new_config(self, vals, None, None)
        self.assertTrue(os.path.isfile(config_file))
        with open(config_file, "r") as json_file:
            target.config = json.load(json_file)
        # Delete the temporary file
        os.remove(config_file)
        # Validate the values loaded from the new config file
        self.assertEqual(target.config["name"], vals[3])
        self.assertEqual(target.config["key"], vals[0])
        self.assertEqual(target.config["token"], vals[1])
        self.assertEqual(target.config["main_board"], vals[2])
        self.assertEqual(len(target.config["destination_lists"]), 2)
        self.assertEqual(len(target.config["destination_lists"]["Label One"]), 1)
        self.assertEqual(target.config["destination_lists"]["Label One"][0], vals[5])
        target.config = None

    @patch("syncboom.perform_request")
    def test_create_new_config_two_label_one_multiple_list(self, t_pr):
        """
        Test creating a new config file, two valid labels including one with multiple list IDs
        """
        t_pr.side_effect = [
            [{"name": "Board One", "id": "m"*24}, {"name": "Board Two", "id": "c"*24}],
            [{"name": "List One", "id": "d"*24}, {"name": "List Two", "id": "e"*24}],
            [{"name": "Label One", "id": "g"*24, "color": "color1"}, {"name": "", "id": "h"*24, "color": "color2"}, {"name": "Label Three", "id": "i"*24, "color": "color3"}]
        ]
        vals = ["a"*32, "b"*64, "c"*24, "Config name", "Label One", "d"*24, "yes", "e"*24, "no", "yes", "Label Three", "e"*24, "no", "no"]
        config_file = run_test_create_new_config(self, vals, None, None)
        self.assertTrue(os.path.isfile(config_file))
        with open(config_file, "r") as json_file:
            target.config = json.load(json_file)
        # Delete the temporary file
        os.remove(config_file)
        # Validate the values loaded from the new config file
        self.assertEqual(target.config["name"], vals[3])
        self.assertEqual(target.config["key"], vals[0])
        self.assertEqual(target.config["token"], vals[1])
        self.assertEqual(target.config["main_board"], vals[2])
        self.assertEqual(len(target.config["destination_lists"]), 2)
        self.assertEqual(len(target.config["destination_lists"]["Label One"]), 2)
        self.assertEqual(len(target.config["destination_lists"]["Label Three"]), 1)
        self.assertEqual(target.config["destination_lists"]["Label One"][0], vals[5])
        target.config = None


class TestLoadConfig(unittest.TestCase):
    def test_load_config(self):
        """
        Test loading a valid config file
        """
        target.config = target.load_config("data/sample_config.json")
        self.assertEqual(target.config["name"], "Sample configuration")
        self.assertEqual(target.config["key"], "abc")
        self.assertEqual(target.config["token"], "def")
        self.assertEqual(target.config["main_board"], "ghi")
        self.assertEqual(len(target.config["destination_lists"]), 3)
        self.assertEqual(len(target.config["destination_lists"]["Label One"]), 1)
        self.assertEqual(target.config["destination_lists"]["Label One"][0], "a1a1a1a1a1a1a1a1a1a1a1a1")
        self.assertEqual(len(target.config["destination_lists"]["All Teams"]), 2)
        self.assertEqual(target.config["destination_lists"]["All Teams"][1], "ddd")
        target.config = None

    def test_load_config_nonexisting(self):
        """
        Test loading a nonexisting config file
        """
        with self.assertRaises(FileNotFoundError) as cm:
            target.config = target.load_config("data/nonexisting_config.json")
        self.assertEqual(str(cm.exception), "[Errno 2] No such file or directory: 'data/nonexisting_config.json'")
        target.config = None

    def test_load_config_invalid(self):
        """
        Test loading an invalid config file (not json)
        """
        with self.assertRaises(json.decoder.JSONDecodeError) as cm:
            target.config = target.load_config("requirements.txt")
        self.assertEqual(str(cm.exception), "Expecting value: line 1 column 1 (char 0)")
        target.config = None


if __name__ == '__main__':
    unittest.main()
