# school-brief skill

A read-only "chief of staff" skill over the vulcan-notify HTTP API, in the [Agent
Skills](https://agentskills.io/specification) format. `SKILL.md` holds the judgment (what
to check, how to rank it, and what a brief looks like). `scripts/vulcan-ask` is a
standard-library Python client, so it runs in any agent with a shell and needs neither this
package nor eduVulcan credentials. Background and design: issue #9.

## Install

Give the agent its own folder and start sessions there. The skill, the API URL, and the
permission rules then load together, and only in that folder:

```bash
mkdir -p ~/code/school/.claude/skills
ln -s ~/code/vulcan-notify/skills/school-brief ~/code/school/.claude/skills/school-brief
# then write ~/code/school/.claude/settings.json (below)

cd ~/code/school && claude      # "anything I need to act on this week?"
```

`.claude/settings.json` sets the API URL (LAN or tailnet), pre-approves the client, and
blocks everything that could log in or sync:

```json
{
  "env": {
    "VULCAN_API_URL": "http://<api-host>:8585"
  },
  "permissions": {
    "allow": [
      "Bash(.claude/skills/school-brief/scripts/vulcan-ask *)",
      "Bash(/Users/<you>/code/school/.claude/skills/school-brief/scripts/vulcan-ask *)"
    ],
    "deny": [
      "Bash(*vulcan-notify sync*)",
      "Bash(*vulcan-notify auth*)",
      "Bash(*session.json*)",
      "Read(//**/session.json)",
      "Read(//**/vulcan_notify.db)"
    ]
  }
}
```

The symlink means skill edits in this repo take effect at the next session. For Codex,
link the skill into that folder's `.agents/skills/` instead (verify the path for your
version). Check connectivity with `vulcan-ask health --pretty`.

## Read-only by construction

The client only sends GETs to the API, which serves cached SQLite data and holds no
credentials. The skill also tells the agent never to run `vulcan-notify` or read the
session. The deny rules above enforce that, so it isn't left to the prompt.

The `//` prefix anchors the Read rules at the filesystem root. A bare `**/session.json`
only covers the agent's own folder, not this repo's local `session.json`. Keep these
rules out of the vulcan-notify repo and your global settings. Development runs `sync` and
`auth` on purpose, and a global deny can't be lifted per project.

## Data note

Whichever model provider runs the agent sees the children's grades and teacher messages.
See #1 for the anonymisation layer.
