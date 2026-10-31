## Default scope: this team's bound expert group

This team was assembled from an expert group (its name and members are in the system prompt section "Selected expert group"). **Without `group`, `list` and `get` stay inside that group**: `list` returns only its spawnable members and `get` looks `name` up as a member name. Pass `group` explicitly to look outside it — another group's name, or `"*"` for the single experts. Look inside the bound group first; go outside only when it has no suitable expert.
