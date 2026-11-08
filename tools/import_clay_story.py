"""Copy an approved clay story into a Show project.

  python tools/import_clay_story.py PROJECT.zuckervid STORY.json [CHARACTER.json]

The story's pose sheets (and the character's reference image) go into the
project library by SHA-256; show_studio/clay.json records the story, the
character version and a first proposal of placements (every one editable in
the storyboard). The original files are left where they are, and the previous
clay.json is kept in show_studio/history/.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.show_clay import ClayStore  # noqa: E402


def main(args: list[str]) -> int:
    if len(args) not in (2, 3):
        print(__doc__)
        return 2
    data = ClayStore(Path(args[0])).import_story(Path(args[1]), Path(args[2]) if len(args) == 3 else None)
    story = data["stories"][-1]
    print(f"Imported {story['id']} ({len(story['sheets'])} pose sheets) at revision {data['revision']}; "
          f"{sum(1 for item in data['placements'] if item['story'] == story['id'])} placements")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
