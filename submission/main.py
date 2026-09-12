"""
Photosperia Farm Bot — Level 1
Grid: 50x50 | Ticks: 500 | Animals: disabled | Weather: none

Strategy:
  This is an ecosystem simulation. We don't buy seeds; we INTRODUCE species
  to cells. Plants spread on their own based on `spread_type` and
  `spread_range`. Species unlock other species based on coverage rules in
  `plant_unlock_conditions.json`.

  Our goal is to build a stable, diverse ecosystem by:
    1. Seeding a starter set of species that are always available.
    2. Letting them spread naturally.
    3. Introducing higher-tier species as soon as their unlock conditions
       are met, prioritizing the species that lead to Worldtree Sapling.
"""

import json
import os

# ---------------------------------------------------------------------------
# Resource loading
# ---------------------------------------------------------------------------
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def _load(name):
    path = os.path.join(_SCRIPT_DIR, name)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


CLASSIFICATIONS = _load("classifications.json") or {}
PLANT_DATASET   = _load("plant_dataset.json") or []
UNLOCK_DATA     = _load("plant_unlock_conditions.json") or []


# ---------------------------------------------------------------------------
# Build lookup tables
# ---------------------------------------------------------------------------
PLANTS_BY_NAME = {}
for p in PLANT_DATASET:
    if isinstance(p, dict) and "plant" in p:
        PLANTS_BY_NAME[p["plant"]] = p

# name -> unlock rule dict
UNLOCK_BY_PLANT = {}
for entry in UNLOCK_DATA:
    if isinstance(entry, dict) and "plant" in entry:
        UNLOCK_BY_PLANT[entry["plant"]] = entry.get("unlock")


# ---------------------------------------------------------------------------
# Which species are "starter" species — always available, no unlock required.
# We pick the foundational ones (Grass, Rose Bush, Lavender) that are the
# roots of the tech tree.
# ---------------------------------------------------------------------------
STARTER_SPECIES = ["Grass", "Rose Bush", "Lavender"]

# Species we prioritize unlocking, in rough tech-tree order.
# This is our "goal chain" toward Worldtree Sapling.
PRIORITY_CHAIN = [
    "Grass",
    "Rose Bush",
    "Lavender",
    "Blue Moss",
    "Crimson Vine",
    "Silver Fern",
    "Orange Blossom",
    "Purple Canopy Tree",
    "Whiteveil Mycelium",
    "Glowcap Fungus",
    "Moonpetal Lily",
    "Ironthorn Shrub",
    "Sporewood Tree",
    "Emberroot Tree",
    "Oak Tree",
    "Ghost Orchid",
    "Sunshard Bloom",
    "Starcap Colony",
    "Worldtree Sapling",
]


# ---------------------------------------------------------------------------
# Read state helpers
# ---------------------------------------------------------------------------
def _extract_plants(farm_state, game_info):
    """
    Returns a dict {plant_name: count_on_grid}.
    Tries multiple plausible state shapes.
    """
    counts = {}

    # Shape A: farm_state has a flat list of cells with "plant" field
    cells = None
    if isinstance(farm_state, dict):
        for key in ("cells", "tiles", "grid"):
            if key in farm_state and isinstance(farm_state[key], (list, dict)):
                cells = farm_state[key]
                break
    if cells is None and isinstance(game_info, dict):
        cells = game_info.get("cells")

    if cells is None:
        return counts

    # cells might be a list or dict
    iterable = cells.values() if isinstance(cells, dict) else cells
    for cell in iterable:
        if not isinstance(cell, dict):
            continue
        # Try various key names for the species on that tile
        name = (cell.get("plant")
                or cell.get("species")
                or cell.get("occupant"))
        if name:
            counts[name] = counts.get(name, 0) + 1
    return counts


def _grid_size(game_info):
    if not isinstance(game_info, dict):
        return (50, 50)
    rows = game_info.get("rows", 50)
    cols = game_info.get("cols", 50)
    return (rows, cols)


def _is_plantable(game_info, row, col):
    """Check the terrain of a cell. Only terrain==1 is plantable in 1.json."""
    if not isinstance(game_info, dict):
        return False
    cells = game_info.get("cells", [])
    # Build a fast lookup once per call — small enough for 50x50
    for c in cells:
        if c.get("row") == row and c.get("col") == col:
            return c.get("terrain") == 1
    return False


def _empty_tiles(farm_state, game_info):
    """Return list of (row, col) tiles that are plantable and currently empty."""
    rows, cols = _grid_size(game_info)
    occupied = set()

    # Gather occupied tiles from state
    cells = None
    if isinstance(farm_state, dict):
        for key in ("cells", "tiles", "grid"):
            if key in farm_state and isinstance(farm_state[key], (list, dict)):
                cells = farm_state[key]
                break
    if cells:
        iterable = cells.values() if isinstance(cells, dict) else cells
        for cell in iterable:
            if not isinstance(cell, dict):
                continue
            if cell.get("plant") or cell.get("species") or cell.get("occupant"):
                occupied.add((cell.get("row"), cell.get("col")))

    # Gather plantable tiles from game_info
    plantable = []
    for c in game_info.get("cells", []):
        if c.get("terrain") == 1:
            plantable.append((c["row"], c["col"], c.get("soil", 0)))

    # Sort by soil quality (higher = better) so we seed the best soil first
    plantable.sort(key=lambda t: t[2], reverse=True)

    return [(r, c) for (r, c, _s) in plantable if (r, c) not in occupied]


# ---------------------------------------------------------------------------
# Unlock evaluation
# ---------------------------------------------------------------------------
def _coverage(counts, plant_name, total_tiles):
    """Fraction of the grid covered by a species."""
    if total_tiles <= 0:
        return 0.0
    return counts.get(plant_name, 0) / total_tiles


def _eval_unlock(rule, counts, total_tiles, present_species):
    """
    Recursively evaluate an unlock rule. Returns True if unlocked.
    Handles: AND, OR, coverage, count, species_present, group_coverage,
             species_absent, event (treated as False — no events in L1).
    """
    if not isinstance(rule, dict):
        return False

    op = rule.get("op")
    if op in ("AND", "OR"):
        children = rule.get("children", [])
        results = [
            _eval_unlock(child, counts, total_tiles, present_species)
            for child in children
        ]
        return all(results) if op == "AND" else any(results)

    # Leaf condition
    t = rule.get("type")
    if t == "coverage":
        plant = rule.get("plant")
        op = rule.get("operator", ">")
        val = rule.get("value", 0)
        cov = _coverage(counts, plant, total_tiles)
        return _compare(cov, op, val)

    if t == "count":
        plant = rule.get("plant")
        op = rule.get("operator", ">=")
        val = rule.get("value", 0)
        cnt = counts.get(plant, 0)
        return _compare(cnt, op, val)

    if t == "group_coverage":
        group = rule.get("species_group", [])
        op = rule.get("operator", ">=")
        val = rule.get("value", 0)
        cov = sum(_coverage(counts, s, total_tiles) for s in group)
        return _compare(cov, op, val)

    if t == "species_present":
        return rule.get("species") in present_species

    if t == "species_absent":
        return rule.get("species") not in present_species

    if t == "event":
        # Level 1 has no weather events.
        return False

    return False


def _compare(a, op, b):
    if op == ">":  return a > b
    if op == ">=": return a >= b
    if op == "<":  return a < b
    if op == "<=": return a <= b
    if op == "==": return a == b
    return False


# ---------------------------------------------------------------------------
# Action building
# ---------------------------------------------------------------------------
def _try_action(action_type, **kwargs):
    """Build an action dict, filtering out None values."""
    action = {"type": action_type}
    for k, v in kwargs.items():
        if v is not None:
            action[k] = v
    return action


def _make_introduce_action(species, row, col):
    """
    Try a few different key names since we don't know the exact schema.
    The engine will hopefully accept at least one of these.
    """
    return {
        "type": "introduce",
        "species": species,
        "plant": species,
        "row": row,
        "col": col,
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def plan(farm_state, game_info):
    """
    Called each tick. Must return a list of actions.
    """
    # Normalize inputs — they could be dicts, objects, or None.
    if not isinstance(farm_state, dict):
        farm_state = {}
    if not isinstance(game_info, dict):
        game_info = {}

    rows, cols = _grid_size(game_info)
    total_tiles = rows * cols

    # Read what's already growing
    counts = _extract_plants(farm_state, game_info)
    present_species = set(counts.keys())

    # Determine which species are unlocked
    unlocked = set(STARTER_SPECIES)  # starters are always available
    for name, rule in UNLOCK_BY_PLANT.items():
        if _eval_unlock(rule, counts, total_tiles, present_species):
            unlocked.add(name)

    # Find empty plantable tiles
    empty = _empty_tiles(farm_state, game_info)
    if not empty:
        return []

    # ----------------------------------------------------------------------
    # Decision: which species to introduce next?
    # Walk our priority chain and pick the FIRST species that is:
    #   - unlocked
    #   - known in the dataset
    #   - below its target coverage
    # ----------------------------------------------------------------------
    TARGET_COVERAGE = {
        "Grass":        0.10,
        "Rose Bush":    0.06,
        "Lavender":     0.05,
        "Blue Moss":    0.04,
        "Crimson Vine": 0.04,
        "Silver Fern":  0.04,
        "Orange Blossom": 0.03,
        "Purple Canopy Tree": 0.03,
        "Whiteveil Mycelium": 0.03,
        "Glowcap Fungus": 0.03,
        "Moonpetal Lily": 0.03,
        "Sporewood Tree": 0.02,
        "Emberroot Tree": 0.02,
        "Oak Tree":      0.02,
        "Worldtree Sapling": 0.01,
    }

    chosen = None
    for species in PRIORITY_CHAIN:
        if species not in unlocked:
            continue
        if species not in PLANTS_BY_NAME:
            continue
        current_cov = _coverage(counts, species, total_tiles)
        target = TARGET_COVERAGE.get(species, 0.02)
        if current_cov < target:
            chosen = species
            break

    # Fallback: if everything is at target, seed Grass as filler
    if chosen is None:
        chosen = "Grass"

    # ----------------------------------------------------------------------
    # Build actions: introduce `chosen` to a few empty tiles this tick.
    # We seed a batch per tick so we don't flood the grid instantly.
    # ----------------------------------------------------------------------
    actions = []
    BATCH_SIZE = 3  # introduce up to 3 plants per tick

    for (r, c) in empty[:BATCH_SIZE]:
        actions.append(_make_introduce_action(chosen, r, c))

    return actions