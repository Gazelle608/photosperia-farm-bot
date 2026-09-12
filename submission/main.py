"""
Photosperia Farm Bot — Level 1
50x50 grid, 500 ticks, no animals, no weather.
Strategy: plant the highest profit-per-tick crop we can afford, harvest, repeat.
"""
import json
import os
import math

# ---------------------------------------------------------------------------
# Load resource data once at startup
# ---------------------------------------------------------------------------
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(_SCRIPT_DIR, "plant_dataset.json")) as f:
    PLANT_DATASET = json.load(f)

with open(os.path.join(_SCRIPT_DIR, "classifications.json")) as f:
    CLASSIFICATIONS = json.load(f)

try:
    with open(os.path.join(_SCRIPT_DIR, "plant_unlock_conditions.json")) as f:
        UNLOCK_CONDITIONS = json.load(f)
except FileNotFoundError:
    UNLOCK_CONDITIONS = {}


# ---------------------------------------------------------------------------
# Build a lookup table: plant_name -> {cost, sale, growth_ticks, season, ...}
# Handles whatever schema the dataset actually uses.
# ---------------------------------------------------------------------------
def _normalize_plants(dataset):
    plants = {}

    # Some datasets are dicts keyed by name, others are lists of objects.
    if isinstance(dataset, dict):
        items = dataset.items()
    elif isinstance(dataset, list):
        items = [(p.get("name") or p.get("id"), p) for p in dataset]
    else:
        return plants

    for name, raw in items:
        if not isinstance(raw, dict):
            continue
        plants[name] = {
            "name": name,
            "cost":       raw.get("cost", raw.get("seed_cost", raw.get("buy_price", 0))),
            "sale":       raw.get("sale", raw.get("sell_price", raw.get("value", 0))),
            "growth":     raw.get("growth", raw.get("growth_time", raw.get("time", 1))),
            "season":     raw.get("season", raw.get("seasons", ["Spring", "Summer", "Autumn", "Winter"])),
            "unlock":     raw.get("unlock", raw.get("unlock_condition", None)),
        }
        if isinstance(plants[name]["season"], str):
            plants[name]["season"] = [plants[name]["season"]]

    return plants


PLANTS = _normalize_plants(PLANT_DATASET)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _profit_per_tick(plant):
    """Higher is better. Protects against divide-by-zero."""
    growth = max(1, int(plant["growth"]))
    return (plant["sale"] - plant["cost"]) / growth


def _is_unlocked(plant, state, game_info):
    """Level 1 has no unlocks, so everything is available. Kept generic."""
    cond = plant.get("unlock")
    if not cond:
        return True
    # If a future level adds unlocks, plug the logic in here.
    return True


def _plant_ok_for_season(plant, season):
    seasons = plant.get("season") or []
    if not seasons:
        return True
    return season in seasons


def _best_affordable_plant(money, season, state, game_info):
    """Return the plant with highest profit/tick that we can afford this season."""
    best = None
    best_score = -float("inf")
    for plant in PLANTS.values():
        if plant["cost"] > money:
            continue
        if not _plant_ok_for_season(plant, season):
            continue
        if not _is_unlocked(plant, state, game_info):
            continue
        score = _profit_per_tick(plant)
        if score > best_score:
            best_score = score
            best = plant
    return best


# ---------------------------------------------------------------------------
# Derive current season from the game's command list.
# ---------------------------------------------------------------------------
def _current_season(tick, game_info):
    season = "Spring"  # Level 1 starts in Spring by convention.
    for cmd in game_info.get("commands", []):
        if cmd.get("type") == "season" and cmd.get("tick", 0) <= tick:
            season = cmd["season"]
    return season


# ---------------------------------------------------------------------------
# Map raw terrain/soil codes to something usable.
# From 1.json: terrain 1 = plantable, terrain 0/2 = not plantable.
# ---------------------------------------------------------------------------
def _is_plantable(cell):
    return cell.get("terrain") == 1


def _soil_rank(cell):
    """Higher = better. Used to prioritize the best tiles first."""
    return cell.get("soil", 0)


# ---------------------------------------------------------------------------
# The entry point.
# ---------------------------------------------------------------------------
def plan(farm_state, game_info):
    """
    farm_state : dict — your current money, inventory, tile states.
    game_info  : dict — rows, cols, ticks, cells, commands.
    Returns    : list of action dicts.
    """
    actions = []
    tick = farm_state.get("tick", 0)
    money = farm_state.get("money", 0)
    season = _current_season(tick, game_info)

    # -----------------------------------------------------------------
    # 1. HARVEST — anything ready to be picked gets picked.
    # -----------------------------------------------------------------
    tiles = farm_state.get("tiles", {})
    for key, tile in tiles.items():
        if tile.get("plant") and tile.get("ready"):
            actions.append({"type": "harvest", "row": tile["row"], "col": tile["col"]})

    # -----------------------------------------------------------------
    # 2. SELL — dump everything we harvested.
    # -----------------------------------------------------------------
    inventory = farm_state.get("inventory", {})
    if inventory:
        actions.append({"type": "sell_all"})

    # -----------------------------------------------------------------
    # 3. PLANT — fill empty plantable tiles with the best crop we can afford.
    #    Prioritize the best soil first.
    # -----------------------------------------------------------------
    plantable_empty = []
    for cell in game_info.get("cells", []):
        if not _is_plantable(cell):
            continue
        key = f"{cell['row']},{cell['col']}"
        tile = tiles.get(key)
        if tile is None or not tile.get("plant"):
            plantable_empty.append(cell)

    # Best soil first.
    plantable_empty.sort(key=_soil_rank, reverse=True)

    for cell in plantable_empty:
        plant = _best_affordable_plant(money, season, farm_state, game_info)
        if plant is None:
            break
        if money < plant["cost"]:
            break

        actions.append({
            "type": "plant",
            "row": cell["row"],
            "col": cell["col"],
            "plant": plant["name"],
        })
        money -= plant["cost"]

    return actions