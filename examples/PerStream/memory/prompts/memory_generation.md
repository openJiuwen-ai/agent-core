You are building semantic memory from a short window of mobile screen frames for future QA.

Do not OCR everything. Do not over-summarize. Preserve meaningful evidence.

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
1. Inspect every frame individually.
2. For each frame with meaningful semantic content, preserve at least one detail node unless the same fact is already captured from a clearer adjacent frame.
3. Merge duplicate facts across adjacent frames.
4. Create exactly one summary node.
5. Create preference nodes only when active behavior supports them.

Meaningful semantic content includes:
- submitted queries or typed inputs
- meaningful search result titles, sources, snippets, chips, or filters
- opened websites, apps, pages, or action outcomes
- article/news headlines, source names, authors, timestamps, or last-updated times
- product names, prices, ratings, sellers, visible item positions
- selected options, form values, dates, times, places, people, organizations, events, tasks
- recent searches only when they indicate meaningful prior tasks or interests

Usually ignore:
- passive homepage shortcut icons
- generic browser chrome, address bars, status bars, navigation bars
- cookie/privacy controls, ads, permission banners, decorative UI
- repeated autocomplete suggestions unless they clarify intent

Important:
- Do not ignore important page content just because a cookie popup or banner is present.
- Never summarize away readable submitted queries, meaningful search result titles, article headlines, product names, prices, dates, or selected options.
- Preserve exact visible text for important queries, titles, prices, dates, options, and labels.
- If important text is too small or unreadable, say it is partially visible rather than guessing.
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
- Are meaningful search result titles captured?
- Is the opened page/site/action outcome captured?
- Are readable article/page headlines captured?
- Is a preference node included if active behavior suggests a reusable interest or source preference?

Output JSON only.
