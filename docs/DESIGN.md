# Design baseline (2026-09-03)

เอกสารนี้คือข้อตกลงการออกแบบที่ใช้เป็นฐานของทุก milestone ใน [PLAN.md](PLAN.md)
ข้อเท็จจริงเกี่ยวกับ thClaws อ้างจาก source ของ fork `kaebmoo/thClaws` ที่ `crates/core` v0.116.0
ถ้า thClaws เปลี่ยนพฤติกรรม ให้แก้เอกสารนี้ก่อนแก้โค้ด

## 1. ขอบเขต

- Builder เป็น **Python project แยก** ไม่ฝังใน thClaws (Rust, ต้อง sync upstream) และไม่ฝังใน Atlas (stdlib-only, ห้ามมี worker logic)
- Builder รู้จัก thClaws ผ่าน **contract** เท่านั้น: format ของ package ที่ `thclaws agent new` สร้าง, `thclaws agent validate`, `thclaws agent pack`, และ `POST /agent/run`
- Builder **ไม่** reimplement manifest validation, packing, หรือ runtime ใด ๆ

## 2. AgentSpec = single source of truth

ไฟล์ `agentspec.schema.json` เป็น schema หลักเพียงตัวเดียว ทุกไฟล์ใน package ถูก generate จาก spec
ห้ามให้ role, permission หรือ output contract ถูกเขียนซ้ำแบบไม่สัมพันธ์กันในหลายไฟล์

หมวดของ spec (ตรงกับ discovery 7 ข้อ)

| หมวด | เนื้อหา |
|---|---|
| identity | `name`, `version`, `owner`, `license`, `thclaws_min_version` |
| mission | วัตถุประสงค์หนึ่งย่อหน้า + domain |
| target | `thclaws-standalone` / `atlas-worker` / `atlas-workflow` (ดู §3) |
| package_pattern | ต้องมีเฉพาะ standalone: `static-pipeline` / `batch-fanout` / `dynamic` |
| routing | ต้องมีเฉพาะ Atlas: `role` + `tags` สำหรับ route |
| inputs | array ของ `{name, transport, path?, mime_types?, schema?}` โดย `transport` ∈ `prompt_json` / `atlas_file_handoff` / `local_workspace`; Atlas file handoff ไม่รับ `path` ที่ผู้ใช้กำหนดเอง; `local_workspace` คือไฟล์ที่ provision ไว้ใน worker workspace ก่อน run |
| outputs | array ของ `{name, transport, schema?, files?}` โดย `transport` ∈ `assistant_json` / `collect_files`; `assistant_json` ต้องมี `schema`, ส่วน `collect_files` ต้องมี `files.globs` และอาจมี schema ของ artifact manifest; การ snapshot ไม่ได้บอกว่า agent เป็นคนเขียนไฟล์ |
| capabilities | รายชื่อ **pack** + พารามิเตอร์ (ดู §5) |
| permissions | `tier` ∈ `T0` (read-only agent behavior) / `T1` (เขียนเฉพาะ `output/**`) / `T2` (side effect ภายนอก) + declared `tools`, `shell`, `network`, `write_scope`; ชื่อ tool คือชื่อที่ runtime เห็น: built-in ตาม catalog หรือ MCP เป็น `<server>__<tool>` (§4) |
| refusal | เงื่อนไขที่ต้องปฏิเสธหรือ hand off พร้อมรูปแบบคำตอบเมื่อปฏิเสธ |
| model | model id ที่ pin หรือ model policy |
| env | **ชื่อ** env var ที่ต้องมี (ไม่มีค่า) |
| evaluation | golden cases: normal / missing input / refusal / malformed พร้อม expected output branch (`result` / `refusal`) |
| state | ต้องประกาศ `{mode, scope}`; M1 ให้ standalone ใช้ `none/turn` หรือ `session/session`, Atlas ใช้ `none/turn`; `durable_memory` เลื่อนไปหลัง M1 |

หมายเหตุ: `execution_surface` คำนวณจาก `target` ผ่าน compatibility matrix ผู้ใช้ไม่กรอกเอง ส่วน `package_pattern` เป็น input เฉพาะ standalone เพราะ target เดียวมีหลาย pattern ที่ถูกต้อง

## 3. Target / pattern compatibility matrix

| target | execution surface | package pattern ที่อนุญาต | flow อยู่ที่ไหน |
|---|---|---|---|
| `thclaws-standalone` | thClaws GUI / CLI / catalog | `static-pipeline`, `batch-fanout`, `dynamic` (ผู้ใช้ต้องเลือก) | static/batch: `.thclaws/agent_workflow/run.js`; dynamic: ไม่มีไฟล์ flow ใช้ `Task` orchestration |
| `atlas-worker` | `POST /agent/run` | `single-worker` เท่านั้น | ไม่มี flow ใน package |
| `atlas-workflow` | Atlas workflow engine | `single-worker` หนึ่ง package ต่อ node | Atlas workflow JSON ที่ builder export |

เหตุผล: บน `/agent/run` (`agent_runtime.rs::build_runtime_for_workspace`) ไม่มี subagent factory และไม่ register `Task` tool
ดังนั้น `.thclaws/agents/*.md` (tools / writePaths / output_schema) และ `thclaws.subagent()` ใน run.js **ไม่ทำงาน** บน path นี้
planner → worker → verifier สำหรับ Atlas จึงต้องเป็น node หลายตัวใน Atlas ไม่ใช่ subagent ใน package

`human-approval` และ `manager-loop` เป็น **flow pattern ของ Atlas** (node `human_gate`, node `manager`) ไม่ใช่ package pattern

ตรวจซ้ำ 2026-09-07 ด้วย `thclaws agent new` ทั้งสาม pattern บน v0.116.0 revision `75edc48`: `dynamic` ไม่สร้าง run.js (`agent_scaffold.rs::scaffold_agent`, `static_like = pattern != "dynamic"`). นี่คือการแก้ baseline ที่ระบุตำแหน่ง flow เหมารวม ไม่ใช่การเปลี่ยน runtime

`routing.role` และ `routing.tags` เป็น package intent สำหรับ Atlas routing ส่วน `worker_id`, `workspace_id`, `workspace_dir` และ `base_url` เป็น deployment-time values ที่ exporter รับจาก operator

`state.mode: session` ใช้ `session_id` ของ `/agent/run` ได้ (`api_v1/agent.rs`) ดังนั้น standalone ใช้คู่ `session/session` ได้ แต่ Atlas ปัจจุบันไม่ส่ง `session_id` ต่อเนื่องระหว่าง node → spec ต้อง reject ถ้าเลือก state ที่ไม่ใช่ `none` กับ target ที่เป็น Atlas จนกว่า Atlas จะรองรับ `durable_memory` ยังไม่อยู่ใน M1 ทุก target

## 4. สิ่งที่ `/agent/run` ใช้จริง vs ไม่ใช้ (thClaws v0.116.0)

ใช้จริง: `AGENTS.md` (walk ขึ้นจาก `workspace_dir`), skills ใน `<workspace_dir>/.thclaws/skills/`, MCP servers จาก settings ของ **daemon** (ไม่ใช่ต่อ workspace), `model` / `max_tokens` / `system` (ต่อท้าย) / `session_id` / `collect_files`, Bash sandbox ระดับ OS

ไม่ใช้: agent defs, hooks (`pre_tool_use`), `allowed_tools` / `disallowed_tools`, permission mode (ใช้ `AutoApprover`), workflow subagent

ผลที่ตามมา: การจำกัด tool/shell ต่อ role บน Atlas worker **ยังบังคับไม่ได้** สิ่งที่บังคับได้คือ MCP ที่ daemon มี/ไม่มี, daemon แยกต่อ tier, sandbox, Atlas `human_gate`, และ schema check ตอน audit

`thclaws_min_version` คือ minimum version ที่อาจมี upper compatibility bound: clause เป็น `<`, `<=`, `==`, `>`, `>=` + numeric `x.y.z` และต่อด้วย comma เช่น `>=0.116.0,<0.120.0` หรือ `0.116.0`; ต้องมี minimum/exact clause, bare version ห้ามปนกับ clause อื่น และ semantic gate reject range ที่ไม่มีทาง satisfy ได้ ไม่รองรับ prerelease, `!=`, `~`, `^`

การตรวจชื่อ tool ใช้สามระดับ: built-in catalog ที่ pin ตาม thClaws version, MCP tools จาก pack/daemon จริง และ unknown tool เป็น warning ใน `draft` แต่ fail เมื่อจะเป็น `shippable` ห้ามสร้าง enum ปิดตายสำหรับ MCP tools

MCP config, tool naming และ isolation (ตรวจ source 2026-09-07 v0.116.0 revision `75edc48`):
- daemon โหลด `mcp_servers` ครั้งเดียวตอน start: user-level `~/.config/thclaws/mcp.json` แล้ว merge กับ project-level `<cwd>/.thclaws/mcp.json` (รองรับ `.mcp.json` และ `.claude/mcp.json` ด้วย; project override user ตามชื่อ) ใน `config.rs::AppConfig::load` / `ProjectConfig::load_mcp_servers`. `/agent/run` ใช้ `config.mcp_servers` ชุดนี้ ไม่อ่าน mcp.json ของ `workspace_dir` ต่อ request (`agent_runtime.rs`). ดังนั้น daemon ที่ start จาก directory ของ package (CWD = package) จะเห็น `.thclaws/mcp.json` ของ package นั้น นี่คือช่องทางที่ live audit และ deployment แบบ daemon-per-package ใช้
- รูปแบบไฟล์ `{"mcpServers": {"<name>": {"command", "args", "env"}}}` (stdio) หรือ `{"transport": "http", "url", "headers"}`. stdio subprocess สืบทอด environment ของ daemon และได้ `env` จากไฟล์เพิ่มแบบ literal (`mcp.rs::McpClient::spawn`); `${VAR}` ถูก interpolate เฉพาะ `headers` ของ HTTP transport → ห้ามใส่ค่า secret ใน mcp.json: pack ประกาศ **ชื่อ** env แล้ว operator ตั้งค่าที่ process ของ daemon
- model เห็น tool ของ MCP เป็น `<server>__<tool>` (`mcp.rs::MCP_NAME_SEPARATOR`, แต่ละส่วน sanitize เป็น `[A-Za-z0-9_-]`) ดังนั้น `permissions.tools` ใน spec และ `tools` ของ pack ต้องใช้ชื่อ qualified นี้ เช่น `sql-readonly__query` ไม่ใช่ `sql.query`
- การ spawn stdio command ครั้งแรกต้องผ่าน allowlist `~/.config/thclaws/mcp_allowlist.json` (`XDG_CONFIG_HOME` เปลี่ยน base ได้) หรือ `THCLAWS_MCP_ALLOW_ALL=1`; `THCLAWS_CONFIG=<path>` ชี้ settings.json, `HOME` กำหนด user-level scope, `thclaws --serve --port <N>` + `THCLAWS_API_TOKEN` (ไม่ตั้ง = `/v1/*` และ `/agent/run` ตอบ 404) คือชุดที่ใช้สร้าง daemon isolated สำหรับ live audit
- `browserEnabled` default เป็น true ตั้งแต่ 0.49.2: `AppConfig::load` ใน `config.rs` ของ v0.116.0 revision `75edc48` ฉีด Playwright MCP ชื่อ `browser` เมื่อหา `npx` บน PATH ได้ (ยกเว้นมี server ชื่อนี้แล้วหรือ policy ห้าม). HOME/config isolation อย่างเดียวจึงไม่จำกัด MCP ให้เหลือเฉพาะ package. Live audit ต้องตั้ง `{"browserEnabled": false}` ใน settings ชั่วคราว และตรวจ `/v1/agent/info` ว่าชุด server เท่ากับที่ pack ประกาศ ทั้งก่อนส่ง golden cases และหลัง run; server ขาดหรือเกินเป็น fail. การตรวจนี้ไม่บังคับ built-in tool allowlist/shell/write-path.
- `GET /v1/agent/info` คืน `skills` และ `mcp_servers` (`name`, `command`, `tool_count` เป็น null จนกว่าจะมี run) ใช้เป็นหลักฐานว่า daemon เห็น MCP ของ pack หลัง deploy
- built-in tools ของ `/agent/run` = `ToolRegistry::with_builtins()` + KMS / Memory / Task / WorkflowRun ที่ `agent_runtime.rs::build_runtime_with_provider` ลงทะเบียนเพิ่ม เป็นแหล่งของ catalog `patterns/tools-<version>.json` ที่ static audit ใช้
- thClaws ลงทะเบียน **ทุก** tool ที่ server advertise โดยไม่กรอง และไม่อ่าน MCP tool annotations (`readOnlyHint` ฯลฯ; `McpToolInfo` มีเพียง name / description / input_schema / ui). ถ้า spawn หรือ `tools/list` ล้มเหลว `agent_runtime.rs::load_mcp_servers_silent` แค่ `eprintln!` แล้ว run ต่อโดยไม่มี tool ของ server นั้น → การมี/ไม่มี tool ต้องพิสูจน์ด้วยหลักฐานเชิงบวก (SSE tool event, `/v1/agent/info`) ไม่ใช่จากการที่ run ไม่ error

OpenAI-compatible provider สำหรับ live audit (ตรวจ source v0.116.0 revision `75edc48`, 2026-09-07): model `oai/<id>` ใช้ `OPENAI_COMPAT_API_KEY` และ `OPENAI_COMPAT_BASE_URL` ใน `repl.rs::build_provider`; ตัด prefix `oai/` ก่อนส่ง request และเติม `/chat/completions` ถ้า URL ยังไม่มี suffix นี้. ไม่พบตัวเลือกปิด TLS certificate verification ของ provider นี้; ใช้การตรวจ TLS ตาม runtime ปกติ. การทดสอบผ่าน custom provider ต้อง pin model ใน spec ของ package ที่ทดสอบ ไม่แทน model อย่างเงียบ ๆ.

## 5. Capability pack

pack คือความสามารถสำเร็จรูปที่ประกอบเข้า package ได้ อยู่ใน `packs/<name>/pack.yaml` ประกาศ: MCP servers / skills / scripts ที่เพิ่ม, env ที่ต้องการ (ชื่อ), tier ต่ำสุดที่ต้องใช้, fixture สำหรับ live audit ที่รันได้โดยไม่ต้องมีข้อมูลจริง
pack แรก ๆ: `sql-readonly` (ห่อ MCP ของ project AI: metadata / query / validation ไม่รวม admin), `report-pipeline` (ห่อ build + verify ของ NT-Report), `publisher-email-sftp` (MCP ใหม่ มี `dry_run` + `idempotency_key`)
กฎ: pack ที่มี tool แบบ mutating ต้องใช้ T2; T2 ใน M1 ใช้ได้เฉพาะ `atlas-workflow` เพราะมี owner สำหรับ `human_gate`; `T2 + atlas-worker` และ `T2 + thclaws-standalone` ถูก reject จนกว่าจะมี approval story ของ target นั้น

T0 ต้องประกาศ `shell: none`, `network: none`, `write_scope: none`; ทุก tier ที่ประกาศ `shell: none` ห้ามประกาศ `Bash`, `write_scope: none` ห้ามประกาศ `Write`, `Edit`, `FetchImages` หรือ document writers, และ `network: none` ห้ามประกาศ `WebFetch`, `WebSearch`, `WebScrape`, `FetchImages` หรือ `YouTubeTranscript`
T1 ต้องประกาศ `shell: none`, `network: none`, `write_scope: output`; นี่เป็น declared boundary ที่ builder ใช้ตรวจและแสดงใน guarantee matrix ยังไม่ใช่ runtime write-path enforcement
T2 ต้องประกาศ `write_scope: workspace`; การเลือก tier อย่างเดียวไม่สร้าง approval ให้ target ที่ไม่มี `human_gate`

### Pack contract (M7a ขึ้นไป) — รอยต่อระหว่าง builder กับงานที่สร้างข้างนอก

MCP server, script และ skill ที่ agent ต้องใช้ **ไม่อยู่ใน builder** และไม่อยู่ใน AgentSpec; เขียนที่ไหนก็ได้ (repo แยก, session ของ thClaws ที่ทำหน้าที่ coding agent, มนุษย์) แล้วเข้ามาทาง pack เท่านั้น. `packs/<name>/pack.yaml` v2 คือ contract และแทนที่ descriptor v1 หกฟิลด์ของ M4 ทั้งชุดใน M7a (generator, audit, report schema และ gate ย้ายพร้อมกัน ไม่มี pack จริงที่ต้อง migrate):

```yaml
min_tier: T0
network: none            # none | allowlist | general-http = ความต้องการต่ำสุดของ pack; spec ต้องประกาศไม่ต่ำกว่านี้
hosts: []                # เมื่อ allowlist: host ที่ MCP ของ pack ต้องออก ใช้เป็น deployment hint ของ egress control
env: [SQL_READONLY_DSN]  # ชื่อ env ที่ process ของ daemon ต้องมี ไม่มีค่า
tools: []                # built-in tools ที่ pack ต้องการเพิ่ม (เช่น Read); MCP tools คำนวณจาก mcp_servers
mcp_servers:
  - name: sql-readonly   # ชื่อ server = prefix ของ tool ที่ model เห็น
    command: python3
    args: [-m, sql_readonly_mcp]
    tools: [metadata, query, validate]   # bare tools ที่ server ต้อง advertise ตรงชุดนี้ ไม่ขาดไม่เกิน
    mutating: []         # tool ที่มี side effect; ไม่ว่าง → min_tier ต้องเป็น T2
scripts: []
skills: [sql-readonly]
fixture:
  setup: fixture/setup.py   # สร้างข้อมูลตัวอย่างใน directory ชั่วคราวและพิมพ์ค่า env ของ fixture ไม่ใช้ข้อมูลจริง
  cases:                    # smoke + negative case ที่ harness ยิงเข้า MCP โดยตรง ไม่ใช้ LLM
    - {tool: sql-readonly__query, args: {statement: "SELECT vendor, SUM(total) FROM invoices GROUP BY vendor"}, expect: ok}
    - {tool: sql-readonly__query, args: {statement: "DELETE FROM invoices"}, expect: error}
source: {repo: https://github.com/kaebmoo/AI, ref: <commit>}   # optional provenance สำหรับ security audit
```

กฎของ contract
- ชื่อ tool ที่ spec ประกาศคือ qualified name `<server>__<tool>` ตาม §4; builder ไม่ประดิษฐ์ alias
- pack read-only ต้องปฏิเสธ mutation **ที่ server** (negative case ใน `fixture.cases`) ไม่ใช่ที่ prompt; `mutating` ที่ไม่ว่างบังคับ T2 และ pack ที่มี side effect ต้องมี `dry_run` + `idempotency_key` ตามกฎเดิม
- config ของ server รับได้ทาง `args` และ env เท่านั้น; ค่า secret ไม่อยู่ในไฟล์ใดของ pack หรือ package
- `fixture.setup` ต้องรันได้โดยไม่มี network และไม่มีข้อมูลจริง; pack ที่ต้องใช้ API ภายนอก (เช่น Google Weather) ใช้ recorded response หรือ mock endpoint ใน fixture และประกาศ `hosts` จริงสำหรับ deployment
- `conformance` ของ pack ไม่ต้องใช้ provider key: harness `forge pack test <name>` (stdlib stdio JSON-RPC) start server ตาม `command`, ทำ `initialize` → `tools/list` ต้องเท่ากับ `tools` ที่ประกาศ, ยิงทุก case, บันทึก `pack-conformance.json`; ไม่มี runtime ของ server ในเครื่อง → exit 2
- ชื่อ server ต้องตรง `^[a-z0-9][a-z0-9-]*$` (ห้าม `_`) และ bare tool ต้องตรง `^[a-z0-9][a-z0-9_]*$` (ห้าม `__`) เพื่อให้ qualified name แยกกลับเป็น server/tool ได้ทางเดียว; thClaws sanitize ชื่ออื่นแบบเงียบ ๆ แต่ builder reject
- `tools` ของ pack ส่วน MCP = **ทุก** tool ที่ server advertise เพราะ thClaws ลงทะเบียนทั้งหมด (§4) และ spec ต้องประกาศ ⊇ ชุดนี้: declaration ของ spec คือ inventory ของสิ่งที่ model จะเห็นจริง ไม่ใช่รายการที่อยากใช้. server ที่มี tool แบบ admin ต้องถูก launch ในโหมดที่ไม่ expose (ผ่าน `args`) หรือ pack นั้นเป็น T2
- harness รัน server ด้วย environment สะอาด (PATH + env ที่ประกาศพร้อมค่าจาก fixture เท่านั้น) เพื่อให้ dependency ต่อ env ที่ไม่ได้ประกาศโผล่เป็น start failure ตั้งแต่ตอน test ไม่ใช่ตอน deploy
- `pack-conformance.json` เก็บ digest ของ `tools/list` (ชื่อ + input schema) ครั้งล่าสุด; server ภายนอกที่เปลี่ยน surface จะถูกจับได้ในการ test ครั้งถัดไป และ pack owner ต้องแก้ `pack.yaml` ก่อน spec ที่ใช้มันจะกลับมา `draft`
- `forge pack discover --command <cmd> [--args ...]` (M10) ร่าง `pack.yaml` จากสิ่งที่ MCP บอกเองได้ (ชื่อ server จาก argument, tools + input schema + description จาก `tools/list`, annotations ถ้ามีเป็นข้อเสนอของ `mutating`) แต่ไม่ตัดสิน `min_tier`, `network`, `env`, `mutating` และ fixture; ผลของ discover ไม่ใช่ `conformant` จนกว่าผู้เขียนจะเติมส่วนที่เหลือแล้ว `forge pack test` ผ่าน

Lifecycle ของความสามารถหนึ่งตัว (ตัวอย่าง: agent พยากรณ์อากาศจาก Google Weather)
1. คนหรือ coding session เขียน MCP `google-weather` ในที่ของมันเองตาม contract: stdio JSON-RPC, tool `forecast`, อ่าน `GOOGLE_WEATHER_API_KEY` จาก env, fixture เป็น recorded response
2. `forge pack new google-weather` (M10) สร้าง `pack.yaml` + fixture skeleton (หรือ `forge pack discover` ร่าง tools/schemas จาก server ที่มีอยู่แล้ว); ผู้เขียนกรอก `network: allowlist`, `hosts: [weather.googleapis.com]`, `env`, `mcp_servers`
3. `forge pack test google-weather` ผ่าน = pack **conformant**: server มีจริง, tools ตรง, negative case ผ่าน. นี่คือจุดที่งานภายนอก "เข้ามาทดสอบ" ได้โดยไม่ต้องมี agent หรือ LLM
4. spec ประกาศ `capabilities: [{pack: google-weather}]`, `permissions.tools: [google-weather__forecast]`, `network: allowlist`, `env: [GOOGLE_WEATHER_API_KEY]` → `forge generate` bundle asset, เขียน `.thclaws/mcp.json` (command/args เท่านั้น) และ `manifest.requires.mcp_servers`; static audit (M5 + catalog ของ M7a) ตรวจว่า tools/env/network/tier ของ spec รองรับ pack
5. live audit (M6) start daemon isolated จาก package พร้อม env ของ fixture → golden cases ต้องผ่าน และ SSE ต้องมี tool event `google-weather__forecast` → `candidate`; ไม่มี key = คง `draft`
6. deploy: operator ติดตั้ง runtime ของ MCP บน daemon ของ tier นั้น, ตั้ง env ตามชื่อใน report, ตั้ง egress ตาม `hosts`; `atlas-register.json` (M8) แนบรายการนี้ และ `/v1/agent/info` ใช้ยืนยันว่า daemon เห็น server หลัง deploy

สถานะของ pack เป็นผลของ harness ไม่ใช่ field ที่เขียนเอง: ไม่มี descriptor = `missing`, มี descriptor และ asset ถูก bundle = `assets_bundled`, `forge pack test` ผ่านล่าสุด = `conformant` (บันทึกใน `pack-conformance.json` ของ pack และอ้างใน build report). guarantee ที่ pack ให้ได้จริงบน `/agent/run` มีสองอย่าง: daemon มี/ไม่มี server นี้ และ server ปฏิเสธ mutation เอง; การจำกัดว่า agent จะเรียก tool ไหนยังเป็น Declared ตาม §6

## 6. Guarantee matrix (บังคับต้องมีในทุก build report)

M7b publisher boundary (2026-09-08): `publisher-email-sftp` เป็น T2 เสมอ แม้เรียกแบบ dry run;
MCP มี `send_email` และ `upload_file` โดยทุก call ต้องระบุ boolean `dry_run` และ `idempotency_key`.
ปลายทาง SMTP/SFTP, ผู้รับ และ credential มาจาก env ของ operator เท่านั้น ไม่รับจาก model.
SMTP ใช้ TLS พร้อม certificate verification; SFTP ใช้ OpenSSH แบบ batch พร้อม known_hosts ที่ operator provision.
Dry run ตรวจ input/config และอ่าน artifact ได้ แต่ไม่เชื่อมต่อ network หรือเขียน ledger.
SQLite ledger อยู่นอก package ใน directory ที่ operator provision และ persist ข้าม restart:
reserve key ก่อนส่ง, payload/ปลายทาง/เนื้อหาไฟล์ต่างกันต้อง reject, สำเร็จแล้ว replay ผลเดิม;
pending/unknown outcome ต้องให้ operator reconcile ห้าม retry ส่งซ้ำอัตโนมัติ (ไม่อ้าง exactly-once delivery).
Fixture/conformance ใช้ dry run และ negative cases; gate ทดสอบ transport adapters ด้วย test doubles ไม่มีการส่งจริง.

T2 generation เพิ่ม `atlas-node-template.json`: deployment requirements บังคับ dedicated T2 daemon
และ workflow fragment เริ่มที่ `human_gate`, ต่อ worker ได้เฉพาะ `human_selected: approve`.
Worker และ policy pin worker/workspace เป็น placeholder ที่ operator ต้อง bind กับ daemon แยก;
ไม่ route T2 ด้วย role อย่างเดียว. Static audit ตรวจไฟล์นี้กับ render จาก spec จึง reject การลบ gate,
เปลี่ยน edge หรือถอด isolation requirement. Template ยังไม่ใช่ registration/export ของ M8
และไม่พิสูจน์การ provision daemon หรือการผูก approval กับ tool arguments ใน runtime;
`human_approval` จึงยังเป็น `Not verified` สำหรับ T2 และ `Not applicable` สำหรับ T0/T1.
การ start publisher daemon หรือเรียก `/agent/run` ตรง ๆ ไม่ได้ผ่าน Atlas gate โดยอัตโนมัติ.

| Claim | สถานะบน `/agent/run` วันนี้ | บังคับด้วย |
|---|---|---|
| ใช้ tool ตาม allowlist | Declared | prompt / package เท่านั้น |
| ห้ามเขียนไฟล์ | Enforced เฉพาะกรณี | daemon แยก + ไม่มี write MCP + sandbox (Write/Edit ยังมี) |
| T1 เขียนได้เฉพาะ `output/**` | Declared | `permissions.write_scope=output`; runtime ยังไม่บังคับ path ใน M1 |
| `collect_files` เป็นผลลัพธ์ | Enforced เป็น snapshot | thClaws copy ไฟล์ที่มีอยู่หลัง run; ไม่ยืนยันว่า agent เขียนไฟล์นั้น |
| ห้าม shell | Not guaranteed | runtime register Bash เสมอ |
| `network: none` ไม่มี web built-in ใน declaration | Static check | reject `WebFetch`, `WebSearch`, `WebScrape`, `FetchImages`, `YouTubeTranscript`; runtime egress ยังไม่ถูกบังคับ |
| network เฉพาะ host | Not guaranteed | policy allowlist ครอบเฉพาะ URL ตอนติดตั้ง plugin/skill/MCP config; `net_guard` กันแค่ private IP ของ WebFetch/WebScrape/FetchImages → ต้องใช้ egress control ของเครื่อง |
| ต้องมีคนอนุมัติก่อน side effect | Enforced | Atlas `human_gate` + T2 daemon แยก |
| output เป็น JSON ตาม schema | Enforced ตอน audit | schema validation ใน live audit (Atlas เองทำแค่ `json.loads`) |
| skill/MCP ถูกเรียก | Evidence | SSE tool events ใน live audit; M1 ยังไม่รองรับการประกาศ `must_call` |

`audit` ห้ามพิมพ์คำว่า read-only ถ้า package มี mutating MCP หรือ runtime ยัง expose Bash

## 7. Audit สองชั้น + สถานะ package

static audit: schema ของ spec, manifest ที่ generate, permission declaration vs pack, env names, MCP/skill มีอยู่จริง, refusal surface ครบ, AGENTS.md มี section บังคับ (Mission / Must / Refuse / Output) และประโยค "ตอบ JSON ล้วน" เมื่อ output เป็น `assistant_json`, `thclaws agent validate` ผ่าน
live audit: start daemon ชั่วคราวแบบ isolated, ยิง `/agent/run` จริงด้วย golden cases (normal / missing input / refusal / malformed); `expect: result` validate กับ schema ของ `assistant_json` output เดียว และ `expect: refusal` validate กับ `refusal.schema`, แล้วเก็บ SSE tool/skill events เป็น evidence. ถ้า response ผ่านทั้ง expected และ opposite branch schema ให้ fail เป็น ambiguous; result ที่มีหลาย output หรือ `collect_files` อย่างเดียวอยู่นอก live-audit scope ของ M6 จนกว่าจะมี output selector contract
สถานะ: `draft` (static ผ่าน) → `candidate` (live ผ่าน) → `shippable` (compatibility + security ผ่าน) ไม่มี API key = อยู่ที่ draft ห้ามบอกว่า deploy-ready
`audit.py` / `studio.py` ใน package เป็น runner บาง ๆ ที่เรียก builder logic ตัวเดียว: `audit.py` ตรวจอย่างเดียวไม่แก้ไฟล์ ส่วน `studio.py` ตรวจแล้วบันทึกผลลง `builder-build-report.json` (M5, ดู [audit.md](audit.md))
กฎ static audit ที่อิง single-worker (ไม่มี `.thclaws/agents/`, ไม่มี `WorkflowRun`) ผูกกับ target Atlas เท่านั้น

Live audit ของ standalone (M9, ตรวจ source 2026-09-07 v0.116.0 revision `75edc48`): ใช้ CLI `thclaws agent run <package> --workflow .thclaws/agent_workflow/run.js --args '<json>'` (`repl.rs::run_agent_workflow`) เพราะเส้นทางนี้สร้าง `SubAgentTool` factory และ `WorkflowRun` ให้แล้ว, headless ใช้ `AutoApprover` + `PermissionMode::Auto` จึงไม่มี approval prompt. ห้ามใช้ `/agent/run` เพราะ `agent_runtime.rs` register `WorkflowRun` โดยไม่มี factory ทำให้ `thclaws.subagent()` error; การควบคุม daemon `--serve` ผ่าน WebSocket (`shared_session.rs`) เลื่อนออกไปจนกว่า CLI จะไม่พอ. ต้องส่ง `--workflow` เสมอเพราะค่า default ของ `agent run` หาไฟล์ใน `.thclaws/state/workflows/` ไม่ใช่ `.thclaws/agent_workflow/` ที่ scaffold สร้าง
- ผลลัพธ์: stdout = ค่า `globalThis.__wf_result` ต่อด้วย token summary `\n\n[workflow: N subagent turn(s), ...]` และอาจมี hint (`workflow_run.rs`); stderr = log. Live audit ต้องตัด summary ท้ายก่อน `json.loads` ห้าม parse ทั้งก้อน
- contract: run.js ของ builder ต้องตั้ง `__wf_result` เป็น JSON ของ output branch สุดท้ายเท่านั้น คือ object ที่ตรง `outputs[0].schema` (`assistant_json`) หรือ `refusal.schema` เหมือน Atlas; ห้ามคืน `{ok, steps}` แบบ scaffold upstream และห้ามใช้ `verifier.ok` หรือคำตัดสินของ LLM ใด ๆ เป็นหลักฐานผ่าน audit verifier เป็น flow control ใน run.js เท่านั้น
- เกณฑ์ผ่าน/ambiguous/หลาย output ใช้กฎเดียวกับ Atlas ข้างบน; timeout ต่อ case เป็นค่า default ของ `live_test` (override ได้ด้วย CLI flag ตอนรัน) ไม่ใช่ field ใน golden case เพราะ `evaluation.golden_cases[]` ปิด additionalProperties และ M9 ไม่ขยาย schema; evidence คือ stderr log + subagent turn count จาก summary
- skip: ไม่มี binary → exit 2; มี binary แต่ไม่มี provider key → exit 2 พร้อมประกาศ. skip ไม่เลื่อนสถานะไม่ว่าทางใด: package คงสถานะเดิมก่อน live audit คือ `draft` เฉพาะเมื่อ static audit (รวม `thclaws agent validate`) ผ่านแล้ว และยังเป็น `unverified` ถ้าไม่มี binary ให้ยืนยัน validate

M1 ตรวจว่า embedded schema เป็น JSON Schema ที่ถูกต้อง แต่ตามมาตรฐาน JSON Schema unknown keyword เป็น annotation ที่ผ่านได้; M5 static audit จะ warn ตอน `draft` และ fail ก่อน `shippable` เมื่อ keyword ไม่อยู่ใน vocabulary ที่ builder รองรับ

ผลลัพธ์ของ check ใช้ exit code เดียวกันทุก milestone: `0` = pass, `1` = fail, `2` = explicit skip เพราะ dependency ภายนอกยังไม่มี; CI ต้องรายงาน skip แยกและ fail เมื่อมี `1`

## 8. ผลลัพธ์ของ builder

- package โฟลเดอร์ตาม format `thclaws agent new` (manifest.json, AGENTS.md, `.thclaws/{settings.json,skills/,schemas/,scripts/,agents/}`) — script ของ pack วางใน `.thclaws/scripts/` เพราะเป็นที่เดียวที่ `thclaws agent validate` syntax-check python
- `.thclaws/mcp.json` เมื่อ pack ประกาศ MCP servers (M7a): generate `mcpServers` จาก command/args ของ pack เท่านั้น ไม่มีค่า secret; operator ตั้ง env และ start daemon โดยใช้ CWD=package ตาม §4
- `builder-build-report.json` (schema: `builder-build-report.schema.json`, สร้างและตรวจใน M4): target, generated files, guarantee matrix, audit result, compatibility result, deployment hints
- `atlas-register.json`: worker `role` / `tags` จาก `routing`, deployment-time `workspace_dir`, node template (`model`, `collect_files`, และ `output_format: json` เฉพาะ `assistant_json`; omit เมื่อเป็น `collect_files` ล้วน) และ edge template (`push_files` + `policy.file_handoff`), flow template เมื่อ target = `atlas-workflow`
- archive จาก `thclaws agent pack`

ขอบเขต generator M4: สร้าง `single-worker` สำหรับ `atlas-worker` และ `atlas-workflow` ตาม fixture ทั้งสอง; standalone pattern ยังเป็น compatibility intent และ generator ต้อง reject จนกว่าจะมี template orchestration ที่ตรวจแล้ว ห้ามสร้าง package ว่างแล้วอ้างว่ารองรับ pattern นั้น

ขอบเขต standalone generator M9 (เพิ่ม 2026-09-07): เริ่มจาก `static-pipeline` ตามโครงสร้างที่ `thclaws agent new --pattern static-pipeline` v0.116.0 สร้าง คือ `.thclaws/agents/{planner,worker,verifier}.md` + `.thclaws/agent_workflow/run.js` และ AGENTS.md ที่สั่งให้ orchestrator เรียก `WorkflowRun`; ไม่มี `routing` และไม่มี `atlas-register.json`. บน surface นี้ agent defs, hooks และ `allowed_tools` ทำงานจริง guarantee matrix จึงอ้าง tool/write restriction ต่อ role ได้ ต่างจาก `/agent/run` ใน §4. `batch-fanout` และ `dynamic` ยัง reject จนกว่าจะมี fixture และ template ของตัวเอง

Permission ต่อ role ใน standalone: spec ยังมี `permissions` ชุดเดียว (ห้ามให้ spec โต) และ generator map ลง role คงที่แบบ deterministic โดยยึดพฤติกรรมของ `subagent.rs` v0.116.0 ที่ `tools: []` หมายถึง **สืบทอด tool ทั้งหมดของ parent** และ `writePaths: []` หมายถึง **ไม่จำกัดการเขียน** ดังนั้น
- ทุก role ต้องมี `tools` ที่ไม่ว่างและเป็น subset ของ `permissions.tools` ใน spec; generator ต้อง reject ถ้า intersection ว่าง ห้ามปล่อยรายการว่างให้กลายเป็น inherit-all
- `planner` และ `verifier` เป็น T0 เสมอ ได้เฉพาะ read-only tools จาก spec (Read, Grep, Glob และ MCP read-only ที่ spec ประกาศ) ไม่มี Bash แม้ `shell: sandboxed` เพราะขัดกฎ T0 และ writePaths ไม่ scope Bash; ไม่มี `writePaths`
- `worker` ได้ `permissions.tools`, `shell`, `network` ของ spec; `write_scope: none` → ตัด Write/Edit และ file-write tools ออกจาก `tools` (ไม่ใช่ตั้ง writePaths ว่าง), `output` → `writePaths: ["output/**"]`, `workspace` → `writePaths: ["**"]` แบบ explicit. `tier` ของ spec คือ tier ของ worker
- M9 gate ต้องมี negative test บน thClaws จริง: role ที่ไม่ได้รับ Write เขียนไฟล์ไม่ได้, worker เขียนนอก `writePaths` ถูกปฏิเสธ, และ tool นอก subset เรียกไม่ได้ ไม่ใช่ตรวจแค่เนื้อหา `agents/*.md`
- ถ้าใช้จริงแล้ว 3 role คงที่ไม่พอ ค่อยพิจารณาเพิ่ม `roles[]` ใน schema เป็น milestone แยก ไม่แก้ในระหว่าง M9

ก่อน M5 ผล generate ใช้ `package_status: unverified` และ audit แต่ละชั้นเป็น `not_run` (ยกเว้น spec ที่ตรวจแล้ว). `thclaws agent validate` ใน check M4 เป็นหลักฐานของ gate ไม่แก้ report ย้อนหลังและไม่เลื่อนสถานะ package. ถ้า pack ยังไม่มี `pack.yaml` ให้ report เป็น dependency `missing`, ไม่เดาชื่อ MCP/secret และไม่ถือว่า capability ใช้งานได้; SQL pack จริงและ live fixture ยังอยู่ใน M7. `assets_bundled` หมายถึงคัดลอก asset ตาม descriptor แล้ว ไม่ได้พิสูจน์ว่า MCP ถูกติดตั้งหรือปลอดภัย

M4 เก็บ `execution_surface` เป็นข้อความใน report ไม่ใช้เป็นชื่อไฟล์. Manifest ใช้ `filesystem_scope: workspace` ตามรูปแบบ upstream ซึ่งไม่ใช่ `permissions.write_scope` enforcement; รายละเอียด tool/network/write declaration เก็บครบใน `agentspec.json` และ report. ไม่สร้าง host allowlist จากค่า network enum ที่ไม่มีรายชื่อ host

สำหรับ `collect_files`, `files.globs` คือ output contract หลัก; thClaws snapshot ไฟล์ที่ match หลัง run จึงใช้ส่งต่อไฟล์ที่ agent สร้าง **หรือ** ไฟล์ที่มีอยู่ก่อน run ได้ และไม่เปลี่ยน `write_scope` ของ agent; `schema` ถ้ามีหมายถึง schema ของ artifact manifest ไม่ใช่ JSON body ของ assistant ส่วน `refusal.schema` เป็น contract แยกของ refusal branch และ `evaluation.golden_cases[].expect` เป็นตัวเลือก schema ที่ live audit ใช้ validate

## 9. บทบาทของ AI

ทำได้: interview 7 ข้อ, แปลงคำอธิบายเป็น mission, ร่าง AGENTS.md / SKILL.md / refusal / golden cases, แนะนำ pack, meta-audit หลัง generate (ผลเป็นข้อเสนอที่คนต้อง accept)
ห้ามตัดสินใจ: เปิด network / shell, เขียนไฟล์ที่ไหน, ส่งอีเมล / เผยแพร่, ใช้ secret / MCP ใด, ผ่าน audit หรือไม่ — ส่วนนี้มาจาก spec + audit แบบ deterministic เท่านั้น

builder-agent (M11) คือการให้ thClaws ทำหน้าที่นี้: workspace `builder/` ที่มี AGENTS.md + skills (`spec-author`, `pack-author`) และเรียก `forge generate` / `forge audit` / `forge pack test` ผ่าน Bash **ใน workspace ของ builder เท่านั้น** ไม่ใช่ใน package ที่กำลังสร้าง. ผู้ใช้พิมพ์ความต้องการ → model ถาม 7 ข้อที่ยังไม่รู้ → เขียน `spec.yaml` → รัน forge วนจนผ่าน → รายงานสถานะตาม `builder-build-report.json` เท่านั้น. block `permissions`, `env`, `network` และ `capabilities` ต้องถูกยืนยันโดยคนก่อน generate (marker `confirmed_by` ใน spec ที่ builder-agent ต้องเห็นก่อนรัน generate; forge ไม่รู้จัก marker นี้ ตัดออกก่อนส่ง); ถ้า pack ที่ต้องใช้ยังไม่มี model ต้องรายงาน `missing` ตรง ๆ ไม่ประดิษฐ์ MCP หรือชื่อ tool และงานเขียน MCP เป็นงานแยกตาม §5 ที่กลับเข้ามาทาง `forge pack test`

## 10. นอกขอบเขต MVP (future)

- patch `agent_runtime.rs` ให้ `/agent/run` เคารพ `allowed_tools` / `disallowed_tools` / MCP allowlist / write-path (ต้อง filter ทั้ง tool definitions และ invocation จริง) — ควร upstream
- `agent_profile` บน `/agent/run` + `profiles[]` ใน `/v1/agent/info`
- auto-deploy ผ่าน `/v1/deploy` และ bundle version tracking (รวมกับ Atlas M4 Fleet)
- semantic router, marketplace, runtime framework ใหม่ — ไม่ทำ
