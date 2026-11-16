import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs

from echoplay.services.wbi import mixin_key, sign_query
from echoplay.storage import LibraryStore


class SigningCompatibilityTests(unittest.TestCase):
    def test_playback_query_matches_pre_cleanup_signature(self):
        params = {"keyword": "hello!'()*", "page": 2}
        with patch("echoplay.services.wbi.time.time", return_value=1700000000):
            self.assertEqual(
                sign_query(params, "example-mixin-key"),
                "keyword=hello&page=2&wts=1700000000&w_rid=e0733eb7671d7bc238fe91988c01de23",
            )
            search = parse_qs(sign_query(params, "example-mixin-key", clean_values=False))
            self.assertEqual(search["keyword"], ["hello!'()*"])
            self.assertEqual(search["w_rid"], ["e0733eb7671d7bc238fe91988c01de23"])
        self.assertEqual(params, {"keyword": "hello!'()*", "page": 2})

    def test_malformed_signing_keys_are_rejected(self):
        for data in ({}, {"wbi_img": {"img_url": "x/a.png", "sub_url": "x/b.png"}}):
            with self.subTest(data=data), self.assertRaises(RuntimeError):
                mixin_key(data)


class LibraryMigrationCompatibilityTests(unittest.TestCase):
    def test_old_libraries_keep_data_and_playlist_relations(self):
        common = """bvid TEXT NOT NULL, title TEXT NOT NULL, author TEXT NOT NULL,
                    duration TEXT NOT NULL, cover_url TEXT NOT NULL, page_url TEXT NOT NULL,
                    added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP"""
        schemas = (
            common + ", PRIMARY KEY (bvid)",
            common + ", page_number INTEGER NOT NULL DEFAULT 1, part_title TEXT NOT NULL DEFAULT '',"
                     " PRIMARY KEY (bvid, page_number)",
            common + ", page_number INTEGER NOT NULL DEFAULT 1, is_standalone INTEGER NOT NULL DEFAULT 0,"
                     " PRIMARY KEY (bvid, page_number, is_standalone)",
        )
        for schema in schemas:
            with self.subTest(schema=schema), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "library.db"
                with closing(sqlite3.connect(path)) as connection:
                    connection.execute(f"CREATE TABLE library ({schema})")
                    connection.execute(
                        "INSERT INTO library (bvid, title, author, duration, cover_url, page_url) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        ("BV1234567890", "Original", "Artist", "03:00", "", "https://video.test"),
                    )
                    connection.commit()
                store = LibraryStore(path)
                item = store.list_tracks()[0]
                self.assertEqual((item.title, item.author, item.page_number), ("Original", "Artist", 1))
                playlist = store.create_playlist("Saved")
                store.add_track_to_playlist(playlist, item)
                self.assertEqual(store.list_playlist_tracks(playlist), store.list_tracks())
                store.rename_video(item.bvid, "Renamed collection")
                store.rename_library_page(item.bvid, 1, "Renamed part")
                reopened = LibraryStore(path)
                saved = reopened.list_playlist_tracks(playlist)[0]
                self.assertEqual(saved.video_title, "Renamed collection")
                self.assertEqual(saved.part_title, "Renamed part")
                self.assertTrue(saved.is_video_title_renamed)
                self.assertTrue(saved.is_part_title_renamed)
                self.assertEqual(reopened.list_tracks(), [saved])
                reopened.remove(item.bvid, 1, False)
                self.assertEqual(reopened.count_tracks(playlist), 0)
                self.assertEqual(reopened.list_playlist_tracks(playlist), [])
