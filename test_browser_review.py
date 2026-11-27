import json
import unittest
from contextlib import contextmanager
from http.client import HTTPConnection
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread

from browser_review import Review, server_for
from eval_core import frozen, read_object
from workbench import prepare


@contextmanager
def running(run: Path):
    with server_for(Review(run), 0) as server:
        thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
        thread.start()
        try:
            yield server.server_port
        finally:
            server.shutdown()
            thread.join()


def request(port: int, path: str, body: dict | None = None, **headers: str) -> tuple[int, str]:
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("GET" if body is None else "POST", path, None if body is None else json.dumps(body),
                     {"Origin": f"http://127.0.0.1:{port}", "Content-Type": "application/json", **headers})
        res = conn.getresponse()
        return res.status, res.read().decode()
    finally:
        conn.close()


def snapshot(port: int) -> dict:
    state = json.loads(request(port, "/api/review")[1])
    return {"packet": state["packet"], "votes": {case["id"]: case["vote"] for case in state["cases"]}}


class BrowserReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.run = Path(tmp.name) / "review"

    def test_blind_packet_projects_only_display_evidence(self) -> None:
        with running(self.run) as port:
            status, raw = request(port, "/api/review")
            self.assertEqual(status, 200)
            state = json.loads(raw)
            fixture = frozen()
            for hidden in ("baseline", "candidate", "demo_vote", "side_a", read_object(self.run / "key.json")["salt"]):
                self.assertNotIn(hidden, raw)
            for case, source in zip(state["cases"], fixture["cases"], strict=True):
                self.assertEqual(case["vote"], {"choice": "", "reason": ""})
                self.assertNotIn(source["demo_vote"]["reason"], raw)
                self.assertCountEqual((v["text"] for v in case["responses"].values()),
                                      (v["text"] for v in source["outputs"].values()))
            for route in ("/key.json", "/fixtures/leaf-room-v1.json", "/report"):
                self.assertEqual(request(port, route)[0], 404)

    def test_invalid_or_cross_origin_vote_does_not_change_ballot(self) -> None:
        with running(self.run) as port:
            state = json.loads(request(port, "/api/review")[1])
            path = self.run / "reviewer" / "ballot.json"
            original = path.read_bytes()
            body = {"case": state["cases"][0]["id"], "packet": state["packet"],
                    "previous": state["cases"][0]["vote"], "vote": {"choice": "abstain", "reason": " "}}
            self.assertEqual(request(port, "/api/vote", body)[0], 400)
            body["vote"]["reason"] = "Not enough evidence"
            self.assertEqual(request(port, "/api/vote", {k: v for k, v in body.items() if k != "previous"})[0], 400)
            self.assertEqual(request(port, "/api/vote", body, Origin="null")[0], 403)
            self.assertEqual(request(port, "/api/vote", {**body, "packet": "other"})[0], 400)
            self.assertEqual(request(port, "/api/reveal", {"packet": state["packet"]})[0], 400)
            self.assertEqual(path.read_bytes(), original)
            self.assertFalse((self.run / "report.html").exists())

    def test_restart_resume_complete_and_lock_report(self) -> None:
        with running(self.run) as port:
            state = json.loads(request(port, "/api/review")[1])
            key = {"packet": state["packet"]}
            vote = {"choice": "abstain", "reason": "Evidence is inconclusive."}
            body = {**key, "case": state["cases"][0]["id"], "previous": state["cases"][0]["vote"], "vote": vote}
            self.assertEqual(request(port, "/api/vote", body)[0], 200)
            self.assertEqual(request(port, "/api/reveal", snapshot(port))[0], 400)
        with running(self.run) as port:
            resumed = json.loads(request(port, "/api/review")[1])
            self.assertEqual(resumed["packet"], state["packet"])
            self.assertEqual(resumed["cases"][0]["vote"], vote)
            for case in resumed["cases"][1:]:
                self.assertEqual(request(port, "/api/vote", {**key, "case": case["id"], "previous": case["vote"],
                                     "vote": {"choice": "tie", "reason": "Same result."}})[0], 200)
            self.assertEqual(request(port, "/report")[0], 404)
            self.assertEqual(request(port, "/api/reveal", snapshot(port))[0], 200)
            status, html = request(port, "/report")
            self.assertEqual(status, 200)
            self.assertIn("4/5 (80%)", html)
            self.assertIn("Evidence is inconclusive.", html)
            self.assertIn("A = ", html)
            self.assertEqual(request(port, "/api/vote", body)[0], 409)
            self.assertEqual(request(port, "/api/reveal", key)[0], 409)
            self.assertEqual(request(port, "/report")[1], html)

    def test_scripted_demo_cannot_be_opened_as_manual_review(self) -> None:
        prepare(self.run, frozen(), demo=True)
        with self.assertRaisesRegex(ValueError, "manual run"):
            Review(self.run)

    def test_stale_reveal_does_not_lock_unseen_votes(self) -> None:
        with running(self.run) as port:
            blank = snapshot(port)
            for case, previous in blank["votes"].items():
                self.assertEqual(request(port, "/api/vote", {"packet": blank["packet"], "case": case,
                    "previous": previous, "vote": {"choice": "tie", "reason": "Same evidence."}})[0], 200)
            seen = snapshot(port)
            case = next(iter(seen["votes"]))
            updated = {"choice": "tie", "reason": "Same answer, different justification."}
            self.assertEqual(request(port, "/api/vote", {"packet": seen["packet"], "case": case,
                             "previous": seen["votes"][case], "vote": updated})[0], 200)
            status, reply = request(port, "/api/reveal", seen)
            self.assertEqual((status, (self.run / "report.html").exists()), (409, False))
            self.assertEqual(json.loads(reply)["changed"], {case: updated})
            self.assertEqual(request(port, "/api/reveal", {"packet": seen["packet"]})[0], 400)
            self.assertEqual(request(port, "/report")[0], 404)
            self.assertEqual(request(port, "/api/reveal", snapshot(port))[0], 200)
            self.assertIn(updated["reason"], request(port, "/report")[1])

    def test_save_returns_the_persisted_vote_for_the_next_edit(self) -> None:
        with running(self.run) as port:
            seen = snapshot(port)
            case = next(iter(seen["votes"]))
            body = {"packet": seen["packet"], "case": case, "previous": seen["votes"][case],
                    "vote": {"choice": "B", "reason": "\u0085Recheck.\u0085"}}
            status, reply = request(port, "/api/vote", body)
            self.assertEqual(status, 200)
            accepted = json.loads(reply).get("vote")
            self.assertEqual(accepted, {"choice": "B", "reason": "Recheck."})
            self.assertEqual(request(port, "/api/vote", {**body, "previous": accepted,
                             "vote": {"choice": "A", "reason": "Edited."}})[0], 200)

    def test_stale_case_save_preserves_the_first_vote_but_notices_only_that_case(self) -> None:
        with running(self.run) as port:
            first = json.loads(request(port, "/api/review")[1])
            second = json.loads(request(port, "/api/review")[1])

            def save(tab: dict, index: int, choice: str, reason: str) -> tuple[int, str]:
                case = tab["cases"][index]
                return request(port, "/api/vote", {"packet": tab["packet"], "case": case["id"],
                               "previous": case["vote"], "vote": {"choice": choice, "reason": reason}})

            self.assertEqual(save(first, 0, "A", "A cites the rule.")[0], 200)
            self.assertEqual(save(second, 1, "tie", "Both cite it.")[0], 200)
            status, reply = save(second, 0, "B", "B is clearer.")
            persisted = read_object(self.run / "reviewer" / "ballot.json")["votes"]
            original = {"choice": "A", "reason": "A cites the rule."}
            self.assertEqual((status, persisted[first["cases"][0]["id"]]), (409, original))
            second["cases"][0]["vote"] = json.loads(reply)["changed"][first["cases"][0]["id"]]
            self.assertEqual(second["cases"][0]["vote"], original)
            self.assertEqual(save(second, 0, "A", "A gives a precise citation.")[0], 200)
            self.assertEqual(save(second, 0, "B", "Reusing the old rationale.")[0], 409)
            self.assertEqual(json.loads(request(port, "/api/review")[1])["cases"][1]["vote"],
                             {"choice": "tie", "reason": "Both cite it."})
