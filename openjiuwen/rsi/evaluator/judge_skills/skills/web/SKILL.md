---
name: web
description: Evaluate browser-delivered websites and interactive web applications from real runtime and user-visible evidence.
kind: judge-skill
artifact_markers_any: index.html,index.htm
task_markers_any: website,web page,web app,browser,HTML,网页,网站,浏览器
evidence_profile: web_browser
required_case_evidence: web_verification
runtime_failure_ceiling: 0.65
runtime_smoke_ceiling: 0.85
---

# Web Judge Skill

Evaluate the delivered website as a real browser experience, not as a collection
of HTML, CSS, and JavaScript strings.

## Evidence priority

1. Prefer machine-collected browser execution, runtime errors, declared case
   verification, viewport measurements, and interaction-state evidence.
2. Use full-file deterministic HTML/CSS/JavaScript summaries for structure and
   implementation coverage.
3. Treat screenshots and source excerpts as supporting evidence.
4. Treat agent-authored QA reports and delivery summaries as claims, not proof.

## Quality contract

- The entry point loads successfully and the primary experience is visible.
- Required interactions produce coherent state changes and visible feedback.
- The main workflow has a reachable success, completion, or recovery state when
  the request requires one.
- Runtime exceptions or broken primary controls are material functional defects.
- Layout remains usable at the requested viewports. Interactive controls should
  provide practical pointer and touch targets; machine measurements below 44 CSS
  pixels are evidence of a mobile usability gap, not an automatic domain-wide
  failure when mobile use was not requested.
- Visual polish must be judged from the actual task and artifact evidence. Do not
  reward decorative complexity that does not improve the requested experience.

## Runtime interpretation

- A failed browser launch, page execution failure, or runtime exception affecting
  the primary experience limits the overall score to 0.65.
- Passing smoke evidence proves only that the artifact starts and basic controls
  can be observed. Without stronger workflow evidence, it limits confidence and
  the overall score to 0.85.
- A passed case verification contract is strong evidence for the declared flow,
  but it does not replace review of the user's broader quality requirements.
