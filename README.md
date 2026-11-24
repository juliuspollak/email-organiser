# Personal Email Organiser

Private source repository for a Home Assistant OS application that organises **personal Gmail and Yahoo** messages.

## Current status

Migration in progress. The existing Home Assistant installation is **not managed by this repository yet**. Do not install or enable automatic mailbox actions from this repository until its code and builds have been tested.

## Safety rules

- Never commit OAuth credentials, Gmail tokens, Yahoo app passwords, personal addresses, message reports, or app configuration containing secrets.
- Keep mailbox actions in dry-run mode until approved tests have passed.
- Preserve the existing Home Assistant app data and credentials during migration.
- Do not enable automated unsubscribe or deletion by default.
- Keep personal Outlook and work Outlook outside the current scope.

## Proposed release process

1. Import and review the latest application source without secrets.
2. Add unit tests and simulated Gmail/IMAP integration tests.
3. Fix Yahoo folder handling and verify a single controlled move.
4. Build a pinned `linux/arm64` image through GitHub Actions and confirm image accessibility from Home Assistant OS.
5. Install a versioned release, verify startup version and scan logs, then selectively enable mailbox actions.

Do not assume private GitHub repository access implies Home Assistant can download a private container image; registry authentication must be tested separately.
