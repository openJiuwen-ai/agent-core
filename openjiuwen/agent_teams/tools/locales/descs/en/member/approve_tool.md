Approve or reject a member's pending tool call request. **Only exposed to the Leader when the team uses `teammate_mode=plan_mode`**; in build_mode this tool will not appear in the tool list.

These requests typically occur when a tool call is blocked by a rail, a safety policy, or another approval mechanism. After the Leader calls this tool, the decision is sent back to the corresponding member; if approved, the member may continue handling that tool call; if rejected, the member receives feedback and must continue in another way.

| Parameter | Usage |
|---|---|
| **member_name** | member_name of the member who initiated the tool approval request; it should refer to a valid member in the current team |
| **tool_call_id** | The interrupted tool_call_id to resume; it must correspond to the specific tool call in the current approval request |
| **approved** | Whether to approve this tool call. true means the member may continue; false means reject and require a different approach |
| **feedback** | Review feedback. When rejecting, explain the reason and the alternative direction; when approving, you may still add boundaries, risk reminders, or usage constraints |
| **auto_confirm** | Whether to auto-approve future calls to the same tool. Set this to true only when you explicitly accept continued use of that tool class |

Use when:
- a member's tool call is blocked by an approval mechanism and the Leader must decide whether the current request may proceed
- the tool call may continue, but only with added boundaries, scope limits, or safety reminders
- the current tool call should not proceed and the member must switch to a safer or more appropriate approach

Notes:
- Approval applies to this specific pending tool call; it does not mean the member's overall task is approved
- Do not reject with vague feedback like "no" or "not allowed"; provide an actionable revision direction or alternative
- auto_confirm=true lowers the approval bar for future calls to the same tool and should not be enabled just for convenience