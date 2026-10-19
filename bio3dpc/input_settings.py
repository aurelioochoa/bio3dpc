"""Persistent PC input preferences, independent of the guest runtime and Tk."""
from dataclasses import dataclass, field, asdict
import math
import string


# Action ID, visible label, default key, canonical frontend binding.
BINDINGS = (
    ('forward', 'Move forward / menu up', 'w', 'w'),
    ('backward', 'Move backward / menu down', 's', 's'),
    ('left', 'Strafe left / menu left', 'a', 'a'),
    ('right', 'Strafe right / menu right', 'd', 'd'),
    ('use', 'Use / pick up / confirm', 'e', 'e'),
    ('reload', 'Reload / refill EVE', 'r', 'r'),
    ('heal', 'Use first aid / skip cutscene', 'f', 'f'),
    ('ammo', 'Next ammunition type', 'q', 'q'),
    ('fire', 'Fire / confirm (keyboard)', '', '@fire'),
    ('mode', 'Weapon / plasmid (keyboard)', '', '@mode'),
    ('weapon1', 'Wrench', '1', '1'),
    ('weapon2', 'Pistol', '2', '2'),
    ('weapon3', 'Machine gun', '3', '3'),
    ('weapon4', 'Shotgun', '4', '4'),
    ('weapon5', 'Grenade launcher', '5', '5'),
    ('plasmid1', 'Plasmid slot 1', 'F1', 'F1'),
    ('plasmid2', 'Plasmid slot 2', 'F2', 'F2'),
    ('plasmid3', 'Plasmid slot 3', 'F3', 'F3'),
)
DEFAULT_BINDINGS = {action: key for action, label, key, canonical in BINDINGS}
ASSIGNABLE_KEYS = set(string.ascii_lowercase + string.digits) | {
    'Up', 'Down', 'Left', 'Right', 'Insert', 'Delete', 'Prior', 'Next',
    'F1', 'F2', 'F3', 'F8', 'F9', 'F12',
}


def normalize_key(key: str) -> str:
    return key.lower() if len(key) == 1 and key.isascii() else key


def key_label(key: str) -> str:
    return {'': 'Unbound', 'Prior': 'Page Up', 'Next': 'Page Down'}.get(key, key.upper())


@dataclass
class ControlSettings:
    mouse_look: bool = True
    sensitivity: float = 1.0
    invert_y: bool = False
    bindings: dict[str, str] = field(default_factory=lambda: DEFAULT_BINDINGS.copy())

    @classmethod
    def from_dict(cls, value) -> 'ControlSettings':
        if not isinstance(value, dict) or set(value) - {'mouse_look', 'sensitivity', 'invert_y', 'bindings'}:
            raise ValueError('Invalid control settings')
        mouse, invert = value.get('mouse_look', True), value.get('invert_y', False)
        sensitivity = value.get('sensitivity', 1.0)
        if type(mouse) is not bool or type(invert) is not bool:
            raise ValueError('Mouse look and invert Y must be enabled or disabled')
        if (type(sensitivity) not in (int, float) or not math.isfinite(sensitivity)
                or not 0.1 <= sensitivity <= 5.0):
            raise ValueError('Mouse sensitivity must be between 0.1 and 5.0')
        overrides = value.get('bindings', {})
        if not isinstance(overrides, dict) or set(overrides) - DEFAULT_BINDINGS.keys():
            raise ValueError('Invalid keyboard actions')
        keys = DEFAULT_BINDINGS.copy()
        for action, key in overrides.items():
            if not isinstance(key, str) or (key and normalize_key(key) not in ASSIGNABLE_KEYS):
                raise ValueError('Use a letter, number, arrow, Page Up/Down, Insert, Delete, or F1–F3/F8/F9/F12')
            keys[action] = normalize_key(key)
        assigned = [key for key in keys.values() if key]
        if len(set(assigned)) != len(assigned):
            raise ValueError('Each keyboard key can only be assigned to one action')
        return cls(mouse, float(sensitivity), invert, keys)

    def to_dict(self) -> dict:
        return asdict(self)

    def translations(self) -> dict[str, str | None]:
        keys = {default: None for action, label, default, canonical in BINDINGS if default}
        keys.update({self.bindings[action]: canonical for action, label, default, canonical in BINDINGS
                     if self.bindings[action]})
        return keys
