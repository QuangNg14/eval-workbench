"""Frozen evidence, blind assignment, ballot validation and scoring."""

import hashlib
import json
import re
import secrets
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FIXTURE = ROOT / "fixtures" / "leaf-room-v1.json"
PIN = "c31127cfdf7bc13ee0e09c0756306a0fd9da44a39293814d2aea27d234de1b73"
ROLES = ("baseline", "candidate")
CHOICES = ("A", "B", "tie", "abstain")


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def frozen(path: Path = FIXTURE) -> dict:
    text = path.read_bytes().replace(b"\r\n", b"\n").decode("utf-8")
    if digest(text) != PIN:
        raise ValueError("Frozen fixture SHA-256 mismatch. Restore the pinned edition.")
    return json.loads(text)


def read_object(path: Path) -> dict:
    def unique(pairs: list[tuple]) -> dict:
        obj = dict(pairs)
        if len(obj) != len(pairs):
            raise ValueError(f"Duplicate JSON key in {path.name}")
        return obj

    obj = json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=unique)
    if not isinstance(obj, dict):
        raise ValueError(f"Expected a JSON object in {path.name}")
    return obj


def make_key(demo: bool) -> dict:
    return {
        "dataset_sha256": PIN,
        "mode": "demo" if demo else "review",
        "salt": "00" * 16 if demo else secrets.token_hex(16),
    }


@dataclass
class Packet:
    id: str
    mode: str
    side_a: dict[str, str]

    def role(self, case_id: str, side: str) -> str:
        a = self.side_a[case_id]
        return a if side == "A" else ("baseline" if a == "candidate" else "candidate")


def assignment(data: dict, key: dict) -> Packet:
    salt, mode = key.get("salt"), key.get("mode")
    if (key.get("dataset_sha256") != PIN or mode not in ("demo", "review")
            or not isinstance(salt, str) or not re.fullmatch(r"[0-9a-f]{32}", salt)):
        raise ValueError("Invalid run key or wrong frozen edition.")
    packet_id = digest(f"pair-review-v1:{PIN}:{mode}:{salt}")
    order = sorted((c["id"] for c in data["cases"]), key=lambda i: digest(f"{salt}:{i}"))
    start = int(packet_id[:2], 16) % 2
    return Packet(packet_id, mode, {i: ROLES[(n + start) % 2] for n, i in enumerate(order)})


def ballot(data: dict, packet: Packet) -> dict:
    votes = {}
    for case in data["cases"]:
        i = case["id"]
        choice, reason = "", ""
        if packet.mode == "demo":
            choice = case["demo_vote"]["prefer"]
            if choice in ROLES:
                choice = "A" if packet.role(i, "A") == choice else "B"
            reason = case["demo_vote"]["reason"]
        votes[i] = {"choice": choice, "reason": reason}
    return {"packet": packet.id, "votes": votes}


@dataclass
class Check:
    label: str
    expected: object
    recorded: object

    @property
    def passed(self) -> bool:
        return self.expected == self.recorded


def checks(case: dict, role: str) -> list[Check]:
    out = case["outputs"][role]
    if case["kind"] == "coding":
        return [Check("disposition", "answer", out["disposition"])] + [
            Check(f"recorded test: {name}", test["want"], out["observed"].get(name))
            for name, test in case["tests"].items()
        ]
    target = case["expected"]
    citations = sorted(set(re.findall(r"\[([^\[\]\n]+)\]", out["text"])))
    return [
        Check("disposition", target["disposition"], out["disposition"]),
        Check("declared decision", target["decision"], out["decision"]),
        Check("cited IDs", target["citations"], citations),
    ]


@dataclass
class Result:
    id: str
    choice: str
    reason: str
    preference: str
    by_role: dict[str, list[Check]]

    def passed(self, role: str) -> bool:
        return all(c.passed for c in self.by_role[role])


def adjudicate(data: dict, packet: Packet, submitted: dict) -> list[Result]:
    if submitted.get("packet") != packet.id:
        raise ValueError("Ballot belongs to a different packet.")
    votes = submitted.get("votes")
    if not isinstance(votes, dict) or votes.keys() != packet.side_a.keys():
        raise ValueError("Expected exactly one vote entry for each frozen case ID.")
    rows = []
    for case in data["cases"]:
        i = case["id"]
        vote = votes[i]
        if not isinstance(vote, dict) or set(vote) != {"choice", "reason"}:
            raise ValueError(f"{i}: expected choice and reason.")
        choice, reason = vote["choice"], vote["reason"]
        if choice not in CHOICES or not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"{i}: select A, B, tie or abstain and give a reason; not scored.")
        preference = choice
        if choice in ("A", "B"):
            preference = "win" if packet.role(i, choice) == "candidate" else "regression"
        rows.append(Result(i, choice, reason.strip(), preference,
                           {r: checks(case, r) for r in ROLES}))
    return rows


def ratio(n: int, total: int) -> str:
    return f"{n}/{total} ({100*n/total:.0f}%)" if total else "0/0 (N/A)"


def metrics(data: dict, rows: list[Result]) -> dict[str, dict[str, str]]:
    total = len(rows)
    counts = Counter(row.preference for row in rows)
    judged = total - counts["abstain"]
    preference = {
        "Judgment coverage (includes ties)": ratio(judged, total),
        "Ballot abstentions": ratio(counts["abstain"], total),
        **{f"Candidate {p} / judged": ratio(counts[p], judged) for p in ("win", "tie", "regression")},
    }
    state = {}
    for r in ROLES:
        state[f"{r}: all checks pass / cases"] = ratio(sum(row.passed(r) for row in rows), total)
        items = [c for row in rows for c in row.by_role[r]]
        state[f"{r}: checks passed / checks"] = ratio(sum(c.passed for c in items), len(items))
        answered = sum(c["outputs"][r]["disposition"] == "answer" for c in data["cases"])
        state[f"{r}: answered / cases (response coverage)"] = ratio(answered, total)
    return {"Ballot preferences": preference, "Deterministic fixture checks": state}
