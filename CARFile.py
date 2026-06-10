# Script to allow moving all information from one account to another
import argparse
import os
from pathlib import Path

import atproto

from BlueSkyExporter import BlueskyExporter
from bluesky_importer_with_media import BlueskyImporter


def transferred():
    client = atproto.Client()

    input_login = input("Please enter your username: ")
    input_password = input("Please enter your password: ")

    client.login(input_login, input_password)

    # !/usr/bin/env python3
    """
    Bluesky Account Data Exporter
    Pulls all data from a Bluesky account using the AT Protocol.
    """

    try:
        from atproto import Client, models
    except ImportError:
        print("Installing required package: atproto...")
        import subprocess
        subprocess.check_call(["pip", "install", "atproto", "-q"])
        from atproto import Client, models


if __name__ == '__main__':
    handle = None
    password = None
    export_file = ""
    output_path = None
    base_filename = ""

    parser = argparse.ArgumentParser(
        description="Move data from one account to another in BlueSky"
    )
    parser.add_argument("-i", "--input", help="Input account")
    parser.add_argument("-p", "--password", help="Password for input account")
    parser.add_argument("-o", "--output", help="Output account")

    args = parser.parse_args()

    if args.input is not None and args.password is not None and args.output is not None:
        if not handle is None:
            handle = input("Enter your handle: ")
        else:
            handle = os.fspath(Path(args.input))
        print("Login: " + handle)
        if not password is None:
            password = input("Enter your password: ")
        else:
            password = str(os.fsencode(Path(args.password)))
        print("Password: " + str(password))
        if not output_path is None:
            output_path = input("Enter output path (default: bluesky_export): ")
        else:
            output_path = os.fspath(Path(args.output))
        print("Output path: " + output_path)

    print("=" * 60)
    print("Bluesky Account Data Exporter")
    print("=" * 60)

    # Get credentials
    if not handle:
        handle = input("Enter your Bluesky handle (e.g., username.bsky.social): ").strip()
    if not password:
        password = input("Enter your Bluesky app password (NOT your account password): ").strip()

    if not handle or not password:
        print("Error: Handle and password are required.")
    else:
        # Create exporter
        exporter = BlueskyExporter(handle, password)
        print(exporter.export(output_path))
        # Login
        if exporter.login():
            # Fetch all data
            exporter.fetch_profile()
            exporter.fetch_posts()
            exporter.fetch_likes()
            exporter.fetch_follows()
            exporter.fetch_followers()
            exporter.fetch_blocks()
            exporter.fetch_mutes()
            exporter.fetch_lists()
            exporter.fetch_feeds()
            exporter.fetch_starter_packs()

            # Export
            base_filename = exporter.export(output_path)
            if base_filename:
                print(f"\nAll data exported to: {output_path}/")
                transferred()

        # Create exporter
        importer = BlueskyImporter(handle, password, f"{base_filename}_all_data.json")

        # Load export data
        # data = importer.load_export_data(export_file)
        # if data:
        #
        #     # Import data
        #     importer.import_posts(data.get("posts", []), dry_run=dry_run)
        #     importer.import_likes(data.get("likes", []), dry_run=dry_run)
        #     importer.import_follows(data.get("follows", []), dry_run=dry_run)
        #     importer.import_blocks(data.get("blocks", []), dry_run=dry_run)
        #     importer.import_mutes(data.get("mutes", []), dry_run=dry_run)
        #     importer.import_lists(data.get("lists", []), dry_run=dry_run)
        #     importer.import_feeds(data.get("feeds", []), dry_run=dry_run)
        #     importer.import_profile_media(data.get("profile", {}), dry_run=dry_run)
        #     importer.import_profile_info(data.get("profile", {}), dry_run=dry_run)
        #
        #     # Print summary
        #     importer.print_summary()

        # if dry_run:
        #     print("\nDry run complete. Run again with 'n' to perform actual import.")
        print(f"\nAll data exported to: {export_file}/")
        transferred()
