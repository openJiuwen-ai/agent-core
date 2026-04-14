Disband the team and delete all resources (team record, members, tasks — cascade delete).

**IMPORTANT**: clean_team will fail if any member is not in SHUTDOWN status. Use shutdown_member to close every member first, then call clean_team.

Call after all tasks are completed and results are summarised. Returns: {success, data: {team_name}} on success.