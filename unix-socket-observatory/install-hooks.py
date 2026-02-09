#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
"""
Claude Code Hooks Observatory - Installer (Unix Socket)

Safely merge hook configuration into Claude Code settings files.
Creates backups before writing, shows diffs for review.

Why?
    Manual JSON editing is error-prone. This script safely merges
    hook configuration while preserving your existing settings.

Usage:
    ./install-hooks.py                    # Interactive
    ./install-hooks.py --global           # Install to ~/.claude/settings.json
    ./install-hooks.py --project          # Install to .claude/settings.json
    ./install-hooks.py --socket /tmp/x.sock  # Custom socket path
    ./install-hooks.py --dry-run          # Preview changes
    ./install-hooks.py --uninstall        # Remove observatory hooks
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_SOCKET = "/tmp/claude-observatory.sock"
ENV_SOCKET = "CLAUDE_UNIX_HOOK_WATCHER"

# All Claude Code hook event types
HOOK_EVENTS = [
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "PermissionRequest",
    "Notification",
    "Stop",
    "SubagentStop",
    "SubagentStart",
    "PreCompact",
    "SessionEnd",
]

# Events that use matchers
MATCHER_EVENTS = {
    "PreToolUse": "*",
    "PostToolUse": "*",
    "PostToolUseFailure": "*",
    "PermissionRequest": "*",
    "Notification": "",
    "PreCompact": "",
    "SubagentStart": "*",
    "SubagentStop": "*",
    "SessionStart": "",
    "SessionEnd": "",
}


def get_global_settings_path() -> Path:
    """Return path to global Claude settings."""
    return Path.home() / ".claude" / "settings.json"


def get_project_settings_path() -> Path:
    """Return path to project Claude settings."""
    return Path.cwd() / ".claude" / "settings.json"


def generate_curl_command(socket_path: str, event: str) -> str:
    """Generate curl command for a hook event using Unix socket.

    curl --unix-socket tells curl to connect via the Unix socket file
    instead of TCP. The http://localhost URL is required by curl but
    the hostname is ignored - only the socket path matters for routing.

    Appends '|| true' so hooks silently no-op when server is not running.
    """
    return (
        f"curl -s --connect-timeout 0.5 --max-time 1 "
        f"--unix-socket {socket_path} "
        f"-X POST -H 'Content-Type: application/json' -d @- "
        f"'http://localhost/hook?event={event}' || true"
    )


def generate_hook_config(socket_path: str) -> dict[str, Any]:
    """Generate complete hook configuration for all events."""
    hooks: dict[str, list[dict[str, Any]]] = {}

    for event in HOOK_EVENTS:
        hook_entry: dict[str, Any] = {
            "hooks": [
                {
                    "type": "command",
                    "command": generate_curl_command(socket_path, event),
                }
            ]
        }

        # Add matcher for events that support it
        if event in MATCHER_EVENTS:
            hook_entry["matcher"] = MATCHER_EVENTS[event]

        hooks[event] = [hook_entry]

    return {"hooks": hooks}


def load_settings(path: Path) -> dict[str, Any]:
    """Load existing settings or return empty dict."""
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError as e:
            sys.stderr.write(f"Error: Invalid JSON in {path}: {e}\n")
            sys.exit(1)
    return {}


def merge_settings(
    existing: dict[str, Any], new_hooks: dict[str, Any], replace: bool = False
) -> dict[str, Any]:
    """Merge new hook configuration into existing settings."""
    result = existing.copy()

    if replace or "hooks" not in result:
        result["hooks"] = new_hooks["hooks"]
    else:
        # Merge hook events
        for event, config in new_hooks["hooks"].items():
            result["hooks"][event] = config

    return result


def remove_observatory_hooks(settings: dict[str, Any], socket_path: str) -> dict[str, Any]:
    """Remove observatory hooks from settings."""
    if "hooks" not in settings:
        return settings

    result = settings.copy()
    result["hooks"] = {}
    # Match on --unix-socket marker to identify our hooks
    observatory_marker = "--unix-socket"

    for event, configs in settings["hooks"].items():
        filtered = []
        for config in configs:
            hooks_list = config.get("hooks", [])
            filtered_hooks = [
                h for h in hooks_list
                if observatory_marker not in h.get("command", "")
            ]
            if filtered_hooks:
                new_config = config.copy()
                new_config["hooks"] = filtered_hooks
                filtered.append(new_config)
        if filtered:
            result["hooks"][event] = filtered

    # Remove empty hooks dict
    if not result["hooks"]:
        del result["hooks"]

    return result


def create_backup(path: Path) -> Path:
    """Create timestamped backup of settings file."""
    timestamp = datetime.now().strftime("%y%m%d-%H%M")
    backup_path = path.with_suffix(f".json.bak-{timestamp}")
    shutil.copy2(path, backup_path)
    return backup_path


def show_diff(old_content: str, new_content: str, path: Path) -> None:
    """Display unified diff between old and new content."""
    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)

    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"{path} (current)",
        tofile=f"{path} (new)",
    )

    diff_text = "".join(diff)
    if diff_text:
        print(diff_text)
    else:
        print("No changes.")


def prompt_choice(message: str, choices: list[str]) -> str:
    """Prompt user to choose from a list of options."""
    print(f"\n{message}")
    for i, choice in enumerate(choices, 1):
        print(f"  {i}. {choice}")

    while True:
        try:
            response = input("\nEnter choice (number): ").strip()
            idx = int(response) - 1
            if 0 <= idx < len(choices):
                return choices[idx]
        except (ValueError, EOFError):
            pass
        print("Invalid choice, please try again.")


def prompt_confirm(message: str, default: bool = False) -> bool:
    """Prompt user for yes/no confirmation."""
    suffix = " [Y/n]: " if default else " [y/N]: "
    try:
        response = input(message + suffix).strip().lower()
    except EOFError:
        return default

    if not response:
        return default
    return response in ("y", "yes")


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Install Claude Code Hooks Observatory (Unix Socket) configuration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    ./install-hooks.py                         # Interactive
    ./install-hooks.py --global                # Install globally
    ./install-hooks.py --project               # Install in current project
    ./install-hooks.py --socket /tmp/my.sock   # Custom socket path
    ./install-hooks.py --dry-run               # Preview changes only
    ./install-hooks.py --uninstall             # Remove observatory hooks
        """,
    )

    scope = parser.add_mutually_exclusive_group()
    scope.add_argument(
        "--global",
        dest="scope",
        action="store_const",
        const="global",
        help="Install to ~/.claude/settings.json",
    )
    scope.add_argument(
        "--project",
        dest="scope",
        action="store_const",
        const="project",
        help="Install to .claude/settings.json",
    )

    parser.add_argument(
        "--socket",
        type=str,
        default=None,
        help=f"Socket path for hook server (default: ${ENV_SOCKET} or {DEFAULT_SOCKET})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be changed without writing",
    )
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Skip confirmation prompts",
    )
    parser.add_argument(
        "--uninstall",
        action="store_true",
        help="Remove observatory hooks from settings",
    )

    return parser.parse_args()


def get_socket_path(cli_path: str | None) -> str:
    """Determine socket path with precedence: CLI > env > default."""
    if cli_path is not None:
        return cli_path
    env_path = os.environ.get(ENV_SOCKET)
    if env_path:
        return env_path
    return DEFAULT_SOCKET


def main() -> None:
    """Main entry point."""
    args = parse_args()
    socket_path = get_socket_path(args.socket)

    # Determine target scope
    if args.scope:
        scope = args.scope
    else:
        scope = prompt_choice(
            "Where do you want to install the hooks?",
            ["global (~/.claude/settings.json)", "project (.claude/settings.json)"],
        )
        scope = "global" if "global" in scope else "project"

    # Get target path
    if scope == "global":
        target_path = get_global_settings_path()
    else:
        target_path = get_project_settings_path()

    print(f"\nTarget: {target_path}")
    print(f"Socket: {socket_path}")

    # Load existing settings
    existing = load_settings(target_path)
    old_content = json.dumps(existing, indent=2) if existing else "{}"

    if args.uninstall:
        # Remove observatory hooks
        new_settings = remove_observatory_hooks(existing, socket_path)
        action = "uninstall"
    else:
        # Generate and merge new hooks
        new_hooks = generate_hook_config(socket_path)

        # Check for existing hooks
        if existing.get("hooks"):
            if not args.yes:
                choice = prompt_choice(
                    "Existing hooks found. How should we proceed?",
                    ["merge (add observatory hooks)", "replace (overwrite all hooks)", "abort"],
                )
                if "abort" in choice:
                    print("Aborted.")
                    sys.exit(0)
                replace = "replace" in choice
            else:
                replace = False
        else:
            replace = False

        new_settings = merge_settings(existing, new_hooks, replace)
        action = "install"

    new_content = json.dumps(new_settings, indent=2)

    # Show diff
    print("\n--- Changes ---")
    show_diff(old_content + "\n", new_content + "\n", target_path)

    if args.dry_run:
        print("\n[Dry run - no changes made]")
        sys.exit(0)

    # Confirm
    if not args.yes:
        if not prompt_confirm(f"\nApply these changes?", default=True):
            print("Aborted.")
            sys.exit(0)

    # Create parent directory if needed
    target_path.parent.mkdir(parents=True, exist_ok=True)

    # Create backup if file exists
    if target_path.exists():
        backup_path = create_backup(target_path)
        print(f"Backup created: {backup_path}")

    # Write new settings
    target_path.write_text(new_content + "\n")
    print(f"\nSettings written to {target_path}")

    if action == "install":
        print(f"\nTo start the observatory:")
        print(f"  ./server.py --socket {socket_path}")
    else:
        print("\nObservatory hooks removed.")


if __name__ == "__main__":
    main()
