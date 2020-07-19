#!/usr/bin/env python
# -*- coding: utf-8 -*-

#    This file is part of SyncBoom and is MIT-licensed.

import argparse
import logging
import json
import requests
import os
import sys
import re
from slugify import slugify
from app import create_app, cache

try:
    import readline
except ImportError:
    # Windows
    pass

METADATA_PHRASE = "DO NOT EDIT BELOW THIS LINE"
METADATA_SEPARATOR = "\n\n%s\n*== %s ==*\n" % ("-" * 32, METADATA_PHRASE)

class TrelloConnectionError(Exception):
    pass
class TrelloAuthenticationError(Exception):
    pass


def rlinput(prompt, prefill=''):
    """Provide an editable input string
    Inspired from https://stackoverflow.com/a/36607077
    """
    if "readline" not in sys.modules:
        # For example on Windows
        return input(prompt)
    else:
        readline.set_startup_hook(lambda: readline.insert_text(prefill))
        try:
            return input(prompt)
        finally:
            readline.set_startup_hook()

def output_summary(args, summary):
    if not summary:
        return ""
    if not args:
        # Called from the website
        return "Processed %d main cards (of which %d active) that have %d subordinate cards (of which %d new)." % (
            summary["main_cards"],
            summary["active_main_cards"],
            summary["subordinate_card"],
            summary["new_subordinate_card"])
    else:
        # Called from the script
        logging.info("="*64)
        if args.cleanup:
            logging.info("Summary%scleaned up %d main cards and deleted %d subordinate cards from %d subordinate boards/%d subordinate lists." % (
                " [DRY RUN]: would have " if args.dry_run else ": ",
                summary["cleaned_up_main_cards"],
                summary["deleted_subordinate_cards"],
                summary["erased_destination_boards"],
                summary["erased_destination_lists"]))
        elif args.propagate:
            logging.info("Summary%s: processed %d main cards (of which %d active) that have %d subordinate cards (of which %d %snew)." % (
                " [DRY RUN]" if args.dry_run else "",
                summary["main_cards"],
                summary["active_main_cards"],
                summary["subordinate_card"],
                summary["new_subordinate_card"],
                "would have been " if args.dry_run else ""))

def get_card_attachments(card, pr_args={}):
    card_attachments = []
    if card["badges"]["attachments"] > 0:
        logging.debug("Getting %d attachments on main card %s" % (card["badges"]["attachments"], card["id"]))
        for a in perform_request("GET", "cards/%s/attachments" % card["id"], **pr_args):
            # Only keep attachments that are links to other Trello cards
            card_shorturl_regex = "https://trello.com/c/([a-zA-Z0-9_-]{8})/.*"
            card_shorturl_regex_match = re.match(card_shorturl_regex, a["url"])
            if card_shorturl_regex_match:
                a["card_shortUrl"] = card_shorturl_regex_match.group(1)
                card_attachments.append(a)
    return card_attachments

def cleanup_test_boards(main_cards):
    # Check if this config has been enabled for cleaning up
    if "cleanup_boards" not in config:
        logging.critical("This configuration has not been enabled to accept the --cleanup operation. See the `cleanup_boards` section in the config file. Exiting...")
        sys.exit(43)

    logging.debug("Removing subordinate cards attachments on the main cards")
    cleaned_up_main_cards = 0
    for idx, main_card in enumerate(main_cards):
        logging.debug("="*64)
        logging.info("Cleaning up main card %d/%d - %s" %(idx+1, len(main_cards), main_card["name"]))
        main_card_attachments = get_card_attachments(main_card)
        if len(main_card_attachments) > 0:
            cleaned_up_main_cards += 1
            for a in main_card_attachments:
                logging.debug("Deleting attachment %s from main card %s" %(a["id"], main_card["id"]))
                perform_request("DELETE", "cards/%s/attachments/%s" % (main_card["id"], a["id"]))

        # Removing teams checklist from the main card
        logging.debug("Retrieving checklists from card %s" % main_card["id"])
        for c in perform_request("GET", "cards/%s/checklists" % main_card["id"]):
            if "Involved Teams" == c["name"]:
                logging.debug("Deleting checklist %s (%s) from main card %s" %(c["name"], c["id"], main_card["id"]))
                perform_request("DELETE", "checklists/%s" % (c["id"]))

        # Removing metadata from the main cards
        update_main_card_metadata(main_card, "")

    logging.debug("Deleting subordinate cards")
    erased_destination_boards = []
    num_lists_to_cleanup = 0
    num_lists_inspected = 0
    num_erased_destination_lists = 0
    deleted_subordinate_cards = 0
    destination_lists = []
    for dl in config["destination_lists"]:
        for idx, l in enumerate((config["destination_lists"][dl])):
            if l not in destination_lists:
                # Get the board which contains this destination list
                board_id = perform_request("GET", "lists/%s/board" % config["destination_lists"][dl][idx])["id"]
                # Validate that this board has been whitelisted for cleanup, to
                # prevent real data from being wiped out inadvertently
                if board_id not in config["cleanup_boards"]:
                    logging.critical("This board %s is not whitelisted to be cleaned up. See the `cleanup_boards` section in the config file. Exiting..." % board_id)
                    sys.exit(44)
                # Get all the lists on that board which contains this destination list
                lists = perform_request("GET", "boards/%s/lists" % board_id)
                for ll in lists:
                    if ll["id"] not in destination_lists:
                        destination_lists.append(ll["id"])
                        num_lists_to_cleanup += 1
    for l in destination_lists:
        logging.debug("="*64)
        num_lists_inspected += 1
        board_name = get_board_name_from_list(l)
        list_name = get_name("list", l)
        logging.debug("Retrieve cards from list %s|%s (list %d/%d)" % (board_name, list_name, num_lists_inspected, num_lists_to_cleanup))
        subordinate_cards = perform_request("GET", "lists/%s/cards" % l)
        logging.debug(subordinate_cards)
        logging.debug("List %s/%s has %d cards to delete" % (board_name, list_name, len(subordinate_cards)))
        if len(subordinate_cards) > 0:
            num_erased_destination_lists += 1
            if not board_name in erased_destination_boards:
                erased_destination_boards.append(board_name)
        for sc in subordinate_cards:
            logging.debug("Deleting subordinate card %s" % sc["id"])
            deleted_subordinate_cards += 1
            perform_request("DELETE", "cards/%s" % sc["id"])
    return {"cleaned_up_main_cards": cleaned_up_main_cards,
            "deleted_subordinate_cards": deleted_subordinate_cards,
            "erased_destination_boards": len(erased_destination_boards),
            "erased_destination_lists": num_erased_destination_lists}

def split_main_card_metadata(main_card_desc):
    if METADATA_SEPARATOR not in main_card_desc:
        if METADATA_PHRASE not in main_card_desc:
            return [main_card_desc, ""]
        else:
            # Somebody has messed with the line, but the main text is still visible
            # Cut off at that text
            return [main_card_desc[:main_card_desc.find(METADATA_PHRASE)], ""]

    else:
        # Split the main description from the metadata added after the separator
        regex_pattern = "^(.*)(%s)(.*)" % re.escape(METADATA_SEPARATOR)
        match = re.search(regex_pattern, main_card_desc, re.DOTALL)
        return [match.group(1), match.group(3)]

def update_main_card_metadata(main_card, new_main_card_metadata, pr_args={}):
    (main_desc, current_main_card_metadata) = split_main_card_metadata(main_card["desc"])
    if new_main_card_metadata != current_main_card_metadata:
        logging.debug("Updating main card metadata")
        if new_main_card_metadata:
            new_full_desc = "%s%s%s" % (main_desc, METADATA_SEPARATOR, new_main_card_metadata)
        else:
            # Also remove the metadata separator when removing the metadata
            new_full_desc = main_desc
        logging.debug(new_full_desc)
        perform_request("PUT", "cards/%s" % main_card["id"], {"desc": new_full_desc}, **pr_args)

@cache.memoize(60)
def get_name(record_type, record_id, pr_args={}):
    return perform_request("GET", "%s/%s" % (record_type, record_id), **pr_args)["name"]

@cache.memoize(60)
def get_board_name_from_list(list_id, pr_args={}):
    return get_name("board", perform_request("GET", "lists/%s" % list_id, **pr_args)["idBoard"], pr_args)

def generate_main_card_metadata(subordinate_cards, pr_args={}):
    mcm = ""
    for sc in subordinate_cards:
        mcm += "\n- '%s' on list '**%s|%s**'" % (sc["name"],
            get_name("board", sc["idBoard"], pr_args),
            get_name("list", sc["idList"], pr_args))
    logging.debug("New main card metadata: %s" % mcm)
    return mcm

def is_not_get_call(*args, **kwargs):
    return not (args[1] == "GET")

@cache.memoize(60, unless=is_not_get_call)
def perform_request(method, url, query=None, key=None, token=None):
    if method not in ("GET", "POST", "PUT", "DELETE"):
        logging.critical("HTTP method '%s' not supported. Exiting..." % method)
        sys.exit(30)
    url = "https://api.trello.com/1/%s" % url
    if "args" in globals() and args.dry_run and method != "GET":
        logging.debug("Skipping %s call to '%s' due to --dry-run parameter" % (method, url))
        return {}
    if not (key and token) and "config" in globals() and "app" in globals():
        key = app.config['TRELLO_API_KEY']
        token = config["token"]
    url += "?key=%s&token=%s" % (key, token)
    try:
        response = requests.request(
            method,
            url,
            params=query
        )
    except requests.exceptions.ConnectionError:
        raise TrelloConnectionError
    # Raise an exception if the response status code indicates an issue
    try:
        response.raise_for_status()
    except requests.exceptions.HTTPError as http_error:
        if http_error.response.status_code == 401:
            raise TrelloAuthenticationError
        else:
            logging.critical("Request failed with code %s and message '%s'" %
                (http_error.response.status_code, response.content))
            raise http_error
    return response.json()

def create_new_subordinate_card(main_card, destination_list, pr_args={}):
    logging.debug("Creating new subordinate card")
    query = {
       "idList": destination_list,
       "desc": "%s\n\nCreated from main card %s" % (main_card["desc"], main_card["shortUrl"]),
       "pos": "bottom",
       "idCardSource": main_card["id"],
        # Explicitly don't keep labels,members
        "keepFromSource": "attachments,checklists,comments,due,stickers"
    }
    new_subordinate_card = perform_request("POST", "cards", query, **pr_args)
    if new_subordinate_card:
        logging.debug("New subordinate card ID: %s" % new_subordinate_card["id"])
    return new_subordinate_card

def process_main_card(main_card, args_from_app=None):
    logging.debug("="*64)
    logging.debug("Process main card '%s'" % main_card["name"])
    # Check if this card is to be synced on a destination list
    destination_lists = []
    if not args_from_app:
        conf_destination_lists = config["destination_lists"]
        pr_args = {}
    else:
        conf_destination_lists = args_from_app["destination_lists"]
        pr_args = {"key": args_from_app["key"], "token": args_from_app["token"]}
    for l in main_card["labels"]:
        if not args_from_app:
            # TODO: Change script config setup from label Name to label ID (#37)
            tracked_label_value = l["name"]
        else:
            tracked_label_value = l["id"]
        if tracked_label_value in conf_destination_lists:
            for list in conf_destination_lists[tracked_label_value]:
                if list not in destination_lists:
                    destination_lists.append(list)
    logging.debug("Main card is to be synced on %d destination lists" % len(destination_lists))

    # Check if subordinate cards are already attached to this main card
    linked_subordinate_cards = []
    main_card_attachments = get_card_attachments(main_card, pr_args)
    for mca in main_card_attachments:
        attached_card = perform_request("GET", "cards/%s" % mca["card_shortUrl"], **pr_args)
        linked_subordinate_cards.append(attached_card)

    new_main_card_metadata = ""
    # Check if subordinate cards need to be unlinked
    if len(destination_lists) == 0 and len(linked_subordinate_cards) > 0:
        logging.debug("Main card has been unlinked from subordinate cards")
        # Information on the main card needs to be updated to remove the reference to the subordinate cards
        new_main_card_metadata = ""
        #TODO: Determine what to do with the subordinate cards

    num_new_cards = 0
    subordinate_cards = []
    newly_created_subordinate_cards = []
    if len(destination_lists) > 0:
        for dl in destination_lists:
            existing_subordinate_card = None
            for lsc in linked_subordinate_cards:
                if dl == lsc["idList"]:
                    existing_subordinate_card = lsc
            if existing_subordinate_card:
                logging.debug("Subordinate card %s already exists on list %s" % (existing_subordinate_card["id"], dl))
                logging.debug(existing_subordinate_card)
                card = existing_subordinate_card
            else:
                # A new subordinate card needs to be created for this subordinate board
                num_new_cards += 1
                card = create_new_subordinate_card(main_card, dl, pr_args)
                if card:
                    newly_created_subordinate_cards.append(card)
            subordinate_cards.append(card)
        if card:
            # Generate main card metadata based on the subordinate cards info
            new_main_card_metadata = generate_main_card_metadata(subordinate_cards, pr_args)
        logging.info("This main card has %d subordinate cards (%d newly created)" % (len(subordinate_cards), num_new_cards))
    else:
        logging.info("This main card has no subordinate cards")

    # Update the main card's metadata if needed
    update_main_card_metadata(main_card, new_main_card_metadata, pr_args)

    # Add a checklist for each team on the main card
    if len(destination_lists) > 0 and not ("args" in globals() and args.dry_run):
        logging.debug("Retrieving checklists from card %s" % main_card["id"])
        main_card_checklists = perform_request("GET", "cards/%s/checklists" % main_card["id"], **pr_args)
        create_checklist = True
        if main_card_checklists:
            logging.debug("Already %d checklists on this main card: %s" % (len(main_card_checklists), ", ".join([c["name"] for c in main_card_checklists])))
            for c in main_card_checklists:
                if "Involved Teams" == c["name"]:
                    #TODO: check if each team is on the checklist and update accordingly
                    create_checklist = False
                    logging.debug("Main card already contains a checklist name 'Involved Teams', skipping checklist creation")
        if create_checklist:
            logging.debug("Creating new checklist")
            cl = perform_request("POST", "cards/%s/checklists" % main_card["id"], {"name": "Involved Teams"}, **pr_args)
            logging.debug(cl)
            for dl in destination_lists:
                # Use that list's board's name as checklist name
                checklistitem_name = get_board_name_from_list(dl, pr_args)
                # Ability to define a more friendly name than the destination board's name
                #TODO: Support friendly names from the website (#38)
                if "config" in globals() and checklistitem_name in config["friendly_names"].keys():
                    checklistitem_name = config["friendly_names"][checklistitem_name]
                logging.debug("Adding new checklistitem '%s' to checklist %s" % (checklistitem_name, cl["id"]))
                new_checklistitem = perform_request("POST", "checklists/%s/checkItems" % cl["id"], {"name": checklistitem_name}, **pr_args)
                logging.debug(new_checklistitem)

        #TODO: Mark checklist item as Complete if subordinate card is Done

    # Link main and newly created child cards together
    for card in newly_created_subordinate_cards:
        logging.debug("Attaching main card %s to subordinate card %s" % (main_card["id"], card["id"]))
        perform_request("POST", "cards/%s/attachments" % card["id"], {"url": main_card["url"]}, **pr_args)
        logging.debug("Attaching subordinate card %s to main card %s" % (card["id"], main_card["id"]))
        perform_request("POST", "cards/%s/attachments" % main_card["id"], {"url": card["url"]}, **pr_args)

    return (1 if len(destination_lists) > 0 else 0, len(subordinate_cards), num_new_cards)

def create_new_config():
    global config
    config = {"name": ""}
    print("Welcome to the new configuration assistant.")
    print("Trello key and token can be created at https://trello.com/app-key")
    print("Please:")

    # Trello key
    error_message = ""
    trello_key = None
    #TODO: propose reusing Trello key and token from existing config files
    while not trello_key:
        trello_key = input("%sEnter your Trello key ('q' to quit): " % error_message)
        if trello_key.lower() == "q":
            print("Exiting...")
            sys.exit(35)
        if not re.match("^[0-9a-fA-F]{32}$", trello_key):
            trello_key = None
            error_message = "Invalid Trello key, must be 32 characters. "
    config["key"] = trello_key

    # Trello token
    error_message = ""
    trello_token = None
    while not trello_token:
        trello_token = input("%sEnter your Trello token ('q' to quit): " % error_message)
        if trello_token.lower() == "q":
            print("Exiting...")
            sys.exit(36)
        if not re.match("^[0-9a-fA-F]{64}$", trello_token):
            trello_token = None
            error_message = "Invalid Trello token, must be 64 characters. "
    config["token"] = trello_token

    # Get the boards associated with the passed Trello credentials
    boards = perform_request("GET", "members/me/boards")
    print("These are your boards and their associated IDs:")
    print("           ID             |  Name")
    print("\n".join(["%s  |  %s" % (b["id"], b["name"]) for b in boards]))

    # Main board
    error_message = ""
    main_board = None
    while not main_board:
        main_board = input("%sEnter your main board ID ('q' to quit): " % error_message)
        if main_board.lower() == "q":
            print("Exiting...")
            sys.exit(37)
        if not re.match("^[0-9a-fA-F]{24}$", main_board):
            main_board = None
            error_message = "Invalid board ID, must be 24 characters. "
        elif main_board not in [b["id"] for b in boards]:
            main_board = None
            error_message = "This is not the ID of one of the boards you have access to. "
    config["main_board"] = main_board

    # Get the name of the selected board, and the lists associated with the other boards
    board_name = None
    lists_from_other_boards = []
    lists_output = ""
    for b in boards:
        if b["id"] == main_board:
            board_name = b["name"]
        else:
            lists_output += "\n\nLists from board '%s':\n           ID             |  Name" % b["name"]
            boards_lists = perform_request("GET", "boards/%s/lists" % b["id"])
            for l in boards_lists:
                lists_from_other_boards.append(l["id"])
                lists_output += "\n%s  |  '%s' (from board '%s')" % (l["id"], l["name"], b["name"])

    # Config name
    error_message = ""
    config_name = None
    while not config_name:
        # Propose the board name as config name
        config_name = rlinput("%sEnter a name for this new configuration ('q' to quit): " % error_message, board_name)
        if config_name.lower() == "q":
            print("Exiting...")
            sys.exit(38)
    config["name"] = config_name

    # Get the labels associated with the main board
    labels = perform_request("GET", "boards/%s/labels" % main_board)
    print("These are the labels from the selected board and their associated IDs:")
    print("           ID             |  Label")
    label_names = []
    for l in labels:
        if l["name"]:
            # Only propose labels that have names
            print("%s  |  '%s' (%s)" % (l["id"], l["name"], l["color"]))
            label_names.append(l["name"])

    # Associate labels with lists
    config["destination_lists"] = {}
    error_message = ""
    continue_label = "yes"
    label = None
    while continue_label == "yes":
        while not label:
            label = input("%sEnter a label name ('q' to quit): " % error_message)
            if label.lower() == "q":
                print("Exiting...")
                sys.exit(39)
            elif label not in label_names:
                label = None
                error_message = "This is not a valid label name for the selected board. "
        config["destination_lists"][label] = []
        # Get list ID to associate with this label
        error_message = ""
        list_id = None
        print("These are the lists associated to the other boards:\n%s" % lists_output)
        while not list_id:
            list_id = input("%sEnter the list ID you want to associate with label '%s' ('q' to quit): " % (error_message, label))
            if list_id.lower() == "q":
                print("Exiting...")
                sys.exit(40)
            if not re.match("^[0-9a-fA-F]{24}$", list_id):
                list_id = None
                error_message = "Invalid list ID, must be 24 characters. "
            if list_id:
                config["destination_lists"][label].append(list_id)
                # Support labels that point to multiple lists
                error_message2 = ""
                other_list = None
                while other_list == None:
                    other_list = input("%sDo you want to associate another list to this label? ('Yes', 'No' or 'q' to quit): " % error_message2)
                    if other_list.lower() == "q":
                        print("Exiting...")
                        sys.exit(42)
                    if other_list.lower() == "yes":
                        other_list = True
                        list_id = None
                    elif other_list.lower() == "no":
                        other_list = False
                    else:
                        other_list = None
                        error_message2 = "Invalid entry. "

        #TODO: Ability to define more friendly names than the destination board's names

        error_message = ""
        continue_label = None
        while not continue_label:
            continue_label = input("%sDo you want to add a new label? (Enter 'yes' or 'no', 'q' to quit): " % error_message)
            if continue_label.lower() == "q":
                print("Exiting...")
                sys.exit(41)
            if continue_label not in ("yes", "no"):
                continue_label = None
                error_message = "Invalid entry. "
            label = None

    logging.debug(config)

    config_file = "data/config_%s.json" % slugify(config_name)
    while os.path.isfile(config_file):
        #TODO: ask the user to enter a new valid file name
        config_file += ".nxt"
    with open(config_file, 'w') as out_file:
        json.dump(config, out_file, indent=2)
    print("New configuration saved to file '%s'" % config_file)
    return config_file

def new_webhook():
    logging.debug("Creating a new webhook for main board %s" % config["main_board"])
    query = {
        #TODO: direct to our future web endpoint
        #TODO: pass config filename for easier retrieval when processing a webhook
        "callbackURL": "https://webhook.site/04b7baf0-1a59-41e2-b41a-245abeabc847?c=config",
        "idModel": config["main_board"]
    }
    webhooks = perform_request("POST", "webhooks", query)
    logging.debug(json.dumps(webhooks, indent=2))

def list_webhooks():
    logging.debug("Existing webhooks:")
    webhooks = perform_request("GET", "tokens/%s/webhooks" % config["token"])
    logging.debug(json.dumps(webhooks, indent=2))
    return webhooks

def delete_webhook():
    logging.debug("Delete existing webhook for main board %s" % config["main_board"])
    for w in list_webhooks():
        if w["idModel"] == config["main_board"]:
            # Delete the webhook for that config's main model
            perform_request("DELETE", "webhooks/%s" % w["id"])
            logging.debug("Webhook %s deleted" % w["id"])

def load_config(config_file):
    logging.debug("Loading configuration %s" % config_file)
    with open(config_file, "r") as json_file:
        config = json.load(json_file)
    logging.info("Config '%s' loaded" % config["name"])
    logging.debug(config)
    return config

def parse_args(arguments):
    parser = argparse.ArgumentParser(description="Sync cards between different teams' Trello boards")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("-p", "--propagate", action='store_true', required=False, help="Propagate the main cards to the subordinate boards")
    group.add_argument("-cu", "--cleanup", action='store_true', required=False, help="Clean up all main cards and delete all cards from the subordinate boards (ONLY TO BE USED IN DEMO MODE)")
    group.add_argument("-nc", "--new-config", action='store_true', required=False, help="Create a new configuration file")
    group.add_argument("-w", "--webhook", choices=["new", "list", "delete"], action='store', required=False, help="Create new, list or delete existing webhooks")

    # These arguments are only to be used in conjunction with --propagate
    parser.add_argument("-c", "--card", action='store', required=False, help="Specify which main card to propagate. Only to be used in conjunction with --propagate")
    parser.add_argument("-l", "--list", action='store', required=False, help="Specify which main list to propagate. Only to be used in conjunction with --propagate")

    # General arguments (can be used both with --propagate and --cleanup)
    parser.add_argument("-dr", "--dry-run", action='store_true', required=False, help="Do not create, update or delete any records")
    parser.add_argument("-cfg", "--config", action='store', required=False, help="Path to the configuration file to use")
    parser.add_argument(
        '-d', '--debug',
        help="Print lots of debugging statements",
        action="store_const", dest="loglevel", const=logging.DEBUG,
        default=logging.WARNING,
    )
    parser.add_argument(
        '-v', '--verbose',
        help="Be verbose",
        action="store_const", dest="loglevel", const=logging.INFO,
    )
    args = parser.parse_args(arguments)

    # Check if the --card argument is a card URL
    if args.card:
        regex_match = re.match("^https://trello.com/c/([0-9a-zA-Z]{8})(/.*)?$", args.card)
        if regex_match:
            args.card = regex_match.group(1)

    # Validate if the arguments are used correctly
    if args.cleanup and not logging.getLevelName(args.loglevel) == "DEBUG":
        logging.critical("The --cleanup argument can only be used in conjunction with --debug. Exiting...")
        sys.exit(3)
    if args.webhook and not logging.getLevelName(args.loglevel) == "DEBUG":
        logging.critical("The --webhook argument can only be used in conjunction with --debug. Exiting...")
        sys.exit(9)
    if args.card and not args.propagate:
        logging.critical("The --card argument can only be used in conjunction with --propagate. Exiting...")
        sys.exit(4)
    if args.card and not (re.match("^[0-9a-zA-Z]{8}$", args.card) or re.match("^[0-9a-fA-F]{24}$", args.card)):
        logging.critical("The --card argument expects an 8 or 24-character card ID. Exiting...")
        sys.exit(5)
    if args.list and not args.propagate:
        logging.critical("The --list argument can only be used in conjunction with --propagate. Exiting...")
        sys.exit(6)
    if args.list and not re.match("^[0-9a-fA-F]{24}$", args.list):
        logging.critical("The --list argument expects a 24-character list ID. Exiting...")
        sys.exit(7)
    if args.config and not os.path.isfile(args.config):
        logging.critical("The value passed in the --path argument is not a valid file path. Exiting...")
        sys.exit(8)

    # Configure logging level
    if args.loglevel:
        logging.basicConfig(level=args.loglevel)
        args.logging_level = logging.getLevelName(args.loglevel)

    logging.debug("These are the parsed arguments:\n'%s'" % args)
    return args

def init():
    if __name__ == "__main__":
        # Define as global variable to be used without passing to all functions
        global args
        global config
        global app

        # Initiate the Flask app to access config, database
        app = create_app()

        # Parse the provided command-line arguments
        args = parse_args(sys.argv[1:])

        config_file = args.config
        if args.new_config:
            config_file = create_new_config()

        # Load configuration values
        if not config_file:
            config_file = "data/config.json"
        config = load_config(config_file)

        summary = None
        if args.cleanup:
            if not args.dry_run:
                # Cleanup deletes data, ensure the user is aware of that
                warning_acknowledged = False
                while not warning_acknowledged:
                    s = input("WARNING: this will delete all cards on the subordinate lists. Type 'YES' to confirm, or 'q' to quit: ")
                    if s.lower() == "q":
                        print("Exiting...")
                        sys.exit(34)
                    if s.lower() in ("yes", "oui", "ok", "yep", "no problemo", "aye"):
                        warning_acknowledged = True
            logging.debug("Get list of cards on the main Trello board")
            main_cards = perform_request("GET", "boards/%s/cards" % config["main_board"])
            # Delete all the main card attachments and cards on the subordinate boards
            summary = cleanup_test_boards(main_cards)
        elif args.propagate:
            summary = {"main_cards": 0, "active_main_cards": 0, "subordinate_card": 0, "new_subordinate_card": 0}
            if args.card:
                # Validate that this specific card is on the main board
                try:
                    main_card = perform_request("GET", "cards/%s" % args.card)
                except requests.exceptions.HTTPError:
                    logging.critical("Invalid card ID %s, card not found. Exiting..." % args.card)
                    sys.exit(33)
                if main_card["idBoard"] == config["main_board"]:
                    logging.debug("Card %s/%s is on the main board" % (main_card["id"], main_card["shortLink"]))
                    # Process that single card
                    output = process_main_card(main_card)
                    summary["main_cards"] = 1
                    summary["active_main_cards"] = output[0]
                    summary["subordinate_card"] += output[1]
                    summary["new_subordinate_card"] += output[2]
                else:
                    #TODO: Check if this is a subordinate card to process the associated main card
                    logging.critical("Card %s is not located on the main board %s. Exiting..." % (args.card, config["main_board"]))
                    sys.exit(31)
            else:
                if args.list:
                    # Validate that this specific list is on the main board
                    main_lists = perform_request("GET", "boards/%s/lists" % config["main_board"])
                    valid_main_list = False
                    for main_list in main_lists:
                        if args.list == main_list["id"]:
                            logging.debug("List %s is on the main board" % main_list["id"])
                            valid_main_list = True
                            # Get the list of cards on this main list
                            main_cards = perform_request("GET", "lists/%s/cards" % main_list["id"])
                            break
                    if not valid_main_list:
                        logging.critical("List %s is not on the main board %s. Exiting..." % (args.list, config["main_board"]))
                        sys.exit(32)
                else:
                    logging.debug("Get list of cards on the main Trello board")
                    main_cards = perform_request("GET", "boards/%s/cards" % config["main_board"])
                # Loop over all cards on the main board or list to sync the subordinate boards
                for idx, main_card in enumerate(main_cards):
                    logging.info("Processing main card %d/%d - %s" %(idx+1, len(main_cards), main_card["name"]))
                    output = process_main_card(main_card)
                    summary["main_cards"] = len(main_cards)
                    summary["active_main_cards"] += output[0]
                    summary["subordinate_card"] += output[1]
                    summary["new_subordinate_card"] += output[2]
        elif args.webhook:
            if args.webhook == "new":
                new_webhook()
            elif args.webhook == "list":
                list_webhooks()
            elif args.webhook == "delete":
                delete_webhook()
        output_summary(args, summary)

init()
