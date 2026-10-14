"""SIA DC-09 and Contact ID frame tests (no Home Assistant needed)."""

import importlib
from pathlib import Path
import sys
import types
import unittest

PACKAGE_PATH = Path(__file__).parents[1] / "custom_components" / "trueguard_mz"
package = types.ModuleType("trueguard_pkg")
package.__path__ = [str(PACKAGE_PATH)]
sys.modules.setdefault("trueguard_pkg", package)
receiver = importlib.import_module("trueguard_pkg.receiver")


def dc09(body: str) -> bytes:
    data = body.encode()
    return b"\n" + f"{receiver.crc16(data)}{len(data):04X}".encode() + data + b"\r"


class FrameTests(unittest.TestCase):
    def test_crc16_arc(self):
        # CRC-16/ARC check value for "123456789".
        self.assertEqual(receiver.crc16(b"123456789"), "BB3D")

    def test_adm_cid(self):
        report = receiver.parse_frame(
            dc09('"ADM-CID"0007R0L0#1234[#1234|1130 01 015]_12:00:00,01-02-2026')
        )
        self.assertEqual(report.format, "ADM-CID")
        self.assertEqual(report.account, "1234")
        self.assertEqual(report.cid, "1130")
        self.assertEqual(report.group, 1)
        self.assertEqual(report.zone, 15)
        self.assertEqual(report.sequence, "0007")

    def test_ack_matches_pysiaalarm_layout(self):
        report = receiver.parse_frame(dc09('"ADM-CID"0007R0L0#1234[#1234|3401 01 002]'))
        ack = receiver.dc09_ack(report)
        self.assertTrue(ack.startswith(b"\n") and ack.endswith(b"\r"))
        body = '"ACK"0007R0L0#1234[]'
        self.assertEqual(ack[1:5].decode(), receiver.crc16(body.encode()))
        self.assertEqual(ack[5:9].decode(), f"{len(body):04X}")
        self.assertEqual(ack[9:-1].decode(), body)

    def test_sia_dcs_maps_to_contact_id(self):
        report = receiver.parse_frame(dc09('"SIA-DCS"0008L0#1234[#1234|Nri1/BA015]'))
        self.assertEqual(report.sia_code, "BA")
        self.assertEqual(report.cid, "1130")
        self.assertEqual(report.zone, 15)

    def test_null_heartbeat(self):
        report = receiver.parse_frame(dc09('"NULL"0009L0#1234[]'))
        self.assertEqual(report.format, "NULL")
        self.assertEqual(report.cid, "")

    def test_raw_contact_id(self):
        report = receiver.parse_frame(b"[1234 18 1401 01 003 7]")
        self.assertEqual(report.format, "CID")
        self.assertEqual(report.cid, "1401")
        self.assertEqual(report.zone, 3)

    def test_climax_binary_crc_frame(self):
        # Captured from a TrueGuard MZ (MZ-8_F 0.0.3.3L): the CRC is two raw
        # bytes, not four hex digits, and the account has no '#' in the data.
        frame = (
            b'\n\xb9*003A"ADM-CID"0008L0#1234[1234|1401 01 001]'
            b"_21:41:08,10-14-2026\r"
        )
        report = receiver.parse_frame(frame)
        self.assertEqual(report.format, "ADM-CID")
        self.assertEqual(report.crc_format, "bin")
        self.assertEqual(report.cid, "1401")
        self.assertEqual(report.zone, 1)
        self.assertEqual(report.sequence, "0008")
        ack = receiver.dc09_ack(report)
        body = b'"ACK"0008L0#1234[]'
        self.assertEqual(ack[1:3].hex().upper(), receiver.crc16(body))
        self.assertEqual(ack[3:7], f"{len(body):04X}".encode())
        self.assertEqual(ack[7:-1], body)

    def test_bad_crc_is_rejected(self):
        frame = (
            b'\n\x00\x00003A"ADM-CID"0008L0#1234[1234|1401 01 001]'
            b"_21:41:08,10-14-2026\r"
        )
        self.assertIsNone(receiver.parse_frame(frame))

    def test_encrypted_and_garbage_are_rejected(self):
        self.assertIsNone(receiver.parse_frame(dc09('"*ADM-CID"0001L0#1234[abcdef]')))
        self.assertIsNone(receiver.parse_frame(b"hello"))


if __name__ == "__main__":
    unittest.main()
