# Development workflow

The `main` branch should always remain deployable.

For every future change:

1. keep secrets, local configuration, databases, uploaded resumes, virtual environments, and generated archives out of Git;
2. make one focused change at a time;
3. run `bash scripts/check.sh` before committing; and
4. create a descriptive commit, preferably using `feat:`, `fix:`, `docs:`, `test:`, `refactor:`, or `chore:`.

Do not rewrite published history. When the project is connected to a remote repository, use a feature branch for larger changes and merge only after checks pass.
