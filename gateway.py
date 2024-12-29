#!/usr/bin/python3 

import traceback
import feedparser
import requests
import base64
import markdown
from datetime import datetime
import html
import logging
from typing import Dict, Optional, List
import time
import json
import sqlite3
import os
import re

TAG_PREFIX = os.getenv("PINBOARD_TAG_PREFIX")
WORDPRESS_URL = os.getenv("WORDPRESS_URL")
USERNAME = os.getenv("USERNAME")
APP_PASSWORD = os.getenv("APP_PASSWORD")
RSS_FEED_URL = os.getenv("RSS_FEED_URL")
DB_PATH = os.getenv("DB_PATH")

NO_POST=False
# NO_POST=True # for markdown debugging

class WordPressRSSPublisher:
    def __init__(self, wordpress_url: str, username: str, application_password: str, db_path: str = "rss_state.db"):
        """Initialize the WordPress RSS publisher."""
        # Set up basic configuration
        self.wordpress_url = wordpress_url.rstrip('/')
        self.username = username
        self.application_password = application_password
        self.api_base = f"{self.wordpress_url}/wp-json/wp/v2"
        self.db_path = db_path
        
        # Setup logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger(__name__)
        
        # Set up authentication headers
        self.auth_header = self.create_auth_header(username, application_password)
        
        # Initialize database and verify auth
        self._init_database()
        self._verify_auth()

    def create_auth_header(self, username: str, password: str) -> Dict[str, str]:
        """Create the authentication header for WordPress API requests."""
        credentials = f"{username}:{password}"
        token = base64.b64encode(credentials.encode()).decode()
        return {
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json"
        }

    def _init_database(self):
        """Initialize SQLite database and create necessary tables."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS published_items (
                        link TEXT PRIMARY KEY,
                        title TEXT,
                        published_date TEXT,
                        wordpress_post_id INTEGER,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                conn.commit()
                self.logger.info("Database initialized successfully")
        except sqlite3.Error as e:
            self.logger.error(f"Database initialization error: {str(e)}")
            raise

    def _verify_auth(self):
        """Verify authentication credentials by making a test API call."""
        try:
            response = requests.get(
                f"{self.api_base}/users/me",
                headers=self.auth_header
            )
            
            if response.status_code == 401:
                self.logger.error("Authentication failed. Please check your credentials.")
                self.logger.error(f"Response: {response.text}")
                raise Exception("Authentication failed")
                
            response.raise_for_status()
            self.logger.info("Authentication successful!")
            
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Error verifying authentication: {str(e)}")
            if hasattr(e, 'response') and e.response is not None:
                self.logger.error(f"Response: {e.response.text}")
            raise

    def _is_item_published(self, link: str) -> bool:
        """Check if an item has already been published."""
        if NO_POST:
            return False # always process them for testing

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT 1 FROM published_items WHERE link = ?", (link,))
                return cursor.fetchone() is not None
        except sqlite3.Error as e:
            self.logger.error(f"Database query error: {str(e)}")
            return False

    def _record_published_item(self, link: str, title: str, published_date: str, wordpress_post_id: int):
        """Record a published item in the database."""
        if NO_POST:
            return

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO published_items (link, title, published_date, wordpress_post_id)
                    VALUES (?, ?, ?, ?)
                """, (link, title, published_date, wordpress_post_id))
                conn.commit()
                self.logger.info(f"Recorded published item: {title}")
        except sqlite3.Error as e:
            self.logger.error(f"Error recording published item: {str(e)}")

    def _clean_content(self, content: str) -> str:
        """Clean and prepare content for WordPress."""
        return html.unescape(content)

    def fetch_rss_feed(self, feed_url: str) -> Optional[feedparser.FeedParserDict]:
        """Fetch and parse the RSS feed."""
        try:
            feed = feedparser.parse(feed_url)
            if feed.bozo:
                self.logger.error(f"Error parsing feed: {feed.bozo_exception}")
                return None
            return feed
        except Exception as e:
            self.logger.error(f"Error fetching feed: {str(e)}")
            return None

    def _extract_tags_from_rss(self, entry) -> List[str]:
        """Extract space-separated tags from the 'term' field in taxo_topics."""
        tags = []
        try:
            tagstr = entry.get('tags', [])
            
            # Ensure tagstr is a list of dictionaries
            if isinstance(tagstr, list):
                for topic in tagstr:
                    # Extract the 'term' field and split by spaces
                    if isinstance(topic, dict) and 'term' in topic:
                        tags.extend(topic['term'].split())
        except Exception as e:
            logging.warning(f"Error extracting tags: {str(e)}")

        return tags

    def create_post_dict(self, title: str, content: str, link: str, tags: List[str], status: str = "draft") -> Dict:
        """Mark up a new WordPress post."""
        # stolen from https://github.com/r0wb0t/markdown-urlize
        urlfinder = re.compile(r'((([A-Za-z]{3,9}:(?:\/\/)?)(?:[\-;:&=\+\$,\w]+@)?[A-Za-z0-9\.\-]+(:[0-9]+)?|'
                    r'(?:www\.|[\-;:&=\+\$,\w]+@)[A-Za-z0-9\.\-]+)((?:/[\+~%/\.\w\-_]*)?\??'
                        r'(?:[\-\+=&;%@\.\w_]*)#?(?:[\.!/\\\w]*))?)')

        content = urlfinder.sub(r'<\1>', content)

        # ensure blockquote tags (used for quote blocks) will have markdown processing enabled inside them
        content = re.sub(r'<blockquote>', '<blockquote markdown="1">', content)
        
        if NO_POST:
            print(content)

        content = markdown.markdown(content, extensions=['extra', 'sane_lists', 'md_in_html'])

        tag_htmls = []
        for tag in tags:
            tag_htmls.append(f"<a class=\"delicioustag\" href=\"{TAG_PREFIX}/t:{tag}\">{tag}</a>")

        tag_html = " ".join(tag_htmls)
        
        # Add source link to content
        content_with_source = f"<ul><li><p>\n" \
                f"<a class=\"deliciouslink\" href=\"{link}\" title=\"{title}\">{title}</a></p>" \
                f"\n\n{content}\n\n<p class=\"taglist\">Tags: {tag_html}</p></li></ul>"
        
        return {
            "title": title,
            "content": content_with_source,
            "status": status
        }

    def create_post(self, title: str, content: str, link: str, tags: List[str], status: str = "draft") -> Optional[Dict]:
        """Create a new WordPress post with tags."""
        data = self.create_post_dict(title, content, link, tags, status)
        endpoint = f"{self.api_base}/posts"
        
        if NO_POST:
            print(data)
            return None

        try:
            response = requests.post(
                endpoint,
                headers=self.auth_header,
                data=json.dumps(data)
            )
            
            if response.status_code == 401:
                self.logger.error("Authentication failed when creating post.")
                self.logger.error(f"Response: {response.text}")
                return None
                
            response.raise_for_status()
            return response.json()
            
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Error creating post: {str(e)}")
            if hasattr(e, 'response') and e.response is not None:
                self.logger.error(f"Response: {e.response.text}")
            return None

    def post_feed_items(self, feed_url: str, post_status: str = "draft"):
        """Post all items from an RSS feed to WordPress."""
        feed = self.fetch_rss_feed(feed_url)
        if not feed:
            return

        for entry in reversed(feed.entries):
            link = entry.get('link', '')
            
            # Skip if already published
            if self._is_item_published(link):
                # self.logger.info(f"Skipping already published item: {entry.get('title', '')}")
                continue
                
            title = entry.get('title', '')
            content = entry.get('description', '') or entry.get('summary', '')
            published_date = entry.get('published', datetime.now().isoformat())
            
            # Extract tags from the entry
            tags = self._extract_tags_from_rss(entry)
            self.logger.info(f"Extracted tags for '{title}': {tags}")
            
            # Clean the content
            content = self._clean_content(content)
            
            self.logger.info(f"Creating post: {title}")
            result = self.create_post(title, content, link, tags, post_status)
            
            if result:
                self.logger.info(f"Successfully created post: {title}")
                self._record_published_item(
                    link=link,
                    title=title,
                    published_date=published_date,
                    wordpress_post_id=result['id']
                )
            else:
                self.logger.error(f"Failed to create post: {title}")

def main():
    try:
        publisher = WordPressRSSPublisher(
            wordpress_url=WORDPRESS_URL,
            username=USERNAME,
            application_password=APP_PASSWORD,
            db_path=DB_PATH
        )
        
        publisher.post_feed_items(
            feed_url=RSS_FEED_URL,
            post_status="publish" # or "draft"
        )
        
    except Exception as e:
        print(traceback.format_exc())
        logging.error(f"Failed to initialize publisher: {str(e)}")

if __name__ == "__main__":
    main()

# vim: set expandtab:tw=4:
