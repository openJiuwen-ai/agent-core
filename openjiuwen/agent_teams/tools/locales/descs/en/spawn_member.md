Create a new team member with domain expertise. Used to split tasks by domain and assign them to specialized members for execution.

| Parameter | Usage |
|---|---|
| **member_name** | Unique member name (semantic slug). It must not conflict with any existing member (e.g., `backend-dev-1`) |
| **display_name** | Member display name that reflects the role (e.g., "Backend Developer Expert") |
| **desc** | Long-term role definition: describe professional background, core expertise, preferred task scope, and boundaries the member should not own |
| **prompt** | First startup instruction: define initial priorities, constraints, or coordination needs without repeating the generic workflow |

You must call build_team before calling spawn_member. Call order: build_team → task_manager → spawn_member → send_message. spawn_member only creates the member record (status: UNSTARTED); on the first send_message call, the system automatically starts all unstarted members. Call shutdown_member after the member completes work. If member_name already exists, creation will fail — use a non-conflicting name. Use desc to define the member's long-term professional role; use prompt to specify the first instruction the member receives at startup. Do not write prompt as generic startup filler such as "start working" or "check the task list"; specify what this member should prioritize when it starts.