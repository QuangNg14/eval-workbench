import { useState } from "react";
import { createRoot } from "react-dom/client";

const choices = ["A", "B", "tie", "abstain"] as const;
type Choice = typeof choices[number] | "";
type Vote = { choice: Choice; reason: string };
type ResponseCard = { text: string; observed?: Record<string, unknown> };
type ReviewCase = {
  id: string; prompt: string; responses: Record<"A" | "B", ResponseCard>;
  examples: Record<string, { input: unknown; want: unknown }>; vote: Vote;
};
type Packet = { packet: string; policy: Record<string, string>; cases: ReviewCase[]; revealed: boolean };
const labels = { A: "Response A", B: "Response B", tie: "Tie", abstain: "Abstain" };

async function post(path: string, body: object): Promise<void> {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" },
                                  body: JSON.stringify(body) });
  if (!res.ok) throw new Error((await res.json()).message);
}

function Pair({ item }: { item: ReviewCase }) {
  return <>
    {Object.keys(item.examples).length > 0 && <><h3>Frozen examples: input to expected output</h3><ul>
      {Object.entries(item.examples).map(([name, e]) => <li key={name}>{name}:{" "}
        <code>{JSON.stringify(e.input)} to {JSON.stringify(e.want)}</code></li>)}
    </ul></>}
    <div className="pair">{(["A", "B"] as const).map(side => {
      const card = item.responses[side];
      return <div className="response" key={side}><h3>Response {side}</h3><pre>{card.text}</pre>
        {card.observed && <><strong>Pre-recorded observations</strong><ul>
          {Object.entries(card.observed).map(([name, value]) =>
            <li key={name}>{name}: <code>{JSON.stringify(value)}</code></li>)}
        </ul></>}</div>;
    })}</div>
  </>;
}

function Review({ initial }: { initial: Packet }) {
  const [saved, setSaved] = useState(initial.cases.map(c => c.vote));
  const [drafts, setDrafts] = useState(saved);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const count = saved.filter(v => v.choice !== "").length;
  const changed = (i: number) => drafts[i].choice !== saved[i].choice || drafts[i].reason !== saved[i].reason;
  const canReveal = count === saved.length && !drafts.some((_, i) => changed(i));
  function edit(i: number, vote: Vote) {
    setDrafts(old => old.map((v, n) => n === i ? vote : v));
    setNote(""); setError("");
  }
  async function submit(i?: number) {
    setBusy(true); setError(""); setNote("");
    try {
      if (i === undefined) {
        await post("/api/reveal", { packet: initial.packet });
        window.location.assign("/report");
      } else {
        const vote = { ...drafts[i], reason: drafts[i].reason.trim() };
        await post("/api/vote", { packet: initial.packet, case: initial.cases[i].id, vote });
        setSaved(old => old.map((v, n) => n === i ? vote : v));
        setDrafts(old => old.map((v, n) => n === i ? vote : v));
        setNote(`Case ${i + 1} saved.`);
      }
    } catch (err) { setError(err instanceof Error ? err.message : "Request failed."); }
    finally { setBusy(false); }
  }
  if (initial.revealed) return <><h1>Review complete</h1><p>This ballot is read-only.</p>
    <a href="/report">Open the revealed report</a></>;
  return <>
    <header><small>LEAF ROOM / MANUAL REVIEW</small><h1>Blind ballot</h1>
      <p>Compare the two anonymous responses to each synthetic request. Save a choice and a reason for each case.
        You can close the browser after saving and resume later. Nothing here runs the example code.</p></header>
    <p role="status">{note} {count} of {saved.length} cases saved.</p>
    {error && <p role="alert">{error}</p>}
    <details open><summary>Fictional Leaf Room rules</summary><dl>
      {Object.entries(initial.policy).map(([id, text]) => <div key={id}><dt>{id}</dt><dd>{text}</dd></div>)}
    </dl></details>
    {initial.cases.map((item, i) => <article key={item.id}>
      <small>Case {i + 1} / {item.id}</small><h2>{item.prompt}</h2><Pair item={item} />
      <form onSubmit={e => { e.preventDefault(); void submit(i); }}>
        <fieldset disabled={busy}><legend>Which response is more useful for case {i + 1}?</legend>
          {choices.map(c => <label key={c}><input type="radio" name={`choice-${i}`} required
            checked={drafts[i].choice === c} onChange={() => edit(i, { ...drafts[i], choice: c })} />
            {labels[c]}</label>)}
        </fieldset>
        <label htmlFor={`reason-${i}`}>Reason for case {i + 1} (required, including abstentions)</label>
        <textarea id={`reason-${i}`} rows={2} required disabled={busy} value={drafts[i].reason}
          onChange={e => edit(i, { ...drafts[i], reason: e.target.value })} />
        <button disabled={busy}>Save case {i + 1}</button>{" "}
        <small>{changed(i) ? "Unsaved changes" : saved[i].choice ? "Saved" : "Not yet saved"}</small>
      </form></article>)}
    <section><h2>Finish review</h2><p>Save all five cases first. Revealing the report locks this ballot.</p>
      <button disabled={busy || !canReveal} onClick={() => void submit()}>Reveal final report</button></section>
  </>;
}

const mount = document.getElementById("root");
if (!mount) throw new Error("Review page is missing its root.");
const root = createRoot(mount);
async function start() {
  try {
    const res = await fetch("/api/review");
    if (!res.ok) throw new Error("Could not open the manual packet.");
    const packet: Packet = await res.json();
    root.render(<Review initial={packet} />);
  } catch (err) {
    root.render(<p role="alert">{err instanceof Error ? err.message : "Could not load review."}</p>);
  }
}
void start();
