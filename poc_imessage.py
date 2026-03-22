"""POC: Send iMessage from macOS via AppleScript."""

import subprocess
import sys

DEFAULT_GROUP_CHAT = "Поважний чат"


def send_imessage(recipient: str, message: str, *, group: bool = False) -> None:
    escaped_message = message.replace("\\", "\\\\").replace('"', '\\"')
    escaped_recipient = recipient.replace("\\", "\\\\").replace('"', '\\"')

    if group:
        script = f'''
        tell application "Messages"
            send "{escaped_message}" to chat "{escaped_recipient}"
        end tell
        '''
    else:
        script = f'''
        tell application "Messages"
            set targetService to 1st account whose service type = iMessage
            set targetBuddy to participant "{escaped_recipient}" of targetService
            send "{escaped_message}" to targetBuddy
        end tell
        '''

    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print(f"Failed to send: {result.stderr.strip()}", file=sys.stderr)
        sys.exit(1)

    print(f"Message sent to {recipient}")


def main() -> None:
    if len(sys.argv) == 2:
        # Single arg = message to default group chat
        send_imessage(DEFAULT_GROUP_CHAT, sys.argv[1], group=True)
    elif len(sys.argv) == 3:
        send_imessage(sys.argv[1], sys.argv[2])
    elif len(sys.argv) == 4 and sys.argv[1] == "--group":
        send_imessage(sys.argv[2], sys.argv[3], group=True)
    else:
        print(
            f"Usage:\n"
            f"  {sys.argv[0]} <message>                        (sends to '{DEFAULT_GROUP_CHAT}')\n"
            f"  {sys.argv[0]} <recipient> <message>            (sends to individual)\n"
            f"  {sys.argv[0]} --group <chat-name> <message>    (sends to group chat)",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
