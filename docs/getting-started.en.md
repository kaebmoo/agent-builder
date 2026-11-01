# Getting started with agent-builder (a guide for newcomers)

This guide walks you from installation to your first agent package. By the end you will know
what you get, where to use it, and what to try next. Every command on this page was run on macOS
(Python 3.14, thClaws 0.146.0). Deeper documentation lives in the [README](../README.md), [agentspec.md](agentspec.md),
[generator.md](generator.md), [audit.md](audit.md), [export.md](export.md).
Thai version: [getting-started.md](getting-started.md)

## 1. What agent-builder is, in one minute

You write an **AgentSpec** (a single YAML file that says what the agent does, which input it takes, what JSON it answers with,
which tools it may use and what it must never do). The `forge` command turns it into a **thClaws agent package** (a folder thClaws can run)
together with a **build report** that states exactly what has been checked and what has not.

```
spec.yaml  →  forge generate  →  package/  →  forge audit  →  (run with thClaws / register with Atlas)
```

What the builder does not do: it never lets an LLM decide permissions or secrets, it does not deploy for you, and it never certifies anything it has not proven.
A package therefore climbs one status at a time, on real evidence only.

| Status | Reached when | Meaning |
|---|---|---|
| `unverified` | generate finished | Nothing checked yet (or no thClaws available to check) |
| `draft` | static audit passed + `thclaws agent validate` passed | Structure is correct and thClaws accepts it, but it has never run against a real model |
| `candidate` | live audit passed all golden cases | Ran against a real model and answered on-schema in every case |
| `shippable` | live + security audit passed in the same run | Ready to hand to an operator for deployment (not "deployed") |

## 2. Prepare your machine: three levels

| Level | You need | What you can do |
|---|---|---|
| A | Python 3.11+ | generate packages, static audit, read reports (highest status `unverified`) |
| B | A + the `thclaws` 0.146.0 binary | `draft`, run the agent for real with `thclaws agent run`, export for Atlas |
| C | B + a provider key for the model | live audit → `candidate`, security audit → `shippable` |

Start at level A. No key and no thClaws needed.

### Level A: install the builder

```bash
git clone https://github.com/kaebmoo/agent-builder.git
cd agent-builder
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/forge --help
```

If you see the commands `generate`, `audit`, `live-test`, `export`, `pack`, you are ready.
(If `python`/`pip` on your machine point at an old Python, always use `.venv/bin/python -m pip` as above.)

### Level B: thClaws available

It must be a desktop build of the fork `kaebmoo/thClaws` at revision `9f69995` (version 0.146.0).
Build it with `cargo build --features gui --bin thclaws`, then point the builder at it:

```bash
export THCLAWS_BIN="$HOME/Documents/GitHub/thClaws/target/debug/thclaws"
export PATH="$(dirname "$THCLAWS_BIN"):$PATH"
export THCLAWS_DISABLE_KEYCHAIN=1   # keeps a binary at a new path from hanging on the macOS keychain dialog
thclaws --version                    # must print thclaws 0.146.0 / revision: 9f69995
```

Other versions are rejected on purpose: the builder checks the version+revision pair and fails loudly until someone has reviewed
the new version's behaviour (see [DESIGN §4](DESIGN.md)).

Since M22, another thClaws version can be checked with `--allow-unverified-runtime` on `forge audit` / `forge live-test` /
`forge export`: the builder still runs the checks, records `runtime: unverified`, downgrades `Enforced` rows for
`tool_allowlist`/`no_shell`/`output_write_scope`/`collect_files` to `Declared`, and caps status at `candidate`
(`shippable` is unreachable). See [audit.md](audit.md#unverified-runtime-m22).

Build details and caveats are in [local-test-plan.md](local-test-plan.md).

### Level C: provider key

Set the environment variable of the provider behind the model the spec pins, for example model `gpt-5.4` uses `OPENAI_API_KEY`.
For an OpenAI-compatible gateway, pin the model as `oai/<model>` and set `OPENAI_COMPAT_API_KEY` and `OPENAI_COMPAT_BASE_URL`
(see [audit.md: OpenAI-compatible endpoint](audit.md#openai-compatible-endpoint)). Never put a key value into a spec or a package.

## 3. First try in five minutes (level A)

Use an example that ships with the repo: an agent that takes a text and echoes it back.

```bash
.venv/bin/forge generate fixtures/text-reviewer-standalone/spec.yaml --out out/text-reviewer
```

Expected output:

```
Generated 19 files in .../out/text-reviewer; package_status=unverified
Manifest, static, live and security audits have not run.
```

See what you got:

```bash
find out/text-reviewer -type f | sort
```

| File | What it is |
|---|---|
| `AGENTS.md` | The instructions the agent reads: mission, what it must do (Must), what it must refuse (Refuse), output format |
| `agentspec.json` | Your spec as JSON; every other file is rendered from it |
| `manifest.json`, `.thclaws/settings.json` | What thClaws uses: identity, model, tool allowlist |
| `.thclaws/schemas/*.json` | JSON Schemas for input, output and refusal |
| `.thclaws/agents/*.md`, `.thclaws/agent_workflow/run.js` | (standalone) planner/worker/verifier roles and the workflow that runs them in order |
| `evaluation/golden-cases.json` | 4 test cases: normal, missing input, refusal, malformed |
| `builder-build-report.json` | Status, guarantee matrix and the result of every audit layer |
| `audit.py`, `studio.py` | Short runners: check only / check and write the result into the report |

Audit the package:

```bash
.venv/bin/forge audit out/text-reviewer
```

Without thClaws you get this (exit code 2 = an explicit skip, not an error):

```
thclaws agent validate: skipped
thclaws is not on PATH (THCLAWS_BIN); manifest validation not run, package stays unverified
package_status=unverified (SKIP)
```

With thClaws (level B) add `--write` to record the result in the report:

```
thclaws agent validate: passed (thclaws 0.146.0 revision: 9f69995 (main))
builder-build-report.json updated
package_status=draft (PASS)
```

Exit codes mean the same for every command and every gate in the repo: `0` pass, `1` fail, `2` skipped because something external is missing (binary or key).

## 4. Write your own spec

Before writing, answer these seven questions for yourself (the same set the builder-agent uses to interview you):

1. What does the agent do, in one paragraph, and what is its short kebab-case name?
2. Where does it run: `thclaws-standalone` (on your own machine) or Atlas (`atlas-worker` / `atlas-workflow`)?
3. Input/output: names, transports and JSON Schemas; the output is a single `assistant_json`
4. Does it need a capability pack (for example read-only SQL)? If not, `capabilities: []`
5. Permissions: tier (`T0` read-only is the safest), tools, shell, network, write_scope, and the **names** of the env variables it needs
6. When must it refuse, and how does it answer?
7. The pinned model and the four golden cases

A complete example that generates and audits cleanly (save it as `work/my-summarizer/spec.yaml`):

```yaml
identity:
  name: my-summarizer
  version: 0.1.0
  owner: your-name
  license: Apache-2.0
  thclaws_min_version: ">=0.146.0"
mission:
  purpose: Summarize the supplied Thai or English text into at most three sentences and report the language detected.
  domain: text-summary
target: thclaws-standalone
package_pattern: static-pipeline
inputs:
  - name: request
    transport: prompt_json
    schema:
      type: object
      additionalProperties: false
      required: [text]
      properties:
        text: {type: string, minLength: 1, maxLength: 20000}
outputs:
  - name: summary
    transport: assistant_json
    schema:
      type: object
      additionalProperties: false
      required: [summary, language]
      properties:
        summary: {type: string, minLength: 1}
        language: {type: string, enum: [th, en, other]}
capabilities: []
permissions:
  tier: T0
  tools: [Read]
  shell: none
  network: none
  write_scope: none
refusal:
  conditions:
    - A required input is missing or malformed.
    - The request asks to send messages, browse the web or perform any side effect.
  response: Return a JSON refusal with the reason.
  schema:
    type: object
    additionalProperties: false
    required: [refused, reason]
    properties:
      refused: {const: true}
      reason: {type: string}
model:
  mode: pinned
  id: gpt-5.4
env: []
evaluation:
  golden_cases:
    - id: normal
      kind: normal
      expect: result
      input:
        request: {text: "agent-builder turns an AgentSpec into an auditable thClaws package and hands it to Atlas to register as a worker"}
    - id: missing-input
      kind: missing_input
      expect: refusal
      input: {}
    - id: refusal
      kind: refusal
      expect: refusal
      input:
        request: {text: "Email this summary to the whole company right now."}
    - id: malformed
      kind: malformed
      expect: refusal
      input:
        request: {text: 42}
state:
  mode: none
  scope: turn
```

```bash
.venv/bin/forge generate work/my-summarizer/spec.yaml --out out/my-summarizer
.venv/bin/forge audit out/my-summarizer --write
```

What usually trips people up at first (the builder prints the reason; it never passes silently):

- `T0` may not have `Bash`, file-writing tools, or tools that reach the network (`WebFetch`, `WebSearch`, ...). If you need the web, move to a higher tier and understand that egress is not enforced at runtime
- Tool names must be ones thClaws knows (built-ins such as `Read`, `Grep`, `Glob`, or MCP tools of the form `<server>__<tool>` from a pack). Names outside the catalog are warnings that block `shippable`
- `permissions.tools` must not be empty; all four golden case kinds must be present; every output needs a `schema`
- `env` takes variable **names** only, never values
- Editing a file inside the package by hand makes the audit report `drift`. Fix the spec and generate again (exception: in Atlas packages `AGENTS.md` and `SKILL.md` may be edited as long as the mandatory content stays)

## 5. Run the agent for real (level B + key)

A standalone package runs on your machine right away with the thClaws CLI (set the key of the pinned model first):

```bash
thclaws agent run out/my-summarizer --workflow .thclaws/agent_workflow/run.js \
  --args '{"request":{"text":"agent-builder turns an AgentSpec into an auditable thClaws package"}}'
```

stdout is JSON matching the output schema (or a refusal), followed by a summary of the subagent turns.
An Atlas worker package is not run with this command; it is started as a daemon and called by Atlas (section 7).

## 6. Let the builder prove it with a live audit (level C)

The live audit fires all four golden cases at the real agent in isolation (the package is copied to a temp dir with its own HOME; your machine is untouched)
and validates every answer against the schema branch by branch:

```bash
.venv/bin/forge live-test out/my-summarizer --write                      # pass → candidate
.venv/bin/forge audit out/my-summarizer --live --security --write        # live + security pass in the same run → shippable
```

For a model pinned as `oai/...` add `--provider-key-env OPENAI_COMPAT_API_KEY`. Without a key you get exit 2 and the status stays `draft`.

`--security` runs four deterministic checks: a strict static audit with no warnings, no secret values or secret-looking path names in the files,
every pack passes `forge pack test`, and `network: allowlist` declares its hosts. If it passes without a live run in the same round you see:

```
security: passed (scanned 18 files)
package_status=draft (PASS)
```

Read the report directly at `out/my-summarizer/builder-build-report.json`. The important parts are `package_status`, `audit` (per-layer results)
and `guarantee_matrix`, which states claim by claim whether it is Enforced, Declared, Evidence or Not guaranteed.
In a T0 standalone package, for example, `tool_allowlist` and `no_shell` are Enforced while `network_hosts` is Not guaranteed.
That table is everything you may truthfully say about the package. Never claim more (no status means "deploy-ready").

## 7. Put it to use

**Option 1: run it yourself (standalone).** Use the command from section 5 from your own script or cron. Input is JSON per the schema, output is JSON per the schema.
Good for batch jobs or pipelines that need no orchestrator.
Or install the package as a **thClaws bot** to chat with from the CLI or desktop: upload the `forge export` archive to a catalogue (`/cloud publish` inside a session; the CLI command is gone),
then the user runs `thclaws bots add <slug>` or installs from the desktop into `.thclaws/bots/<slug>/`. Caveat: when the installing machine has no credential for the provider the spec pins,
thClaws replaces the model with the host's model at install time, and audits must run against the source package, not the installed tree (DESIGN §4 "Bot channel").

**Option 2: as an Atlas worker.** Write the spec with `target: atlas-worker` (with `routing.role`/`tags` and no `package_pattern`),
then export after the audit:

```bash
.venv/bin/forge export out/<pkg> --out out/<pkg>-export \
  --base-url http://<daemon-host>:4317 \
  --workspace-dir /srv/atlas/<pkg>
```

You get `atlas-register.json` (worker/workspace payload, node template, env names and reachability checks), `atlas-workflow.json` (only for `atlas-workflow`)
and an archive `.tar.gz` + `.sha256`. The operator then performs five manual steps per [export.md](export.md#ลำดับลงทะเบียน-ทำด้วยมือ):
unpack the archive on the worker machine → start `thclaws --serve` with `THCLAWS_AUTO_MIGRATE=0` and the env names from the report →
`POST /api/workers` and `/api/workspaces` → put the node template into a workflow → poll until `online`.
Important: never open the package folder in the thClaws desktop app. It converts the folder in place and Atlas will no longer reach the agent.
On a real host follow [deploy-runbook.md](deploy-runbook.md) (M18a, Thai): prerequisites, systemd/launchd launchers that `scripts/render_launcher.py` renders from
`atlas-register.json` (env names only, never values), daemon and Atlas checks, rollback, and the checklist where the operator ticks `deployment_verified` outside the builder.

**Option 3: let thClaws build the agent for you.** Talk to thClaws in plain language and let it write the spec and run the builder. See section 8.

## 8. Let thClaws build the agent (builder-agent)

The `builder/` folder in this repo is a thClaws workspace prepared so that the model acts as "interviewer and the person who presses build".
Deciding whether a package is usable stays entirely with `forge` (the model may not decide permissions, network or secrets, nor declare an audit passed).

**Steps**

1. Open the `builder/` folder with a thClaws desktop build (or `thclaws --serve` from that folder). The folder is already a host workspace;
   thClaws finds the agent at `.thclaws/bots/main/` by itself. Never run `thclaws bots migrate` on this folder
2. Type what you need, for example "I need an agent that summarizes the daily report from Markdown files and answers as JSON"
3. The model asks only the questions it cannot answer from the seven in section 4, then writes `work/<name>/spec.yaml` for you
4. The model stops and shows the `permissions` / `env` / `capabilities` blocks for you to confirm in the next turn. The rule: the model may not add the `confirmed_by` marker itself;
   a person must confirm (reply that you confirm, with your name)
5. After confirmation the model runs `build.py`, which calls `forge pack test` → `forge generate` → `forge audit --write` and reports the status from
   `builder-build-report.json`. The package lands in `builder/out/<name>/` and is used exactly like one you built yourself (sections 5–7)

**What thClaws cannot do at this stage (by design)**

- Invent packs or MCP servers. Only packs printed by `--list-packs` are usable (today: the 5 packs in the table in section 9, not counting `echo`).
  Asking for a capability that does not exist, such as a Google Weather report, gets a plain `missing`; it does not guess tool names
- Decide or claim audit results itself. Every status line is copied from forge's output
- Live audit, export and deployment to Atlas remain steps you trigger yourself (sections 6–7)

**Shortcut without an LLM.** If you can write the spec yourself, run the same runner from a normal terminal:

```bash
python3 builder/.thclaws/bots/main/.thclaws/scripts/build.py --list-packs
python3 builder/.thclaws/bots/main/.thclaws/scripts/build.py work/my-summarizer/spec.yaml --out out/my-summarizer
```

The spec must carry a `confirmed_by` block (see [builder/README.md](../builder/README.md)). Exit codes match forge: `0` = `draft`, `1` = rejected or a layer failed,
`2` = no `thclaws`, so it stays `unverified`.

**Real-world readiness.** The two-turn interview passed the M11 live gate 3/3 rounds on thClaws 0.138.0 (2026-10-28) and 4/6 rounds on 0.146.0 (2026-10-31)
with `gpt-5.4-mini`. The failed rounds came from the model ending its turn before answering completely, not from the builder's code; a stronger model is steadier.
A desktop build is required because the CLI-only build has no `--serve`.

## 9. What to try next

- **Use external capabilities through packs**: put the pack name in `capabilities` and the builder fills in the MCP config, tools and env names. These packs exist today:

| pack | What it does | tier/network | tools (`<server>__<tool>`) | env the operator must set | example spec |
|---|---|---|---|---|---|
| `sql-readonly` | Read-only queries against a SQLite database (metadata, SQL validation, query); wraps the MCP of the `kaebmoo/AI` project, no admin tools | T0, none | `sql-readonly__metadata`, `__query`, `__validate` | `SQL_READONLY_AI_ROOT`, `SQL_READONLY_DSN` | `fixtures/sql-reader/spec.yaml` |
| `publisher-email-sftp` | Send email or upload files over SFTP to destinations the operator configured in advance; every call must state `dry_run` and `idempotency_key`; real sends must pass the Atlas `human_gate` | T2, general-http | `publisher-email-sftp__send_email`, `__upload_file` | `PUBLISHER_CONFIG`, `PUBLISHER_INPUT_ROOT`, `PUBLISHER_STATE_DIR` | `fixtures/publisher/spec.yaml` |
| `sqlite-readonly` | Read any SQLite file with `mode=ro` + an authorizer | T0, none | `sqlite-readonly__tables`, `__schema`, `__query` | `SQLITE_READONLY_DB` | `fixtures/sqlite-reader/spec.yaml` |
| `csv-readonly` | List, inspect headers, preview and aggregate CSV/TSV files read-only inside one configured directory | T0, none | `csv-readonly__files`, `__columns`, `__preview`, `__aggregate` | `CSV_READONLY_ROOT` | `fixtures/csv-reader/spec.yaml` |
| `datetime-tools` | Current time, timezone conversion, durations and business-day arithmetic | T0, none | `datetime-tools__now`, `__convert`, `__diff`, `__add_business_days` | none | `fixtures/datetime-helper/spec.yaml` |
| `echo` (in `fixtures/packs/`) | The smallest reference example: echo/reverse a text, no env, no network; a template for writing packs and used by the gates, not a capability for real work | T0, none | `echo__echo`, `echo__reverse` | none | `fixtures/packs/echo/spec.yaml` (needs `--packs-dir fixtures/packs`) |

  The three newest packs are independent examples (M21, stdlib only, usable with any target). For email/SFTP use `publisher-email-sftp` with ordinary work too; the operator sets destinations and credentials in its config by design, so the agent never chooses recipients.

  `sql-readonly` needs a checkout of `kaebmoo/AI` at revision `d95dc28` placed next to agent-builder under the name `AI`, plus `pip install -e ".[sql-pack]"` (`mcp>=1.26,<2`).
  `publisher-email-sftp` needs OpenSSH `sftp` on PATH for real sends. See each pack's README for details
- **Write your own pack** when you already have an MCP server: `forge pack new <name>` → `forge pack discover --name <name> --command <cmd>` → fill in tier/network/fixture → `forge pack test <name>`.
  See [packs.md](packs.md); the smallest example is `fixtures/packs/echo/`
- **T1 standalone that may write only under `output/`**: see `fixtures/note-writer-standalone/spec.yaml` (`write_scope: output`, tools add `Write`/`Edit`)
- **Check that the builder itself still works**: `python3 scripts/check_m5_static_audit.py` and the other gates in `scripts/` (exit 2 without a binary/key is normal)

## 10. Short Q&A

- **Why do I only get `unverified`?** Two causes: (1) no `thclaws` to validate with (exit 2): set `THCLAWS_BIN` as in section 2 and run `forge audit --write` again; (2) a `thclaws` that is not the baseline `0.146.0`/`9f69995` (exit 1, line `is not the verified baseline`): if you must use that version, add `--allow-unverified-runtime` (M22) to reach `draft` with `runtime: unverified` in the report, runtime-dependent guarantee rows as `Declared` and a status capped at `candidate`; see [audit.md](audit.md#unverified-runtime-m22)
- **Why does `candidate` not become `shippable` by itself?** The security audit must run in the same round as the live audit (`--live --security`); the builder does not trust an older status in the report
- **Can I edit the package directly?** Standalone: no, fix the spec and generate again; Atlas: only `AGENTS.md` and `.thclaws/skills/<name>/SKILL.md`
- **Where do keys live?** Only in the environment of the machine that runs the daemon or CLI. The spec declares variable names only, and the security audit reports any value found in a file
- **Does `shippable` mean I can deploy?** No. It means the evidence inside the builder is complete. Deployment, egress control and human approval are the operator's and Atlas's work, and `deployment_verified` in the export stays `false` until the operator checks it themselves

## 11. Limitations and what you must do yourself

Summarized from [DESIGN §6](DESIGN.md) (the guarantee matrix is the builder's only certification), [export.md](export.md) and the
"status 2026-10-31" section of [PLAN](PLAN.md), in two groups: limitations of agent-builder itself that apply to everyone, and limitations that come from the external projects or dependencies the builder is tied to.

### 11.1 General limitations of agent-builder

| Limitation | If you need it: what to do and how |
|---|---|
| No deployment: no launcher, secret provisioning or monitoring; `deployment_verified` in the export is always `false` | Do the five manual steps in [export.md](export.md#ลำดับลงทะเบียน-ทำด้วยมือ): unpack the archive, start `thclaws --serve` with `THCLAWS_AUTO_MIGRATE=0` and the env names from the report, POST worker/workspace, add the node template, poll until `online`; write your own launchd/systemd unit and record the deployment check outside the builder (proposed M18, not started) |
| The LLM never decides permissions, network, secrets, MCP or audit results (by design) | Always write those parts of the spec yourself; the builder-agent (section 8) only drafts them and a person confirms with `confirmed_by` |
| `shippable` means the evidence inside the builder is complete, not deploy-ready; live + security must pass in the same round and the builder does not trust an older status in the report | Run `forge audit --live --security --write` every time before handing over, then let the operator verify egress, approval and deployment |
| M22: a runtime outside the baseline can be checked with `--allow-unverified-runtime`, but runtime-dependent guarantee rows are only Declared and `shippable` is unreachable | Review every DESIGN §4 claim against that version’s source and re-baseline as in M17 for full certification |
| Network egress is Not guaranteed at runtime: `network: none` is only a static check that no web tool is declared, `allowlist` only checks that hosts are declared | Use a firewall or egress proxy on the machine or network, matching `deployment.network`/`hosts` in `atlas-register.json` |
| `no_file_writes` is Not guaranteed for declared writers, MCP servers and runtime state; Skill/MCP tools are outside the allowlist claim; T1 `output/**` is Enforced only for standalone `static-pipeline` | Run one daemon per package under its own OS user or sandbox and avoid file-writing MCP servers unless needed; runtime enforcement needs an upstream thClaws patch (DESIGN §10) |
| T2 human approval is Not verified: the package carries an approval fragment, but nothing proves the runtime binds the approval to the tool arguments; calling `/agent/run` directly bypasses the gate | Deploy T2 on a dedicated daemon, route every run through the Atlas `human_gate`, and never expose the daemon for direct calls (proposed M20, not started) |
| The output schema is enforced only in the live audit; Atlas does a bare `json.loads` | Validate the output on the consumer side with `.thclaws/schemas/*.json` |
| Standalone supports only `static-pipeline` (`batch-fanout`/`dynamic` are rejected); an Atlas package is a single node; `--layout host` accepts only `prompt_json`/`assistant_json` and one package per host | Split multi-step work into one spec per node and assemble the workflow in Atlas yourself; there is no path for dynamic patterns today |
| Package files cannot be edited directly (the audit reports `drift`) except `AGENTS.md`/`SKILL.md` in Atlas packages; there is no `must_call` in the schema yet | Fix the spec and generate again; to confirm a tool was called, read the SSE evidence of the live audit |
| Native/live evidence lives in `out/` (gitignored) on the machine that ran it; GitHub CI really checks only M0–M3 + ruff | Run the gates in `scripts/` yourself with `THCLAWS_BIN` before trusting a status, or set up a self-hosted runner with the binary and a key |
| The three independent example packs (M21) are not hardened services: no OS sandbox, no row-level access control, `csv-readonly` reads whole files into memory | Use them with data the agent may read in full anyway; to harden, copy the pack, change it and run `forge pack test` again |

### 11.2 Limitations tied to an external project or dependency

| Project | Limitation | If you need it: what to do and how |
|---|---|---|
| thClaws (fork `kaebmoo/thClaws`) | Must be a desktop build at the baseline `0.146.0`/`9f69995` for the daemon, native gates and export archive; other versions fail with 1 by design unless `--allow-unverified-runtime` (M22) is enabled, which downgrades guarantees and caps status at `candidate`; the CLI-only build has no `--serve` | Build the fork at that revision with `cargo build --features gui --bin thclaws`; moving versions requires reviewing every DESIGN §4 claim against the source first (as in M12/M17) |
| thClaws | The desktop app and `--serve` convert an opened folder into a host workspace in place (auto-migrate), so an Atlas package stops being an agent | Start the daemon with `THCLAWS_AUTO_MIGRATE=0` only; never open a package in the desktop app or run `thclaws bots migrate` on it; if it happened, redeploy from the archive |
| thClaws | The built-in tool allowlist fails open if `.thclaws/settings.json` is missing or `THCLAWS_PROJECT_ROOT` is set; pack MCP servers must appear in `~/.config/thclaws/mcp_allowlist.json` with their full command line unless `THCLAWS_MCP_ALLOW_ALL=1`; the engine injects extra MCP servers when the machine has office apps or an AI Server hub | Start the daemon with CWD = the package folder, do not set `THCLAWS_PROJECT_ROOT`, provision mcp_allowlist from `.thclaws/mcp.json`, and check that `agent_info.agent.mcp_servers` matches `deployment.mcp_servers` |
| Atlas control plane (`atlas-control-plane`) | The builder uses only `POST /agent/run`; schemas are pinned at revision `daa0f49`; `output_format: json` does not validate a schema; upserting a worker with a reused `base_url` overwrites the existing worker | The M8/M16 gates need a checkout at `daa0f49` (`ATLAS_ROOT`); use one URL per daemon or `--worker-id` matching the existing worker; validate output yourself as in 11.1 |
| Repo `kaebmoo/AI` (pack `sql-readonly`) | The pack wraps the AI project's MCP at revision `d95dc28` and needs `mcp>=1.26,<2`; the current AI is 300+ commits ahead and untested | Place a checkout of AI at `d95dc28` named `AI` next to agent-builder and `pip install -e ".[sql-pack]"`; for ordinary SQLite work use `sqlite-readonly` instead, which needs no AI checkout |
| Model provider / gateway | The live audit needs an OpenAI-compatible provider key; fixtures pin `gpt-5.4` while all evidence used `oai/gpt-5.4-mini`; the builder-agent passed 4/6 rounds because the model ended its turn early | Set `OPENAI_API_KEY` or `OPENAI_COMPAT_API_KEY` + `OPENAI_COMPAT_BASE_URL` in the environment; pin `model.id` to a model your gateway really has and run live three rounds; a stronger model is steadier |
| The machine (OS) | `datetime-tools` needs the machine's tz database; `publisher-email-sftp` needs OpenSSH `sftp` and operator-provisioned known_hosts; the macOS keychain may hang on a dialog when a binary at a new path is called | Install tzdata/OpenSSH as your OS requires; set `THCLAWS_DISABLE_KEYCHAIN=1` when running the CLI |

The milestones proposed to close these gaps (M18 real deployment, M20 T2 side-effect proof) are listed in [PLAN](PLAN.md) and have not started.
