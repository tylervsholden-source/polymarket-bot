Analyze all staged and unstaged changes in the repository. Then:

1. Run `git status` and `git diff` to understand all changes
2. Group related changes logically
3. Write a semantic commit message following Conventional Commits format:
   - `feat:` for new features
   - `fix:` for bug fixes
   - `refactor:` for code restructuring
   - `chore:` for maintenance tasks
   - `docs:` for documentation
   - `test:` for test changes
   - `perf:` for performance improvements
4. Stage the relevant files (prefer specific files over `git add .`)
5. Create the commit with a clear, concise message that explains WHY, not just WHAT
6. Show the commit result with `git log --oneline -1`

If there are unrelated changes, suggest splitting into multiple commits.
