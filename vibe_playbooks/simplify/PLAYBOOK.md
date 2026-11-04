---
name: simplify
description: Make the code the goal names shorter and clearer without changing what it does.
tools: list_files, read_file, search, edit_file, write_file, run_check, ask, finish
---
The goal is clearer code that does exactly what it did before.

1. Read the files the goal names. If it names none, ask which part to simplify.
2. Pick the changes worth making: repeated code that can become one function, a long function that can be split,
   names that say nothing, dead code, a needless copy or loop. Leave working code alone when the change would only
   be a matter of taste.
3. Before you change a function, search for every place that uses it, so nothing that calls it breaks.
4. Make small edits, one at a time. Keep every name that other files use. Do not add new features.
5. If the project has tests and you can run them, run them after your edits.
6. Finish with what you simplified, file by file, and what you checked. Say plainly anything you left as it was and
   why.
