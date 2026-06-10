#!/usr/bin/env python3
"""
Bluesky Data Importer with Media Re-upload
Imports data exported by bluesky_exporter.py into another Bluesky account,
including re-uploading images and videos.
"""

import json
import mimetypes
import os
import random
import time
from datetime import datetime, timezone
from typing import Optional, List, Dict, Tuple

try:
    from atproto import Client, models
    from atproto_client.utils import TextBuilder
except ImportError:
    print("Installing required package: atproto...")
    import subprocess

    subprocess.check_call(["pip", "install", "atproto", "-q"])
    from atproto import Client, models
    from atproto_client.utils import TextBuilder


class BlueskyImporter:
    def __init__(self, handle: str, password: str, output_path: str, dry_run: bool = False):
        self.client = Client()
        self.handle = handle
        self.password = password
        self.profile = None
        self.media_dir = None
        self.import_stats = {
            "posts_created": 0,
            "posts_skipped": 0,
            "posts_with_media": 0,
            "media_uploaded": 0,
            "media_failed": 0,
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

            # Determine media directory
            export_dir = os.path.dirname(os.path.abspath(export_file))
            self.media_dir = os.path.join(export_dir, "media")

            if os.path.exists(self.media_dir):
                print(f"  ✓ Media directory found: {self.media_dir}")
            else:
                print(f"  ⚠ Media directory not found: {self.media_dir}")
                print(f"    Media will not be re-uploaded.")

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

    def _resolve_media_path(self, local_path: str) -> Optional[str]:
        """Resolve a media path from the export to a local file."""
        if not local_path:
            return None

        # If it's already an absolute path that exists
        if os.path.isabs(local_path) and os.path.exists(local_path):
            return local_path

        # Try relative to media directory
        if self.media_dir:
            # Handle various path formats
            possible_paths = [
                os.path.join(self.media_dir, local_path),
                os.path.join(self.media_dir, os.path.basename(local_path)),
                os.path.join(self.media_dir, "posts", os.path.basename(local_path)),
                os.path.join(self.media_dir, "likes", os.path.basename(local_path)),
            ]

            for path in possible_paths:
                if os.path.exists(path):
                    return path

        return None

    def _upload_image(self, image_path: str, alt_text: str = "") -> Optional[models.AppBskyEmbedImages.Image]:
        """Upload an image and return image embed data."""
        try:
            with open(image_path, "rb") as f:
                image_data = f.read()

            # Detect MIME type
            mime_type, _ = mimetypes.guess_type(image_path)
            if not mime_type:
                mime_type = "image/jpeg"

            # Upload the blob
            upload_response = self.client.upload_blob(image_data, content_type=mime_type)

            return models.AppBskyEmbedImages.Image(
                alt=alt_text[:3000],  # Bluesky alt text limit
                image=upload_response.blob,
            )
        except Exception as e:
            print(f"    ✗ Failed to upload image {image_path}: {e}")
            self.import_stats["media_failed"] += 1
            return None

    def _upload_video(self, video_path: str, alt_text: str = "") -> Optional[Dict]:
        """Upload a video and return video embed data."""
        try:
            # Check file size (Bluesky has limits, typically ~50MB)
            file_size = os.path.getsize(video_path)
            if file_size > 50 * 1024 * 1024:  # 50MB
                print(f"    ⚠ Video too large ({file_size / 1024 / 1024:.1f}MB), skipping: {video_path}")
                self.import_stats["media_failed"] += 1
                return None

            with open(video_path, "rb") as f:
                video_data = f.read()

            # Detect MIME type
            mime_type, _ = mimetypes.guess_type(video_path)
            if not mime_type:
                mime_type = "video/mp4"

            # Upload the blob
            upload_response = self.client.upload_blob(video_data, content_type=mime_type)

            return {
                "blob": upload_response.blob,
                "alt": alt_text[:3000],
                "mime_type": mime_type,
            }
        except Exception as e:
            print(f"    ✗ Failed to upload video {video_path}: {e}")
            self.import_stats["media_failed"] += 1
            return None

    def _process_media_for_post(self, post_data: Dict) -> Tuple[Optional[List], Optional[Dict]]:
        """Process media for a post and return embed data."""
        media_items = post_data.get("media", [])
        local_paths = post_data.get("media_local_paths", [])

        if not media_items or not self.media_dir:
            return None, None

        images = []
        videos = []

        for i, item in enumerate(media_items):
            media_type = item.get("type", "")
            alt_text = item.get("alt", "")

            # Try to find the local file
            local_path = None
            if i < len(local_paths):
                local_path = self._resolve_media_path(local_paths[i])

            if not local_path:
                # Try to find by URL hash or filename
                url = item.get("url", "")
                if url:
                    import hashlib
                    url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
                    for subdir in ["posts", "likes", ""]:
                        search_dir = os.path.join(self.media_dir, subdir) if subdir else self.media_dir
                        if os.path.exists(search_dir):
                            for fname in os.listdir(search_dir):
                                if fname.startswith(url_hash):
                                    local_path = os.path.join(search_dir, fname)
                                    break
                        if local_path:
                            break

            if not local_path or not os.path.exists(local_path):
                continue

            if media_type == "image" or media_type == "thumbnail":
                img = self._upload_image(local_path, alt_text)
                if img:
                    images.append(img)
                    self.import_stats["media_uploaded"] += 1
                    time.sleep(0.5)  # Rate limiting between uploads

            elif media_type == "video":
                vid = self._upload_video(local_path, alt_text)
                if vid:
                    videos.append(vid)
                    self.import_stats["media_uploaded"] += 1
                    time.sleep(1.0)  # Longer delay for videos

        # Create embed based on what we have
        if len(images) > 0 and len(videos) == 0:
            embed = models.AppBskyEmbedImages.Main(images=images)
            return embed, None
        elif len(videos) > 0 and len(images) == 0:
            # For video, we need to use a different embed type
            video = videos[0]
            embed = models.AppBskyEmbedVideo.Main(
                video=video["blob"],
                alt=video["alt"],
            )
            return None, embed
        elif len(images) > 0 and len(videos) > 0:
            # Mixed - prioritize images for now
            embed = models.AppBskyEmbedImages.Main(images=images)
            return embed, None

        return None, None

    def import_posts(self, posts: List[Dict], dry_run: bool = False, delay: float = 3.0):
        """Import posts from exported data, including media re-upload."""
        print(f"\n[1/8] Importing posts ({len(posts)} found)...")

        for i, post in enumerate(posts):
            if post.get("is_repost"):
                self.import_stats["posts_skipped"] += 1
                continue

            text = post.get("text", "")
            if not text.strip():
                self.import_stats["posts_skipped"] += 1
                continue

            # Skip replies to avoid broken threads
            if post.get("is_reply"):
                self.import_stats["posts_skipped"] += 1
                continue

            if dry_run:
                media_count = len(post.get("media", []))
                if media_count > 0:
                    print(f"  [DRY RUN] Would post with {media_count} media: {text[:60]}...")
                else:
                    print(f"  [DRY RUN] Would post: {text[:80]}...")
                self.import_stats["posts_skipped"] += 1
                continue

            try:
                # Process media
                image_embed = None
                video_embed = None
                has_media = False

                if post.get("media") and self.media_dir:
                    print(f"  Processing media for post {i + 1}...")
                    image_embed, video_embed = self._process_media_for_post(post)
                    if image_embed or video_embed:
                        has_media = True
                        self.import_stats["posts_with_media"] += 1

                # Create the post with or without media
                if video_embed:
                    self.client.send_video(
                        text=text,
                        video=video_embed.video,
                        video_alt=video_embed.alt if hasattr(video_embed, 'alt') else "",
                    )
                elif image_embed:
                    self.client.send_image(
                        text=text,
                        image=image_embed.images[0].image if image_embed.images else None,
                        image_alt=image_embed.images[0].alt if image_embed.images else "",
                    )
                    # For multiple images, we'd need to use a different approach
                    # The atproto library handles single images easily
                    # For multiple images, we use send_post with embed
                    if len(image_embed.images) > 1:
                        self.client.send_post(
                            text=text,
                            embed=image_embed,
                        )
                else:
                    self.client.send_post(text=text)

                self.import_stats["posts_created"] += 1

                if has_media:
                    print(f"  ✓ Posted with media ({i + 1}/{len(posts)}): {text[:50]}...")
                else:
                    print(f"  ✓ Posted ({i + 1}/{len(posts)}): {text[:60]}...")

                # Rate limiting - longer if media was uploaded
                sleep_time = delay + random.uniform(0, 2)
                if has_media:
                    sleep_time += 2  # Extra delay after media posts
                time.sleep(sleep_time)

            except Exception as e:
                error_msg = f"Post {i + 1}: {str(e)}"
                self.import_stats["errors"].append(error_msg)
                print(f"  ✗ Error posting: {error_msg}")
                time.sleep(delay * 2)

        print(f"  ✓ Posts created: {self.import_stats['posts_created']}")
        print(f"  ✓ Posts with media: {self.import_stats['posts_with_media']}")
        print(f"  ✓ Posts skipped: {self.import_stats['posts_skipped']}")
        print(f"  ✓ Media uploaded: {self.import_stats['media_uploaded']}")
        print(f"  ✓ Media failed: {self.import_stats['media_failed']}")

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
                self.client.like(uri=post_uri, cid=post_cid)
                self.import_stats["likes_created"] += 1

                if (i + 1) % 10 == 0:
                    print(f"  ... liked {i + 1}/{len(likes)} posts")

                time.sleep(delay + random.uniform(0, 0.5))

            except Exception as e:
                self.import_stats["likes_skipped"] += 1
                if "not found" not in str(e).lower():
                    error_msg = f"Like {i + 1}: {str(e)}"
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

            if handle == self.handle or did == self.profile.did:
                self.import_stats["follows_skipped"] += 1
                continue

            if dry_run:
                print(f"  [DRY RUN] Would follow: {handle or did}")
                self.import_stats["follows_skipped"] += 1
                continue

            try:
                target = did if did else handle
                self.client.follow(target)
                self.import_stats["follows_created"] += 1

                if (i + 1) % 10 == 0:
                    print(f"  ... followed {i + 1}/{len(follows)} accounts")

                time.sleep(delay + random.uniform(0, 0.5))

            except Exception as e:
                self.import_stats["follows_skipped"] += 1
                if "not found" not in str(e).lower():
                    error_msg = f"Follow {i + 1} ({handle}): {str(e)}"
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
                error_msg = f"Block {i + 1}: {str(e)}"
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
                error_msg = f"Mute {i + 1}: {str(e)}"
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

    def import_profile_media(self, profile_data: Dict, dry_run: bool = False):
        """Import avatar and banner images."""
        print(f"\n[8/8] Importing profile media...")

        avatar_path = profile_data.get("avatar_local", "")
        banner_path = profile_data.get("banner_local", "")

        if dry_run:
            if avatar_path:
                print(f"  [DRY RUN] Would upload avatar: {avatar_path}")
            if banner_path:
                print(f"  [DRY RUN] Would upload banner: {banner_path}")
            return

        # Upload avatar
        if avatar_path:
            resolved = self._resolve_media_path(avatar_path)
            if resolved and os.path.exists(resolved):
                try:
                    with open(resolved, "rb") as f:
                        avatar_data = f.read()

                    mime_type, _ = mimetypes.guess_type(resolved)
                    if not mime_type:
                        mime_type = "image/jpeg"

                    upload = self.client.upload_blob(avatar_data, content_type=mime_type)

                    # Update profile with new avatar
                    current = self.client.app.bsky.actor.get_profile({"actor": self.handle})
                    self.client.app.bsky.actor.profile.create(
                        repo=self.profile.did,
                        record={
                            "$type": "app.bsky.actor.profile",
                            "displayName": current.display_name or "",
                            "description": current.description or "",
                            "avatar": upload.blob,
                            "banner": current.banner,
                            "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                        }
                    )
                    print(f"  ✓ Avatar uploaded and set")
                    time.sleep(1)
                except Exception as e:
                    print(f"  ✗ Failed to upload avatar: {e}")
            else:
                print(f"  ⚠ Avatar file not found: {avatar_path}")

        # Upload banner
        if banner_path:
            resolved = self._resolve_media_path(banner_path)
            if resolved and os.path.exists(resolved):
                try:
                    with open(resolved, "rb") as f:
                        banner_data = f.read()

                    mime_type, _ = mimetypes.guess_type(resolved)
                    if not mime_type:
                        mime_type = "image/jpeg"

                    upload = self.client.upload_blob(banner_data, content_type=mime_type)

                    # Update profile with new banner
                    current = self.client.app.bsky.actor.get_profile({"actor": self.handle})
                    self.client.app.bsky.actor.profile.create(
                        repo=self.profile.did,
                        record={
                            "$type": "app.bsky.actor.profile",
                            "displayName": current.display_name or "",
                            "description": current.description or "",
                            "avatar": current.avatar,
                            "banner": upload.blob,
                            "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                        }
                    )
                    print(f"  ✓ Banner uploaded and set")
                    time.sleep(1)
                except Exception as e:
                    print(f"  ✗ Failed to upload banner: {e}")
            else:
                print(f"  ⚠ Banner file not found: {banner_path}")

    def import_profile_info(self, profile_data: Dict, dry_run: bool = False):
        """Display profile info for manual setup."""
        print(f"\nProfile information:")
        print(f"  Display name: {profile_data.get('display_name', '')}")
        print(f"  Description: {profile_data.get('description', '')}")
        print(f"  \n  Note: Update display name and bio manually in Bluesky settings.")
        print(f"  Avatar and banner will be imported if files are available.")

    def print_summary(self):
        """Print import summary."""
        print(f"\n{'=' * 60}")
        print(f"IMPORT SUMMARY")
        print(f"{'=' * 60}")
        print(f"Posts created:       {self.import_stats['posts_created']}")
        print(f"Posts with media:    {self.import_stats['posts_with_media']}")
        print(f"Posts skipped:       {self.import_stats['posts_skipped']}")
        print(f"Media uploaded:      {self.import_stats['media_uploaded']}")
        print(f"Media failed:        {self.import_stats['media_failed']}")
        print(f"Likes created:       {self.import_stats['likes_created']}")
        print(f"Likes skipped:       {self.import_stats['likes_skipped']}")
        print(f"Follows created:     {self.import_stats['follows_created']}")
        print(f"Follows skipped:     {self.import_stats['follows_skipped']}")
        print(f"Blocks created:      {self.import_stats['blocks_created']}")
        print(f"Blocks skipped:      {self.import_stats['blocks_skipped']}")
        print(f"Mutes created:       {self.import_stats['mutes_created']}")
        print(f"Mutes skipped:       {self.import_stats['mutes_skipped']}")
        print(f"Lists created:       {self.import_stats['lists_created']}")
        print(f"Feeds listed:        {self.import_stats['feeds_saved']}")

        if self.import_stats["errors"]:
            print(f"\nErrors ({len(self.import_stats['errors'])}):")
            for error in self.import_stats["errors"][:10]:
                print(f"  - {error}")
            if len(self.import_stats["errors"]) > 10:
                print(f"  ... and {len(self.import_stats['errors']) - 10} more")

        print(f"{'=' * 60}")


def main():
    print("=" * 60)
    print("Bluesky Data Importer with Media Re-upload")
    print("=" * 60)
    print("\nWARNING: This will create posts, likes, follows, blocks,")
    print("mutes, and upload media on the target account.")
    print("Use dry-run first!")
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
    importer.import_profile_media(data.get("profile", {}), dry_run=dry_run)
    importer.import_profile_info(data.get("profile", {}), dry_run=dry_run)

    # Print summary
    importer.print_summary()

    if dry_run:
        print("\nDry run complete. Run again with 'n' to perform actual import.")


if __name__ == "__main__":
    main()
