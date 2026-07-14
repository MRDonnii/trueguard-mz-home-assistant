"""Tests for bounded local WebPanel discovery."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types
import unittest

if "aiohttp" not in sys.modules:
    aiohttp = types.ModuleType("aiohttp")
    aiohttp.ClientSession = object
    aiohttp.ClientError = OSError
    aiohttp.ClientTimeout = object
    sys.modules["aiohttp"] = aiohttp

MODULE_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "trueguard_mz"
    / "discovery.py"
)
SPEC = spec_from_file_location("trueguard_discovery", MODULE_PATH)
discovery = module_from_spec(SPEC)
sys.modules[SPEC.name] = discovery
SPEC.loader.exec_module(discovery)


class DiscoveryNetworkTests(unittest.TestCase):
    def test_private_network_is_scanned_without_ha_address(self):
        adapters = [
            {
                "enabled": True,
                "ipv4": [{"address": "192.0.2.10", "network_prefix": 24}],
            }
        ]

        hosts = discovery.local_ipv4_hosts(adapters)

        self.assertEqual(len(hosts), 253)
        self.assertIn("192.0.2.1", hosts)
        self.assertIn("192.0.2.254", hosts)
        self.assertNotIn("192.0.2.10", hosts)

    def test_broad_private_network_is_limited_to_local_24(self):
        adapters = [
            {
                "enabled": True,
                "ipv4": [{"address": "198.51.100.40", "network_prefix": 8}],
            }
        ]

        hosts = discovery.local_ipv4_hosts(adapters)

        self.assertIn("198.51.100.1", hosts)
        self.assertNotIn("198.51.101.1", hosts)
        self.assertLessEqual(len(hosts), 254)

    def test_disabled_and_non_private_adapters_are_ignored(self):
        adapters = [
            {
                "enabled": False,
                "ipv4": [{"address": "203.0.113.20", "network_prefix": 24}],
            },
            {
                "enabled": True,
                "ipv4": [{"address": "8.8.8.8", "network_prefix": 24}],
            },
        ]

        self.assertEqual(discovery.local_ipv4_hosts(adapters), [])


if __name__ == "__main__":
    unittest.main()
