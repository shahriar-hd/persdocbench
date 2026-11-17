# Sandboxing Pi

Pi has **no built-in filesystem sandbox**. Its bash tool, extensions and child
processes all run with the permissions of the account that started Pi. A
setting, a trust prompt or a system-prompt instruction cannot change that; only
an operating-system boundary can.

`pi-sandboxed.sh` uses [bubblewrap](https://github.com/containers/bubblewrap)
to start Pi inside a mount namespace where the personal parts of the home
directory do not exist at all.

## What is visible inside the sandbox

| Path | Mode | Why |
| --- | --- | --- |
| the project directory | read-write | the actual work |
| `~/Public` | read-only | shared/scratch material handed to the agent |
| `/tmp` | read-write, private tmpfs | ephemeral scratch, not the host `/tmp` |
| `~/.pi/agent` | read-write | Pi's config, sessions, skills, npm packages |
| `/usr`, `/etc`, `/sys`, `/proc`, `/dev` | read-only | operating system |
| `~/miniconda3` | read-only | `python3`, `conda` |
| `~/.local/bin` | read-only | `uv` |
| `~/.local/share/uv` | read-only | uv-managed interpreters |
| `~/.gitconfig` | read-only | commit identity |

Everything else is absent: `~/Desktop`, `~/Downloads`, `~/Documents` (other than
the project), `~/.ssh`, `~/.config`, `~/.gnupg`, `~/.local/share/keyrings`,
browser profiles, mail, other repositories. Absent means `ENOENT`, not "denied
by policy".

## Usage

```bash
./scripts/sandbox/pi-sandboxed.sh                # start Pi, sandboxed
./scripts/sandbox/pi-sandboxed.sh -- ls -a       # run one command, sandboxed
```

## Credentials

No token is stored in a file and nothing is added to a shell profile. The
launcher resolves credentials **outside** the sandbox and passes them in as
environment variables only:

- `GITHUB_TOKEN` — from `gh auth token` (or the caller's environment)
- `HF_TOKEN` — from `~/.cache/huggingface/token`

This is also what makes the `mcp.json` entries work: they reference
`${GITHUB_TOKEN}` and `${HF_TOKEN}` rather than containing literal secrets, so
the config file stays safe to read, copy and back up.

`AI4SCHOLAR_API_KEY`, `NVIDIA_API_KEY*`, `HTTPS_PROXY`/`HTTP_PROXY` and editor
variables are forwarded when set.

## Known limitation

`git push` over SSH needs `~/.ssh`, which is deliberately hidden. Either push
from an unsandboxed shell or opt in explicitly:

```bash
PI_SANDBOX_ALLOW_SSH=1 ./scripts/sandbox/pi-sandboxed.sh
```

## Notes

- `bash -lc` inside the sandbox prints `Failed to create stream fd` three times.
  That comes from Debian's `/etc/profile.d` scripts in a login shell; `bash -c`
  (what Pi's bash tool uses) is clean. Harmless.
- `PI_SANDBOX_PROJECT` and `PI_SANDBOX_PUBLIC` override the two main paths.
- Docker and the `docker` socket are **not** exposed; a sandboxed process that
  could reach the Docker socket would escape the boundary immediately.
- This sandbox constrains files, not network. Prompts and documents can still
  cause an outbound request; keep secrets out of the sandbox environment unless
  a task needs them.