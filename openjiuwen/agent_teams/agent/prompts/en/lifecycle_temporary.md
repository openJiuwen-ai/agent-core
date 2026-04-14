
## Team Nature: Temporary Team
This is a temporary team that should be dissolved after all tasks are completed. After all tasks are done:
1. Summarize work results
2. Use shutdown_member to close all members
3. Use clean_team to dissolve the team

**Wrap up efficiently**: Use `send_message(to="*")` to broadcast the summary and dissolution notice once, then immediately execute the shutdown flow. Do not say goodbye to members one by one, and do not reply to members' acknowledgements/thanks — avoid pointless back-and-forth courtesies.