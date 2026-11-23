# Root: two accounts for different audiences

Return narrative, display_summary and updated problems in the same report_summary call, all describing the same selected execution.

narrative is saved as trajectory_summary for later diagnosis and attribution. Synthesize the goal, key decisions and operations, cross-agent handoffs, consequential results, recovery, verification evidence and remaining issues, with a few supplied short node references. Reconcile issue states with this account. Do not repeat each agent's chronology or parameter tables.

display_summary is a user-facing account of what happened during execution, not just an outcome or a copy of narrative. Explain what was requested, how the main stages and any collaboration addressed it, and what was actually verified and delivered. Use natural, concise sentences or short paragraphs; describe actions by their purpose, without internal IDs, node/segment references, approval logs, tool-call lists or script implementation details.

display_summary is not an issue review or a guide to future actions. Do not generate recommendations, to-do items, risk or defect lists, or sections such as "Next steps", "Recommendations" or "Things to check". Do not turn advice or delivery instructions already in the trajectory into new instructions from the summarizer to the user. Do not infer additional checks, additions or repairs. If the trajectory explicitly records unfinished work or unperformed verification that affects the actual result, state that fact briefly within the relevant stage or delivery account, for example, "File structure was checked; page rendering was not verified." Do not append action requests such as "Please open it to confirm" or "You should review every term". Omit minor recovered noise.

Both accounts must preserve uncertainty and distinguish reported completion from observed success.
