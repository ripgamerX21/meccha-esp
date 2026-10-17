"""Config dataclass with JSON save/load persistence."""
import json
import os
import sys
from dataclasses import dataclass, field, asdict
from typing import Tuple

if getattr(sys, "frozen", False):
    _config_dir = os.path.dirname(sys.executable)
else:
    _config_dir = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(_config_dir, "esp_config.json")


@dataclass
class Config:
    # ESP basics
    enabled: bool = True
    dot_esp: bool = True
    box_esp: bool = False
    corner_box: bool = False
    show_local: bool = True
    show_names: bool = True
    show_roles: bool = True
    show_distance: bool = True
    snap_lines: bool = True
    team_filter: bool = False
    enemy_only: bool = False

    # Colors
    enemy_color: Tuple[int, int, int] = (255, 0, 0)
    local_color: Tuple[int, int, int] = (0, 255, 0)
    box_color: Tuple[int, int, int] = (255, 255, 255)
    radar_color: Tuple[int, int, int] = (255, 255, 255)
    hunter_visual_color: Tuple[int, int, int] = (255, 60, 60)
    survivor_visual_color: Tuple[int, int, int] = (60, 180, 255)
    invincible_color: Tuple[int, int, int] = (255, 215, 0)

    # Per-role toggles
    hunter_esp: bool = True
    survivor_esp: bool = True

    # Sizing
    dot_radius: int = 8
    box_height_world: float = 100.0
    box_y_offset: int = 0
    line_thickness: int = 1
    point_size: int = 2

    # Distance scaling
    distance_scaling: bool = True
    scale_reference_dist: float = 1500.0

    # Invincible detect
    invincible_detect: bool = True

    # Show cursor
    show_cursor: bool = False

    # Draw All (nearby actors)
    draw_all: bool = False
    draw_all_max_distance: float = 3000.0
    draw_all_names: bool = True

    # Radar
    radar_enabled: bool = False
    radar_size: int = 180
    radar_range: float = 5000.0
    radar_opacity: int = 160

    # Process
    game_process_name: str = "PenguinHotel-Win64-Shipping.exe"


def config_to_dict(config: Config) -> dict:
    d = asdict(config)
    for key in (
        "enemy_color", "local_color", "box_color", "radar_color",
        "hunter_visual_color", "survivor_visual_color", "invincible_color",
    ):
        d[key] = list(d[key])
    return d


def config_from_dict(d: dict) -> Config:
    for key in (
        "enemy_color", "local_color", "box_color", "radar_color",
        "hunter_visual_color", "survivor_visual_color", "invincible_color",
    ):
        if key in d and isinstance(d[key], list):
            d[key] = tuple(d[key])
    return Config(**d)


def save_config(config: Config, path: str = CONFIG_FILE):
    try:
        with open(path, "w") as f:
            json.dump(config_to_dict(config), f, indent=2)
        return True
    except Exception:
        return False


def load_config(path: str = CONFIG_FILE) -> Config:
    config = Config()
    if not os.path.exists(path):
        return config
    try:
        with open(path) as f:
            d = json.load(f)
        return config_from_dict(d)
    except Exception:
        return config
