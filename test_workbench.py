import io
import json
import shutil
import unittest
from contextlib import redirect_stderr, redirect_stdout
from html import escape
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import workbench
from eval_core import FIXTURE, adjudicate, assignment, ballot, checks, frozen, make_key, metrics, read_object
from presentation import review_html


class WorkbenchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data = frozen()
        self.demo = assignment(self.data, make_key(demo=True))
        self.votes = ballot(self.data, self.demo)

    def scratch(self) -> Path:
        path = workbench.RUNS / f"test-{uuid4().hex}"
        path.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, path)
        return path

    def test_frozen_edition_rejects_drift(self) -> None:
        copy = self.scratch() / "fixture.json"
        lf = FIXTURE.read_bytes().replace(b"\r\n", b"\n")
        copy.write_bytes(lf.replace(b"\n", b"\r\n"))
        self.assertEqual(frozen(copy), self.data)
        copy.write_bytes(lf.replace(b"three evenings", b"four evenings", 1))
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            frozen(copy)

    def test_blind_packet_hides_key_and_orders_cards(self) -> None:
        key = make_key(demo=False)
        packet = assignment(self.data, key)
        html = review_html(self.data, packet)
        blank = ballot(self.data, packet)
        for hidden in ("baseline", "candidate", "demo_vote", key["salt"]):
            self.assertNotIn(hidden, html.lower() + json.dumps(blank))
        self.assertIn("Pre-recorded observations", html)
        articles = html.split("<article>")[1:]
        self.assertEqual(len(articles), len(self.data["cases"]))
        for case, article in zip(self.data["cases"], articles):
            i = case["id"]
            self.assertEqual(blank["votes"][i], {"choice": "", "reason": ""})
            a, b = article.split("<h3>Response B</h3>")
            for side, block in [("A", a), ("B", b)]:
                self.assertIn(escape(case["outputs"][packet.role(i, side)]["text"]), block)
        self.assertIn(list(packet.side_a.values()).count("candidate"), (2, 3))

    def test_demo_outcomes_and_denominators(self) -> None:
        rows = adjudicate(self.data, self.demo, self.votes)
        self.assertEqual({r.id: r.preference for r in rows}, {
            "foldout-loan": "tie", "pleated-wrap": "win", "credit-slip": "win",
            "tab-list": "regression", "shelf-placard": "abstain",
        })
        result = metrics(self.data, rows)
        prefs, det = result["Ballot preferences"], result["Deterministic fixture checks"]
        self.assertEqual(prefs["Candidate win / judged"], "2/4 (50%)")
        self.assertEqual(prefs["Ballot abstentions"], "1/5 (20%)")
        self.assertEqual(prefs["Judgment coverage (includes ties)"], "4/5 (80%)")
        self.assertEqual(det["baseline: checks passed / checks"], "14/16 (88%)")
        self.assertEqual(det["candidate: checks passed / checks"], "15/16 (94%)")
        self.assertEqual(det["candidate: answered / cases (response coverage)"], "4/5 (80%)")
        for r in ("baseline", "candidate"):
            self.assertEqual(det[f"{r}: all checks pass / cases"], "4/5 (80%)")

    def test_all_abstain_preserves_checks(self) -> None:
        for vote in self.votes["votes"].values():
            vote.update(choice="abstain", reason="Insufficient basis")
        result = metrics(self.data, adjudicate(self.data, self.demo, self.votes))
        self.assertEqual(result["Ballot preferences"]["Candidate regression / judged"], "0/0 (N/A)")
        self.assertEqual(result["Ballot preferences"]["Judgment coverage (includes ties)"], "0/5 (0%)")
        self.assertEqual(result["Deterministic fixture checks"]["baseline: all checks pass / cases"], "4/5 (80%)")

    def test_checks_use_citations_and_recorded_outputs(self) -> None:
        policy = next(c for c in self.data["cases"] if c["id"] == "pleated-wrap")
        policy["outputs"]["candidate"]["text"] = "Refer to the desk [LEAF-2]."
        self.assertEqual([c.label for c in checks(policy, "candidate") if not c.passed], ["cited IDs"])
        code = next(c for c in self.data["cases"] if c["id"] == "tab-list")
        code["outputs"]["candidate"]["text"] = "Not executable Python <sample>."
        self.assertEqual([c.label for c in checks(code, "candidate") if not c.passed], ["recorded test: repeat"])
        self.assertIn("&lt;sample&gt;", review_html(self.data, self.demo))

    def test_rejects_wrong_packet_and_invalid_votes(self) -> None:
        other = assignment(self.data, make_key(demo=False))
        with self.assertRaisesRegex(ValueError, "different packet"):
            adjudicate(self.data, other, self.votes)
        for choice, reason in [("", ""), ("best", "clear"), ("tie", "   ")]:
            with self.subTest(choice=choice), self.assertRaisesRegex(ValueError, "foldout-loan"):
                self.votes["votes"]["foldout-loan"].update(choice=choice, reason=reason)
                adjudicate(self.data, self.demo, self.votes)
        self.votes["votes"]["unexpected-id"] = self.votes["votes"].pop("foldout-loan")
        with self.assertRaisesRegex(ValueError, "case ID"):
            adjudicate(self.data, self.demo, self.votes)

    def test_rejects_duplicate_json_keys(self) -> None:
        path = self.scratch() / "ballot.json"
        path.write_text('{"votes":{"case":{"choice":"A","choice":"B"}}}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Duplicate JSON key"):
            read_object(path)

    def test_manual_cli_reveals_only_complete_ballot_once(self) -> None:
        root = self.scratch()
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(workbench, "RUNS", root), redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(workbench.main(["prepare", "blind"]), 0)
            run = root / "blind"
            self.assertEqual(workbench.main(["score", "blind"]), 1)
            report = run / "report.html"
            self.assertFalse(report.exists())
            path = run / "reviewer" / "ballot.json"
            submitted = read_object(path)
            for vote in submitted["votes"].values():
                vote.update(choice="A", reason="I prefer <A> here.")
            path.write_text(json.dumps(submitted), encoding="utf-8")
            self.assertEqual(workbench.main(["prepare", "blind"]), 1)
            self.assertEqual(workbench.main(["score", "blind"]), 0)
            html = report.read_text(encoding="utf-8")
            self.assertIn("I prefer &lt;A&gt; here.", html)
            self.assertIn("MANUAL BALLOT", stdout.getvalue())
            packet = assignment(self.data, read_object(run / "key.json"))
            for i, role in packet.side_a.items():
                outcome = "win" if role == "candidate" else "regression"
                self.assertIn(f"{i} | {outcome} |", stdout.getvalue())
            self.assertEqual(workbench.main(["score", "blind"]), 1)
            self.assertEqual(report.read_text(encoding="utf-8"), html)
