# The Claude Code plugin surface

A user installs KeyPulse as a plugin and gets a skill plus slash commands that
call the same CLI. Driving this surface means loading the plugin, confirming
its components register, and proving the skill actually routes work to the CLI.

## Sub-features

- `plugin-load` loads the plugin from a checkout and registers its components.
- `plugin-validate` passes manifest + marketplace validation.
- `plugin-install` installs from the local marketplace into user scope.
- `plugin-skill` the skill fires and resolves the CLI path correctly.
- `plugin-commands` slash commands exist and point at the skill.

## How to get to it (user POV)

- `claude --plugin-dir /path/to/KeyPulse`
- `/plugin marketplace add <path>` then `/plugin install keypulse@...`
- `/keypulse:keywords`, `/keypulse:providers`

## Driving it with keypulse-cli

Preconditions:

- A checkout of the repo; `claude` CLI on `PATH`.
- Evals are opt-in and cost money; run them only when asked.

- **Validate.** Run `claude plugin validate . --strict`. Exit `0`; output ends
  with `Validation passed`.
- **Load and inventory.** Run
  `claude --plugin-dir . plugin details keypulse`. Exit `0`; the component
  inventory lists at least 3 skills (`keypulse`, `keywords`, `providers`).
- **Install.** Run `claude plugin marketplace add .` then
  `claude plugin install keypulse@keypulse-marketplace`. Both exit `0`;
  `claude plugin list` shows `keypulse` with `Status: enabled`.
- **Skill routes to the CLI.** Run an eval case:
  `claude plugin eval . --trust-plugin --no-publish --scaffold --case scan-ranks
  --runs 1 --ablation none --allow-tools Bash Read Grep Glob Skill`. Score `1.00`
  means the agent ran `keypulse scan` and reported a real ranking.
- **Honesty case.** Same command with `--case providers-honesty`. Score `1.00`
  means unavailable sources were reported as unavailable.
- **Proof.** Save the eval's `--json` output into `artifacts/plugin/`.

## Gotchas

- Evals need `Bash` **and** `Skill` granted; without `Skill` the agent cannot
  load the plugin skill and scores 0 for a reason unrelated to the plugin.
- The eval sandbox has no network; cases replay recorded values. A live
  upstream failure will not show up here.
- `--case` accepts a single glob; repeated flags silently keep only the last,
  so run one case per invocation or omit `--case` to run all.
- In a sandbox the binary is not on `PATH`; the skill resolves
  `${CLAUDE_PLUGIN_ROOT}/bin/keypulse` instead. A verification that only tests
  the `PATH` form does not prove the sandbox path.
- The first eval run in a new directory asks for trust confirmation;
  `--trust-plugin` answers it and is required in CI.
