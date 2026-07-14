"""Parser tests using synthetic, non-private panel HTML."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import unittest

MODULE_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "trueguard_mz"
    / "parser.py"
)
SPEC = spec_from_file_location("trueguard_parser", MODULE_PATH)
parser = module_from_spec(SPEC)
sys.modules[SPEC.name] = parser
SPEC.loader.exec_module(parser)


class ParserTests(unittest.TestCase):
    def test_parse_control_page(self):
        html = """
          <h1>Panel styring</h1>
          <table><tr><td>Panel tilstand:</td><td>Frakoblet</td></tr></table>
          <h2>Aktive fejl</h2><p>Ingen enheder fundet</p><footer>©2026</footer>
        """
        state, faults = parser.parse_control_page(html)
        self.assertEqual(state, "Frakoblet")
        self.assertEqual(faults, ())

    def test_parse_device_page(self):
        html = """
          <table class="fm">
            <tr><td>Index</td><td>Type</td><td>Navn</td><td>Attribute</td>
            <td>Tilstand</td><td>Batteri</td><td>Sabotage</td>
            <td>Deaktiver enhed</td><td>Signal</td><td>Status</td><td></td></tr>
            <tr><td><input name="Z8" type="checkbox">8</td><td>Dørkontakt</td>
            <td>Køkkenvindue</td><td>Indbrud</td><td></td><td></td><td></td>
            <td></td><td>9</td><td>Åben</td><td>Ændre</td></tr>
          </table>
        """
        devices = parser.parse_device_page(html)
        self.assertEqual(devices[8].name, "Køkkenvindue")
        self.assertEqual(devices[8].signal, 9)
        self.assertEqual(devices[8].status, "Åben")


if __name__ == "__main__":
    unittest.main()
