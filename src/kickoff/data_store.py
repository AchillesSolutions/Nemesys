"""Loading and saving the generated world and match as JSON."""

from __future__ import annotations

from pathlib import Path

from .generator import generate_match
from .models import Match, World
from .world import build_world

DATA_DIR = Path(__file__).parent / "data"
WORLD_PATH = DATA_DIR / "world.json"
MATCH_PATH = DATA_DIR / "match.json"


def build_all() -> tuple[World, Match]:
    world = build_world()
    match = generate_match(world)
    DATA_DIR.mkdir(exist_ok=True)
    WORLD_PATH.write_text(world.model_dump_json(indent=2), encoding="utf-8")
    MATCH_PATH.write_text(match.model_dump_json(indent=1, exclude_none=True), encoding="utf-8")
    return world, match


def load() -> tuple[World, Match]:
    if not (WORLD_PATH.exists() and MATCH_PATH.exists()):
        return build_all()
    world = World.model_validate_json(WORLD_PATH.read_text(encoding="utf-8"))
    match = Match.model_validate_json(MATCH_PATH.read_text(encoding="utf-8"))
    return world, match
