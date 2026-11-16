from dataclasses import dataclass


@dataclass(frozen=True)
class Track:
    bvid: str
    title: str
    author: str
    duration: str
    cover_url: str
    page_url: str
    page_number: int = 1
    part_title: str = ""
    is_standalone: bool = False
    video_title: str = ""
    is_video_title_renamed: bool = False
    is_part_title_renamed: bool = False

    @property
    def track_id(self) -> tuple[str, int]:
        return self.bvid, self.page_number

    @property
    def playlist_id(self) -> tuple[str, int, bool]:
        return self.bvid, self.page_number, self.is_standalone

    @property
    def playback_id(self) -> str:
        return f"{self.bvid}:{self.page_number}:{int(self.is_standalone)}"


