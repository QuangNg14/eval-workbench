# Leaf Room eval workbench

Compare two frozen, invented responses to each of five requests. Four cases use the fictional Leaf Room policy; one checks a label-cleaning function against three pre-recorded examples. The CLI requires only Python 3.10+. The optional React/TypeScript reviewer runs on loopback. Neither runs response code, calls a provider, or requires a login.

## Review in your browser

From the repo root, install the locked UI dependencies and build once with Node.js 20+ and npm:

```powershell
npm ci --ignore-scripts --no-audit --no-fund
npm run build
```

Start or resume a manual packet:

```powershell
python -B -S browser_review.py my-review
```

Open the printed `http://127.0.0.1:PORT/` URL. Compare A and B, then save a choice and rationale for each case. Tab moves between controls; arrow keys change the radio choice. Saved votes survive closing the browser or restarting the server with the same run name. Unsaved edits do not. **Reveal final report** becomes available after all five votes are saved; it runs the CLI scorer and locks the ballot.

Use one server and one reviewer per run; do not edit the same ballot through another CLI or tab while reviewing. Only display evidence and manual votes reach the browser before reveal. Anyone with filesystem access can still read the local key. No server is needed for the file-based workflow below.

## Try it locally

PowerShell, from this directory (choose unused run names):

```powershell
Remove-Item Env:GH_TOKEN,Env:GITHUB_TOKEN -ErrorAction SilentlyContinue
python -B -S workbench.py demo sample
python -B -S workbench.py prepare round1
```

**Demo:** open `runs\sample\report.html` for the scripted results and expand a case's checks to see expected versus recorded values. `runs\sample\reviewer\review.html` shows the corresponding blind view. Votes, rationales, outputs and observations in the demo are fixtures, not measurements of real people or models.

**Human review:** give the reviewer only `runs\round1\reviewer\` (HTML and blank `ballot.json`). Keep `key.json`, the source fixture and all revealed reports away from the reviewer. A/B order varies by case, is balanced to within one case, and uses a fresh local secret for each manual packet. This is procedural blinding, not access control. No account or identity is verified.

1. Open `review.html` offline. Read the request, source rules or recorded examples, and both responses.
2. In `ballot.json`, set each `choice` to `A`, `B`, `tie` (equally useful), or `abstain` (cannot judge). Add a specific `reason` per case. Return that JSON to the same `reviewer` folder.
3. Reveal only after the ballot is complete:

```powershell
Remove-Item Env:GH_TOKEN,Env:GITHUB_TOKEN -ErrorAction SilentlyContinue
python -B -S workbench.py score round1
```

The CLI prints a case ledger and aggregates; the standalone `report.html` also reveals each A/B assignment and the individual checks. Blank, incomplete, duplicate-key or wrong-packet ballots are rejected. Existing runs and reports are not overwritten. All generated files stay under ignored `runs\`.

## Reading the numbers

| Measure | Denominator | Meaning |
| --- | --- | --- |
| Candidate win / tie / regression | Judged ballots, including ties | Vote for candidate / equal / vote for baseline. No abstentions in this denominator. |
| Judgment coverage; ballot abstentions | All cases | Cases judged; cases explicitly skipped. All-abstain gives `0/0 (N/A)` preference shares. |
| Response coverage, by role | All cases | Responses marked `answer`. A required deferral is not an answer, but can be correct. |
| All checks pass, by role | All cases, even on ballot abstention | Policy: expected disposition, declared decision and literal bracketed citation IDs. Coding: disposition and exact recorded list for each test. |
| Individual checks passed, by role | All checks for that role | Partial credit; not a case pass rate or a preference score. |

The SHA-256 pin in `eval_core.py` covers the UTF-8 fixture after CRLF normalization; drift stops the run. The checks do not establish that prose entails a declared decision, or that a snippet produced its recorded outputs. Reviewers can disagree with the checks. Five hand-written cases and a single ballot are not a benchmark of model quality.

## Test

```powershell
Remove-Item Env:GH_TOKEN,Env:GITHUB_TOKEN -ErrorAction SilentlyContinue
python -B -S -m unittest -v test_workbench test_browser_review
```
