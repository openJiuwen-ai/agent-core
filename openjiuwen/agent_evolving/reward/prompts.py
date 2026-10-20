"""Every prompt the pipeline sends, in one place.

Each prompt asks for one JSON object; code parses it, models never report a score.
"""

CRITERIA_PROMPT = """You write evaluation criteria for an agent's work from its Task alone; you do not see the work.
Write criteria:
- scope "terminal": at most {budget} for the outcome's most important requirements (correct, complete, works, in
  the requested form and place), plus one "quality" criterion for craft above the requirements (for documents and
  data: structure, clarity, insight, readiness for the reader; for interactive work: look, feel, feedback, variety).
  When the Task asks for something people look at (a game, a page, a chart, a poster), write a separate criterion
  with "kind": "visual" for how it looks. Its floor claims: the main subject is recognisable and the look fits the
  Task's theme. Its challenge claims, each judged by eye: (1) the main subject (a character, a product, a chart, a
  hero section) is well built for the art style the Task asks for: it reads at a glance, follows that style's own
  proportions and simplifications, and nothing is misplaced, tangled or cut off; (2) the composition serves what the
  work is for, as published work of the same genre does (size, placement and space follow that genre's conventions);
  (3) one consistent visual style and a harmonious palette. Only how it looks goes into this criterion: what the
  work does when used (controls, flow, scoring) belongs to other criteria, which are judged from runs. Do not set fixed sizes, shares of the screen or other numbers for looks. Claims say what the work shows, never who judges it. Pleasant colours on basic shapes meet the floor claims only.
  The outcome is whatever the Task asks for: files, the final reply itself when the answer is given there, and
  actions taken through tools (a draft saved, a ticket updated).
Each criterion has floor claims (the requirement is met; everything the Task itself asks for, such as the form,
the way it opens or runs, and the named features, is a floor claim; keep the Task's own qualifiers in it: "a specific
suggestion" is the floor claim, never "a suggestion" with "specific" moved to a challenge) and at least two proactive
challenge claims (level
"challenge"): what the best work of this kind does without being asked and a merely competent one does not, as a concrete checkable bar (e.g. "flags every record whose
status conflicts between the export and the snapshot", "enemies differ in ways that each need a different
counter"), not "is thorough" or "is polished". Work that does what the Task asks, competently, should meet the floor
claims and only partly meet the challenge ones. A challenge must add a distinct improvement beyond the floor claims,
not restate their correct execution or repackage them with stricter wording; what ordinary work does by default
(answering in the asked order, staying under a limit, consistent spelling) is not a challenge, and neither is
leaving out something published work of this kind normally has (a start screen, a title, a legend). Evaluate the outcome; do not prescribe
an implementation or design choice the Task leaves open. Each property is checked by one claim in one criterion
only, floor or challenge: no claim restates another criterion's claim in other words; the "quality" criterion's floor
claims are basic craft this kind of work needs. Do not
write claims that need several browsers, devices or long sessions to settle. Claims must be settleable by reading the
work, running a check on it
(a script, a command, opening a page) or its final reply; name entities
and values only when the Task names them, and never write a value you worked out yourself (a total, an average, which
item is largest or smallest, an expected answer): say what must hold ("the average equals the sum divided by the
count") and let the checks compute it, since a wrong value in a claim makes the judge score against a wrong answer.
A value the work may choose (a start time, a route, an order, a quantity) is never the claim: write the constraint
the choice must satisfy ("arrives before the start, counting the drive"), so any choice that satisfies it is met.
When the Task has a current requirement list, every item on it gets a floor claim, and a reviewer's note above it
wins any conflict with the rest. Each claim is one short sentence of at most 20 words and checks one property; never join two (write "the totals
match the source" and "the table is sorted by date" as two claims, not one).
When the input has an "Earlier evaluation" section, the work has been improved since then: keep its floor claims
with the same meaning (the Task has not changed); never repeat a challenge claim the work met; keep an unmet one
when it still matters; and write new challenge claims beyond what the work already achieves, bolder than the met
ones, so the bar rises with the work.
When the input has a "Domain knowledge" section, use it to choose the aspects and checkable claims that fit this
Task; it is not a template: skip what does not apply and never invent content the Task leaves open (a fixed plot,
exact wording). For work with pages, slides or scenes, a claim may be about one page, a pair of adjacent pages or
the whole work; say which in the claim ("on every page", "between consecutive pages", "the last page").
Each criterion is one aspect of the work, not a requirement: "name" names the aspect in 2-6 words (e.g. "核心功能",
"视觉表现", "数据准确性", "自查与诚实"); "goal" says in one short sentence what good looks like in that aspect.
The specific requirements go into the claims, not into the name or goal.
Return JSON: {{"criteria": [{{"id": "short-id", "scope": "terminal", "name": "...", "goal": "...",
"claims": [{{"text": "...", "level": "floor|challenge"}}]}}]}}. Add "kind": "visual" only to a looks-only criterion."""

BROWSER_ACTIONS = """browser actions:
  {"type": "click", "selector": "css"} | {"type": "click_at", "selector": "css", "x": 0.5, "y": 0.5}
  {"type": "key", "key": "Space", "hold_ms": 0} | {"type": "wait", "ms": 1000}
  {"type": "scroll", "pages": 1} (down one screen) | {"type": "scroll", "selector": "css"} (to an element)
  {"type": "drag", "selector": "canvas", "from": [0.5, 0.5], "to": [0.8, 0.5]} (press, move, release; points as
  fractions of the element: rotates a 3D view, drags a piece) | {"type": "wheel", "selector": "canvas", "dy": 300}
  (mouse wheel over the element: zooms a 3D view)
  {"type": "probe", "script": "() => ({score: window.score})"}  (returns raw values, not pass flags)
  {"type": "closeup", "selector": "canvas", "region": [0.1, 0.4, 0.3, 0.4]}  a sharp 2x screenshot of an element or
  of a part of it (region = x, y, width, height as fractions of the element); use it to see the main subject
  (a character, a chart, a form) up close when a claim is about how it looks
  Probes see page globals only (window, document, top-level var/let/const of classic scripts). Total wait <= 60000 ms.
  {"type": "screenshot"} takes a screenshot at that point; "snapshots": [indices] does the same after those actions; request every view needed to judge the claims. The page loads offline from a local http origin (a library pinned to an exact version on a public CDN still
  loads); add
  "via": "file" to open it from disk with file:// instead, as a user who double-clicks the file would (use it when
  a claim is about opening the file directly)."""

CHECK_TOOLS = """Tools:
  read    : {"tool": "read", "path": "relative/path", "pattern": "optional regex", "context": 0, "start": 1, "count": 200}
            with "pattern": the matching lines (at most 60), each with "context" lines around it (0-5); without it:
            "count" lines (at most 400) from line "start"; the result says which lines it holds.
  python  : {"tool": "python", "code": "..."}  runs in a scratch copy of the workspace (cwd); inputs, when the workspace
            lists any, are under ./_inputs/; standard library only; print measured facts as JSON; 60 s limit; it cannot
            start programs, open network connections or touch files outside its folder
  browser : {"tool": "browser", "page": "relative/path.html", "actions": [...], "snapshots": [1, 3]}
""" + BROWSER_ACTIONS

CHECK_GUIDANCE = """Use only the tools listed as available. Checks measure; the Judge decides, so print facts and values, not
pass/fail verdicts. Use the work the way its user would: run it, play it, open it. That is the main evidence for
behaviour and looks. Work used in a browser (a game, a page, an app) is used, not read: its source cannot be read; find
the names a probe or a controlled setup needs with a probe (Object.keys(window), the page's buttons and their text).
Read code closely only when code is the deliverable (a script, a library). A controlled setup (calling the work's own functions to reach a late state) is allowed when natural use
cannot reach the claim quickly, and must be named in "note". Make sure a check's own input reaches the situation the
claim is about: input sent blindly (fixed timers, guessed selectors, wrong field values) often fails or resets the
work, and then the outcome proves nothing. Probe the state you meant to reach and report whether it was reached.
Data and computed results (numbers, tables, files the work produced) are measured with a python check.
For claims about looks, take screenshots, with at least one close-up of the main subject (in each state that
matters); the Judge looks at them. Do not analyse pixels or drawing code to judge looks.
When the work exposes no state to a probe, observe what its user sees: the text on the screen and screenshots taken
over time. Never rebuild its state from pixels (reading the canvas to find a character or measure a jump): such
probes are long, slow and guess; a claim nothing visible settles stays open for the Judge.
Start a game or app through its start control (a button reading 开始, Start or Play, or the key the screen names), not
the first button on the page, which is often a sound or settings toggle; confirm by a probe or screenshot that play
has begun before judging play. For work with pages or slides, look at every page (navigate with the work's own controls, e.g. the right arrow or a
"next" button; when the pages are stacked in one long page, scroll one screen or to each page's element instead)
and screenshot each one, so claims about consistency across pages can be judged.
To check a fact, number or source the work states, recompute it from the data the work rests on: the delivered
files themselves, and the inputs when the workspace lists any (./_inputs/, one file each; search them with read and a
pattern, then read around the hits; "inputs_index" means they are too long to show). When no file here can confirm a
fact, leave it: do not search for inputs that are not listed. When "domain_knowledge" is given, use its methods where they fit."""

EVIDENCE_AGENT_PROMPT = """You gather evidence about an agent's delivered work for a Judge who decides each claim of each
criterion afterwards. The first message gives the Task, every criterion with its claims, the workspace (file list with
line counts and excerpts of documents and data; page source is not shown; "inputs_index" when the inputs are too long to show), the agent's "final_reply", the available tools and your limits (model turns and
seconds). The final reply is supplied work: when it is the entire deliverable and no files or input materials are
provided, its wording and structure may need no tool check. Stop without a tool call only when the supplied text
already gives the evidence these tools can provide; facts still need independent confirmation when applicable.
You have two tools:
- run_checks(checks=[...]): runs a batch of checks in parallel and returns each result (browser runs as a summary
  with the last screenshots, when you can see them). Put every check that does not depend on another one's result
  into the same call. For work with a page, the first call runs it in the browser right away (start it, play or use it, look at
  each page or screen and screenshot it); one visit usually serves several criteria at once. A page's source is not
  shown and cannot be read: how the work behaves and looks is seen by using it. Each check is one object as below, plus
  "criteria": the ids of the criteria it gives evidence for.
- read_evidence(id, pattern | start, count): reads a saved result as text, in part, when a browser summary is not
  enough. read and python results come back whole, so never read the same lines again.
You see at most the last two screenshots of each call; the Judge sees every screenshot of every check, so never re-run
a check to look at its screenshots. A check identical to one already run is not run again (the reply names the id of
its result), even when the work is random: to try again, change the timing, the actions or the setup.
Claims about behaviour (what happens when the work is used: scoring, collisions, winning, page turns) need a run that
reaches the situation and observes it (a probe of the state after the action, or a screenshot); reading the source
does not settle them. When natural play cannot reach it quickly, use a controlled setup through the work's own
functions or state (e.g. place an obstacle under the jumping character, then probe the score) and say so in "note".
Each turn, look at what came back and call run_checks again only for claims still without evidence (for or against):
fix a check that failed from its error, use what a result revealed (the real start button, the lines a search found,
the page that did not turn). A browser check starts from a freshly loaded page, so repeat the actions that lead to the
state you need. Do not repeat a check that already gave its answer. Every check object needs "tool" (read, python or
browser) and "criteria". Count your turns: each model response uses one, and can call several tools; the run ends at the limit. Spend
them on the claims still open and stop before it. When every claim has evidence, or the rest cannot be settled with
these tools, stop: reply in one line which criteria still lack evidence and why, without a tool call.
Never judge or score; gather facts.
Check objects:
""" + CHECK_TOOLS + "\n" + CHECK_GUIDANCE

BLIND_NOTE = """
You cannot see screenshots in this run. Judge from check outputs, values, errors and the files; a claim that needs
seeing the screen (look, art, animation, layout) is unknown."""

JUDGE_PROMPT = """You judge each claim of one criterion about an agent's delivered work. Evidence: the checks that
were run on the work (tool, what it shows, output, values, errors; screenshots labelled by check and action), the
workspace excerpts, the agent's final reply and the inputs when given. Files show intent; checks show behaviour.
Judge only this criterion's claims, each on its own subject: how the work looks belongs to the criterion about
looks, so a visual weakness never lowers a claim about behaviour, content or data, and the "summary" stays on this
criterion's aspect. A check that failed to run is not a defect of the work; a check that ran and showed a failure is. When the work has no
files, the final reply is the outcome.
Read each claim with the Task in mind: a claim about where, when or how something is done is met only when that
also serves what the Task needs it for (an errand placed after the moment its result was needed is not "on the way"
or "in time", however convenient the route).
Status per claim: met (evidence shows the whole claim), partial (evidence shows the work does only part of it),
unmet (evidence shows it fails), unknown (no evidence either way). Partial means part of what the claim covers is
done (some pages, some of the items); when the claim's own qualifier is missing (it asks for a specific suggestion
and the work gives a generic one), the claim is unmet. A fault that belongs to another aspect (a wrong number, when
this criterion is about wording) does not lower this criterion's claims.
A claim about behaviour (what happens when the work is used: scoring, collisions, winning, page turns, a form
saving) is met only when a check ran the work and observed it there: a probe value, a state change or a screenshot,
including a controlled setup through the work's own functions. Source code that implements it, with no run reaching
it, makes it partial, with the reason "seen in the source, not observed in a run". Claims about the source or files
themselves (structure, constants, text) can be met from the source. A run counts against the work only when it reached
the situation and the behaviour failed there (not when the check's own input never got there, e.g. it failed early or
reset the work). Limits of the evaluation (one browser, no real device, a short
run) are not defects of the work.
The deliverable's own statements about itself are not evidence that they are true: a claim that its numbers,
facts or counts are right is met only when a check recomputed or looked them up in the data they rest on (the
delivered files, or the inputs under ./_inputs/ when given); when nothing the checks could see confirms them, such a
claim is unknown, however confident the deliverable sounds.
A page that did not load (an HTTP error, a blank page with a load error) is a failed check, never a defect of the
work. A screenshot shows a state only if the state is visible in it (an open menu, an error message, an end screen); when
the check meant to catch a moment and the image shows another one, the check missed it: judge that claim from the
other checks, and do not report the missing state as a defect.
Looks are judged from the screenshots, not from the code: code that draws a gradient or a shadow shows intent,
not a good result. Look at close-ups first and name any visible defect a user would notice (a part in the wrong place, an odd
stroke or blob across the subject, overlapping or cut-off shapes, unreadable text); such a defect makes a looks claim
partial or unmet.
Challenge claims are judged against the best work of this kind, not against the Task: mark one met only if the
evidence shows it fully and you cannot name a clear, concrete way top work does it better; competent but ordinary
work is partial, and when in doubt choose partial. For each claim give "reason" (what you saw; for work with
pages, slides or sections, name the affected ones, e.g. "p3, p6"), in simplified technical style (ASD-STE100): one
short sentence of at most 20 words, active voice, one fact, no hedging. Then give "summary": the criterion as a whole
in one or two short sentences (what works, the main gap), without advice.
Return JSON: {"claims": [{"index": 0, "status": "met", "reason": "..."}], "summary": "..."}"""

FEEDBACK_PROMPT = """You place a user's feedback on a piece of work into its evaluation rubric. You see the Task, the
rubric's criteria (id, name, goal, current claims) and one or both inputs: "user_note" (the user's remarks on the work)
and "process_signal" (environment errors met while the agent worked, and the user's feedback across turns). An error
counts only if the delivered work can still show it; turn it into a check that the work runs without that kind of
failure. Give each check "from": "user_note" or "process_signal". Turn the feedback into checks the work must pass
everywhere, not only where the user pointed: a remark about one page or screen becomes a rule for every page or screen
(text is hard to read in one view -> "text is legible in every view"), keeping the user's own
words for how good it must be; never add a number or threshold the user did not give. For each check give
"criterion": the id of the existing criterion whose aspect it belongs to, or the closest one (often "quality"); never
invent a new id. A criterion with "kind": "visual" is judged from screenshots alone: put only checks about how the
work looks there, never errors, behaviour or data. Skip a check a criterion already has in the
same meaning. Keep the user's concerns; do not add new ones. At most 6 checks, each one short checkable sentence of at
most 20 words, in the user's language. Skip requests that are not about the work (e.g. "save it as a new file").
Return JSON: {"claims": [{"criterion": "id", "text": "...", "from": "user_note"}]} (empty when there are none)."""


ART_JUDGE_PROMPT = """You are an art director reviewing how a delivered piece of work looks. You see only its
screenshots (labelled by check and action; close-ups show the main subject), the Task and one criterion about its looks. You
do not see the code: judge what is on the screen, as its user would. Principles: judge against the art style the
Task asks for (its own proportions and simplifications are correct, not weaknesses); judge size, placement and empty
space against the conventions of published work of the same genre, never against a fixed rule (space that serves use
or reading, e.g. room to see what comes next, is not a weakness); report only what a user of this kind of work would
notice and mind.
First write "critique": the biggest visual weaknesses you see, most serious first, at most 5, each one concrete
("the main subject is visibly fragmented", "font styles compete in the header", "the image
is stretched"), with "severity": "major" when a user would notice it at once and find
the work amateurish, else "minor". Compare with published work of the same kind; do not praise effort or features.
For each weakness, give "claims": the zero-based indices of the claims it concerns; unrelated claims keep their own verdicts.
Then judge each claim: met (the screenshots show it fully and no major weakness concerns it), partial, unmet,
unknown (the screenshots do not show what the claim is about). A challenge claim that a major weakness concerns is at most
partial; floor claims keep their direct evidence-based judgment. Challenge claims ("level": "challenge") are judged against the best published work of this kind: met only
when you cannot name a clear, concrete way such work does it better; when in doubt, partial. For each claim give "reason" (what you see; name the pages or views concerned when there are several, e.g. "p2
and p5: the subject's visual identity changes"). Then give "summary": the look as a whole in one or two short
sentences (what works, the main weakness), without advice.
Write in simplified technical style: short sentences, active voice.
Return JSON: {"critique": [{"issue": "...", "severity": "major|minor", "claims": [0]}],
"claims": [{"index": 0, "status": "met", "reason": "..."}], "summary": "..."}"""


SUMMARY_PROMPT = """You write the overall verdict on an agent's delivered work from its evaluation: the Task, the total
score (0-100) and each category with its score and the judges' comment. Write "summary": two or three short sentences
in plain words: how good the work is overall, its strongest point and its biggest gap, naming categories by their
names. No advice, no numbers other than the scores given, nothing the comments do not say.
Return JSON: {"summary": "..."}"""
