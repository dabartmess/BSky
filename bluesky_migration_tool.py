#!/usr/bin/env python3
"""
Bluesky Complete Migration Tool
Exports ALL data and media from one Bluesky account, then imports into another.
"""
import argparse
import hashlib
import json
import mimetypes
import os
import time
import zipfile
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple

import requests

try:
    from atproto import Client, models
except ImportError:
    print("Installing required package: atproto...")
    import subprocess

    subprocess.check_call(["pip", "install", "atproto", "-q"])
    from atproto import Client, models

account_in = ""
password_in = ""
account_out = ""
password_out = ""
dry_run = False


# ═══════════════════════════════════════════════════════════════
# EXPORTER CLASS
# ═══════════════════════════════════════════════════════════════

class BlueskyExporter:
    def __init__(self, handle: str, password: str):
        self.client = Client()
        self.handle = handle
        self.password = password
        self.profile = None
        self.data = {
            "profile": {},
            "posts": [],
            "reposts": [],
            "likes": [],
            "follows": [],
            "followers": [],
            "blocks": [],
            "mutes": [],
            "lists": [],
            "feeds": [],
            "starter_packs": [],
            "bookmarks": [],
            "export_metadata": {
                "exported_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "account": handle,
                "version": "2.0",
            },
        }
        self.media_dir = None
        self.downloaded_media = set()

    def login(self) -> bool:
        try:
            self.profile = self.client.login(self.handle, self.password)
            print(f"✓ Logged in as: {self.profile.display_name or self.profile.handle}")
            print(f"  DID: {self.profile.did}")
            print(f"  Followers: {self.profile.followers_count}")
            print(f"  Following: {self.profile.follows_count}")
            print(f"  Posts: {self.profile.posts_count}")
            return True
        except Exception as e:
            print(f"✗ Login failed: {e}")
            return False

    def _extract_media_urls(self, record: Any) -> List[Dict[str, str]]:
        media_items = []
        if not hasattr(record, "embed"):
            return media_items
        embed = record.embed

        # Images
        if hasattr(embed, "images") and embed.images:
            for img in embed.images:
                if hasattr(img, "image") and hasattr(img.image, "ref"):
                    cid = img.image.ref.link if hasattr(img.image.ref, "link") else str(img.image.ref)
                    url = f"https://cdn.bsky.app/img/feed_fullsize/plain/{self.profile.did}/{cid}@jpeg"
                    media_items.append({"url": url, "type": "image", "alt": img.alt if hasattr(img, "alt") else "",
                                        "mime_type": img.image.mime_type if hasattr(img.image,
                                                                                    "mime_type") else "image/jpeg"})
                if hasattr(img, "fullsize") and img.fullsize:
                    media_items.append(
                        {"url": img.fullsize, "type": "image", "alt": img.alt if hasattr(img, "alt") else "",
                         "mime_type": "image/jpeg"})

        # Video
        if hasattr(embed, "video") and embed.video:
            video = embed.video
            if hasattr(video, "ref") and video.ref:
                cid = video.ref.link if hasattr(video.ref, "link") else str(video.ref)
                url = f"https://video.bsky.app/watch/{self.profile.did}/{cid}"
                media_items.append({"url": url, "type": "video", "alt": video.alt if hasattr(video, "alt") else "",
                                    "mime_type": video.mime_type if hasattr(video, "mime_type") else "video/mp4"})
            if hasattr(video, "playlist") and video.playlist:
                media_items.append(
                    {"url": video.playlist, "type": "video", "alt": video.alt if hasattr(video, "alt") else "",
                     "mime_type": "application/x-mpegURL"})

        # External thumbnails
        if hasattr(embed, "external") and embed.external:
            ext = embed.external
            if hasattr(ext, "thumb") and ext.thumb:
                thumb_url = str(ext.thumb) if hasattr(ext.thumb, "uri") else ext.thumb
                if thumb_url.startswith("http"):
                    media_items.append(
                        {"url": thumb_url, "type": "thumbnail", "alt": ext.title if hasattr(ext, "title") else "",
                         "mime_type": "image/jpeg"})

        # Quote post media
        if hasattr(embed, "record") and embed.record:
            if hasattr(embed.record, "embeds") and embed.record.embeds:
                for sub_embed in embed.record.embeds:
                    if hasattr(sub_embed, "images") and sub_embed.images:
                        for img in sub_embed.images:
                            if hasattr(img, "fullsize") and img.fullsize:
                                media_items.append({"url": img.fullsize, "type": "image",
                                                    "alt": img.alt if hasattr(img, "alt") else "",
                                                    "mime_type": "image/jpeg"})
        return media_items

    def _get_extension(self, mime_type: str, media_type: str) -> str:
        mime_map = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp",
                    "video/mp4": ".mp4", "video/webm": ".webm", "application/x-mpegURL": ".m3u8"}
        if mime_type in mime_map:
            return mime_map[mime_type]
        if media_type == "image":
            return ".jpg"
        if media_type == "video":
            return ".mp4"
        return ".bin"

    def _download_media(self, media_items: List[Dict[str, str]], subfolder: str = "posts") -> List[str]:
        if not media_items or not self.media_dir:
            return []
        downloaded = []
        for item in media_items:
            url = item.get("url", "")
            if not url or not url.startswith("http"):
                continue
            url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
            ext = self._get_extension(item.get("mime_type", ""), item.get("type", ""))
            filename = f"{url_hash}{ext}"
            filepath = os.path.join(self.media_dir, subfolder, filename)
            if filepath in self.downloaded_media:
                downloaded.append(filepath)
                continue
            try:
                os.makedirs(os.path.dirname(filepath), exist_ok=True)
                response = requests.get(url, timeout=30, stream=True)
                response.raise_for_status()
                with open(filepath, "wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                self.downloaded_media.add(filepath)
                downloaded.append(filepath)
                time.sleep(0.2)
            except Exception as e:
                print(f"    ✗ Failed to download {url[:60]}...: {e}")
        return downloaded

    def _get_avatar_banner(self, profile_data: Any) -> Dict[str, str]:
        urls = {}
        if hasattr(profile_data, "avatar") and profile_data.avatar:
            urls["avatar"] = str(profile_data.avatar)
        if hasattr(profile_data, "banner") and profile_data.banner:
            urls["banner"] = str(profile_data.banner)
        return urls

    def fetch_profile(self):
        print("\n[1/11] Fetching profile...")
        try:
            profile = self.client.app.bsky.actor.get_profile(params={"actor": self.handle})
            profile_media = self._get_avatar_banner(profile)
            downloaded_profile_media = {}
            for key, url in profile_media.items():
                if url:
                    try:
                        ext = ".jpg"
                        filename = f"profile_{key}{ext}"
                        filepath = os.path.join(self.media_dir, filename)
                        response = requests.get(url, timeout=30, stream=True)
                        response.raise_for_status()
                        with open(filepath, "wb") as f:
                            for chunk in response.iter_content(chunk_size=8192):
                                f.write(chunk)
                        downloaded_profile_media[key] = filepath
                        print(f"  ✓ Downloaded {key}")
                    except Exception as e:
                        print(f"  ✗ Failed to download {key}: {e}")

            self.data["profile"] = {
                "did": profile.did,
                "handle": profile.handle,
                "display_name": profile.display_name,
                "description": profile.description,
                "avatar": profile.avatar,
                "avatar_local": os.path.relpath(downloaded_profile_media.get("avatar"),
                                                self.media_dir) if downloaded_profile_media.get("avatar") else None,
                "banner": profile.banner,
                "banner_local": os.path.relpath(downloaded_profile_media.get("banner"),
                                                self.media_dir) if downloaded_profile_media.get("banner") else None,
                "followers_count": profile.followers_count,
                "follows_count": profile.follows_count,
                "posts_count": profile.posts_count,
                "created_at": profile.created_at,
                "indexed_at": profile.indexed_at,
            }
            print(f"  ✓ Profile fetched")
        except Exception as e:
            print(f"  ✗ Error: {e}")

    def fetch_posts(self, limit: int = 100):
        print(f"\n[2/11] Fetching posts with media (batch size: {limit})...")
        cursor = None
        total = 0
        while True:
            try:
                params = {"actor": self.handle, "limit": limit}
                if cursor:
                    params["cursor"] = cursor
                response = self.client.app.bsky.feed.get_author_feed(params=params)
                for feed_view in response.feed:
                    post = feed_view.post
                    record = post.record
                    media_items = self._extract_media_urls(record)
                    downloaded_media = self._download_media(media_items, "posts")
                    post_data = {
                        "uri": post.uri, "cid": post.cid,
                        "text": record.text if hasattr(record, "text") else "",
                        "created_at": record.created_at if hasattr(record, "created_at") else None,
                        "reply_count": post.reply_count, "repost_count": post.repost_count,
                        "like_count": post.like_count, "quote_count": post.quote_count,
                        "indexed_at": post.indexed_at,
                        "is_reply": hasattr(record, "reply") and record.reply is not None,
                        "is_repost": feed_view.reason is not None and hasattr(feed_view.reason, "by"),
                        "langs": record.langs if hasattr(record, "langs") else [],
                        "labels": [l.val for l in post.labels] if post.labels else [],
                        "media": media_items,
                        "media_local_paths": [os.path.relpath(p, self.media_dir) for p in
                                              downloaded_media] if self.media_dir else [],
                    }
                    if feed_view.reason and hasattr(feed_view.reason, "by"):
                        post_data["reposted_by"] = str(feed_view.reason.by)
                        self.data["reposts"].append(post_data)
                    else:
                        self.data["posts"].append(post_data)
                    total += 1
                cursor = response.cursor
                if not cursor or len(response.feed) == 0:
                    break
                if total % 500 == 0:
                    print(f"  ... fetched {total} posts so far ({len(self.downloaded_media)} media files)")
                    time.sleep(0.5)
            except Exception as e:
                print(f"  ✗ Error fetching posts: {e}")
                break
        print(f"  ✓ Fetched {len(self.data['posts'])} original posts, {len(self.data['reposts'])} reposts")
        print(f"  ✓ Downloaded {len(self.downloaded_media)} media files")

    def fetch_likes(self, limit: int = 100):
        print(f"\n[3/11] Fetching likes with media...")
        cursor = None
        total = 0
        while True:
            try:
                params = {"actor": self.handle, "limit": limit}
                if cursor:
                    params["cursor"] = cursor
                response = self.client.app.bsky.feed.get_actor_likes(params=params)
                for liked in response.feed:
                    post = liked.post
                    record = post.record
                    media_items = self._extract_media_urls(record)
                    downloaded_media = self._download_media(media_items, "likes")
                    like_data = {
                        "post_uri": post.uri, "post_cid": post.cid,
                        "post_text": record.text if hasattr(record, "text") else "",
                        "post_author": post.author.handle,
                        "post_author_display_name": post.author.display_name,
                        "post_created_at": record.created_at if hasattr(record, "created_at") else None,
                        "liked_at": post.indexed_at,
                        "media": media_items,
                        "media_local_paths": [os.path.relpath(p, self.media_dir) for p in
                                              downloaded_media] if self.media_dir else [],
                    }
                    self.data["likes"].append(like_data)
                    total += 1
                cursor = response.cursor
                if not cursor or len(response.feed) == 0:
                    break
                if total % 500 == 0:
                    print(f"  ... fetched {total} likes so far ({len(self.downloaded_media)} total media files)")
                    time.sleep(0.5)
            except Exception as e:
                print(f"  ✗ Error fetching likes: {e}")
                break
        print(f"  ✓ Fetched {len(self.data['likes'])} likes")

    def fetch_follows(self, limit: int = 100):
        print(f"\n[4/11] Fetching follows...")
        cursor = None
        total = 0
        while True:
            try:
                params = {"actor": self.handle, "limit": limit}
                if cursor:
                    params["cursor"] = cursor
                response = self.client.app.bsky.graph.get_follows(params=params)
                for follow in response.follows:
                    self.data["follows"].append(
                        {"did": follow.did, "handle": follow.handle, "display_name": follow.display_name,
                         "description": follow.description, "followers_count": follow.followers_count,
                         "follows_count": follow.follows_count, "posts_count": follow.posts_count,
                         "avatar": follow.avatar})
                    total += 1
                cursor = response.cursor
                if not cursor or len(response.follows) == 0:
                    break
                if total % 500 == 0:
                    print(f"  ... fetched {total} follows so far")
                    time.sleep(0.5)
            except Exception as e:
                print(f"  ✗ Error fetching follows: {e}")
                break
        print(f"  ✓ Fetched {len(self.data['follows'])} follows")

    def fetch_followers(self, limit: int = 100):
        print(f"\n[5/11] Fetching followers...")
        cursor = None
        total = 0
        while True:
            try:
                params = {"actor": self.handle, "limit": limit}
                if cursor:
                    params["cursor"] = cursor
                response = self.client.app.bsky.graph.get_followers(params=params)
                for follower in response.followers:
                    self.data["followers"].append(
                        {"did": follower.did, "handle": follower.handle, "display_name": follower.display_name,
                         "description": follower.description, "followers_count": follower.followers_count,
                         "follows_count": follower.follows_count, "posts_count": follower.posts_count,
                         "avatar": follower.avatar})
                    total += 1
                cursor = response.cursor
                if not cursor or len(response.followers) == 0:
                    break
                if total % 500 == 0:
                    print(f"  ... fetched {total} followers so far")
                    time.sleep(0.5)
            except Exception as e:
                print(f"  ✗ Error fetching followers: {e}")
                break
        print(f"  ✓ Fetched {len(self.data['followers'])} followers")

    def fetch_blocks(self, limit: int = 100):
        print(f"\n[6/11] Fetching blocks...")
        cursor = None
        total = 0
        while True:
            try:
                params = {"limit": limit}
                if cursor:
                    params["cursor"] = cursor
                response = self.client.app.bsky.graph.get_blocks(params=params)
                for block in response.blocks:
                    self.data["blocks"].append(
                        {"did": block.did, "handle": block.handle, "display_name": block.display_name,
                         "description": block.description})
                    total += 1
                cursor = response.cursor
                if not cursor or len(response.blocks) == 0:
                    break
                if total % 500 == 0:
                    print(f"  ... fetched {total} blocks so far")
                    time.sleep(0.5)
            except Exception as e:
                print(f"  ✗ Error fetching blocks: {e}")
                break
        print(f"  ✓ Fetched {len(self.data['blocks'])} blocks")

    def fetch_mutes(self, limit: int = 100):
        print(f"\n[7/11] Fetching mutes...")
        cursor = None
        total = 0
        while True:
            try:
                params = {"limit": limit}
                if cursor:
                    params["cursor"] = cursor
                response = self.client.app.bsky.graph.get_mutes(params=params)
                for mute in response.mutes:
                    self.data["mutes"].append(
                        {"did": mute.did, "handle": mute.handle, "display_name": mute.display_name,
                         "description": mute.description})
                    total += 1
                cursor = response.cursor
                if not cursor or len(response.mutes) == 0:
                    break
                if total % 500 == 0:
                    print(f"  ... fetched {total} mutes so far")
                    time.sleep(0.5)
            except Exception as e:
                print(f"  ✗ Error fetching mutes: {e}")
                break
        print(f"  ✓ Fetched {len(self.data['mutes'])} mutes")

    def fetch_lists(self, limit: int = 100):
        print(f"\n[8/11] Fetching lists...")
        cursor = None
        total = 0
        while True:
            try:
                params = {"actor": self.handle, "limit": limit}
                if cursor:
                    params["cursor"] = cursor
                response = self.client.app.bsky.graph.get_lists(params=params)
                for lst in response.lists:
                    self.data["lists"].append({"uri": lst.uri, "cid": lst.cid, "name": lst.name, "purpose": lst.purpose,
                                               "description": lst.description, "avatar": lst.avatar,
                                               "creator": lst.creator.handle, "indexed_at": lst.indexed_at})
                    total += 1
                cursor = response.cursor
                if not cursor or len(response.lists) == 0:
                    break
                if total % 100 == 0:
                    print(f"  ... fetched {total} lists so far")
                    time.sleep(0.5)
            except Exception as e:
                print(f"  ✗ Error fetching lists: {e}")
                break
        print(f"  ✓ Fetched {len(self.data['lists'])} lists")

    def fetch_feeds(self, limit: int = 100):
        print(f"\n[9/11] Fetching feeds...")
        cursor = None
        total = 0
        while True:
            try:
                params = {"actor": self.handle, "limit": limit}
                if cursor:
                    params["cursor"] = cursor
                response = self.client.app.bsky.feed.get_actor_feeds(params=params)
                for feed in response.feeds:
                    self.data["feeds"].append({"uri": feed.uri, "cid": feed.cid, "display_name": feed.display_name,
                                               "description": feed.description, "avatar": feed.avatar,
                                               "creator": feed.creator.handle, "like_count": feed.like_count,
                                               "indexed_at": feed.indexed_at})
                    total += 1
                cursor = response.cursor
                if not cursor or len(response.feeds) == 0:
                    break
                if total % 100 == 0:
                    print(f"  ... fetched {total} feeds so far")
                    time.sleep(0.5)
            except Exception as e:
                print(f"  ✗ Error fetching feeds: {e}")
                break
        print(f"  ✓ Fetched {len(self.data['feeds'])} feeds")

    def fetch_starter_packs(self, limit: int = 100):
        print(f"\n[10/11] Fetching starter packs...")
        cursor = None
        total = 0
        while True:
            try:
                params = {"actor": self.handle, "limit": limit}
                if cursor:
                    params["cursor"] = cursor
                response = self.client.app.bsky.graph.get_actor_starter_packs(params=params)
                for pack in response.starter_packs:
                    self.data["starter_packs"].append({"uri": pack.uri, "cid": pack.cid, "name": pack.record.name,
                                                       "description": pack.record.description if hasattr(pack.record,
                                                                                                         "description") else "",
                                                       "creator": pack.creator.handle, "indexed_at": pack.indexed_at})
                    total += 1
                cursor = response.cursor
                if not cursor or len(response.starter_packs) == 0:
                    break
                if total % 100 == 0:
                    print(f"  ... fetched {total} starter packs so far")
                    time.sleep(0.5)
            except Exception as e:
                print(f"  ✗ Error fetching starter packs: {e}")
                break
        print(f"  ✓ Fetched {len(self.data['starter_packs'])} starter packs")

    def fetch_bookmarks(self):
        print(f"\n[11/11] Fetching bookmarks...")
        try:
            response = self.client.app.bsky.actor.get_preferences()
            if hasattr(response, "preferences") and response.preferences:
                for pref in response.preferences:
                    if hasattr(pref, "savedFeeds") and pref.savedFeeds:
                        for feed in pref.savedFeeds:
                            self.data["bookmarks"].append({"id": feed.id if hasattr(feed, "id") else "",
                                                           "type": feed.type if hasattr(feed, "type") else "",
                                                           "value": feed.value if hasattr(feed, "value") else "",
                                                           "pinned": feed.pinned if hasattr(feed, "pinned") else False})
        except Exception as e:
            print(f"  ✗ Error fetching bookmarks: {e}")
        print(f"  ✓ Fetched {len(self.data['bookmarks'])} bookmarks")

    def export(self, output_dir: str) -> str:
        os.makedirs(output_dir, mode=0o777, exist_ok=True)
        self.media_dir = os.path.join(output_dir, "media")
        os.makedirs(self.media_dir, exist_ok=True)
        os.makedirs(os.path.join(self.media_dir, "posts"), exist_ok=True)
        os.makedirs(os.path.join(self.media_dir, "likes"), exist_ok=True)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        base_name = self.handle.replace(".", "_")
        data_file = os.path.join(output_dir, f"{base_name}_export_{timestamp}.json")

        # Save main data file
        with open(data_file, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False, default=str)

        # Save individual category files
        categories = ["profile", "posts", "reposts", "likes", "follows", "followers", "blocks", "mutes", "lists",
                      "feeds", "starter_packs", "bookmarks"]
        for category in categories:
            if self.data.get(category):
                cat_file = os.path.join(output_dir, f"{base_name}_{category}_{timestamp}.json")
                with open(cat_file, "w", encoding="utf-8") as f:
                    json.dump(self.data[category], f, indent=2, ensure_ascii=False, default=str)

        # Save media manifest
        manifest = {"total_media_files": len(self.downloaded_media), "media_directory": self.media_dir,
                    "files": sorted(list(self.downloaded_media))}
        manifest_file = os.path.join(output_dir, f"{base_name}_media_manifest_{timestamp}.json")
        with open(manifest_file, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False, default=str)

        # Create ZIP archive
        zip_file = os.path.join(output_dir, f"{base_name}_complete_export_{timestamp}.zip")
        with zipfile.ZipFile(zip_file, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(data_file, os.path.basename(data_file))
            for root, dirs, files in os.walk(self.media_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, output_dir)
                    zf.write(file_path, arcname)

        print(f"\n{'=' * 60}")
        print(f"EXPORT COMPLETE")
        print(f"{'=' * 60}")
        print(f"Data file: {data_file}")
        print(f"ZIP archive: {zip_file}")
        print(f"Media directory: {self.media_dir}")
        print(f"Total media files: {len(self.downloaded_media)}")
        print(f"\nData counts:")
        print(f"  Posts: {len(self.data['posts'])}")
        print(f"  Reposts: {len(self.data['reposts'])}")
        print(f"  Likes: {len(self.data['likes'])}")
        print(f"  Follows: {len(self.data['follows'])}")
        print(f"  Followers: {len(self.data['followers'])}")
        print(f"  Blocks: {len(self.data['blocks'])}")
        print(f"  Mutes: {len(self.data['mutes'])}")
        print(f"  Lists: {len(self.data['lists'])}")
        print(f"  Feeds: {len(self.data['feeds'])}")
        print(f"  Starter packs: {len(self.data['starter_packs'])}")
        print(f"  Bookmarks: {len(self.data['bookmarks'])}")
        print(f"{'=' * 60}")

        return data_file


# ═══════════════════════════════════════════════════════════════
# IMPORTER CLASS
# ═══════════════════════════════════════════════════════════════

class BlueskyImporter:
    def __init__(self, handle: str, password: str):
        self.client = Client()
        self.handle = handle
        self.password = password
        self.profile = None
        self.media_dir = None
        self.import_stats = {
            "posts_created": 0, "posts_skipped": 0, "posts_with_media": 0,
            "media_uploaded": 0, "media_failed": 0,
            "likes_created": 0, "likes_skipped": 0,
            "follows_created": 0, "follows_skipped": 0,
            "blocks_created": 0, "blocks_skipped": 0,
            "mutes_created": 0, "mutes_skipped": 0,
            "lists_created": 0, "feeds_saved": 0,
            "errors": [],
        }

    def login(self) -> bool:
        try:
            self.profile = self.client.login(self.handle, self.password)
            print(f"✓ Logged in as: {self.profile.display_name or self.profile.handle}")
            print(f"  DID: {self.profile.did}")
            return True
        except Exception as e:
            print(f"✗ Login failed: {e}")
            return False

    def load_export_data(self, export_file: str) -> Dict:
        print(f"\nLoading export data from: {export_file}")
        try:
            with open(export_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            export_dir = os.path.dirname(os.path.abspath(export_file))
            self.media_dir = os.path.join(export_dir, "media")
            if os.path.exists(self.media_dir):
                print(f"  ✓ Media directory found: {self.media_dir}")
            else:
                print(f"  ⚠ Media directory not found: {self.media_dir}")
            if "export_metadata" in data:
                print(f"  ✓ Loaded export from {data['export_metadata'].get('account', 'unknown')}")
                return data
            else:
                print(f"  ✓ Loaded individual category file")
                return {"data": data}
        except Exception as e:
            print(f"  ✗ Error loading export: {e}")
            return {}

    def _resolve_media_path(self, local_path: str) -> Optional[str]:
        if not local_path:
            return None
        if os.path.isabs(local_path) and os.path.exists(local_path):
            return local_path
        if self.media_dir:
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
        try:
            with open(image_path, "rb") as f:
                image_data = f.read()
            mime_type, _ = mimetypes.guess_type(image_path)
            if not mime_type:
                mime_type = "image/jpeg"
            upload_response = self.client.upload_blob(image_data, content_type=mime_type)
            return models.AppBskyEmbedImages.Image(alt=alt_text[:3000], image=upload_response.blob)
        except Exception as e:
            print(f"    ✗ Failed to upload image {image_path}: {e}")
            self.import_stats["media_failed"] += 1
            return None

    def _upload_video(self, video_path: str, alt_text: str = "") -> Optional[Dict]:
        try:
            file_size = os.path.getsize(video_path)
            if file_size > 50 * 1024 * 1024:
                print(f"    ⚠ Video too large ({file_size / 1024 / 1024:.1f}MB), skipping: {video_path}")
                self.import_stats["media_failed"] += 1
                return None
            with open(video_path, "rb") as f:
                video_data = f.read()
            mime_type, _ = mimetypes.guess_type(video_path)
            if not mime_type:
                mime_type = "video/mp4"
            upload_response = self.client.upload_blob(video_data, content_type=mime_type)
            return {"blob": upload_response.blob, "alt": alt_text[:3000], "mime_type": mime_type}
        except Exception as e:
            print(f"    ✗ Failed to upload video {video_path}: {e}")
            self.import_stats["media_failed"] += 1
            return None

    def _process_media_for_post(self, post_data: Dict) -> Tuple[Optional[Any], Optional[Any]]:
        media_items = post_data.get("media", [])
        local_paths = post_data.get("media_local_paths", [])
        if not media_items or not self.media_dir:
            return None, None
        images = []
        videos = []
        for i, item in enumerate(media_items):
            media_type = item.get("type", "")
            alt_text = item.get("alt", "")
            local_path = None
            if i < len(local_paths):
                local_path = self._resolve_media_path(local_paths[i])
            if not local_path:
                url = item.get("url", "")
                if url:
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
            if media_type in ("image", "thumbnail"):
                img = self._upload_image(local_path, alt_text)
                if img:
                    images.append(img)
                    self.import_stats["media_uploaded"] += 1
                    time.sleep(0.5)
            elif media_type == "video":
                vid = self._upload_video(local_path, alt_text)
                if vid:
                    videos.append(vid)
                    self.import_stats["media_uploaded"] += 1
                    time.sleep(1.0)
        if len(images) > 0 and len(videos) == 0:
            return models.AppBskyEmbedImages.Main(images=images), None
        elif len(videos) > 0 and len(images) == 0:
            video = videos[0]
            return None, models.AppBskyEmbedVideo.Main(video=video["blob"], alt=video["alt"])
        elif len(images) > 0 and len(videos) > 0:
            return models.AppBskyEmbedImages.Main(images=images), None
        return None, None

    def import_posts(self, posts: List[Dict], dry_run: bool = False, delay: float = 3.0):
        print(f"\n[1/8] Importing posts ({len(posts)} found)...")
        for i, post in enumerate(posts):
            if post.get("is_repost"):
                self.import_stats["posts_skipped"] += 1
                continue
            text = post.get("text", "")
            if not text.strip():
                self.import_stats["posts_skipped"] += 1
                continue
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
                image_embed = None
                video_embed = None
                has_media = False
                if post.get("media") and self.media_dir:
                    print(f"  Processing media for post {i + 1}...")
                    image_embed, video_embed = self._process_media_for_post(post)
                    if image_embed or video_embed:
                        has_media = True
                        self.import_stats["posts_with_media"] += 1
                if video_embed:
                    self.client.send_video(text=text, video=video_embed.video,
                                           video_alt=video_embed.alt if hasattr(video_embed, 'alt') else "")
                elif image_embed:
                    if len(image_embed.images) == 1:
                        self.client.send_image(text=text, image=image_embed.images[0].image,
                                               image_alt=image_embed.images[0].alt)
                    else:
                        self.client.send_post(text=text, embed=image_embed)
                else:
                    self.client.send_post(text=text)
                self.import_stats["posts_created"] += 1
                if has_media:
                    print(f"  ✓ Posted with media ({i + 1}/{len(posts)}): {text[:50]}...")
                else:
                    print(f"  ✓ Posted ({i + 1}/{len(posts)}): {text[:60]}...")
                sleep_time = delay + random.uniform(0, 2)
                if has_media:
                    sleep_time += 2
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
                response = self.client.app.bsky.graph.list.create(repo=self.profile.did, record=record)
                self.import_stats["lists_created"] += 1
                print(f"  ✓ Created list: {name}")
                time.sleep(delay)
            except Exception as e:
                error_msg = f"List '{name}': {str(e)}"
                self.import_stats["errors"].append(error_msg)
                print(f"  ✗ Error creating list: {error_msg}")
        print(f"  ✓ Lists created: {self.import_stats['lists_created']}")

    def import_feeds(self, feeds: List[Dict], dry_run: bool = False):
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
        print(f"\n[8/8] Importing profile media...")
        avatar_path = profile_data.get("avatar_local", "")
        banner_path = profile_data.get("banner_local", "")
        if dry_run:
            if avatar_path:
                print(f"  [DRY RUN] Would upload avatar: {avatar_path}")
            if banner_path:
                print(f"  [DRY RUN] Would upload banner: {banner_path}")
            return
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
        print(f"\nProfile information:")
        print(f"  Display name: {profile_data.get('display_name', '')}")
        print(f"  Description: {profile_data.get('description', '')}")
        print(f"  \n  Note: Update display name and bio manually in Bluesky settings.")

    def print_summary(self):
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


# ═══════════════════════════════════════════════════════════════
# MAIN MENU
# ═══════════════════════════════════════════════════════════════

def run_exporter(account_out: str, password_out: str, export_file: str, dry_run: bool = False):
    print("=" * 60)
    print("BLUESKY DATA EXPORTER")
    print("=" * 60)

    handle = account_out
    password = password_out
    output_dir = export_file
    dryrun = dry_run

    if not handle or not password:
        handle = input("\nEnter SOURCE Bluesky handle (e.g., username.bsky.social): ").strip()
        password = input("Enter SOURCE Bluesky app password: ").strip()
        if not handle or not password:
            print("Error: Handle and password are required.")
            return

    if output_dir == "":
        output_dir = input("Output directory for export (default: bluesky_export): ").strip() or "bluesky_export"

    exporter = BlueskyExporter(handle, password)
    if not exporter.login():
        return

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
    exporter.fetch_bookmarks()

    data_file = exporter.export(output_dir)
    print(f"\nExport saved to: {data_file}")
    return data_file


def run_importer(account_in, password_in, export_file, dry_run):
    print("=" * 60)
    print("BLUESKY DATA IMPORTER")
    print("=" * 60)
    print("\nWARNING: This will create posts, likes, follows, blocks,")
    print("mutes, and upload media on the target account.")
    print("Use dry-run first!")
    print("=" * 60)

    if (account_in != ""):
        handle = account_in
    else:
        handle = input("\nEnter TARGET Bluesky handle (e.g., newaccount.bsky.social): ").strip()
    if (password_in != ""):
        password = password_in
    else:
        password = input("Enter TARGET Bluesky app password: ").strip()
    if not handle or not password:
        print("Error: Handle and password are required.")
        return
    if (export_file == ""):
        export_file = input(
            "\nPath to exportedy data file (e.g., bluesky_export/username_export_20260101_120000.json): ").strip()
    if not export_file or not os.path.exists(export_file):
        print(f"Error: File not found: {export_file}")
        return

    if dry_run == "":
        dry_run = dry_run
    else:
        dry_run_input = input("\nRun in DRY RUN mode first? (recommended) [Y/n]: ").strip().lower()
        dry_run = dry_run_input in ("", "y", "yes")

    if dry_run:
        print("\n*** DRY RUN MODE - No changes will be made ***")
    else:
        confirm = input("\nAre you sure you want to import data? This cannot be undone! [yes/no]: ").strip().lower()
        if confirm != "yes":
            print("Import cancelled.")
            return

    importer = BlueskyImporter(handle, password)
    # if not importer.login():
    #     return

    data = importer.load_export_data(export_file)
    # if not data:
    #     return

    importer.import_posts(data.get("posts", []), dry_run=dry_run)
    importer.import_likes(data.get("likes", []), dry_run=dry_run)
    importer.import_follows(data.get("follows", []), dry_run=dry_run)
    importer.import_blocks(data.get("blocks", []), dry_run=dry_run)
    importer.import_mutes(data.get("mutes", []), dry_run=dry_run)
    importer.import_lists(data.get("lists", []), dry_run=dry_run)
    importer.import_feeds(data.get("feeds", []), dry_run=dry_run)
    importer.import_profile_media(data.get("profile", {}), dry_run=dry_run)
    importer.import_profile_info(data.get("profile", {}), dry_run=dry_run)

    importer.print_summary()

    if dry_run:
        print("\nDry run complete. Run again with 'n' to perform actual import.")


def main():
    parser = argparse.ArgumentParser(
        description="Move data from one account to another in BlueSky"
    )
    parser.add_argument("-i", "--account_in", help="Input account")
    parser.add_argument("-p", "--password_in", help="Password for input account")
    parser.add_argument("-o", "--account_out", help="Output account")
    parser.add_argument("-t", "--password_out", help="Password for input account")
    parser.add_argument("-x", "--export_dir", help="Export directory")
    parser.add_argument("-d", "--dry_run", help="Dry run mode", action="store_true")
    args = parser.parse_args()

    account_in = args.account_in
    password_in = args.password_in
    account_out = args.account_out
    password_out = args.password_out
    export_dir = args.export_dir
    dry_run = args.dry_run

    print("=" * 60)
    print("BLUESKY COMPLETE MIGRATION TOOL")
    print("=" * 60)
    print("\n1. Export data from a Bluesky account")
    print("2. Import data into a Bluesky account")

    print("Entering run_exporter")
    datafile = run_exporter(account_in, password_in, export_dir, dry_run)
    print("Entering run_importer")
    run_importer(account_out, password_out, datafile, dry_run)
    print("Goodbye!")


if __name__ == "__main__":
    main()
