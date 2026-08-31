#!/usr/bin/env python3
"""Keep the public trend catalogue small, ordered, and repeatable.

Usage: python3 scripts/curate_trends.py /path/to/rapclips.db
The script is intentionally idempotent: disabled presets are kept for old jobs,
while only the curated titles are enabled for the public API.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


TITLES = [
    "Кинотеатр смотрит на тебя",
    "Earth Zoom",
    "Bullet Time",
    "Comic",
    "Cold Vision",
    "Particles",
    "Windows",
    "Canvas",
    "Pigeons",
    "Superstar",
    "LSD",
    "Blue Depth",
    "Palette",
    "Moonwalk",
    "Fragments",
    "2000's Paparazzi",
    "Overexposed",
    "Animal Ride",
    "Sticker Peel",
    "Multiverse",
    "Skatedog",
    "Noir",
    "Selfie Twin",
    "Akrill",
    "Magazine",
    "Cannabis",
    "3D Render",
    "Action Figure",
    "Orbit 360",
    "Orbital Presence",
    "Acid",
    "Race Track",
    "Flash Comic",
    "Paper",
    "Random Glow",
    "Toxic",
    "Broken Mirror",
    "Lava",
    "Modern",
    "Ocean",
    "Origami",
    "Two Color",
    "Ultraviolet",
    "Vintage",
]


NEW_PRESETS = {
    "Masterpiece": (
        "Use the reference photo to preserve the person's identity. Transform the subject into "
        "a living museum masterpiece with elegant classical composition, tactile painterly detail, "
        "soft gallery light and no text. Vertical 9:16, premium cinematic finish.",
        "Subtle brush strokes come alive across the portrait, light moves over the painted surface "
        "and the camera slowly pushes in. Preserve face and body consistency, no text, no logo.",
    ),
    "Animal Ride": (
        "Use the reference photo to preserve the person's identity. Place the subject on a playful "
        "cinematic ride with one friendly fantastical animal in a vivid natural environment. "
        "Keep the animal choice surprising but coherent. Vertical 9:16, premium cinematic realism.",
        "The animal carries the subject confidently through the scene while the camera tracks beside "
        "them. Preserve face and body consistency, natural motion, no text, no logo.",
    ),
}


def ensure_preset(db: sqlite3.Connection, title: str) -> None:
    row = db.execute(
        "SELECT id FROM trend_presets WHERE title = ? AND kind = 'trend' ORDER BY id LIMIT 1",
        (title,),
    ).fetchone()
    if row:
        if title in NEW_PRESETS:
            db.execute(
                "UPDATE trend_presets SET image_prompt = ?, motion_prompt = ? WHERE id = ?",
                (*NEW_PRESETS[title], row[0]),
            )
        return

    image_prompt, motion_prompt = NEW_PRESETS[title]
    db.execute(
        """INSERT INTO trend_presets (
               position, title, image_prompt, motion_prompt, poster_filename,
               sample_filename, image_engine, video_engine, duration_sec, aspect,
               enabled, created_at, kind, landing_url, reward_note, reward_pct
           ) VALUES (?, ?, ?, ?, '', '', 'nano-banana', 'seedance-2-mini', 6,
                     '9:16', 1, CURRENT_TIMESTAMP, 'trend', '', '', 10)""",
        (TITLES.index(title) + 1, title, image_prompt, motion_prompt),
    )


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: curate_trends.py /path/to/rapclips.db")
    path = Path(sys.argv[1]).expanduser().resolve()
    if not path.is_file():
        raise SystemExit(f"database not found: {path}")

    with sqlite3.connect(path) as db:
        for title in NEW_PRESETS:
            ensure_preset(db, title)
        db.execute("UPDATE trend_presets SET enabled = 0 WHERE kind = 'trend'")
        for position, title in enumerate(TITLES, 1):
            result = db.execute(
                """UPDATE trend_presets
                   SET enabled = 1, position = ?
                   WHERE id = (
                     SELECT id FROM trend_presets
                     WHERE title = ? AND kind = 'trend'
                     ORDER BY id LIMIT 1
                   )""",
                (position, title),
            )
            if result.rowcount != 1:
                raise RuntimeError(f"missing trend preset: {title}")

        enabled = [
            row[0]
            for row in db.execute(
                "SELECT title FROM trend_presets WHERE enabled = 1 AND kind = 'trend' ORDER BY position, id"
            )
        ]
        if enabled != TITLES:
            raise RuntimeError(f"unexpected public catalogue: {enabled!r}")

    print(f"curated {len(TITLES)} trends in {path}")


if __name__ == "__main__":
    main()
