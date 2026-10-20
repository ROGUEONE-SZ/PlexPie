# Security

PlexPie runs on your home network and can control your player, so security problems matter.

**Please report them privately**, not in a public issue:
[Report a vulnerability](https://github.com/ROGUEONE-SZ/PlexPie/security/advisories/new).
Include what you found, how to reproduce it, and which version (Settings › System).

This is a one-person hobby project: I'll reply as soon as I can, usually within a week, and credit you
in the fix unless you'd rather stay anonymous.

## How PlexPie is built to stay safe

- Settings from another device need a PIN, and five wrong tries pause logins for a minute.
  Each registered device can be signed out, and **New PIN** signs them all out.
- PlexPie never stores your Apple, Spotify or Plex passwords. Each service signs in on its own page.
- Root actions go through one small helper (`/opt/plexpie/plexpie-admin`) that only accepts a fixed list
  of commands.
- The diagnostics reports hide sign-in tokens and passwords.
- PlexPie is meant for your home network. Don't expose port 8080 to the internet.
