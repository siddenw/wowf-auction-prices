"""Static knowledge about WoW Forever markets, plus user settings and paths."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

REGIONS = {"eu": "en_GB", "us": "en_US", "kr": "ko_KR", "tw": "zh_TW"}

FACTIONS = ("alliance", "horde", "blackwater")

# The Blizzard API ignores the ruleset in the URL: every ruleset returns the same
# 15 houses. Which house serves which market was worked out by matching stock
# against ForeverMastery (see docs/RESEARCH.md section 7). The IDs are the same in every region.
MARKETS: dict[str, dict[str, int]] = {
    "normal": {"alliance": 111, "horde": 114, "blackwater": 117},
    "pvp": {"alliance": 110, "horde": 113, "blackwater": 116},
    "rp4": {"alliance": 119, "horde": 120, "blackwater": 121},
    "rp5": {"alliance": 123, "horde": 124, "blackwater": 125},
}
MARKET_LABELS = {
    "normal": "Normal",
    "pvp": "PvP",
    "rp4": "Roleplay/RP-PvP (group 4)",
    "rp5": "Roleplay/RP-PvP (group 5)",
}
# KR and TW only run Normal and PvP.
REGION_MARKETS = {"eu": list(MARKETS), "us": list(MARKETS), "kr": ["normal", "pvp"], "tw": ["normal", "pvp"]}

RULESET_ALIASES = {
    "normal": "normal", "pve": "normal", "n": "normal",
    "pvp": "pvp", "p": "pvp",
    "rp4": "rp4", "rp5": "rp5",
}
FACTION_ALIASES = {
    "a": "alliance", "alliance": "alliance", "ally": "alliance",
    "h": "horde", "horde": "horde",
    "none": None, "any": None, "-": None,
}


def _xdg(var: str, fallback: str) -> Path:
    return Path(os.environ.get(var) or Path.home() / fallback) / "wowf"


CACHE_DIR = _xdg("XDG_CACHE_HOME", ".cache")
CONFIG_DIR = _xdg("XDG_CONFIG_HOME", ".config")
SETTINGS_FILE = CONFIG_DIR / "settings.json"


@dataclass
class Settings:
    region: str = "eu"
    ruleset: str = "normal"
    faction: str | None = "horde"  # your faction; None shows every house as usable
    location: int | None = None  # flight node you're at; None = your faction's capital
    time_value: int = 0  # copper per hour your time is worth (adds travel time to the cost)
    round_trip: bool = False  # False: you hearth back after buying (free)

    @classmethod
    def load(cls) -> "Settings":
        try:
            data = json.loads(SETTINGS_FILE.read_text())
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(asdict(self), indent=2))


def normalise_region(value: str) -> str:
    v = value.lower()
    if v not in REGIONS:
        raise ValueError(f"unknown region {value!r} (choose from {', '.join(REGIONS)})")
    return v


def normalise_ruleset(value: str, region: str) -> str:
    v = RULESET_ALIASES.get(value.lower().replace("_", "-"))
    if v is None:
        raise ValueError(f"unknown ruleset {value!r} (choose from {', '.join(MARKETS)})")
    if v not in REGION_MARKETS[region]:
        raise ValueError(f"{region.upper()} has no {MARKET_LABELS[v]} market")
    return v


def normalise_faction(value: str | None) -> str | None:
    if value is None:
        return None
    key = value.lower()
    if key not in FACTION_ALIASES:
        raise ValueError(f"unknown faction {value!r} (alliance, horde or none)")
    return FACTION_ALIASES[key]


def load_credentials() -> tuple[str, str]:
    """Read client credentials from the environment or a .env file. Never print them."""
    cid = os.environ.get("BLIZZARD_CLIENT_ID")
    secret = os.environ.get("BLIZZARD_CLIENT_SECRET")
    if cid and secret:
        return cid, secret
    candidates = [Path.cwd() / ".env", CONFIG_DIR / ".env", Path(__file__).resolve().parents[2] / ".env"]
    for path in candidates:
        if not path.is_file():
            continue
        values: dict[str, str] = {}
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            values[k.strip().removeprefix("export ").strip()] = v.strip().strip("'\"")
        cid = cid or values.get("BLIZZARD_CLIENT_ID")
        secret = secret or values.get("BLIZZARD_CLIENT_SECRET")
        if cid and secret:
            return cid, secret
    raise RuntimeError(
        "No Blizzard API credentials found. Set BLIZZARD_CLIENT_ID and BLIZZARD_CLIENT_SECRET "
        f"in the environment, ./.env or {CONFIG_DIR / '.env'} "
        "(create a client at https://develop.battle.net/access/clients)."
    )
