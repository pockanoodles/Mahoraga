# Security policy

## Reporting a vulnerability

Please report it privately, not in a public issue. Use GitHub's private
reporting: open the repository's **Security** tab, choose **Report a
vulnerability**, and describe what you found and how to reproduce it. Only the
maintainer sees it, and a fix can be prepared before anything is public.

You will get a reply as soon as the maintainer can manage; this is a one-person
project, so please allow a few days. If you want credit in the release notes,
say so in the report.

## What is covered

The latest release. Older versions are not patched; the fix is to upgrade.

## Things worth knowing before you report

- **`mahoraga serve` has no login.** It listens on `127.0.0.1` by default. The
  Docker image listens on every interface inside the container, which is why its
  documented command publishes the port on `127.0.0.1` only. Exposing the port
  to a network is a deployment choice, not a vulnerability.
- **An audit runs code.** Mahoraga runs your repository's own tests and the code
  that local coding agents write. Pointing it at a repository you do not trust is
  like running that repository's test suite.
- **Unexpected network traffic is in scope.** Mahoraga talks to your local Ollama,
  and to a cloud agent only if you configure one. A request to anywhere else is a
  bug worth reporting.
