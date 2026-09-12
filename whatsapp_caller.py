#!/usr/bin/env python3
"""
WhatsApp Voice Call Trigger for StrokeGuard
Initiates WhatsApp calls via desktop app automation.

Requirements:
- WhatsApp Desktop installed and signed in
- macOS (uses AppleScript for automation)

Usage:
  python whatsapp_caller.py              # Call hardcoded number
  python whatsapp_caller.py +1234567890  # Call specific number
"""

import subprocess
import sys
import time
import webbrowser
from urllib.parse import quote

# Hardcoded WhatsApp number for testing
DEFAULT_NUMBER = "+79854311122"


def check_whatsapp_installed():
    """Check if WhatsApp Desktop is installed."""
    result = subprocess.run(
        ["mdfind", "kMDItemCFBundleIdentifier == 'net.whatsapp.WhatsApp'"],
        capture_output=True,
        text=True
    )
    return bool(result.stdout.strip())


def open_whatsapp():
    """Open WhatsApp Desktop."""
    subprocess.run(["open", "-a", "WhatsApp"], check=True)
    time.sleep(2)  # Wait for app to open


def open_chat(phone_number: str):
    """Open a chat with the specified number using WhatsApp's URL scheme."""
    # Strip any non-digit characters except +
    clean_number = ''.join(c for c in phone_number if c.isdigit() or c == '+')
    if clean_number.startswith('+'):
        clean_number = clean_number[1:]  # Remove + for the URL

    # Use WhatsApp's URL scheme to open a chat
    url = f"whatsapp://send?phone={clean_number}"
    print(f"Opening chat with: +{clean_number}")
    webbrowser.open(url)
    time.sleep(2)


def initiate_call_via_applescript(phone_number: str):
    """
    Use AppleScript to navigate WhatsApp and click the call button.
    This opens the chat first, then simulates clicking the call button.
    """
    clean_number = ''.join(c for c in phone_number if c.isdigit())

    # AppleScript to:
    # 1. Activate WhatsApp
    # 2. Use keyboard shortcut or click to initiate call
    applescript = '''
    tell application "WhatsApp"
        activate
    end tell

    delay 1

    -- Use keyboard shortcut for voice call (Cmd+Shift+A in WhatsApp Desktop)
    tell application "System Events"
        tell process "WhatsApp"
            -- First ensure the chat is open, then trigger call
            keystroke "a" using {command down, shift down}
        end tell
    end tell
    '''

    # Open chat first
    open_chat(phone_number)
    time.sleep(2)

    # Then trigger the call via AppleScript
    print(f"Initiating voice call to +{clean_number}...")
    result = subprocess.run(
        ["osascript", "-e", applescript],
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        print(f"AppleScript error: {result.stderr}")
        return False

    return True


def manual_call_instructions(phone_number: str):
    """Show instructions for manual call initiation."""
    clean_number = ''.join(c for c in phone_number if c.isdigit())
    print(f"""
╔══════════════════════════════════════════════════════════════════╗
║                   WHATSAPP CALL READY                            ║
╠══════════════════════════════════════════════════════════════════╣
║  Number: +{clean_number:<52} ║
║                                                                  ║
║  The chat window should now be open.                            ║
║  To start the call:                                             ║
║                                                                  ║
║  1. Click the PHONE icon (📞) in the top-right of the chat      ║
║     - OR -                                                       ║
║  2. Press Cmd+Shift+A for voice call                            ║
║                                                                  ║
╚══════════════════════════════════════════════════════════════════╝
""")


def main():
    phone_number = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_NUMBER

    print("=" * 60)
    print("  STROKEGUARD WHATSAPP CALLER")
    print("=" * 60)
    print(f"\nTarget: {phone_number}")

    # Check WhatsApp is installed
    if not check_whatsapp_installed():
        print("\n❌ WhatsApp Desktop not found!")
        print("   Install from: https://www.whatsapp.com/download")
        print("   Or: brew install --cask whatsapp")
        sys.exit(1)

    print("✓ WhatsApp Desktop found")

    # Open WhatsApp and the chat
    print("\nOpening WhatsApp...")
    open_whatsapp()

    # Open chat with the number
    open_chat(phone_number)

    # Try to initiate call via AppleScript
    print("\nAttempting to initiate call...")
    success = initiate_call_via_applescript(phone_number)

    if not success:
        manual_call_instructions(phone_number)
    else:
        print("\n✓ Call initiated! Speak into your microphone.")
        print("  Press Ctrl+C in this terminal when done.\n")

    # Keep the script running to show it's active
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n\nCall session ended.")


if __name__ == "__main__":
    main()
