"""Desktop chrome colours, independent of Qt, recipes and photographic pixels."""
from dataclasses import dataclass, replace
from functools import lru_cache
import re


def _rgb(color):
    return tuple(int(color[i:i+2], 16) for i in (1, 3, 5))


@lru_cache(maxsize=256)
def _luminance(color):
    channels = [v / 255 for v in _rgb(color)]
    linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in channels]
    return sum(v * weight for v, weight in zip(linear, (.2126, .7152, .0722)))


def text_on(background: str) -> str:
    """Choose the greater of the black and white relative-luminance contrast ratios."""
    luminance = _luminance(background)
    return "#000000" if (luminance + .05) / .05 >= 1.05 / (luminance + .05) else "#ffffff"


def selection_rule(accent, background):
    light, dark = sorted((_luminance(accent), _luminance(background)), reverse=True)
    return accent if (light + .05) / (dark + .05) >= 3 else text_on(background)


@dataclass(frozen=True)
class Theme:
    background: str
    surface: str
    input: str
    muted: str
    disabled: str
    border: str
    button: str
    hover: str
    accent: str
    selected: str
    canvas: str
    grid: str
    diagonal: str
    point: str
    red: str
    green: str
    blue: str
    tone: str

    @property
    def text(self) -> str:
        return text_on(self.background)


THEMES = {
    "dark": Theme("#222426", "#1b1d1f", "#17191b", "#b2ada5", "#85827d",
                  "#484a4d", "#35383b", "#454a4f", "#d0ae78", "#494234", "#151b23",
                  "#354050", "#718297", "#f0ede5", "#ef7878", "#73c89b", "#81aef0", "#e9c885"),
    "ivory": Theme("#eee8dc", "#e4ddcf", "#f5f0e6", "#655f54", "#8a8275",
                   "#b8ae9f", "#e1d8c8", "#d3c7b4", "#765735", "#ddd2bd", "#eee7db",
                   "#d0c6b7", "#928573", "#302e29", "#a83d40", "#35694a", "#355ea0", "#775a20"),
    "slate": Theme("#27292b", "#202224", "#191b1d", "#aeb5bb", "#82898f",
                  "#484d51", "#363a3d", "#41474c", "#a7c0d2", "#34444f", "#1d1f21",
                  "#394046", "#77858f", "#eef0f2", "#ef8585", "#7bc89d", "#88b6ed", "#d4d7da"),
    "silver": Theme("#e6e8e9", "#d9dddf", "#f7f8f8", "#515d65", "#69747c",
                   "#b2b9bd", "#dfe2e4", "#cbd2d6", "#375b73", "#c6d6e0", "#e1e4e6",
                   "#bac2c7", "#78868f", "#293239", "#a83d40", "#35694a", "#355ea0", "#4b5359"),
}
THEME_KEYS = (*THEMES, "custom")


def normalize_custom(value):
    if not isinstance(value, dict):
        return None
    result = {}
    for key in ("background", "accent"):
        color = value.get(key)
        if not isinstance(color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            return None
        result[key] = color.lower()
    return result


def custom_theme(background, accent):
    ink = text_on(background)
    dark = ink == "#ffffff"
    base = THEMES["slate" if dark else "silver"]

    def mix(other, fraction):
        rgb = (round(a + (b - a) * fraction) for a, b in zip(_rgb(background), _rgb(other)))
        return "#" + "".join(f"{v:02x}" for v in rgb)

    return replace(base, background=background, surface=mix("#000000", .12 if dark else .06),
                   input=mix("#000000" if dark else "#ffffff", .30 if dark else .48),
                   button=mix(ink, .08), hover=mix(ink, .17), border=mix(ink, .30),
                   selected=mix(accent, .32), muted=mix(ink, .67), disabled=mix(ink, .43),
                   accent=accent)
