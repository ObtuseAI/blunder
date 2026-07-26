# Security policy

## Reporting a vulnerability

Use this repository's **Security** tab and **Report a vulnerability** to open a
private GitHub security advisory. Do not open a public issue or pull request for
a suspected vulnerability, leaked credential, unsafe authority change, or
unintended external effect.

Include the affected revision, component, preconditions, source-to-sink path,
impact, and a minimal non-destructive reproducer when practical. Never include
live credentials, private memory, raw provider reasoning, browser tokens, or
operator approvals.

Good-faith, authorized research that respects the license, avoids privacy
violations and service disruption, and gives the maintainers reasonable time to
remediate is welcome.

## Supported versions

Only the current default branch and latest tagged public preview are supported.
Historical private-archive commits, handoff bundles, and generated runtime
artifacts are not supported releases.

## Security boundaries

- The core service and desktop sidecar bind to loopback.
- The React renderer never receives the raw API token and has no arbitrary
  shell, filesystem, process-control, or direct-network authority.
- Provider output, retrieved content, reputation, consensus, and fitness are
  evidence—not permission.
- Generated validation receives a credential-filtered environment and an audit
  guard that blocks non-loopback networking, outside-worktree access, child
  processes, and native-library loading.
- Windows validation requests a one-process, 768 MiB, kill-on-close Job Object.
  `BLOCKED_BY_PARENT_JOB_POLICY` is not described as kernel-enforced isolation.
- Blunder does not claim AppContainer, virtual-machine, or hostile-native-code
  containment.

## Release hygiene

Before publication, run the native suite, desktop build, dependency audit,
current-tree and history secret scans, private-path scan, and exact-SHA GitHub
quality workflow. Stop publication and rotate any affected credential if a
secret is discovered.
