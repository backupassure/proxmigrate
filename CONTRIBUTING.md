# Contributing to ProxOrchestrator

Welcome to the ProxOrchestrator project. This guide explains how we work together as a team using Git and GitHub. Read this before you start writing any code.

---

## The Big Picture

We use a workflow called **Gitflow**. Think of it like this:

```
main              ← what the public downloads and uses
  ↑
dev               ← where we test finished features together
  ↑
feature/your-feature   ← where you do your work
hotfix/your-fix        ← for urgent fixes only - cloned off main then pulled into main then pushed into dev and bellow
bugfix/your-fix        ← for bug fixes that don't impact the working functionality - cloned off of dev, pulled into dev then pushed into main at next release cycle, merge bellow for existing branches

cleanup - After a feature, hotfix, bugfix is validated and approved the branch should be deleted and any new features or bugfixes are cloned off of dev. Do not allow branches to go stale
```

The key rule is simple:

> **Nobody writes code directly in `dev` or `main`. Ever.**

All work happens in a branch you create. When you are done, you ask for it to be reviewed before it gets merged in.

---

## Branch Rules

### `main`
- This is the public release branch. Users clone and pull from here.
- Only receives merges from `dev` after testing is complete.
- You cannot push directly to `main`. A pull request is required.

### `dev`
- This is the testing branch. After a feature is reviewed and approved, it lands here first.
- We test on the test server after each merge into `dev`.
- When `dev` is stable and ready for release, it gets merged into `main`.
- You cannot push directly to `dev`. A pull request is required.

### `feature/your-feature-name`
- This is where all new features are built.
- Always created from `dev`.
- When done, a pull request is opened targeting `dev`.
- Naming examples:
  - `feature/lxc-support`
  - `feature/password-reset`
  - `feature/job-cancellation`

### `hotfix/your-fix-name`
- Used only when there is a bug in `main` that needs fixing right now.
- Always created from `main` (not `dev`) because the bug is in production.
- When done, a pull request is opened targeting `main`.
- After it merges to `main`, it must also be merged back into `dev` so the fix is not lost.
- Naming examples:
  - `hotfix/cpu-type-default`
  - `hotfix/login-redirect-loop`

---

## Setting Up Your Machine

You only need to do this once.

### 1. Clone the repository

```bash
git clone git@github.com:ForgedIO/ProxOrchestrator.git
cd ProxOrchestrator
```

### 2. Check your remotes

```bash
git remote -v
```

You should see `origin` pointing to `github.com:ForgedIO/ProxOrchestrator.git`. If not, add it:

```bash
git remote add origin git@github.com:ForgedIO/ProxOrchestrator.git
```

### 3. Set your name and email (if not already done)

```bash
git config --global user.name "Your Name"
git config --global user.email "you@example.com"
```

---

## Starting a New Feature — Step by Step

### Step 1. Make sure your `dev` is up to date

Before you create a new branch, always pull the latest `dev` first. This way your new branch starts from the most current code.

```bash
git checkout dev
git fetch origin
git pull origin dev
```

`git fetch` checks what has changed on GitHub without touching your local files. `git pull` then downloads and applies those changes.

### Step 2. Create your feature branch

```bash
git checkout -b feature/your-feature-name
```

This creates a new branch and switches you to it. You are now working in your own space and cannot affect `dev` or `main`.

### Step 3. Write your code

Work normally. Save files, test your work, repeat.

### Step 4. Save your work with commits

A commit is a save point. Do this often — at least once a day, and any time you finish a piece of work.

```bash
git add apps/yourapp/views.py templates/yourapp/page.html
git commit -m "Add container detail view with CPU and memory stats"
```

**Tips for good commit messages:**
- Write what the change does, not what you did. "Add cancel button to dashboard" not "I added a cancel button."
- Keep it short — one line under 72 characters.
- If you need to explain more, leave a blank line and add detail below.

### Step 5. Push your branch to GitHub

```bash
git push origin feature/your-feature-name
```

Do this at the end of each day. It backs up your work and lets the team see your progress.

---

## Keeping Your Branch Up to Date

If you are working on a feature for more than a day or two, changes will land in `dev` that you do not have yet. You need to pull those in regularly to avoid a big messy merge at the end.

Do this at least every couple of days:

```bash
# Save your current work first
git add .
git commit -m "WIP: work in progress"

# Pull dev into your branch
git fetch origin
git merge origin/dev
```

If Git reports a conflict (two people edited the exact same lines), see the **Resolving Conflicts** section below.

Push your branch again after:

```bash
git push origin feature/your-feature-name
```

---

## Opening a Pull Request

When your feature is finished and tested locally, open a pull request on GitHub.

### Step 1. Push your final changes

```bash
git push origin feature/your-feature-name
```

### Step 2. Go to GitHub

Open `https://github.com/ForgedIO/ProxOrchestrator` in your browser.

GitHub will usually show a yellow banner saying your branch was recently pushed with a **"Compare & pull request"** button. Click it.

If you do not see the banner:
1. Click the **"Pull requests"** tab
2. Click **"New pull request"**
3. Set **base** to `dev` and **compare** to your feature branch

### Step 3. Fill in the pull request

- **Title** — short description of what this does. Example: `Add job cancellation for import pipeline`
- **Description** — explain what you built and why. List any important decisions you made. Mention if there are any known issues or things the reviewer should look at closely.
- Check the target branch is `dev` (not `main`)

### Step 4. Submit and wait for review

The reviewer (currently Chris) will read through your code on GitHub and either:
- **Approve** it — you can merge
- **Request changes** — make the changes, push again, the PR updates automatically

---

## After Your PR is Approved

Once approved, the reviewer will merge it into `dev`. You do not need to do anything.

After the merge, update your local `dev`:

```bash
git checkout dev
git pull origin dev
```

Your feature branch is no longer needed. You can delete it:

```bash
# Delete it locally
git branch -d feature/your-feature-name

# Delete it on GitHub
git push origin --delete feature/your-feature-name
```

---

## Hotfixes — Urgent Production Bugs

A hotfix is only for bugs that are broken in `main` right now and cannot wait for the normal feature process.

### Step 1. Branch from `main`

```bash
git checkout main
git pull origin main
git checkout -b hotfix/description-of-bug
```

### Step 2. Fix the bug, commit, push

```bash
git add apps/affected/file.py
git commit -m "Fix CPU type default causing VM boot failure"
git push origin hotfix/description-of-bug
```

### Step 3. Open two pull requests

- One targeting `main` — for the immediate fix
- One targeting `dev` — so the fix does not get lost when dev eventually merges to main

### Step 4. After both are merged

```bash
git checkout dev
git pull origin dev
git checkout main
git pull origin main
```

---

## Resolving Conflicts

A conflict happens when you and someone else edited the exact same lines of the same file. Git cannot decide which version to keep so it asks you to choose.

You will see this message:

```
CONFLICT (content): Merge conflict in apps/importer/views.py
Automatic merge failed; fix conflicts and then commit the result.
```

### Step 1. Open the conflicted file

Look for the conflict markers Git added:

```python
<<<<<<< HEAD
    cpu_type = vm_config.get("cpu_type", "host")
=======
    cpu_type = vm_config.get("cpu_type", "x86-64-v2-AES")
>>>>>>> origin/dev
```

Everything between `<<<<<<< HEAD` and `=======` is your version.
Everything between `=======` and `>>>>>>>` is the other version.

### Step 2. Edit the file to keep the right version

Delete the conflict markers and keep what is correct. In this example we want `host`:

```python
    cpu_type = vm_config.get("cpu_type", "host")
```

### Step 3. Mark it resolved and commit

```bash
git add apps/importer/views.py
git commit -m "Merge origin/dev — resolve cpu_type conflict"
```

### Step 4. If you are not sure which version to keep

Stop. Message Chris before you guess. A wrong resolution is worse than a delay.

---

## Checking What Is Going On

These commands help you understand the state of your work at any time.

```bash
# What branch am I on? What files have I changed?
git status

# What exactly did I change in each file?
git diff

# History of commits on this branch
git log --oneline

# See all branches (local and remote)
git branch -a

# See what is on GitHub without changing anything local
git fetch origin
git status
```

---

## The Golden Rules

1. **Never commit directly to `dev` or `main`.**
2. **Always pull the latest `dev` before starting a new branch.**
3. **Pull `dev` into your feature branch every couple of days.**
4. **Push your branch to GitHub at the end of every working day.**
5. **One feature per branch.** Do not mix two unrelated features in one branch.
6. **Tell Chris before you merge.** A quick message — "merging now" — prevents both of you touching the same thing at the same time.
7. **If something feels wrong, ask before you push.**

---

## Quick Reference Card

```bash
# Start a new feature
git checkout dev && git pull origin dev
git checkout -b feature/my-feature

# Save your work
git add path/to/file.py
git commit -m "What this change does"
git push origin feature/my-feature

# Keep your branch current
git fetch origin
git merge origin/dev
git push origin feature/my-feature

# After your PR is merged
git checkout dev && git pull origin dev
git branch -d feature/my-feature
git push origin --delete feature/my-feature

# Emergency hotfix
git checkout main && git pull origin main
git checkout -b hotfix/the-problem
# fix, commit, push, open PR to main AND dev
```

---

## Questions

If you are not sure about something, ask before you act. A wrong push to `main` or a bad merge is much harder to fix than a question is to ask.
