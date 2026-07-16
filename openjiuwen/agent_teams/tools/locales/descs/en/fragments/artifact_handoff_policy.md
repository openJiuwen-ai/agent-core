**The channel is decided by the shape of the content, not by the recipient.**

**By default, just put the text in `content` and send it.** Instructions, requests, acknowledgements, short replies, progress updates, conclusions, decisions, questions and answers — anything you can say in a few sentences goes straight into the message. Do **not** write a file first and send its path for these: that only buys one extra disk write plus one extra read on the recipient's side.

**Only finished artifacts go to disk, with the message carrying just the path.** Research reports, full proposals, code, data tables, long checklists, synthesis or delivery documents — content that is complex, bulky, or meant to be consulted repeatedly — must first be written with `write_file` into the shared team workspace under `.team/`; `content` then carries only the file path plus a one- or two-sentence summary, never the body itself. Files written to your own working directory (especially under worktree isolation) are unreadable by other members — they must land in `.team/`.

When unsure, judge by length: if it fits on one screen, send it directly; if the body is long enough to scroll, or the recipient may need to look it up again later, write the file and send the path.

**This is enforced**: past 2000 characters of `content` the tool fails the call and nothing is delivered — you must switch to the file channel and resend. This holds for every recipient, `user` included. Do not split an oversized body across several messages to get around it; that only makes it harder to read.
