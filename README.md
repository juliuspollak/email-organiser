# Personal Email Organiser

Self-hosted Home Assistant OS app for **personal Gmail and Yahoo** accounts. Runs on ARM64 (Raspberry Pi). Work Outlook and personal Outlook are intentionally excluded.

## How it works

- Starts automatically with Home Assistant; no app configuration switches.
- Uses Gmail's native `CATEGORY_PROMOTIONS` labels, when available.
- Integrates **real MIT-licensed CleanYourMail classification and protected-message logic** from [jparedesDS/cleanyourmail](https://github.com/jparedesDS/cleanyourmail), in `cym_mailparse.py` and `cym_safety.py`. Attribution and license are in [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).
- Combines provider labels, independent header-based classifier signals, and conservative sender/subject patterns. Unknown or protected mail remains in Inbox.
- Never automatically deletes or unsubscribes. Filing uses Gmail label updates or Yahoo IMAP MOVE, with an audit log.
- Rotates through old Inbox pages over successive scans, with up to 500 messages inspected and 50 approved folder moves per account per run, every 15 minutes.
- Leaves all credentials and personal reports in the persistent Home Assistant app config directory; these must never enter Git.

## What is *not* guaranteed

Not every commercial message can be identified from headers and sender/subject alone. The upstream CleanYourMail classifier is a small 2026 project, not a large-scale validated AI model. Gmail's category labels and protected rules help but do not guarantee correctness. Check the first batch of moved messages in the EO folders. Yahoo IMAP moves must still be verified against the real mailbox.

## Release process

GitHub validation runs Python safety tests and an ARM64 Docker build. The publish workflow is manual so a new image is not offered until the validated version is deliberately published. Home Assistant app updates come from [email-organiser-ha](https://github.com/juliuspollak/email-organiser-ha).

## Safety and provenance

- Do not commit OAuth tokens, Yahoo app passwords or report exports.
- Do not permanently delete mail or automatically unsubscribe.
- Keep user-starred and high-risk mail in Inbox.
- Refer to [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md) for the upstream MIT license and attribution.
