# echo — reference pack for the contract (M10 fixture)

A complete, conformant pack in the smallest possible form: `scripts/server.py` is a stdlib stdio MCP
(initialize, tools/list, tools/call) with two read-only tools (`echo`, `reverse`), no env, no network,
no data. It is a gate fixture and an authoring example, not a capability for real agents, so it lives
under `fixtures/packs/` rather than `packs/`.

```bash
forge pack test echo --packs-dir fixtures/packs       # conformant (exit 0); writes pack-conformance.json (ignored)
forge pack discover --name echo --command python3 --args fixtures/packs/echo/scripts/server.py
forge generate fixtures/packs/echo/spec.yaml --out out/echo-reader   # requires the pack under packs/ or --packs-dir
```

`spec.yaml` is an AgentSpec that declares `capabilities: [{pack: echo}]` and the qualified tools
`echo__echo` / `echo__reverse`; `scripts/check_m10_pack_authoring.py` copies this pack into a temporary
pack root, reproduces it from `forge pack new` and `forge pack discover`, and audits the generated package.
See `docs/packs.md` for the contract this pack satisfies.
