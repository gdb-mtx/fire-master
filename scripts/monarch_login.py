"""One-time interactive script to authenticate with Monarch Money and save the session."""

import asyncio
import os
import sys
from getpass import getpass
from pathlib import Path

from dotenv import load_dotenv
from monarchmoney import MonarchMoney, RequireMFAException

load_dotenv()


async def main():
    if os.environ.get("DEMO_MODE", "").strip().lower() == "true":
        sys.exit("Refusing to log in: DEMO_MODE is enabled (this instance is demo-only).")

    session_file = os.environ.get("MONARCH_SESSION_FILE", ".monarch_session")

    # Give the client the configured path up front. Otherwise its login() helper
    # also writes a second, untracked .mm/mm_session.pickle with the same secret.
    mm = MonarchMoney(session_file=session_file)

    print("=== Monarch Money Login ===")
    email = input("Email: ").strip()
    password = getpass("Password: ")

    try:
        # Save exactly once below, after authentication succeeds, so permissions
        # can be tightened immediately.
        await mm.login(email, password, use_saved_session=False, save_session=False)
    except RequireMFAException:
        # The Monarch API only accepts authenticator-app (TOTP) codes here — it
        # cannot trigger Monarch's email codes, so email-MFA users would wait
        # for a code that never arrives (first hit in the wild: r/MM, Aug 6).
        print(
            "\nMFA required. Enter the 6-digit code from your authenticator app."
            "\nNOTE: email codes do NOT work here — if your Monarch MFA is set to"
            "\nemail, first enable an authenticator app in Monarch: Settings ->"
            "\nSecurity -> Enable MFA, then re-run this script."
        )
        mfa_code = getpass("MFA code: ")
        await mm.multi_factor_authenticate(email, password, mfa_code)
    except Exception as e:
        print(f"Login failed: {e}", file=sys.stderr)
        sys.exit(1)

    # Make the file private at creation time as well as after the write, avoiding
    # a window where a permissive process umask could expose the session token.
    previous_umask = os.umask(0o077)
    try:
        mm.save_session(session_file)
    finally:
        os.umask(previous_umask)

    try:
        Path(session_file).chmod(0o600)
    except OSError as exc:
        print(f"WARNING: could not secure {session_file}: {exc}", file=sys.stderr)
    print(f"\nSession saved to {session_file}")

    # Verify by fetching accounts
    accounts = await mm.get_accounts()
    count = len(accounts.get("accounts", []))
    print(f"Verified: found {count} accounts")


if __name__ == "__main__":
    asyncio.run(main())
