---
description: How to release a new version of the Smarting HOME integration to HACS
---

# Release Workflow — Smarting HOME

This workflow MUST be followed every time changes are committed to the repository. Every commit = new version = new GitHub Release. HACS only detects updates via GitHub Releases, not bare tags.

## Prerequisites (one-time setup)

The user must authorize GitHub CLI before the first release. Run this command ONCE:

```bash
gh auth login
```

Follow the prompts: GitHub.com → HTTPS → Authenticate with browser.

## Release Steps

### 1. Determine the new version number

// turbo
Read the current version from `manifest.json`:

```bash
grep '"version"' custom_components/smartinghome/manifest.json
```

Bump the version following SemVer:
- **PATCH** (1.5.1 → 1.5.2): bug fixes, small UI tweaks, text changes
- **MINOR** (1.5.x → 1.6.0): new features, new sensors, new tabs
- **MAJOR** (1.x → 2.0.0): breaking changes, full rewrites

### 2. Update version in ALL 3 locations

The version string appears in exactly 3 files. ALL must be updated:

1. **`custom_components/smartinghome/manifest.json`** → `"version": "X.Y.Z"`
2. **`README.md`** → version badge: `Version-X.Y.Z-2ECC71`
3. **`custom_components/smartinghome/frontend/panel.js`** → Settings tab info div: `Wersja integracji</span><span class="vl">X.Y.Z`

### 3. Commit changes

// turbo
```bash
git add -A && git commit -m "release: vX.Y.Z — <short description of changes>"
```

### 4. Create git tag

// turbo
```bash
git tag vX.Y.Z
```

### 5. Push to GitHub (code + tag)

```bash
git push origin main --tags
```

### 6. Create GitHub Release (CRITICAL for HACS)

```bash
gh release create vX.Y.Z --title "vX.Y.Z — <title>" --notes "<release notes in markdown>"
```

Release notes should include a `### ✨ What's New` section listing all changes with emoji bullets.

Example:
```bash
gh release create v1.5.2 --title "v1.5.2 — Bug fixes" --notes "### ✨ What's New

- 🐛 Fixed entity selector for weather sensors
- 📱 Improved mobile responsiveness"
```

### 7. Verify

// turbo
```bash
gh release list --limit 3
```

Confirm the new release appears at the top.

## ⚠️ Important Rules

- **NEVER** commit without bumping the version
- **NEVER** push a tag without creating a GitHub Release
- **ALWAYS** update all 3 version locations (manifest, README badge, panel.js settings)
- Release titles should be bilingual-friendly (English preferred for GitHub, Polish ok for descriptions)
