import json
import re
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

import feedparser
import requests
import yaml
from dateutil import parser as dateparser

from bs4 import BeautifulSoup


CONFIG = yaml.safe_load(Path("config.yaml").read_text())
EPISODES_PATH = Path("episodes.json")
FEED_PATH = Path("feed.xml")


def clean_episode_title(raw_title: str) -> str:
    title = raw_title.strip()
    title = re.sub(r"^Amud Yomi:\s*", "", title, flags=re.I)
    title = title.replace("-", " ")
    title = re.sub(r"\s+", " ", title)
    return title.strip()


def get_audio_url(entry):
    for link in entry.get("links", []):
        if link.get("type") == "audio/mpeg":
            return link.get("href")
        href = link.get("href", "")
        if href.endswith(".mp3"):
            return href

    for key in ["link", "id"]:
        val = entry.get(key, "")
        if isinstance(val, str) and val.endswith(".mp3"):
            return val

    return None


def get_pub_date(entry):
    for key in ["published", "updated", "created"]:
        if entry.get(key):
            return dateparser.parse(entry[key]).astimezone(timezone.utc)

    return datetime.now(timezone.utc)

def get_audio_length(url):
    try:
        r = requests.head(url, allow_redirects=True, timeout=20)
        return r.headers.get("content-length", "0")
    except Exception:
        return "0"


def load_episodes():
    if not EPISODES_PATH.exists():
        return []
    return json.loads(EPISODES_PATH.read_text())


def save_episodes(episodes):
    EPISODES_PATH.write_text(
        json.dumps(episodes, indent=2, ensure_ascii=False) + "\n"
    )

def build_feed(episodes):
    now = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S %z")

    items = []
    for ep in sorted(episodes, key=lambda e: e["pub_date"], reverse=True):
        items.append(f"""
    <item>
      <title>{escape(ep["title"])}</title>
      <description>{escape(ep["description"])}</description>
      <pubDate>{ep["rss_date"]}</pubDate>
      <guid isPermaLink="false">{escape(ep["guid"])}</guid>
      <enclosure url="{escape(ep["audio_url"])}" length="{ep["length"]}" type="audio/mpeg"/>
      <link>{escape(ep["source_url"])}</link>
    </item>""")

    feed = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
  xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
  xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>{escape(CONFIG["podcast_title"])}</title>
    <link>{escape(CONFIG["site_url"])}</link>
    <description>{escape(CONFIG["podcast_description"])}</description>
    <language>{CONFIG["language"]}</language>
    <lastBuildDate>{now}</lastBuildDate>
    <itunes:author>{escape(CONFIG["podcast_author"])}</itunes:author>
    <itunes:explicit>false</itunes:explicit>
    <itunes:category text="Religion &amp; Spirituality"/>
{''.join(items)}
  </channel>
</rss>
"""
    FEED_PATH.write_text(feed)

def slug_to_title(slug: str) -> str:
    slug = slug.strip("/")
    slug = re.sub(r"^Amud-Yomi:-?", "", slug, flags=re.I)
    slug = slug.replace("-", " ")
    slug = re.sub(r"\s+", " ", slug)
    return slug.strip()


def title_to_audio_slug(title_slug: str) -> str:
    return title_slug.lower().replace(":", "").replace(" ", "-")


def discover_yutorah_episodes():
    url = CONFIG["source_teacher_page"]
    r = requests.get(url, timeout=30)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")

    found = {}

    for a in soup.find_all("a", href=True):
        href = a["href"]

        match = re.search(r"/lectures/(\d+)/([^\"?#]+)", href)
        if not match:
            continue

        shiur_id = match.group(1)
        slug = match.group(2)

        if CONFIG["required_title_text"].lower().replace(" ", "-") not in slug.lower():
            continue

        title = slug_to_title(slug)
        year = datetime.now(timezone.utc).year

        audio_slug = title_to_audio_slug(slug)
        audio_url = (
            f"https://download.yutorah.org/{year}/"
            f"{CONFIG['media_folder_id']}/{shiur_id}/{audio_slug}.mp3"
        )

        source_url = f"https://www.yutorah.org/lectures/{shiur_id}/{slug}"

        found[shiur_id] = {
            "guid": shiur_id,
            "title": title,
            "description": f"{title}. Source: YUTorah.",
            "source_url": source_url,
            "audio_url": audio_url,
            "length": "1000000",
            "pub_date": datetime.now(timezone.utc).isoformat(),
            "rss_date": datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S %z"),
        }

    return list(found.values())

def main():
    start_date = datetime.fromisoformat(CONFIG["start_date"]).replace(tzinfo=timezone.utc)

    existing = load_episodes()
    seen = {ep["guid"] for ep in existing}

    discovered = discover_yutorah_episodes()

    print("Discovered episodes:", len(discovered))

    new_count = 0

    for episode in discovered:
        if episode["guid"] in seen:
            continue

        # Since the teacher page may not expose exact upload dates,
        # we only add future/newly discovered episodes after initial setup.
        pub_date = dateparser.parse(episode["pub_date"]).astimezone(timezone.utc)
        if pub_date < start_date:
            continue

        existing.append(episode)
        seen.add(episode["guid"])
        new_count += 1

    save_episodes(existing)
    build_feed(existing)

    print(f"Added {new_count} new episode(s). Total: {len(existing)}")
if __name__ == "__main__":
    main()
