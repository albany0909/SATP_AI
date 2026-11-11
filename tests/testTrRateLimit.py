"""계좌 TR 19종은 모의·실전 모두 같은 api-id 당 1.05초."""

import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.trRateLimit import (
    ACCT_TR_IDS_1SEC,
    acquireTrSlot,
    resetTrSlotsForTest,
)


class TrRateLimitTest(unittest.TestCase):
    def setUp(self):
        resetTrSlotsForTest()

    def tearDown(self):
        resetTrSlotsForTest()

    def test_notice_ids_are_the_nineteen(self):
        self.assertEqual(len(ACCT_TR_IDS_1SEC), 19)
        self.assertIn("kt00004", ACCT_TR_IDS_1SEC)
        self.assertIn("kt50075", ACCT_TR_IDS_1SEC)
        self.assertNotIn("ka00001", ACCT_TR_IDS_1SEC)
        self.assertNotIn("kt00006", ACCT_TR_IDS_1SEC)

    def test_same_account_tr_waits_on_real_and_paper(self):
        for mode in ("real", "paper"):
            resetTrSlotsForTest()
            acquireTrSlot("kt00004", mode=mode)
            started = time.perf_counter()
            acquireTrSlot("kt00004", mode=mode)
            elapsed = time.perf_counter() - started
            self.assertGreaterEqual(elapsed, 1.0, mode)
            self.assertLess(elapsed, 1.4, mode)

    def test_concurrent_same_api_id_is_serialized(self):
        resetTrSlotsForTest()
        ready = threading.Barrier(2)
        elapsed = {}

        def _call(name):
            ready.wait()
            t0 = time.perf_counter()
            acquireTrSlot("kt00018", mode="real")
            elapsed[name] = time.perf_counter() - t0

        threads = [threading.Thread(target=_call, args=(str(i),)) for i in range(2)]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=3)
        self.assertEqual(len(elapsed), 2)
        self.assertGreaterEqual(max(elapsed.values()), 1.0)
        self.assertLess(min(elapsed.values()), 0.3)

    def test_different_account_ids_do_not_share_the_gap(self):
        acquireTrSlot("kt00004", mode="real")
        started = time.perf_counter()
        acquireTrSlot("kt00005", mode="real")
        self.assertLess(time.perf_counter() - started, 0.2)

    def test_real_quote_tr_stays_on_the_quarter_second_gap(self):
        acquireTrSlot("ka10001", mode="real")
        started = time.perf_counter()
        acquireTrSlot("ka10001", mode="real")
        elapsed = time.perf_counter() - started
        self.assertGreaterEqual(elapsed, 0.2)
        self.assertLess(elapsed, 0.5)

    def test_get_acnt_evlt_uses_the_gate(self):
        from api.getAcntEvlt import getAcntEvlt

        class _Res:
            status_code = 200
            text = ""

            def json(self):
                return {"return_code": 0, "entr": "1"}

        with patch("api.getAcntEvlt.requests.post", return_value=_Res()) as posted:
            getAcntEvlt("https://mockapi.kiwoom.com", "tok")
            started = time.perf_counter()
            getAcntEvlt("https://api.kiwoom.com", "tok")
            elapsed = time.perf_counter() - started
        self.assertEqual(posted.call_count, 2)
        self.assertGreaterEqual(elapsed, 1.0)
        self.assertEqual(posted.call_args_list[0].kwargs["headers"]["api-id"], "kt00004")
        self.assertEqual(posted.call_args_list[1].kwargs["headers"]["api-id"], "kt00004")


if __name__ == "__main__":
    unittest.main()
