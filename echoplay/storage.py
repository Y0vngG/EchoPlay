import json
from dataclasses import asdict
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
import sqlite3

from echoplay.models import Track


_LIBRARY_SCHEMA = """CREATE TABLE library (
    bvid TEXT NOT NULL,
    page_number INTEGER NOT NULL DEFAULT 1,
    title TEXT NOT NULL,
    part_title TEXT NOT NULL DEFAULT '',
    video_title TEXT NOT NULL DEFAULT '',
    author TEXT NOT NULL,
    duration TEXT NOT NULL,
    cover_url TEXT NOT NULL,
    page_url TEXT NOT NULL,
    added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_standalone INTEGER NOT NULL DEFAULT 0,
    is_video_title_renamed INTEGER NOT NULL DEFAULT 0,
    is_part_title_renamed INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (bvid, page_number, is_standalone)
)"""


def _track_from_row(row) -> Track:
    return Track(
        *row[:6],
        page_number=row[6],
        part_title=row[7],
        is_standalone=bool(row[8]),
        video_title=row[9],
        is_video_title_renamed=bool(row[10]),
        is_part_title_renamed=bool(row[11]),
    )


class LibraryStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(library)")
            }
            if not columns:
                connection.execute(_LIBRARY_SCHEMA)
            elif "page_number" not in columns:
                connection.execute("ALTER TABLE library RENAME TO library_legacy")
                connection.execute(_LIBRARY_SCHEMA)
                connection.execute(
                    """INSERT INTO library (
                        bvid, page_number, title, author, duration,
                        cover_url, page_url, added_at
                    )
                    SELECT bvid, 1, title, author, duration,
                           cover_url, page_url, added_at
                    FROM library_legacy"""
                )
                connection.execute("DROP TABLE library_legacy")
            elif "is_standalone" not in columns:
                connection.execute("ALTER TABLE library RENAME TO library_legacy")
                connection.execute(_LIBRARY_SCHEMA)
                part_title = "part_title" if "part_title" in columns else "''"
                connection.execute(
                    f"""INSERT INTO library (
                        bvid, page_number, title, part_title, author, duration,
                        cover_url, page_url, added_at, is_standalone
                    )
                    SELECT bvid, page_number, title, {part_title}, author, duration,
                           cover_url, page_url, added_at, 0
                    FROM library_legacy"""
                )
                connection.execute("DROP TABLE library_legacy")
            elif "part_title" not in columns:
                connection.execute(
                    "ALTER TABLE library ADD COLUMN part_title TEXT NOT NULL DEFAULT ''"
                )
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(library)")
            }
            if "video_title" not in columns:
                connection.execute(
                    "ALTER TABLE library ADD COLUMN video_title TEXT NOT NULL DEFAULT ''"
                )
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(library)")
            }
            if "is_video_title_renamed" not in columns:
                connection.execute(
                    """ALTER TABLE library ADD COLUMN is_video_title_renamed
                       INTEGER NOT NULL DEFAULT 0"""
                )
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(library)")
            }
            if "is_part_title_renamed" not in columns:
                connection.execute(
                    """ALTER TABLE library ADD COLUMN is_part_title_renamed
                       INTEGER NOT NULL DEFAULT 0"""
                )
            connection.execute("""CREATE TABLE IF NOT EXISTS search_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT NOT NULL UNIQUE
            )""")
            connection.execute("""CREATE TABLE IF NOT EXISTS playback_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bvid TEXT NOT NULL,
                page_number INTEGER NOT NULL,
                payload TEXT NOT NULL
            )""")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS removed_library_pages (
                    bvid TEXT NOT NULL,
                    page_number INTEGER NOT NULL,
                    PRIMARY KEY (bvid, page_number)
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS playlists (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS playlist_tracks (
                    playlist_id INTEGER NOT NULL
                        REFERENCES playlists(id) ON DELETE CASCADE,
                    bvid TEXT NOT NULL,
                    page_number INTEGER NOT NULL,
                    is_standalone INTEGER NOT NULL,
                    PRIMARY KEY (playlist_id, bvid, page_number, is_standalone),
                    FOREIGN KEY (bvid, page_number, is_standalone)
                        REFERENCES library(bvid, page_number, is_standalone)
                        ON DELETE CASCADE
                )"""
            )

    def record_search(self, keyword: str) -> None:
        keyword = keyword.strip()
        if not keyword or keyword.lower().startswith(("http://", "https://")):
            return
        with self._connect() as connection:
            connection.execute("DELETE FROM search_history WHERE keyword=?", (keyword,))
            connection.execute("INSERT INTO search_history (keyword) VALUES (?)", (keyword,))
            connection.execute("""DELETE FROM search_history WHERE id NOT IN
                (SELECT id FROM search_history ORDER BY id DESC LIMIT 6)""")

    def remove_search_keyword(self, keyword: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM search_history WHERE keyword=?", (keyword,))

    def list_search_history(self) -> list[str]:
        with self._connect() as connection:
            return [row[0] for row in connection.execute(
                "SELECT keyword FROM search_history ORDER BY id DESC LIMIT 6")]

    def record_playback(self, track: Track) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM playback_history WHERE bvid=? AND page_number=?",
                               (track.bvid, track.page_number))
            connection.execute(
                "INSERT INTO playback_history (bvid, page_number, payload) VALUES (?, ?, ?)",
                (track.bvid, track.page_number, json.dumps(asdict(track), ensure_ascii=False)))
            connection.execute("""DELETE FROM playback_history WHERE id NOT IN
                (SELECT id FROM playback_history ORDER BY id DESC LIMIT 50)""")

    def list_playback_history(self) -> list[Track]:
        with self._connect() as connection:
            return [Track(**json.loads(row[0])) for row in connection.execute(
                "SELECT payload FROM playback_history ORDER BY id DESC LIMIT 50")]

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            with connection:
                yield connection
        finally:
            connection.close()

    def add(self, track: Track) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO library (
                       bvid, page_number, title, part_title, video_title, author, duration,
                       cover_url, page_url, is_standalone, is_video_title_renamed,
                       is_part_title_renamed
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(bvid, page_number, is_standalone) DO UPDATE SET
                       title=CASE
                           WHEN library.is_part_title_renamed = 1
                                THEN library.title
                           ELSE excluded.title
                       END,
                       part_title=CASE
                           WHEN library.is_part_title_renamed = 0
                                AND excluded.part_title != '' THEN excluded.part_title
                           ELSE library.part_title
                       END,
                       video_title=CASE
                           WHEN library.is_video_title_renamed = 0
                                AND excluded.video_title != '' THEN excluded.video_title
                           ELSE library.video_title
                       END,
                       is_video_title_renamed=MAX(
                           library.is_video_title_renamed,
                           excluded.is_video_title_renamed
                       ),
                       is_part_title_renamed=MAX(
                           library.is_part_title_renamed,
                           excluded.is_part_title_renamed
                       ),
                       author=excluded.author,
                       duration=excluded.duration, cover_url=excluded.cover_url,
                       page_url=excluded.page_url""",
                (
                    track.bvid, track.page_number, track.title, track.part_title,
                    track.video_title, track.author, track.duration, track.cover_url,
                    track.page_url,
                    int(track.is_standalone),
                    int(track.is_video_title_renamed),
                    int(track.is_part_title_renamed),
                ),
            )

    def clear_removed_pages(self, bvid: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM removed_library_pages WHERE bvid = ?", (bvid,)
            )

    def list_removed_pages(self, bvid: str) -> set[int]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT page_number FROM removed_library_pages WHERE bvid = ?",
                (bvid,),
            ).fetchall()
        return {row[0] for row in rows}

    def remove_library_page(self, bvid: str, page_number: int) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO removed_library_pages (bvid, page_number)
                   VALUES (?, ?)""",
                (bvid, page_number),
            )
            connection.execute(
                """DELETE FROM library
                   WHERE bvid = ? AND page_number = ? AND is_standalone = 0""",
                (bvid, page_number),
            )

    def remove(
        self,
        bvid: str,
        page_number: int | None = None,
        is_standalone: bool | None = None,
    ) -> None:
        with self._connect() as connection:
            if page_number is None:
                connection.execute("DELETE FROM library WHERE bvid = ?", (bvid,))
            elif is_standalone is None:
                connection.execute(
                    "DELETE FROM library WHERE bvid = ? AND page_number = ?",
                    (bvid, page_number),
                )
            else:
                connection.execute(
                    """DELETE FROM library
                       WHERE bvid = ? AND page_number = ? AND is_standalone = ?""",
                    (bvid, page_number, int(is_standalone)),
                )

    def rename(
        self,
        bvid: str,
        title: str,
        page_number: int = 1,
        is_standalone: bool = False,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """UPDATE library SET title = ?
                   WHERE bvid = ? AND page_number = ? AND is_standalone = ?""",
                (title, bvid, page_number, int(is_standalone)),
            )

    def rename_video(self, bvid: str, title: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """UPDATE library
                   SET video_title = ?,
                       is_video_title_renamed = 1,
                       title = CASE WHEN part_title = '' THEN ? ELSE title END
                   WHERE bvid = ? AND is_standalone = 0""",
                (title, title, bvid),
            )

    def rename_library_page(self, bvid: str, page_number: int, title: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """UPDATE library
                   SET title = ?, part_title = ?, is_part_title_renamed = 1
                   WHERE bvid = ? AND page_number = ? AND is_standalone = 0""",
                (title, title, bvid, page_number),
            )

    def set_part_title(self, bvid: str, part_title: str, page_number: int) -> None:
        with self._connect() as connection:
            connection.execute(
                """UPDATE library SET part_title = ?
                   WHERE bvid = ? AND page_number = ? AND is_standalone = 0
                     AND is_part_title_renamed = 0""",
                (part_title, bvid, page_number),
            )

    def set_video_title(self, bvid: str, video_title: str, page_number: int) -> None:
        with self._connect() as connection:
            connection.execute(
                """UPDATE library SET video_title = ?
                   WHERE bvid = ? AND page_number = ? AND is_standalone = 0
                     AND is_video_title_renamed = 0""",
                (video_title, bvid, page_number),
            )

    def list_tracks(self, query: str = "") -> list[Track]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT bvid, title, author, duration, cover_url, page_url, page_number,
                          part_title, is_standalone, video_title,
                          is_video_title_renamed, is_part_title_renamed
                   FROM library WHERE title LIKE ? OR author LIKE ?
                       OR part_title LIKE ? OR video_title LIKE ? ORDER BY added_at DESC""",
                (f"%{query}%",) * 4,
            ).fetchall()
        return [_track_from_row(row) for row in rows]

    def count_tracks(self, playlist_id: int | None = None) -> int:
        """Count saved audio without constructing or sorting Track objects."""
        with self._connect() as connection:
            if playlist_id is None:
                return connection.execute("SELECT COUNT(*) FROM library").fetchone()[0]
            return connection.execute(
                """SELECT COUNT(*) FROM playlist_tracks AS p
                   JOIN library AS l ON l.bvid = p.bvid
                     AND l.page_number = p.page_number
                     AND l.is_standalone = p.is_standalone
                   WHERE p.playlist_id = ?""",
                (playlist_id,),
            ).fetchone()[0]

    def saved_track_ids(self) -> set[tuple[str, int]]:
        with self._connect() as connection:
            return set(connection.execute("SELECT bvid, page_number FROM library"))

    def contains(self, track: Track) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT 1 FROM library
                   WHERE bvid = ? AND page_number = ? AND is_standalone = ?""",
                (
                    track.bvid,
                    track.page_number,
                    int(track.is_standalone),
                ),
            ).fetchone()
        return row is not None

    def create_playlist(self, name: str) -> int:
        name = name.strip()
        if not name:
            raise ValueError("歌单名称不能为空。")
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO playlists (name) VALUES (?)", (name,)
            )
            return int(cursor.lastrowid)

    def list_playlists(self) -> list[tuple[int, str]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, name FROM playlists ORDER BY id"
            ).fetchall()
        return [(int(playlist_id), name) for playlist_id, name in rows]

    def rename_playlist(self, playlist_id: int, name: str) -> None:
        name = name.strip()
        if not name:
            raise ValueError("歌单名称不能为空。")
        with self._connect() as connection:
            connection.execute(
                "UPDATE playlists SET name = ? WHERE id = ?",
                (name, playlist_id),
            )

    def delete_playlist(self, playlist_id: int) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))

    def add_track_to_playlist(self, playlist_id: int, track: Track) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO playlist_tracks (
                       playlist_id, bvid, page_number, is_standalone
                   ) VALUES (?, ?, ?, ?)""",
                (
                    playlist_id,
                    track.bvid,
                    track.page_number,
                    int(track.is_standalone),
                ),
            )
            return cursor.rowcount > 0

    def remove_track_from_playlist(self, playlist_id: int, track: Track) -> None:
        with self._connect() as connection:
            if track.is_standalone:
                connection.execute(
                    """DELETE FROM playlist_tracks
                       WHERE playlist_id = ? AND bvid = ? AND page_number = ?
                         AND is_standalone = 1""",
                    (playlist_id, track.bvid, track.page_number),
                )
            else:
                connection.execute(
                    """DELETE FROM playlist_tracks
                       WHERE playlist_id = ? AND bvid = ? AND is_standalone = 0""",
                    (playlist_id, track.bvid),
                )

    def list_playlist_tracks(
        self, playlist_id: int, query: str = ""
    ) -> list[Track]:
        pattern = f"%{query}%"
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT library.bvid, library.title, library.author,
                          library.duration, library.cover_url, library.page_url,
                          library.page_number, library.part_title,
                          library.is_standalone, library.video_title,
                          library.is_video_title_renamed,
                          library.is_part_title_renamed
                   FROM playlist_tracks
                   JOIN library
                     ON library.bvid = playlist_tracks.bvid
                    AND library.page_number = playlist_tracks.page_number
                    AND library.is_standalone = playlist_tracks.is_standalone
                   WHERE playlist_tracks.playlist_id = ?
                     AND (library.title LIKE ? OR library.author LIKE ?
                            OR library.part_title LIKE ? OR library.video_title LIKE ?)
                   ORDER BY library.added_at DESC""",
                (playlist_id, pattern, pattern, pattern, pattern),
            ).fetchall()
        return [_track_from_row(row) for row in rows]
