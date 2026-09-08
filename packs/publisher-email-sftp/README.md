# Publisher email/SFTP pack (M7b)

T2 MCP with two tools: `publisher-email-sftp__send_email` and
`publisher-email-sftp__upload_file`. Requires Python 3.11+; actual SFTP delivery also
requires OpenSSH `sftp` on PATH. No provider key or additional Python dependency is
needed for pack conformance.

```bash
forge pack test publisher-email-sftp
forge generate fixtures/publisher/spec.yaml --out out/publisher
forge audit out/publisher --write
python3 scripts/check_m7b_packs.py
```

The gate uses temporary pack copies, synthetic data and transport test doubles. It
checks real MCP stdio, preview/negative cases, persistence across server restarts,
concurrent key reservation, unknown outcomes, adapter TLS/SSH options, deterministic
generation and template tampering. It sends no email or SFTP traffic. Native
`thclaws agent validate` uses v0.116.0 revision `75edc48`; absent binary means an
explicit skip (exit 2), failed checks exit 1, all checks passed exit 0.

## Operator configuration

Set these environment variables on the dedicated publisher daemon; never store
values in AgentSpec, pack.yaml, `.thclaws/mcp.json`, a generated package or Git:

| Variable | Value |
|---|---|
| `PUBLISHER_CONFIG` | JSON object containing `smtp` and/or `sftp` settings below |
| `PUBLISHER_INPUT_ROOT` | Existing absolute directory of operator-provisioned artifacts |
| `PUBLISHER_STATE_DIR` | Existing private absolute directory outside the package for the persistent `publisher.db` ledger |

SMTP settings: `host`, integer `port`, `sender`, and one `recipient` address.
Optional authentication requires both `username` and `password`. SMTP uses implicit
TLS (`SMTP_SSL`, normally port 465) with certificate and hostname verification;
plaintext SMTP and STARTTLS endpoints are not supported by this pack.

SFTP settings: `host`, integer `port`, `username`, absolute `identity_file` and
`known_hosts` paths, and an absolute `remote_dir` that already exists. Provision an
SSH key usable in batch mode, without an agent or interactive password prompt.
Unknown or changed host keys fail. Only simple ASCII hostnames/users/paths are
accepted; a remote path cannot contain whitespace, quotes, shell syntax or `..`.
Uploads write the requested basename under `remote_dir`, replacing an existing
file with that name. This pack does not claim atomic remote publication.

Each tool requires an explicit boolean `dry_run` and `idempotency_key` (1–128 ASCII
letters, digits, `_` or `-`). Email also requires `subject` and `body`. SFTP requires
`filename`, a single basename under `PUBLISHER_INPUT_ROOT`; symlinks, non-regular
files and artifacts larger than 10 MiB are rejected. Source bytes are snapshotted
before delivery. Tools cannot select a destination or supply credential/config
overrides. Preview returns `status: dry_run` and a request digest without network
access, ledger creation or key consumption. It does not validate authentication
or prove destination reachability.

Pack v2 calls its broad network enum `general-http`; this publisher uses that
existing unrestricted declaration level for SMTP/SFTP, not HTTP as a transport.
Operator egress rules must restrict the actual configured hosts and ports. The
pack's fixed-destination checks do not constrain thClaws built-in tools.

## Delivery and idempotency

Keep the same key, payload, destination and artifact bytes across retries. The
ledger stores only hashes and status, reserves before sending, and returns the
same `sent` result for a completed attempt without sending again. A changed
payload/destination with the same key fails. Dry runs never consume a key.

Crashes, concurrent attempts and transport failures can leave a key `pending`.
The server blocks retries of that key: an operator must inspect the destination
and reconcile the ledger before authorizing another attempt. Do not delete the
ledger or invent a new key to bypass this state. Keep it durable and backed up;
deleting it loses deduplication history. This is at most one automatic delivery
attempt per key, not an exactly-once guarantee from SMTP/SFTP. A successful SMTP
handoff is not evidence of inbox receipt.

## T2 deployment template

`atlas-node-template.json` contains `deployment_requirements` and a native Atlas
`workflow` fragment. It requires a dedicated T2 daemon and operator-bound worker
and workspace IDs, with matching policy allowlists. The fragment starts at
`human_gate`; only the human `approve` choice reaches the publishing worker.
Rejection has no outgoing publishing edge. Static audit rejects missing/changed
gates, approval edges, daemon requirements and bindings.

Keep the generated template unchanged as the auditable source; bind its
`__OPERATOR_T2_*__` placeholders in a deployment copy. The template itself cannot
register a worker, provision isolation, or check that IDs correspond to a separate
daemon. Start the daemon with CWD=package and provision only the intended MCP
inventory, secrets, filesystem permissions and egress controls. Only approved
Atlas runs should reach that daemon; direct `/agent/run` calls bypass Atlas gates.
The template does not cryptographically bind approval to the LLM's eventual tool
arguments. `human_approval` remains `Not verified`, and Bash/tool/write restrictions
retain the limitations in DESIGN §4/§6. Draft is not deployment approval.

The fragment's workflow, graph and prompt envelope were checked against local
Atlas source `daa0f4966899327fb563501435f5d6180eeef9c3`. Registration and complete
flow export remain M8. Pack conformance tests MCP behavior; it is separate from
agent live evidence and security review.
