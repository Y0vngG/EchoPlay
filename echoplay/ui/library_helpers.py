"""Shared grouping rules for library rows and playback queues."""

from echoplay.models import Track


def group_library_tracks(tracks: list[Track]) -> list[tuple[Track, list[Track], bool]]:
    """Keep standalone tracks separate and group collection pages in input order."""
    rows = []
    collections = {}
    for track in tracks:
        if track.is_standalone:
            rows.append((track, [track], True))
        elif track.bvid in collections:
            collections[track.bvid].append(track)
        else:
            pages = collections[track.bvid] = [track]
            rows.append((track, pages, False))
    return rows


def item_for_widget(widget, property_name, items):
    """Find the owning list item for a row or any of its child controls."""
    while widget is not None:
        item = items.get(widget.property(property_name))
        if item is not None:
            return item
        widget = widget.parentWidget()
    return None
