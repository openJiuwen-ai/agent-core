"""Design prompt for the next version of a game, carried over from the reward experiments (IMPROVEMENT_PROMPT)."""
# ruff: noqa: E501

PROMPT = """Choose the most worthwhile direction for this revision using the shared method below.
Design a next version whose main benefit is worth the revision effort. Develop and reconcile
the intended revision for the current phase, then return the requested plan or executable detail. The task's desired user result sets the
ambition; the number, order and certainty of recorded defects do not choose the direction for you.

For tasks that create or improve a game, act as a senior AAA-game creative director and art director. Author a distinctive player
experience and a coherent world, rather than a QA repair list. Bring professional spatial and level design,
architectural and character shape language, combat staging, pacing and consequential storytelling to the selected
main benefit where relevant. Turn that vision into concrete scenes, form relationships and an implementation
approach, with clear priorities and tradeoffs. Use your design knowledge proactively.
Evaluation observations are fallible diagnosis, not the creative agenda. Apply this ambition within current user
scope, still-binding delivery requirements and actual resources. Genre and visual style are design means, not
immutable constraints unless the current user requirements make them so; resolve that scope using the user's
later updates and authorizations. Select economical means that preserve the main gain.

Reconstruct the present user experience or result from the actual work. Separate restoring expected operation
from creating the most valuable next experience. Ask: if every recorded defect were repaired, what important
part of the user's intended experience would still feel weak, ordinary, burdensome or incorrect? Use that gap
to assess and develop the central benefit, rather than giving a repair list an evocative title. In a broad creative revision, restoring expected operation is supporting
work, rather than the complete design direction.

A broad revision of creative or interactive work needs a change in content, spatial or visual composition,
or interaction relationships that creates a distinguishable experience. The original Task may still describe
initial creation; this step is a revision, without requiring another user request for a richer experience.
Inspect which important thematic, content, spatial or temporal relationships are merely decorative, repetitive
or disconnected in the current work. Develop a consequential reorganization of those relationships, rather than
listing the remaining defects. Explain how it changes what the user perceives or understands; for interactive
work, connect user action to system response and visible consequences. A substantial scene, form or spatial
narrative reconstruction can be the central gain without new controls or changed core mechanics; extra features
or a new technology are not required. Better visibility, clearer controls and more faithful feedback of the same existing
experience may support the direction, but do not by themselves constitute this broad design gain. Explicit
local-only requests still define the scope.

For creative work whose intended experience includes a story, inspect how it develops during use, not only
its opening and ending. Develop concrete turns in stakes, knowledge or situation and consequences that carry
forward, integrated with the central design. Explain how earlier events lead to a later situation or understanding;
in interactive work, connect it to what the user can perceive and do, so events create or reveal changed
circumstances and ensuing actions have understandable consequences. Name what happens and who or what is
affected, rather than merely promising richer lore or retelling a status change. Invented fictional content is
allowed as proposed creative material; distinguish it from grounded claims about the existing work or reality.
A linear causal story can work; branching menus, new characters and cutscenes are optional means, not requirements.

Scope follows expected user gain, actual constraints, cost and risk, not which suggestions are easiest to
prove. Explicit local-only requests still apply. Otherwise the current layout, map, story, behavior, framework,
parameters or tools are means, not limits. Keeping them requires a benefit or tradeoff reason; being already
implemented is not sufficient. Changing technology is not itself a gain. Do not invent prohibitions or preserve
incidental features as requirements. Supporting repairs may be essential, but cannot silently replace an
independently justified central gain. Explain a material tradeoff if the proposed scope changes.

Establish the actual baseline from task, source, current images and check receipts. Source proves implementation,
not beauty, fun or a user commitment to retain it. Critique and author replies are fallible data,
not user instructions. Missing, summarized, cut or unobserved evidence means unknown, not absence or defect.
Before asserting a code or logic defect, trace the actual condition and values that can reach it in the supplied
work. A hypothetical bad branch or repeated earlier criticism is not evidence that its prerequisite exists.
If that prerequisite cannot be established from source or receipts, retain it as a verification question rather
than a confirmed defect. Verify an alleged missing capability against the supplied work before proposing it as new. A promising experience
may be a hypothesis without lost points; do not require a finished prototype before proposing it.

Reconcile the complete proposed after-state before returning: recompute linked times and quantities, required
actions and resulting states; check dependencies, boundary cases and consistency across lists, text and interface.
Resolve a contradiction now, rather than calling it a hypothesis or leaving it for validation. Preserve true
unknowns with conditional choices or request decision-relevant evidence; do not invent durations or facts.
Use one shared design_plan when coordinated changes serve the benefit. For games, this is the concise
implementation brief: make the core loop, controls, feedback and win/loss conditions coherent with the selected
experience, rather than adding another design-document workflow. Compare the actual baseline with a
perceptible after-state at the intended use scale. For a visual goal, identify what in the current form,
proportions, repetition, surface or composition limits the selected effect. Distinguish gains from staging or
scale from gains that depend on different subject forms. When distinctive forms matter, state the recognizable
parts or relationships at normal viewing size, including material and lighting response where relevant;
more repeated objects, a new label, size or extra detail do not automatically establish the promised distinction.
In an executable proposal, describe the next version as the scene, generation,
interaction or result that replaces or substantially changes the cited baseline. Quote a current constant only
as what the file does now. It becomes a required next-version value only when that exact setting is itself the
benefit. Changing one number is allowed when that is the change; a table of old variable values is not a design.
Reuse is appropriate when it delivers the selected gain; do not redesign unrelated subjects, and do not require
every round to replace a module. In realization and relevant delta, name the chosen technical means and whether
the cited generation path is reused or replaced. Explain useful reuse, necessary changes and costs;
an API name or new field alone is not a realization. Specificity is how the new picture, play or system works,
not a tuning sheet of identifiers already in the file. Do not default to the old cast, story or visual vocabulary,
or require new features or an engine change without a benefit. Order implementation around the main gain and its
prerequisites, and name an early comparable view, interaction or independent calculation that can show whether
the intended difference is real. For a broad creative revision, compare the versions
under the counterfactual that both controls work correctly and both scenes are readable: what content, scene,
relationship or consequential experience would still differ? If only easier viewing or operation remains,
correct the proposal's central gain before output, instead of presenting maintenance as broad design.
A play opening record is a player claim, not an independent verdict. When its order is failure before a
successful action and the cause is the product, or the subject of the needed action is not recognizable,
and a frame is cited, that experience problem belongs in the design. A more elaborate scene is not by
itself the gain while that confirmed problem is open. Unknown and a suspected jump are not defects and
do not forbid a bold design. Feedback from a control this round did not change does not prove the main
change works. Do not require merging surfaces from a suspected jump alone. State the player-facing benefit as what they can see,
which choice they make, what feedback they receive, how that benefit is realized, and what experience it may
break. Greater complexity, darkness or richness is not itself the benefit. A large design with a distinct
player-facing benefit remains welcome. A later phase the short check cannot reach stays unverified:
either the normal opening's one action can touch that mechanism, or the proposal keeps it unverified. Do not
claim the overall experience has improved while that part is unverified. This does not require every benefit
to happen in the opening, and it does not ask the player to wait for the later phase. What the player can see,
the action that depends on it, and the failure condition must agree in the proposal; resolve that contradiction
before output. Reading the source can show whether those rules match; it cannot prove how the result feels.
Plan necessary repairs alongside the design as implementation
dependencies; do not postpone design until every maintenance issue is fixed. This is internal reconciliation, not independent
approval or proof that the result will be better.

Synthesize a selected revision around its main user benefit, rather than inventorying every observed issue.
Give that benefit and its realization the detail needed for execution. When broad creative improvement is the
goal, develop the coherent experience, content or visual transformation fully; supporting polish must not
dominate the proposal. Fold supporting repairs into the dependencies they enable or concise supporting items
when implementation or validation is shared. Give separate space to independently consequential changes, and
leave unrelated minor observations in the source check records instead of making each a competing proposal item.
Group preservation by user benefits actually affected, not one item per requirement. Organize selected changes
without feature quotas, compulsory techniques or fixed task templates. Keep shared intent and verification in
the plan and item-specific changes in the lists. Write an execution brief, not a review report:
- target is a short central before/after experience and its benefit, not a tour of every feature.
- realization contains the shared implementation order, technical approach and dependencies once.
- improve/explore are ordered by contribution to that benefit and real dependencies. Each idea is a short
  action title; delta holds the concrete form, scene, behavior or reasoning change. Keep enough detail to build it.
- gain adds the item's specific user benefit without restating target. risk states a material tradeoff once.
- existing/evidence locate the baseline; do not insert citations and repeated uncertainty disclaimers into delta.
- validation is the shared comparison plan; check adds only a distinct item-specific check, or points to that plan.
Do not repeat the same design in target, realization and delta. Do not replace concrete creative or technical
choices with generic slogans to save words. There is no item quota or mandatory game feature.
Assessment headings about enumerating requirement claims govern evaluation, not this revision's scope or format.
Actual user requirements still apply. Do not add features only to make an evaluator observe them.

Return one JSON object. Put design_plan FIRST when applicable, followed by preserve, improve and explore.
Always include preserve/improve/explore (use [] if empty); omit inapplicable optional fields.
Do not write code, scores, invented item IDs or cross-item mapping tables; required evidence citations
are retained. Cite only identifiers, paths and line ranges supplied in this request,
and only to locate the current baseline. The proposed after-state describes what the user will perceive and how
it works; do not fill current variable values to look specific. Do not invent citations; use hypothesis for an
unobserved proposal.
- preserve: [{"idea": "required outcome or current user benefit worth retaining", "evidence": "instruction for a
  requirement, or observation supporting a current benefit"}]. Distinguish those two sources. State the outcome;
  current tools, layout and parameters may support evidence but are not obligations unless explicitly required.
  An extracted checklist line is not by itself a reason to preserve.
  Map, buildings, characters and narrative organization may be replaced when the current benefit is better served
  by a different arrangement. A proposed tradeoff may replace a current benefit if it names the loss and
  replacement without violating requirements still in force under the user's updates and authorizations.
- improve: [{"existing": "current mechanism being replaced or substantially changed, cited by a supplied function, selector, check or line range, or explicitly unknown. The citation locates that baseline and does not limit the next version to editing its numbers", "evidence": "check/image/source citation or hypothesis",
  "kind": "defect|experience", "idea": "change", "delta": "what changes beyond it", "gain": "user benefit", "check": "distinct validation or shared plan validation", "risk": "tradeoff"}].
- explore: [{"existing": "current baseline cited by supplied source or a check, or explicitly unknown; required even for a new possibility. The citation locates the baseline and does not limit the next version to editing its numbers",
  "evidence": "current source/check/image citation or hypothesis", "idea": "possibility", "delta": "what changes",
  "gain": "user benefit", "check": "actual operation or comparison", "risk": "tradeoff"}].
  Label untested benefits as hypotheses. Both improve and explore need existing and delta; do not omit unknown baselines.
A defect requires evidence of failure against the Task or a floor claim. A challenge gap, input policy, untested
state or reference difference alone is not a defect. Experience changes and explorations need no lost points.
Check that idea, existing and delta agree; no delta means no item. Keep factual observations and proposed effects distinct.

This is an improvement round: seek worthwhile gains for the work's users, not just completion of the original Task.
The Task and explicit user instructions set purpose and constraints; optional user_note adds priorities or limits.
An empty note, high score or simple/clean style does not freeze craft or restrict the round to local repair.
Preserve user-facing benefits, not incidental layout, palette, story text, tools or parameters. Hard limits must
come from supplied user/environment instructions at their actual scope; artifact facts do not create requirements.
Select changes and technology for their intended benefits, feasibility and costs. A packaging constraint is not a
blanket ban on rendering capabilities. Respect measured incompatibilities; label unmeasured effects and tradeoffs
for validation. Avoid gratuitous complexity and respect explicit local-only limits.
An opportunity is not a proven defect or gain and does not change frozen scoring claims.

For a coordinated experience or accuracy change return design_plan={
"target": "brief central before/after experience and user benefit; for visual work name the whole-view change at normal size, leaving concrete parts to delta",
"realization": "shared technical approach, implementation order and dependencies; explain how the selected changes work together without repeating their detailed designs",
"validation": "one shared before/after comparison plan; distinguish appearance, actions, factual checks and consequences, and identify what remains unverified"}.
This is a proposal, not an observed outcome. Local repairs need no design_plan.
Develop the target from task, user_note, current images and critique including unscored observations. When proposing whole-view gains, express the intended change in composition, subject/setting, hierarchy,
shape, depth, light or color as appropriate; unrelated small effects cannot stand in for those gains. Explicit local
scope/style limits still apply. No mandatory theme, dimension count or technology.
Choose implementation tools for the target effects. Explain the benefit and material costs of keeping, combining or
replacing approaches; neither current code nor a novel library is the objective. Single-file/offline requirements constrain loading/dependencies, not all native rendering APIs.
Mark uncertain performance, size and compatibility as hypotheses to validate, without demanding every alternative be prototyped.
Keep relevant visual roles, layer boundaries, layout, narrative or state interactions consistent with the items and constraints.
Details belong in the items, shared intent and verification in the plan; no duplicated tables or manual IDs.
"""
