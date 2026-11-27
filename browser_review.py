"""Loopback review of one manual packet. No directory is exposed as a web root."""

import argparse
import json
import os
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock

from eval_core import ROOT, assignment, ballot_votes, frozen, read_object, valid_vote
from workbench import prepare, run_path, score


class VoteConflict(Exception):
    def __init__(self, changed: dict) -> None:
        super().__init__("Saved votes changed in another tab. Review the affected cases.")
        self.changed = changed


class Review:
    def __init__(self, run: Path) -> None:
        self.run = run
        self.data = frozen()
        if not run.exists():
            prepare(run, self.data, demo=False)
        self.packet = assignment(self.data, read_object(run / "key.json"))
        if self.packet.mode != "review":
            raise ValueError("Choose a manual run, not a scripted demo.")
        self.path = run / "reviewer" / "ballot.json"
        self.report = run / "report.html"
        self.lock = Lock()

    def submitted(self) -> dict:
        obj = read_object(self.path)
        votes = ballot_votes(self.packet, obj)
        for case_id, vote in votes.items():
            if vote != {"choice": "", "reason": ""}:
                valid_vote(case_id, vote)
        return obj

    def state(self) -> dict:
        submitted = self.submitted()
        cases = []
        for case in self.data["cases"]:
            i = case["id"]
            responses = {}
            for side in ("A", "B"):
                out = case["outputs"][self.packet.role(i, side)]
                responses[side] = {k: out[k] for k in ("text", "observed") if k in out}
            cases.append({"id": i, "prompt": case["prompt"], "responses": responses,
                          "examples": case.get("tests", {}), "vote": submitted["votes"][i]})
        return {"packet": self.packet.id, "policy": self.data["policy"],
                "cases": cases, "revealed": self.report.exists()}

    def mutate(self, route: str, body: dict) -> dict:
        with self.lock:
            if self.report.exists():
                raise FileExistsError("The ballot has already been revealed and is read-only.")
            if body.get("packet") != self.packet.id:
                raise ValueError("Wrong packet. Reload this review.")
            submitted = self.submitted()
            if route == "/api/reveal":
                seen = ballot_votes(self.packet, body)
                changed = {i: vote for i, vote in submitted["votes"].items() if seen[i] != vote}
                if changed:
                    raise VoteConflict(changed)
                score(self.run, self.data)
                return {"message": "Revealed."}
            i = body.get("case")
            if not isinstance(i, str) or i not in submitted["votes"]:
                raise ValueError("Unknown case.")
            if not isinstance(body.get("previous"), dict):
                raise ValueError("Missing previous vote. Reload the review before saving.")
            if body["previous"] != submitted["votes"][i]:
                raise VoteConflict({i: submitted["votes"][i]})
            submitted["votes"][i] = valid_vote(i, body.get("vote"))
            with tempfile.NamedTemporaryFile(dir=self.path.parent, delete=False) as tmp:
                name = Path(tmp.name)
            try:
                with name.open("w", encoding="utf-8") as stream:
                    json.dump(submitted, stream, indent=2, ensure_ascii=False)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                name.replace(self.path)
            finally:
                name.unlink(missing_ok=True)
            return {"vote": submitted["votes"][i]}


def server_for(review: Review, port: int) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def reply(self, status: int, content: bytes, mime: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; script-src 'self'; connect-src 'self'; "
                             "style-src 'self' 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(content)

        def message(self, status: int, msg: str) -> None:
            self.reply(status, json.dumps({"message": msg}).encode(), "application/json")

        def same_host(self) -> bool:
            return self.headers.get("Host") == f"127.0.0.1:{self.server.server_address[1]}"

        def do_GET(self) -> None:
            if not self.same_host():
                self.message(403, "Open the printed loopback address.")
                return
            files = {"/": (ROOT / "ui" / "index.html", "text/html; charset=utf-8"),
                     "/app.js": (ROOT / "ui" / "dist" / "app.js", "text/javascript; charset=utf-8"),
                     "/style.css": (ROOT / "ui" / "style.css", "text/css; charset=utf-8")}
            if self.path == "/report" and review.report.is_file():
                files["/report"] = (review.report, "text/html; charset=utf-8")
            try:
                if self.path == "/api/review":
                    self.reply(200, json.dumps(review.state()).encode(), "application/json")
                elif self.path in files:
                    path, mime = files[self.path]
                    self.reply(200, path.read_bytes(), mime)
                else:
                    self.message(404, "Not found.")
            except (OSError, ValueError):
                self.message(500, "Could not read the review. Check the local run and UI files.")

        def do_POST(self) -> None:
            if not self.same_host() or self.headers.get("Origin") != "http://" + self.headers["Host"]:
                self.message(403, "Review writes must come from this loopback page.")
                return
            if self.path not in ("/api/vote", "/api/reveal"):
                self.message(404, "Not found.")
                return
            if self.headers.get_content_type() != "application/json":
                self.message(415, "Expected JSON.")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                limit = 65536 * (len(review.packet.side_a) if self.path == "/api/reveal" else 1)
                if not 0 < length <= limit:
                    raise ValueError(f"Expected a request of 1 to {limit} bytes.")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("Expected an object.")
                self.reply(200, json.dumps(review.mutate(self.path, body)).encode(), "application/json")
            except VoteConflict as exc:
                self.reply(409, json.dumps({"message": str(exc), "changed": exc.changed}).encode(),
                           "application/json")
            except FileExistsError:
                self.message(409, "This review is already revealed.")
            except ValueError as exc:
                self.message(400, str(exc))
            except OSError:
                self.message(500, "Could not persist the review.")

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", help="New or existing manual run name")
    parser.add_argument("--port", type=int, default=0, help="Loopback port; default chooses a free port")
    args = parser.parse_args()
    try:
        if not (ROOT / "ui" / "dist" / "app.js").is_file():
            raise ValueError("Build the reviewer UI first; see README.")
        with server_for(Review(run_path(args.run)), args.port) as server:
            print(f"Open http://127.0.0.1:{server.server_port}/ (Ctrl+C to stop)", flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        pass
    except (ValueError, OSError) as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
