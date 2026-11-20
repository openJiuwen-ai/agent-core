# Root: two accounts for different audiences

Return narrative, display_summary and updated problems in the same report_summary call, all describing the same selected execution.

narrative is saved as trajectory_summary for later diagnosis and attribution. Synthesize the goal, key decisions and operations, cross-agent handoffs, consequential results, recovery, verification evidence and remaining issues, with a few supplied short node references. Reconcile issue states with this account. Do not repeat each agent's chronology or parameter tables.

display_summary is a user-facing account of the execution process, not just an outcome or a copy of narrative. Explain what was requested, how the main stages and any collaboration addressed it, and what was actually verified and delivered. Use natural, concise sentences or short paragraphs; describe actions by their purpose, without internal IDs, node/segment references, approval logs, tool-call lists or script implementation details. Mention only evidence-supported gaps important to the goal or confidence in delivery. Omit minor recovered noise and do not invent a caveat when none is supported.

Both accounts must preserve uncertainty and distinguish reported completion from observed success.
