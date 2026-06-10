#!/usr/bin/env python3
"""
Bluesky Data Importer
Imports data exported by bluesky_exporter.py into another Bluesky account.
"""

import os
import json
import time
import random
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

try:
    from atproto import Client, models
    from atproto.xrpc_client.models import ids
except ImportError:
    print("Installing required package: atproto...")
    import subprocess
    subprocess.check_call(["pip", "install", "atproto", "-q"])
    from atproto import Client, models
    from atproto.xrpc_client.models import ids


class BlueskyImporter:
    def __init__(self, handle: str, password: str):
        self.client = Client()
        self.handle = handle
        self.password = password
        self.profile = None
        self.import_stats = {
            "posts_created": 0,
            "posts_skipped": 0,
            "likes_created": 0,
            "likes_skipped": 0,
            "follows_created": 0,
            "follows_skipped": 0,
            "blocks_created": 0,
            "blocks_skipped": 0,
            "mutes_created": 0,
            "mutes_skipped": 0,
            "lists_created": 0,
            "feeds_saved": 0,
            "errors": [],
        }

    def login(self) -> bool:
        """Authenticate with Bluesky."""
        try:
            self.profile = self.client.login(self.handle, self.password)
            print(f"✓ Logged in as: {self.profile.display_name or self.profile.handle}")
            print(f"  DID: {self.profile.did}")
            return True
        except Exception as e:
            print(f"✗ Login failed: {e}")
            return False

    def load_export_data(self, export_file: str) -> Dict:
        """Load exported data from JSON file."""
        print(f"\nLoading export data from: {export_file}")
        try:
            with open(export_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Check if it's the combined file or individual category file
            if "export_metadata" in data:
                print(f"  ✓ Loaded combined export from {data['export_metadata'].get('account', 'unknown')}")
                print(f"  Exported at: {data['export_metadata'].get('exported_at', 'unknown')}")
                return data
            else:
                # Individual category file - wrap it
                print(f"  ✓ Loaded individual category file")
                return {"data": data}
        except Exception as e:
            print(f"  ✗ Error loading export: {e}")
            return {}

    def import_posts(self, posts: List[Dict], dry_run: bool = False, delay: float = 2.0):
        """Import posts from exported data."""
        print(f"\n[1/8] Importing posts ({len(posts)} found)...")

        for i, post in enumerate(posts):
            if post.get("is_repost"):
                # Skip reposts for now (would need different handling)
                self.import_stats["posts_skipped"] += 1
                continue

            text = post.get("text", "")
            if not text.strip():
                self.import_stats["posts_skipped"] += 1
                continue

            # Check if it's a reply - skip replies to avoid broken threads
            if post.get("is_reply"):
                self.import_stats["posts_skipped"] += 1
                continue

            if dry_run:
                print(f"  [DRY RUN] Would post: {text[:80]}...")
                self.import_stats["posts_skipped"] += 1
                continue

            try:
                # Create the post
                self.client.send_post(text=text)
                self.import_stats["posts_created"] += 1
                print(f"  ✓ Posted ({i+1}/{len(posts)}): {text[:60]}...")

                # Rate limiting
                time.sleep(delay + random.uniform(0, 1))

            except Exception as e:
                error_msg = f"Post {i+1}: {str(e)}"
                self.import_stats["errors"].append(error_msg)
                print(f"  ✗ Error posting: {error_msg}")
                time.sleep(delay * 2)  # Longer delay after error

        print(f"  ✓ Posts created: {self.import_stats['posts_created']}")
        print(f"  ✓ Posts skipped: {self.import_stats['posts_skipped']}")

    def import_likes(self, likes: List[Dict], dry_run: bool = False, delay: float = 1.0):
        """Import likes by re-liking posts."""
        print(f"\n[2/8] Importing likes ({len(likes)} found)...")

        for i, like in enumerate(likes):
            post_uri = like.get("post_uri", "")
            post_cid = like.get("post_cid", "")

            if not post_uri or not post_cid:
                self.import_stats["likes_skipped"] += 1
                continue

            if dry_run:
                print(f"  [DRY RUN] Would like: {post_uri[:60]}...")
                self.import_stats["likes_skipped"] += 1
                continue

            try:
                # Like the post
                self.client.like(uri=post_uri, cid=post_cid)
                self.import_stats["likes_created"] += 1

                if (i + 1) % 10 == 0:
                    print(f"  ... liked {i+1}/{len(likes)} posts")

                time.sleep(delay + random.uniform(0, 0.5))

            except Exception as e:
                # Post might be deleted or unavailable
                self.import_stats["likes_skipped"] += 1
                if "not found" not in str(e).lower():
                    error_msg = f"Like {i+1}: {str(e)}"
                    self.import_stats["errors"].append(error_msg)

        print(f"  ✓ Likes created: {self.import_stats['likes_created']}")
        print(f"  ✓ Likes skipped: {self.import_stats['likes_skipped']}")

    def import_follows(self, follows: List[Dict], dry_run: bool = False, delay: float = 1.0):
        """Import follows by following accounts."""
        print(f"\n[3/8] Importing follows ({len(follows)} found)...")

        for i, follow in enumerate(follows):
            handle = follow.get("handle", "")
            did = follow.get("did", "")

            if not handle and not did:
                self.import_stats["follows_skipped"] += 1
                continue

            # Skip if it's yourself
            if handle == self.handle or did == self.profile.did:
                self.import_stats["follows_skipped"] += 1
                continue

            if dry_run:
                print(f"  [DRY RUN] Would follow: {handle or did}")
                self.import_stats["follows_skipped"] += 1
                continue

            try:
                # Follow by handle or DID
                target = did if did else handle
                self.client.follow(target)
                self.import_stats["follows_created"] += 1

                if (i + 1) % 10 == 0:
                    print(f"  ... followed {i+1}/{len(follows)} accounts")

                time.sleep(delay + random.uniform(0, 0.5))

            except Exception as e:
                # Account might not exist anymore
                self.import_stats["follows_skipped"] += 1
                if "not found" not in str(e).lower():
                    error_msg = f"Follow {i+1} ({handle}): {str(e)}"
                    self.import_stats["errors"].append(error_msg)

        print(f"  ✓ Follows created: {self.import_stats['follows_created']}")
        print(f"  ✓ Follows skipped: {self.import_stats['follows_skipped']}")

    def import_blocks(self, blocks: List[Dict], dry_run: bool = False, delay: float = 1.0):
        """Import blocks."""
        print(f"\n[4/8] Importing blocks ({len(blocks)} found)...")

        for i, block in enumerate(blocks):
            did = block.get("did", "")
            handle = block.get("handle", "")

            if not did and not handle:
                self.import_stats["blocks_skipped"] += 1
                continue

            if dry_run:
                print(f"  [DRY RUN] Would block: {handle or did}")
                self.import_stats["blocks_skipped"] += 1
                continue

            try:
                target = did if did else handle
                self.client.block(target)
                self.import_stats["blocks_created"] += 1
                time.sleep(delay)
            except Exception as e:
                self.import_stats["blocks_skipped"] += 1
                error_msg = f"Block {i+1}: {str(e)}"
                self.import_stats["errors"].append(error_msg)

        print(f"  ✓ Blocks created: {self.import_stats['blocks_created']}")
        print(f"  ✓ Blocks skipped: {self.import_stats['blocks_skipped']}")

    def import_mutes(self, mutes: List[Dict], dry_run: bool = False, delay: float = 1.0):
        """Import mutes."""
        print(f"\n[5/8] Importing mutes ({len(mutes)} found)...")

        for i, mute in enumerate(mutes):
            did = mute.get("did", "")
            handle = mute.get("handle", "")

            if not did and not handle:
                self.import_stats["mutes_skipped"] += 1
                continue

            if dry_run:
                print(f"  [DRY RUN] Would mute: {handle or did}")
                self.import_stats["mutes_skipped"] += 1
                continue

            try:
                target = did if did else handle
                self.client.mute(target)
                self.import_stats["mutes_created"] += 1
                time.sleep(delay)
            except Exception as e:
                self.import_stats["mutes_skipped"] += 1
                error_msg = f"Mute {i+1}: {str(e)}"
                self.import_stats["errors"].append(error_msg)

        print(f"  ✓ Mutes created: {self.import_stats['mutes_created']}")
        print(f"  ✓ Mutes skipped: {self.import_stats['mutes_skipped']}")

    def import_lists(self, lists: List[Dict], dry_run: bool = False, delay: float = 1.0):
        """Import moderation lists."""
        print(f"\n[6/8] Importing lists ({len(lists)} found)...")

        for i, lst in enumerate(lists):
            name = lst.get("name", "")
            description = lst.get("description", "")
            purpose = lst.get("purpose", "app.bsky.graph.defs#modlist")

            if not name:
                continue

            if dry_run:
                print(f"  [DRY RUN] Would create list: {name}")
                continue

            try:
                # Create the list
                record = {
                    "$type": "app.bsky.graph.list",
                    "name": name,
                    "description": description,
                    "purpose": purpose,
                    "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                }

                response = self.client.app.bsky.graph.list.create(
                    repo=self.profile.did,
                    record=record
                )
                self.import_stats["lists_created"] += 1
                print(f"  ✓ Created list: {name}")
                time.sleep(delay)
            except Exception as e:
                error_msg = f"List '{name}': {str(e)}"
                self.import_stats["errors"].append(error_msg)
                print(f"  ✗ Error creating list: {error_msg}")

        print(f"  ✓ Lists created: {self.import_stats['lists_created']}")

    def import_feeds(self, feeds: List[Dict], dry_run: bool = False):
        """Import saved feeds."""
        print(f"\n[7/8] Importing feeds ({len(feeds)} found)...")
        print(f"  Note: Feed preferences cannot be directly imported via API.")
        print(f"  Feed URIs to manually save:")

        for feed in feeds:
            uri = feed.get("uri", "")
            name = feed.get("display_name", "")
            if uri:
                print(f"    - {name}: {uri}")
                self.import_stats["feeds_saved"] += 1

        print(f"  ✓ Feeds listed: {self.import_stats['feeds_saved']}")
        print(f"  (Manually add these feeds in Bluesky settings)")

    def import_profile_info(self, profile_data: Dict, dry_run: bool = False):
        """Display profile info for manual setup."""
        print(f"\n[8/8] Profile information (manual setup required):")
        print(f"  Display name: {profile_data.get('display_name', '')}")
        print(f"  Description: {profile_data.get('description', '')}")
        print(f"  Avatar: {profile_data.get('avatar', 'None')}")
        print(f"  Banner: {profile_data.get('banner', 'None')}")
        print(f"  \n  Note: Profile details must be updated manually in Bluesky settings.")

    def print_summary(self):
        """Print import summary."""
        print(f"\n{'='*60}")
        print(f"IMPORT SUMMARY")
        print(f"{'='*60}")
        print(f"Posts created:     {self.import_stats['posts_created']}")
        print(f"Posts skipped:     {self.import_stats['posts_skipped']}")
        print(f"Likes created:     {self.import_stats['likes_created']}")
        print(f"Likes skipped:     {self.import_stats['likes_skipped']}")
        print(f"Follows created:   {self.import_stats['follows_created']}")
        print(f"Follows skipped:   {self.import_stats['follows_skipped']}")
        print(f"Blocks created:    {self.import_stats['blocks_created']}")
        print(f"Blocks skipped:    {self.import_stats['blocks_skipped']}")
        print(f"Mutes created:     {self.import_stats['mutes_created']}")
        print(f"Mutes skipped:     {self.import_stats['mutes_skipped']}")
        print(f"Lists created:     {self.import_stats['lists_created']}")
        print(f"Feeds listed:      {self.import_stats['feeds_saved']}")

        if self.import_stats["errors"]:
            print(f"\nErrors ({len(self.import_stats['errors'])}):")
            for error in self.import_stats["errors"][:10]:
                print(f"  - {error}")
            if len(self.import_stats["errors"]) > 10:
                print(f"  ... and {len(self.import_stats['errors']) - 10} more")

        print(f"{'='*60}")


def main():
    print("=" * 60)
    print("Bluesky Data Importer")
    print("=" * 60)
    print("\nWARNING: This will create posts, likes, follows, blocks,")
    print("and mutes on the target account. Use dry-run first!")
    print("=" * 60)

    # Get target account credentials
    handle = input("\nEnter TARGET Bluesky handle (e.g., newaccount.bsky.social): ").strip()
    password = input("Enter TARGET Bluesky app password: ").strip()

    if not handle or not password:
        print("Error: Handle and password are required.")
        return

    # Get export file path
    export_file = input("\nPath to exported data file (e.g., bluesky_export/username_all_data.json): ").strip()
    if not export_file or not os.path.exists(export_file):
        print(f"Error: File not found: {export_file}")
        return

    # Dry run option
    dry_run_input = input("\nRun in DRY RUN mode first? (recommended) [Y/n]: ").strip().lower()
    dry_run = dry_run_input in ("", "y", "yes")

    if dry_run:
        print("\n*** DRY RUN MODE - No changes will be made ***")
    else:
        confirm = input("\nAre you sure you want to import data? This cannot be undone! [yes/no]: ").strip().lower()
        if confirm != "yes":
            print("Import cancelled.")
            return

    # Create importer
    importer = BlueskyImporter(handle, password)

    # Login
    if not importer.login():
        return

    # Load export data
    data = importer.load_export_data(export_file)
    if not data:
        return

    # Import data
    importer.import_posts(data.get("posts", []), dry_run=dry_run)
    importer.import_likes(data.get("likes", []), dry_run=dry_run)
    importer.import_follows(data.get("follows", []), dry_run=dry_run)
    importer.import_blocks(data.get("blocks", []), dry_run=dry_run)
    importer.import_mutes(data.get("mutes", []), dry_run=dry_run)
    importer.import_lists(data.get("lists", []), dry_run=dry_run)
    importer.import_feeds(data.get("feeds", []), dry_run=dry_run)
    importer.import_profile_info(data.get("profile", {}), dry_run=dry_run)

    # Print summary
    importer.print_summary()

    if dry_run:
        print("\nDry run complete. Run again with 'n' to perform actual import.")


if __name__ == "__main__":
    main()
