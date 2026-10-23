"""Key owner colours on the shortcut page stay apart and readable in every
theme, and older colors.toml files (color1..color3) still give their own
colours."""
import glob
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import desktop_omarchy  # noqa: E402
import key_colors as kc  # noqa: E402
from palette import DEFAULT_COLORS  # noqa: E402


def colors(green, yellow, orange, red, background="#000000"):
    return {"green": green, "yellow": yellow, "orange": orange, "red": red, "background": background}


def min_distance(picked):
    return min(kc.hue_distance(a, b) for i, a in enumerate(picked) for b in picked[i + 1:])


def readable(picked, background):
    return all(kc.contrast(kc.rgb(c), kc.rgb(background)) >= kc.MIN_KEY_CONTRAST for c in picked)


class KeyOwnerColorsTest(unittest.TestCase):
    def test_distinct_theme_colours_are_kept(self):
        vivid = colors("#40c057", "#ffe066", "#ff5f00", "#e0004d")
        self.assertEqual(kc.key_owner_colors(vivid), ("#40c057", "#ffe066", "#ff5f00", "#e0004d"))

    def test_hackerman_four_greens_get_apart(self):
        picked = kc.key_owner_colors(colors("#4fe88f", "#50f7d4", "#50f7a3", "#50f872", "#0b0c16"))
        self.assertGreaterEqual(min_distance(picked), kc.MIN_KEY_HUE_DISTANCE)

    def test_yellow_equal_to_orange_gets_apart(self):
        picked = kc.key_owner_colors(colors("#86c79c", "#f0a060", "#f0a060", "#e89a8a"))
        self.assertGreaterEqual(min_distance(picked), kc.MIN_KEY_HUE_DISTANCE)

    def test_greys_get_colour(self):
        picked = kc.key_owner_colors(colors("#888888", "#999999", "#aaaaaa", "#bbbbbb"))
        self.assertGreaterEqual(min_distance(picked), kc.MIN_KEY_HUE_DISTANCE)

    def test_hue_distance_wraps_around(self):
        # Red at hue 0 and a red just below 1 are neighbours.
        self.assertAlmostEqual(kc.hue_distance("#ff0000", "#ff0019"), 1 - kc.hue("#ff0019"))
        self.assertLess(kc.hue_distance("#ff0000", "#ff0019"), 0.05)

    def test_readable_on_light_mid_and_dark_cards(self):
        for background in ("#ffffff", "#eff1f5", "#fffcf0", "#888888", "#777777", "#000000", "#0b0c16"):
            picked = kc.key_owner_colors(colors("#cccccc", "#cccccc", "#cccccc", "#cccccc", background))
            self.assertTrue(readable(picked, background), (picked, background))

    def test_installed_themes_stay_apart_and_readable(self):
        files = glob.glob("/usr/share/omarchy/themes/*/colors.toml")
        if not files:
            self.skipTest("no Omarchy themes installed")
        for path in files:
            theme = desktop_omarchy.theme_colors(path)
            picked = kc.key_owner_colors(theme)
            if picked != (theme["green"], theme["yellow"], theme["orange"], theme["red"]):
                self.assertGreaterEqual(min_distance(picked), kc.MIN_KEY_HUE_DISTANCE, path)
                self.assertTrue(readable(picked, theme["background"]), (path, picked))

    def test_short_hex_keeps_its_brightness(self):
        self.assertEqual(kc.rgb("#fff"), (1.0, 1.0, 1.0))
        self.assertEqual(kc.hue("#f00"), 0.0)

    def test_bad_values_do_not_crash(self):
        picked = kc.key_owner_colors(colors(42, True, "#zzzzzz", "#12", background=None))
        self.assertEqual(len(picked), 4)
        self.assertTrue(all(kc.rgb(c) for c in picked))


class OldThemeFormatTest(unittest.TestCase):
    def read(self, text):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "colors.toml"
            path.write_text(text)
            return desktop_omarchy.theme_colors(path)

    def test_numbered_colours_fill_named_ones(self):
        got = self.read('color1 = "#ff0000"\ncolor2 = "#00ff00"\ncolor3 = "#ffff00"\n'
                        'selection_background = "#123456"\n')
        self.assertEqual((got["red"], got["green"], got["yellow"], got["selection"]),
                         ("#ff0000", "#00ff00", "#ffff00", "#123456"))

    def test_named_colours_win(self):
        got = self.read('red = "#aa0000"\ncolor1 = "#ff0000"\n')
        self.assertEqual(got["red"], "#aa0000")

    def test_non_colour_values_fall_back(self):
        got = self.read('green = 42\nyellow = true\nred = "blue"\ncolor1 = "#ff0000"\n')
        self.assertEqual((got["green"], got["yellow"], got["red"]),
                         (DEFAULT_COLORS["green"], DEFAULT_COLORS["yellow"], "#ff0000"))


if __name__ == "__main__":
    unittest.main()
