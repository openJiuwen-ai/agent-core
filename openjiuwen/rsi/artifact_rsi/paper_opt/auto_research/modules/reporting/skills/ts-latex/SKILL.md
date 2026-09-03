---
name: ts-latex
description: Assemble and compile the final PDF, repairing compile errors. Use last, after ts-review.
---

# ts-latex

Run (both `{SKILLS_DIR}` and `{PAPER_WORKSPACE}` are absolute paths given
in your system prompt — pass `{PAPER_WORKSPACE}` exactly as shown, do not
`cd` there instead):

```
python {SKILLS_DIR}/ts-latex/scripts/compile.py {PAPER_WORKSPACE}
```

It reads `title.txt`, `keywords.txt`, and every file under `sections/`
from `{PAPER_WORKSPACE}`, assembles `main.tex`, compiles it with
latexmk/pdflatex, and prints JSON.

If `"success": true`, you are done.

If not, read `error_lines` (the compiler's own `! <error>` messages) and
`log_tail`. Make the smallest safe fix in the specific section `.tex`
file the error points to: unescaped special characters, unclosed
environments, malformed table/figure syntax, an undefined command. Do
not rewrite prose, remove citations, or change numbers — fix only what's
breaking the build. Then run the compile script again.

Repeat up to 4 times total. If it's still failing after that, stop and
report which section and which compiler error are still unresolved — do
not claim success the script didn't confirm.