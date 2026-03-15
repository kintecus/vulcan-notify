# vulcan-notify contract

**Created**: 2026-03-15
**Confidence Score**: 91/100
**Status**: Draft

## Problem statement

eduVulcan (the Polish school e-journal) paywalls push notifications behind a subscription. The web version is legally required to remain free but has no notification support. Parents must manually log in and check for grade changes, attendance issues, teacher messages, and upcoming exams. With two kids, this means checking multiple dashboards daily - easy to miss important updates.

The broader pain: the entire Vulcan experience is locked inside a clunky web portal. There's no way to get a quick summary, filter signal from noise, or integrate school data into your own workflows.

## Goals

1. **Replace manual Vulcan checking** - run `vulcan-notify sync` and immediately see what's new across both kids: grades, attendance, exams, homework, filtered messages
2. **Mirror all relevant Vulcan data locally** - SQLite database that stores the full history, so you never need to open the Vulcan website for routine checks
3. **Detect and surface changes** - diff each sync against stored state, clearly showing what changed since last run
4. **Filter message noise** - only surface messages from a configurable whitelist of senders (homeroom teachers, specific staff)
5. **Build for extensibility** - clean abstractions for data fetching, storage, diffing, and output so that notification channels, AI summaries, and daemon mode can be added without rewriting core logic

## Success criteria

- [ ] `vulcan-notify sync` fetches grades, attendance, exams, homework, and messages for all students and stores them in SQLite
- [ ] Running sync twice with no changes reports "no changes"
- [ ] New or changed grades are clearly displayed with student name, subject, grade value, teacher, and date
- [ ] Attendance changes (absences, late arrivals) are displayed with student name, date, subject, and type
- [ ] Messages from whitelisted senders are displayed; messages from other senders are silently stored but not surfaced
- [ ] Upcoming exams and homework are displayed with date and subject
- [ ] All output includes the student name so multi-kid households can tell who it's about
- [ ] Session expiry is detected gracefully with a clear message to re-run `vulcan-notify auth`
- [ ] The tool works with the current cookie-based auth (no iris dependency for runtime)
- [ ] Existing tests continue to pass; new data layer has test coverage

## Scope boundaries

### In scope

- Cookie-based API client for uczen.eduvulcan.pl endpoints (replacing iris)
- Local SQLite storage with full schema for grades, attendance, exams, homework, messages
- Change detection (diff) between syncs for all tracked data types
- CLI `sync` command with human-readable terminal output
- CLI `auth` command (current Playwright flow, unchanged)
- CLI `test` command to verify session validity
- Multi-student support (iterate over all students from /api/Context)
- Configurable message sender whitelist (in .env or config)
- Session expiry detection and clear error messaging

### Out of scope

- Push notifications (ntfy.sh, Telegram, etc.) - requires deciding on runtime model (daemon vs cron vs manual); defer until sync is solid
- Daemon / background polling mode - depends on hosting decision; defer
- AI-powered summaries - Phase 2 feature after data pipeline is proven
- Auto re-authentication (headless login) - manual `auth` is fine for now
- Web UI or dashboard - CLI is the interface for v1
- Commercialization / multi-tenant support - build clean but don't over-engineer for others yet
- Mobile app - ntfy.sh or Telegram will serve this role later

### Future considerations

- Notification delivery layer (pluggable: ntfy.sh, Telegram, email)
- Background daemon mode with configurable poll interval
- AI digest: daily/weekly Claude-powered summary of school activity
- Headless auto re-auth when session expires
- Open source release with docs, contributor guide, multi-tenant config
- Historical analytics (grade trends, attendance patterns over time)
- Calendar integration (exams/homework to Google Calendar or iCal)

---

_This contract was generated from brain dump input. Review and approve before proceeding to specification._
