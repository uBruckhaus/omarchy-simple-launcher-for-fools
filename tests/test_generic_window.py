"""The app window (generic desktops) can get narrow again; the Omarchy popup's
card columns and page widths stay as they were. Without starting GTK."""
import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
TREE = ast.parse((ROOT / "launcher_popup.py").read_text())


def launcher_method(name):
    cls = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == "Launcher")
    return next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name)


def apply_grid_columns(popup, card_w):
    namespace = {"POPUP": popup, "grid_columns": lambda width: 3, "folder_fit": lambda *a: (3, 0)}
    exec(compile(ast.Module(body=[launcher_method("apply_grid_columns")], type_ignores=[]),
                 "launcher_popup.py", "exec"), namespace)
    fake = SimpleNamespace(grid=MagicMock(), card_w=card_w, list_h=600, showing_folders=lambda: False)
    namespace["apply_grid_columns"](fake)
    return fake.grid


class GenericWindowTests(unittest.TestCase):
    def test_app_window_columns_are_only_a_cap(self):
        grid = apply_grid_columns(popup=False, card_w=900)
        grid.set_min_children_per_line.assert_called_once_with(1)
        grid.set_max_children_per_line.assert_called_once_with(3)

    def test_popup_keeps_exactly_its_columns(self):
        grid = apply_grid_columns(popup=True, card_w=900)
        grid.set_min_children_per_line.assert_called_once_with(3)
        grid.set_max_children_per_line.assert_called_once_with(3)

    def test_stack_pages_share_a_width_only_in_the_popup(self):
        calls = [n for n in ast.walk(TREE) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "set_hhomogeneous"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(ast.unparse(calls[0]), "stack.set_hhomogeneous(POPUP)")


if __name__ == "__main__":
    unittest.main()
