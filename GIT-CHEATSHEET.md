# Git Cheat Sheet — ProxMigrate Workflow

## Everyday Commands

| Command | When to use |
|---|---|
| `git status` | See what's changed |
| `git diff` | See the actual changes |
| `git add <file>` | Stage a specific file for commit |
| `git add .` | Stage all changes (be careful) |
| `git commit -m "message"` | Commit staged changes |
| `git push` | Push commits to GitHub |
| `git pull` | Pull latest from GitHub |
| `git log --oneline -10` | See last 10 commits |

## Branches

| Command | When to use |
|---|---|
| `git branch` | List local branches |
| `git branch -a` | List all branches (local + remote) |
| `git checkout <branch>` | Switch to an existing branch |
| `git checkout -b <branch>` | Create a new branch and switch to it |
| `git branch -d <branch>` | Delete a local branch (after merge) |
| `git fetch --all` | Download all remote branches (doesn't change your code) |
| `git fetch --prune` | Fetch and remove stale remote branches that were deleted on GitHub |
| `git config --global fetch.prune true` | Auto-prune on every fetch (run once) |

## Branch Types — When to Use What

| Type | Branch off | Merge to | When to use |
|---|---|---|---|
| **Feature** | `dev` | `dev` | New functionality, enhancements, or significant additions. Goes to `main` with the next release. |
| **Bugfix** | `dev` | `dev` | Non-urgent bug fixes found during development or testing. Goes to `main` with the next release. |
| **Hotfix** | `main` | `main` then `dev` | Urgent production fix that can't wait for the next release. Merged to `main` immediately, then synced back to `dev`. |

### How to tell the difference

- **Feature** — "We want to add hardware presets to the import page." New capability that didn't exist before.
- **Bugfix** — "The row disappears when you click stop." Something is broken but it's not breaking production right now, or it was found during dev/testing.
- **Hotfix** — "The console stopped working after the server rebooted." Production is broken and users are affected. Needs to go out now, can't wait for the next dev → main merge.

### Branch naming

| Type | Name format | Example |
|---|---|---|
| Feature | `feature/short-description` | `feature/hardware-presets` |
| Bugfix | `bugfix/short-description` | `bugfix/row-disappears` |
| Hotfix | `hotfix/short-description` | `hotfix/console-disconnect` |

### Release flow

```
feature/xyz ──→ dev ──→ main (scheduled release)
bugfix/xyz  ──→ dev ──→ main (scheduled release)
hotfix/xyz  ──→ main ──→ dev (emergency, sync back)
```

## Feature Workflow (new work)

```bash
# 1. Start from dev
git checkout dev
git pull

# 2. Create feature branch
git checkout -b feature/my-feature

# 3. Do your work, commit as you go
git add <files>
git commit -m "Add the thing"

# 4. Push to GitHub
git push -u origin feature/my-feature

# 5. Create PR on GitHub (web UI or CLI)
gh pr create --base dev --head feature/my-feature --title "Add the thing"

# 6. After PR is approved and merged, clean up
git checkout dev
git pull
git branch -d feature/my-feature
```

## Bugfix Workflow (non-urgent fix)

```bash
# 1. Start from dev
git checkout dev
git pull

# 2. Create bugfix branch
git checkout -b bugfix/fix-something

# 3. Fix, commit, push
git add <files>
git commit -m "Fix the thing"
git push -u origin bugfix/fix-something

# 4. Create PR to dev, get it reviewed, merge
# Goes to main with the next release merge (dev → main)

# 5. Clean up
git checkout dev
git pull
git branch -d bugfix/fix-something
```

## Hotfix Workflow (urgent production fix)

```bash
# 1. Start from main
git checkout main
git pull

# 2. Create hotfix branch
git checkout -b hotfix/fix-something

# 3. Fix, commit, push
git add <files>
git commit -m "Fix the thing"
git push -u origin hotfix/fix-something

# 4. Create PR to main, get it reviewed, merge

# 5. Sync the fix into dev
git checkout dev
git pull
git merge main
git push origin dev

# 6. Clean up
git branch -d hotfix/fix-something
```

## Testing on the Server

```bash
# Switch server to a branch for testing
cd /opt/proxmigrate
git fetch --all
git checkout <branch-name>
git pull
sudo ./update.sh

# Switch back to dev after testing
git checkout dev
git pull
sudo ./update.sh
```

## Pull Requests (CLI)

| Command | When to use |
|---|---|
| `gh pr create --base dev --title "Title"` | Create a PR |
| `gh pr list` | List open PRs |
| `gh pr merge <number> --merge` | Merge a PR |
| `gh pr merge <number> --merge --admin` | Merge with admin bypass |

## Undo Mistakes

| Command | When to use |
|---|---|
| `git checkout -- <file>` | Discard changes to a file (before commit) |
| `git reset HEAD <file>` | Unstage a file (before commit) |
| `git stash` | Temporarily save changes without committing |
| `git stash pop` | Bring back stashed changes |

## Release Process (dev → main)

Before merging `dev` into `main`, update versioning on `dev`:

1. Update `README.md` — bump the build date and add changelog entries for everything new since the last release
2. Commit: `git commit -m "Update version and changelog for release"`
3. Push to `dev`
4. Create PR from `dev` → `main` and merge

## Golden Rules

1. **Never work directly on `main` or `dev`** — always use a branch
2. **Pull before you branch** — `git pull` before `git checkout -b`
3. **Commit often** — small commits are easier to review and revert
4. **Write meaningful commit messages** — future you will thank present you
5. **Delete merged branches** — keep the repo tidy
6. **Update the README changelog** — before every release merge to main
