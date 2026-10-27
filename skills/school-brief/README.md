# school-brief skill

A read-only "chief of staff" skill over the vulcan-notify HTTP API, in the [Agent
Skills](https://agentskills.io/specification) format. `SKILL.md` holds the judgment (what
to check, how to rank it, and what a brief looks like). `scripts/vulcan-ask` is a
standard-library Python client, so it runs in any agent with a shell and needs neither this
package nor eduVulcan credentials. Background and design: issue #9.

## Install

Point the API URL at the server (LAN or tailnet), then link the skill where each agent
looks for skills:

```bash
export VULCAN_API_URL=http://<api-host>:8585      # e.g. in ~/.zshrc

ln -s "$PWD/skills/school-brief" ~/.claude/skills/school-brief   # Claude Code
ln -s "$PWD/skills/school-brief" ~/.agents/skills/school-brief   # Codex (verify the path)
```

Check it: `skills/school-brief/scripts/vulcan-ask health --pretty`.

## Read-only by construction

The client only sends GETs to the API, which serves cached SQLite data and holds no
credentials. The skill also forbids running `vulcan-notify` or reading the session. In
the directory where the agent runs, back that with Claude Code deny rules
(`.claude/settings.json`):

```json
{
  "permissions": {
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

The `//` prefix anchors the Read rules at the filesystem root. A bare `**/session.json`
only covers the agent's own directory, not this repo's local `session.json`. Don't add
these rules to the vulcan-notify repo itself, because development there runs `sync` on purpose.

## Data note

Whichever model provider runs the agent sees the children's grades and teacher messages.
See #1 for the anonymisation layer.
