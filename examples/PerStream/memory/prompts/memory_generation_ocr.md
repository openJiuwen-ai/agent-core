You are building semantic memory from OCR text extracted from a short window of mobile screen frames.

The input is not raw images. Each frame contains OCR lines extracted from a screenshot. OCR may contain broken lines, duplicated lines, incorrect characters, status-bar text, browser chrome, cookie/privacy banners, and other noise.

Generate semantic memory nodes from the OCR evidence. Do not copy OCR text wholesale. Preserve meaningful evidence with high recall.

Node types:
- detail: one meaningful, directly visible fact that may help future QA.
- summary: one compact event-level summary of the window.
- preference: a reusable possible preference or interest hypothesis supported by active behavior in the window. Do not output confidence; preference strength is estimated later from repeated time_ids.

Return valid JSON only:

{
  "nodes": [
    {
      "node_type": "detail",
      "description_text": "...",
      "related_frame_ids": ["event_id_local_frame_id"],
      "time_ids": [0]
    }
  ]
}

Process:
1. Read every frame's OCR lines individually.
2. For each frame with meaningful semantic content, preserve detail nodes unless the same fact is already captured from a clearer adjacent frame.
3. Merge duplicate facts across adjacent frames.
4. Create exactly one summary node.
5. Create preference nodes only when active behavior supports them.
6. Each node must cite only frame_ids whose OCR text supports the description.

Detail density:
- A window usually needs 4-12 detail nodes.
- Content-dense windows may need more than 12 detail nodes.
- Empty, repeated, or mostly-noise windows may need fewer.
- A single frame may produce multiple detail nodes when it contains multiple independent facts.
- Do not collapse independent facts into one broad detail node just to keep the node count low.

Meaningful semantic content includes:
- submitted queries or typed inputs
- meaningful search result titles, sources, snippets, chips, or filters
- opened websites, apps, pages, or action outcomes
- article/news headlines, source names, authors, timestamps, or last-updated times
- product names, prices, ratings, sellers, visible item positions when OCR gives enough evidence
- selected options, form values, dates, times, places, people, organizations, events, tasks
- recent searches when they indicate meaningful prior tasks or interests

Usually ignore:
- passive homepage shortcut icons
- generic browser chrome, address bars, status bars, navigation bars
- cookie/privacy controls, ads, permission banners, decorative UI
- OCR fragments that are unreadable or do not form meaningful evidence
- repeated autocomplete suggestions unless they clarify intent

Important:
- Do not ignore important page content just because a cookie popup or banner is present.
- An "opened/accessed page" detail does not replace details for readable page content on that page.
- If a page is opened and also shows a readable headline, title, field, price, date, email, account number, or selected option, create separate detail nodes for those contents.
- Never summarize away readable submitted queries, meaningful search result titles, article headlines, product names, prices, dates, emails, account numbers, selected options, or labels.
- Preserve exact visible text for important queries, titles, prices, dates, emails, account numbers, options, and labels.
- If OCR text is broken across lines, reconstruct it when the reconstruction is obvious.
- If OCR text is too corrupted to trust, say it is partially visible rather than guessing.
- If the same fact appears in many adjacent frames, cite the clearest 1-3 frames.
- Do not cite frames that do not show the described information.

Preference rules:
- Create preference nodes when the window shows active behavior suggesting a reusable interest, source preference, content preference, product preference, activity preference, or recurring need.
- Query -> result -> opened page is strong evidence for a possible interest or source preference.
- Use cautious wording, such as "The user may be interested in..." or "The user may prefer...".
- Do not infer preferences from passive icons, ads, cookie dialogs, browser chrome, or irrelevant suggestions.
- Do not output confidence values.

Before final output, check:
- Is the submitted query or typed input captured?
- Are all meaningful search result titles captured as separate details when they are distinct results?
- Is the opened page/site/action outcome captured?
- Are readable article/page headlines captured as separate details, not only implied by an opened-page detail?
- Are visible fields, prices, dates, emails, account numbers, selected options, and action outcomes captured separately when present?
- Is a preference node included if active behavior suggests a reusable interest or source preference?

Output JSON only.
