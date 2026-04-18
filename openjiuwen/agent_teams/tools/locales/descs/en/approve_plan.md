Approve or reject a member's execution plan. **Only exposed to the Leader when the team uses `teammate_mode=plan_mode`**; in build_mode members do not submit plans and this tool will not appear in the tool list.

After the Leader calls this tool, the system sends the decision to that member; if approved, the member's claimed tasks move into an executable state (plan_approved); if rejected, the member should revise and resubmit.

| Parameter | Usage |
|---|---|
| **member_name** | member_name of the member who submitted the plan; it should refer to a valid member in the current team |
| **approved** | Whether to approve the member's current plan. true means the member may proceed to implementation; false means revise and resubmit |
| **feedback** | Review feedback. When rejecting, explain the problem and the expected revision direction; when approving, you may still include constraints or reminders |

Use when:
- a member has submitted a plan and the Leader must decide whether implementation may begin
- the plan is generally correct but needs extra constraints or reminders before execution
- the plan has wrong direction, unclear scope, missing dependencies, or acceptance risks and must be revised

Notes:
- Approval does not mean the task is complete; it means the member may proceed with implementation
- Do not reject with vague feedback like "rewrite it" or "think again"; give concrete revision guidance
- If the member has no pending plan or no claimed tasks, approval may not advance any task state