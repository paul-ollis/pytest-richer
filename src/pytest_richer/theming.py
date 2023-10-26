"""An attempt at providing (limited) theming for readability.

This is currently primarily aimed at providing readable colors using dark_bg
and light_bg themes.
"""
from __future__ import annotations

from rich.color import Color as RichColor
from rich.syntax import ANSISyntaxTheme, ANSI_DARK, ANSI_LIGHT, String, Style
from textual.color import Color

dark_bg_styles = {
    'default': ('white', 0.5),
    's-bold_bright_green': ('bold bright_green', 0.65),
    's-green4': ('green4', 0.3),
    's-green': ('green', 0.25),
    's-red': ('red1', 0.50),
    's-yellow': ('yellow', 0.27),
}
light_bg_styles = {
    'default': ('black', 0.75),
    's-bold_bright_green': ('bold bright_green', 0.30),
    's-green4': ('green4', 0.35),
    's-green': ('dark_green', 0.45),
    's-red': ('red1', 0.50),
    's-yellow': ('gold3', 0.30),
}

# The Rich ANSI_LIGHT theme does not display stack tracebacks very well; it
# uses yellow, which is often unreadable on white backgrounds. So we use a
# tweaked copy.
modified_ansi_light = ANSI_LIGHT.copy()
modified_ansi_light[String] = Style(color='dark_green')

# Instantiate themes for stack traces.
traceback_themes = {
    False: ANSISyntaxTheme(modified_ansi_light),
    True: ANSISyntaxTheme(ANSI_DARK)}


class Theme:
    """A theme wrapper around a style dictionary.

    :@dark_bg:
        Set to ``True`` if the background of the terminal is dark.
    :@styles:
        The style dictionary. The keys are arbitrary style names and the values
        are typically generic color or style names, such a 'g-red'.
    """

    def __init__(self, *, dark_bg: bool, styles: dict[str, str]):
        self.dark_bg = dark_bg
        self.styles = styles

    def get(self, style_name, default=None, *, disabled: bool = False) -> str:
        """Get a theme color for a given style name.

        The style name is looked up in the `styles` dictionary and the result
        then converted to a suitable style, color combination depending on
        whether this is a light or dark theme.

        :name:
            A name in the style dictionary used to construct this `Theme`
            instance.
        :default:
            A default style and color to return if the name is not found.
        :disabled:
            If true then a dimmed version of the color is returned.
        """
        gen_name = self.styles.get(style_name, 'default')
        m = dark_bg_styles if self.dark_bg else light_bg_styles
        if gen_name in m:
            normal, fade = m[gen_name]
        elif default:
            normal, fade = default
        else:
            normal, fade = m['default']
        if disabled:
            styling, _, color_name = normal.rpartition(' ')
            color = Color.from_rich_color(RichColor.parse(color_name))
            color = color.darken(fade) if self.dark_bg else color.lighten(fade)
            if styling:
                return f'{styling} {color.hex}'
            else:
                return color.hex
        else:
            return normal

    def __getitem__(self, name: str) -> str:
        return self.get(name)
