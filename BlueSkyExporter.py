import json
import os
import time
from datetime import datetime, timezone

try:
    from atproto import Client, models
except ImportError:
    print("Installing required package: atproto...")
    import subprocess

    subprocess.check_call(["pip", "install", "atproto", "-q"])
    from atproto import Client, models


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
            "conversations": [],
            "export_metadata": {
                "exported_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "account": handle,
            },
        }
        self.media_dir = "Media"
        self.downloaded_media = set()  # Track downloaded files to avoid duplicates

    def login(self) -> bool:
        """Authenticate with Bluesky."""
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

    def fetch_profile(self):
        """Fetch detailed profile information."""
        print("\n[1/10] Fetching profile...")
        try:
            profile = self.client.app.bsky.actor.get_profile(
                params={"actor": self.handle}
            )
            self.data["profile"] = {
                "did": profile.did,
                "handle": profile.handle,
                "display_name": profile.display_name,
                "description": profile.description,
                "avatar": profile.avatar,
                "banner": profile.banner,
                "followers_count": profile.followers_count,
                "follows_count": profile.follows_count,
                "posts_count": profile.posts_count,
                "created_at": profile.created_at,
                "indexed_at": profile.indexed_at,
                "viewer": {
                    "muted": profile.viewer.muted if profile.viewer else None,
                    "blocked_by": profile.viewer.blocked_by if profile.viewer else None,
                    "following": str(
                        profile.viewer.following) if profile.viewer and profile.viewer.following else None,
                    "followed_by": str(
                        profile.viewer.followed_by) if profile.viewer and profile.viewer.followed_by else None,
                },
            }
            print(f"  ✓ Profile fetched")
        except Exception as e:
            print(f"  ✗ Error: {e}")

    def _extract_media_urls(self, record: Any) -> List[Dict[str, str]]:
        """Extract all media URLs from a post record."""
        media_items = []

        if not hasattr(record, "embed"):
            return media_items

        embed = record.embed

        # Images (app.bsky.embed.images)
        if hasattr(embed, "images") and embed.images:
            for img in embed.images:
                if hasattr(img, "image") and hasattr(img.image, "ref"):
                    # Construct full image URL from blob reference
                    cid = img.image.ref.link if hasattr(img.image.ref, "link") else str(img.image.ref)
                    url = f"https://cdn.bsky.app/img/feed_fullsize/plain/{self.profile.did}/{cid}@jpeg"
                    media_items.append({
                        "url": url,
                        "type": "image",
                        "alt": img.alt if hasattr(img, "alt") else "",
                        "mime_type": img.image.mime_type if hasattr(img.image, "mime_type") else "image/jpeg",
                    })
                # Also check for direct URL
                if hasattr(img, "fullsize") and img.fullsize:
                    media_items.append({
                        "url": img.fullsize,
                        "type": "image",
                        "alt": img.alt if hasattr(img, "alt") else "",
                        "mime_type": "image/jpeg",
                    })

        # Video (app.bsky.embed.video)
        if hasattr(embed, "video") and embed.video:
            video = embed.video
            if hasattr(video, "ref") and video.ref:
                cid = video.ref.link if hasattr(video.ref, "link") else str(video.ref)
                url = f"https://video.bsky.app/watch/{self.profile.did}/{cid}"
                media_items.append({
                    "url": url,
                    "type": "video",
                    "alt": video.alt if hasattr(video, "alt") else "",
                    "mime_type": video.mime_type if hasattr(video, "mime_type") else "video/mp4",
                })
            # Check for playlist URL
            if hasattr(video, "playlist") and video.playlist:
                media_items.append({
                    "url": video.playlist,
                    "type": "video",
                    "alt": video.alt if hasattr(video, "alt") else "",
                    "mime_type": "application/x-mpegURL",
                })

        # External links with thumbnails (app.bsky.embed.external)
        if hasattr(embed, "external") and embed.external:
            ext = embed.external
            if hasattr(ext, "thumb") and ext.thumb:
                thumb_url = str(ext.thumb) if hasattr(ext.thumb, "uri") else ext.thumb
                if thumb_url.startswith("http"):
                    media_items.append({
                        "url": thumb_url,
                        "type": "thumbnail",
                        "alt": ext.title if hasattr(ext, "title") else "",
                        "mime_type": "image/jpeg",
                    })

        # Record with media (quote posts, etc.)
        if hasattr(embed, "record") and embed.record:
            if hasattr(embed.record, "embeds") and embed.record.embeds:
                for sub_embed in embed.record.embeds:
                    if hasattr(sub_embed, "images") and sub_embed.images:
                        for img in sub_embed.images:
                            if hasattr(img, "fullsize") and img.fullsize:
                                media_items.append({
                                    "url": img.fullsize,
                                    "type": "image",
                                    "alt": img.alt if hasattr(img, "alt") else "",
                                    "mime_type": "image/jpeg",
                                })

        return media_items

    def _download_media(self, media_items: List[Dict[str, str]], subfolder: str = "posts") -> List[str]:
        """Download media files and return local paths."""
        if not media_items or not self.media_dir:
            return []

        downloaded = []
        for item in media_items:
            url = item.get("url", "")
            if not url or not url.startswith("http"):
                continue

            # Create unique filename from URL hash
            url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
            ext = self._get_extension(item.get("mime_type", ""), item.get("type", ""))
            filename = f"{url_hash}{ext}"
            filepath = os.path.join(self.media_dir, subfolder, filename)

            # Skip if already downloaded
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
                time.sleep(0.2)  # Rate limiting for media downloads

            except Exception as e:
                print(f"    ✗ Failed to download {url[:60]}...: {e}")

        return downloaded

    def _get_extension(self, mime_type: str, media_type: str) -> str:
        """Get file extension from MIME type or media type."""
        mime_map = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/gif": ".gif",
            "image/webp": ".webp",
            "video/mp4": ".mp4",
            "video/webm": ".webm",
            "application/x-mpegURL": ".m3u8",
        }
        if mime_type in mime_map:
            return mime_map[mime_type]
        if media_type == "image":
            return ".jpg"
        if media_type == "video":
            return ".mp4"
        return ".bin"

    def _get_avatar_banner(self, profile_data: Any) -> Dict[str, str]:
        """Extract avatar and banner URLs from profile."""
        urls = {}
        if hasattr(profile_data, "avatar") and profile_data.avatar:
            urls["avatar"] = str(profile_data.avatar)
        if hasattr(profile_data, "banner") and profile_data.banner:
            urls["banner"] = str(profile_data.banner)
        return urls

    def fetch_posts(self, limit: int = 100):
        """Fetch all posts (including replies and reposts)."""
        print(f"\n[2/10] Fetching posts (batch size: {limit})...")
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
                    post_data = {
                        "uri": post.uri,
                        "cid": post.cid,
                        "text": post.record.text if hasattr(post.record, "text") else "",
                        "created_at": post.record.created_at if hasattr(post.record, "created_at") else None,
                        "reply_count": post.reply_count,
                        "repost_count": post.repost_count,
                        "like_count": post.like_count,
                        "quote_count": post.quote_count,
                        "indexed_at": post.indexed_at,
                        "is_reply": hasattr(post.record, "reply") and post.record.reply is not None,
                        "is_repost": feed_view.reason is not None and hasattr(feed_view.reason, "by"),
                        "langs": post.record.langs if hasattr(post.record, "langs") else [],
                        "labels": [l.val for l in post.labels] if post.labels else [],
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
                    print(f"  ... fetched {total} posts so far")
                    time.sleep(0.5)  # Rate limiting

            except Exception as e:
                print(f"  ✗ Error fetching posts: {e}")
                break

        print(f"  ✓ Fetched {len(self.data['posts'])} original posts, {len(self.data['reposts'])} reposts")

    def fetch_likes(self, limit: int = 100):
        """Fetch all liked posts."""
        print(f"\n[3/10] Fetching likes...")
        cursor = None
        total = 0

        while True:
            try:
                params = {"actor": self.handle, "limit": limit}
                if cursor:
                    params["cursor"] = cursor

                response = self.client.app.bsky.feed.get_actor_likes(params=params)

                for liked in response.feed:
                    like_data = {
                        "post_uri": liked.post.uri,
                        "post_cid": liked.post.cid,
                        "post_text": liked.post.record.text if hasattr(liked.post.record, "text") else "",
                        "post_author": liked.post.author.handle,
                        "post_created_at": liked.post.record.created_at if hasattr(liked.post.record,
                                                                                   "created_at") else None,
                        "liked_at": liked.post.indexed_at,
                    }
                    self.data["likes"].append(like_data)
                    total += 1

                cursor = response.cursor
                if not cursor or len(response.feed) == 0:
                    break

                if total % 500 == 0:
                    print(f"  ... fetched {total} likes so far")
                    time.sleep(0.5)

            except Exception as e:
                print(f"  ✗ Error fetching likes: {e}")
                break

        print(f"  ✓ Fetched {len(self.data['likes'])} likes")

    def fetch_follows(self, limit: int = 100):
        """Fetch accounts being followed."""
        print(f"\n[4/10] Fetching follows...")
        cursor = None
        total = 0

        while True:
            try:
                params = {"actor": self.handle, "limit": limit}
                if cursor:
                    params["cursor"] = cursor

                response = self.client.app.bsky.graph.get_follows(params=params)

                for follow in response.follows:
                    follow_data = {
                        "did": follow.did,
                        "handle": follow.handle,
                        "display_name": follow.display_name,
                        "description": follow.description,
                        "followers_count": follow.followers_count,
                        "follows_count": follow.follows_count,
                        "posts_count": follow.posts_count,
                        "avatar": follow.avatar,
                    }
                    self.data["follows"].append(follow_data)
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
        """Fetch followers."""
        print(f"\n[5/10] Fetching followers...")
        cursor = None
        total = 0

        while True:
            try:
                params = {"actor": self.handle, "limit": limit}
                if cursor:
                    params["cursor"] = cursor

                response = self.client.app.bsky.graph.get_followers(params=params)

                for follower in response.followers:
                    follower_data = {
                        "did": follower.did,
                        "handle": follower.handle,
                        "display_name": follower.display_name,
                        "description": follower.description,
                        "followers_count": follower.followers_count,
                        "follows_count": follower.follows_count,
                        "posts_count": follower.posts_count,
                        "avatar": follower.avatar,
                    }
                    self.data["followers"].append(follower_data)
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
        """Fetch blocked accounts."""
        print(f"\n[6/10] Fetching blocks...")
        cursor = None
        total = 0

        while True:
            try:
                params = {"limit": limit}
                if cursor:
                    params["cursor"] = cursor

                response = self.client.app.bsky.graph.get_blocks(params=params)

                for block in response.blocks:
                    block_data = {
                        "did": block.did,
                        "handle": block.handle,
                        "display_name": block.display_name,
                        "description": block.description,
                    }
                    self.data["blocks"].append(block_data)
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
        """Fetch muted accounts."""
        print(f"\n[7/10] Fetching mutes...")
        cursor = None
        total = 0

        while True:
            try:
                params = {"limit": limit}
                if cursor:
                    params["cursor"] = cursor

                response = self.client.app.bsky.graph.get_mutes(params=params)

                for mute in response.mutes:
                    mute_data = {
                        "did": mute.did,
                        "handle": mute.handle,
                        "display_name": mute.display_name,
                        "description": mute.description,
                    }
                    self.data["mutes"].append(mute_data)
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
        """Fetch moderation lists and starter packs created."""
        print(f"\n[8/10] Fetching lists...")
        cursor = None
        total = 0

        while True:
            try:
                params = {"actor": self.handle, "limit": limit}
                if cursor:
                    params["cursor"] = cursor

                response = self.client.app.bsky.graph.get_lists(params=params)

                for lst in response.lists:
                    list_data = {
                        "uri": lst.uri,
                        "cid": lst.cid,
                        "name": lst.name,
                        "purpose": lst.purpose,
                        "description": lst.description,
                        "avatar": lst.avatar,
                        "creator": lst.creator.handle,
                        "indexed_at": lst.indexed_at,
                    }
                    self.data["lists"].append(list_data)
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
        """Fetch saved/custom feeds."""
        print(f"\n[9/10] Fetching feeds...")
        cursor = None
        total = 0

        while True:
            try:
                params = {"actor": self.handle, "limit": limit}
                if cursor:
                    params["cursor"] = cursor

                response = self.client.app.bsky.feed.get_actor_feeds(params=params)

                for feed in response.feeds:
                    feed_data = {
                        "uri": feed.uri,
                        "cid": feed.cid,
                        "display_name": feed.display_name,
                        "description": feed.description,
                        "avatar": feed.avatar,
                        "creator": feed.creator.handle,
                        "like_count": feed.like_count,
                        "indexed_at": feed.indexed_at,
                    }
                    self.data["feeds"].append(feed_data)
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
        """Fetch starter packs created by the user."""
        print(f"\n[10/10] Fetching starter packs...")
        cursor = None
        total = 0

        while True:
            try:
                params = {"actor": self.handle, "limit": limit}
                if cursor:
                    params["cursor"] = cursor

                response = self.client.app.bsky.graph.get_actor_starter_packs(params=params)

                for pack in response.starter_packs:
                    pack_data = {
                        "uri": pack.uri,
                        "cid": pack.cid,
                        "name": pack.record.name,
                        "description": pack.record.description if hasattr(pack.record, "description") else "",
                        "creator": pack.creator.handle,
                        "indexed_at": pack.indexed_at,
                    }
                    self.data["starter_packs"].append(pack_data)
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

    def export(self, output_dir: str = "bluesky_export"):
        """Export all data to JSON files."""
        os.makedirs(output_dir, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        base_filename = f"{output_dir}/{self.handle.replace('.', '_')}_{timestamp}"

        # Main combined file
        combined_file = f"{base_filename}_all_data.json"
        with open(combined_file, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False, default=str)

        # Individual files for each category
        categories = [
            "profile", "posts", "reposts", "likes", "follows", "followers",
            "blocks", "mutes", "lists", "feeds", "starter_packs"
        ]

        for category in categories:
            if self.data.get(category):
                filename = f"{base_filename}_{category}.json"
                with open(base_filename, "a", encoding="utf-8") as f1:
                    json.dump(self.data[category], f1, indent=2, ensure_ascii=False, default=str)
                with open(filename, "w", encoding="utf-8") as f2:
                    json.dump(self.data[category], f2, indent=2, ensure_ascii=False, default=str)

        print(f"\n{'=' * 60}")
        print(f"EXPORT COMPLETE")
        print(f"{'=' * 60}")
        print(f"Combined file: {combined_file}")
        print(f"Total posts: {len(self.data['posts'])}")
        print(f"Combined file: {combined_file}")
        print(f"Media directory: {self.media_dir}")
        print(f"Total media files: {len(self.downloaded_media)}")
        print(f"Total reposts: {len(self.data['reposts'])}")
        print(f"Total likes: {len(self.data['likes'])}")
        print(f"Total follows: {len(self.data['follows'])}")
        print(f"Total followers: {len(self.data['followers'])}")
        print(f"Total blocks: {len(self.data['blocks'])}")
        print(f"Total mutes: {len(self.data['mutes'])}")
        print(f"Total lists: {len(self.data['lists'])}")
        print(f"Total feeds: {len(self.data['feeds'])}")
        print(f"Total starter packs: {len(self.data['starter_packs'])}")
        print(f"{'=' * 60}")

        return f"{base_filename}_all_data.json"
