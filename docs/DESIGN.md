# Design baseline (2026-09-03)

เอกสารนี้คือข้อตกลงการออกแบบที่ใช้เป็นฐานของทุก milestone ใน [PLAN.md](PLAN.md)
ข้อเท็จจริงเกี่ยวกับ thClaws อ้างจาก source ของ fork `kaebmoo/thClaws` ที่ `crates/core`: baseline เดิม v0.116.0 (revision `75edc48`)
และตั้งแต่ M12 (2026-10-27) คือ v0.138.0 revision `d452ece` ซึ่งตรวจทุก claim ซ้ำแล้วใน §4 "Baseline 0.138.0 / d452ece"; ข้อความที่ยังอ้าง v0.116.0
คือ claim ที่ diff ยืนยันว่าไม่เปลี่ยน. ถ้า thClaws เปลี่ยนพฤติกรรม ให้แก้เอกสารนี้ก่อนแก้โค้ด

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
- ตรวจเพิ่ม 2026-09-10 บน v0.121.0 revision `35ccf16`: `.thclaws/state/todos.md` ถูกโหลดจาก CWD เข้า system prompt เมื่อมี open todo lines (`agent.rs::build_todos_reminder` เรียกจาก `agent.rs` turn loop, cap 80 บรรทัด/6 KB) จึงไม่ถือเป็น ignorable source-package state; static audit ต้องจับไฟล์นี้และชื่อ case variant บน filesystem แบบ case-insensitive และ live audit ต้องไม่คัดลอก state จาก source package เข้า temporary run directory.
- ตรวจเพิ่ม 2026-09-08 บน v0.116.0 revision `75edc48`: `agent_runtime.rs` โหลด MCP ก่อนสร้าง system prompt และส่ง `InitializeResult.instructions` ผ่าน `mcp.rs::collect_mcp_instructions` ไปที่ `prompts.rs::mcp_instructions_section` ตั้งแต่ request แรก. Skill catalog แสดงเพียง metadata; body ต้องโหลดผ่าน Skill ภายหลัง. Pack ที่ต้องให้คำแนะนำการใช้ MCP พร้อมตั้งแต่เริ่มจึงส่งคำแนะนำผ่าน initialize ได้ โดยไม่เพิ่มสิทธิ์ Skill หรือพึ่งข้อความเฉพาะ pack ใน template กลาง.
- `prompts.rs::load("system", …)` ของ ref เดียวกันรองรับ `.thclaws/prompt/system.md` ที่ **CWD ของ daemon** เพื่อแทน base prompt สำหรับงาน coding; ProjectContext/AGENTS.md, MCP instructions และ skill catalog ยังถูกต่อท้ายตามเดิม. Builder generate mission-runner profile ผ่านช่องทาง native นี้และ static audit ตรวจ drift เช่น generated files อื่น. ต้อง start daemon จาก package CWD เช่นเดียวกับ MCP config; การมีไฟล์ใน workspace_dir เพียงอย่างเดียวไม่ยืนยันว่า profile ถูกโหลด.
- built-in tools ของ `/agent/run` = `ToolRegistry::with_builtins()` + KMS / Memory / Task / WorkflowRun ที่ `agent_runtime.rs::build_runtime_with_provider` ลงทะเบียนเพิ่ม เป็นแหล่งของ catalog `patterns/tools-<version>.json` ที่ static audit ใช้
- thClaws ลงทะเบียน **ทุก** tool ที่ server advertise โดยไม่กรอง และไม่อ่าน MCP tool annotations (`readOnlyHint` ฯลฯ; `McpToolInfo` มีเพียง name / description / input_schema / ui). ถ้า spawn หรือ `tools/list` ล้มเหลว `agent_runtime.rs::load_mcp_servers_silent` แค่ `eprintln!` แล้ว run ต่อโดยไม่มี tool ของ server นั้น → การมี/ไม่มี tool ต้องพิสูจน์ด้วยหลักฐานเชิงบวก (SSE tool event, `/v1/agent/info`) ไม่ใช่จากการที่ run ไม่ error

OpenAI-compatible provider สำหรับ live audit (ตรวจ source v0.116.0 revision `75edc48`, 2026-09-07): model `oai/<id>` ใช้ `OPENAI_COMPAT_API_KEY` และ `OPENAI_COMPAT_BASE_URL` ใน `repl.rs::build_provider`; ตัด prefix `oai/` ก่อนส่ง request และเติม `/chat/completions` ถ้า URL ยังไม่มี suffix นี้. ไม่พบตัวเลือกปิด TLS certificate verification ของ provider นี้; ใช้การตรวจ TLS ตาม runtime ปกติ. การทดสอบผ่าน custom provider ต้อง pin model ใน spec ของ package ที่ทดสอบ ไม่แทน model อย่างเงียบ ๆ.

### Standalone T0 rollout (M9, 2026-09-09)

ผู้ใช้อนุมัติให้ส่งมอบ M9 เฉพาะ T0 และแยก T1 write boundary เป็นงานค้างที่ปิดการใช้งาน.
Generator ต้อง reject standalone ที่ไม่ใช่ T0 ก่อนเขียนไฟล์; static/live audit ใช้ render เดียวกันจึง block
package T1 รุ่นเก่าด้วย. ทุก role รับเฉพาะ Read/Grep/Glob หรือ qualified MCP tools ที่ pack ประกาศ nonmutating;
unknown tools, Task/WorkflowRun, KMS/Memory writers และ shell ไม่อยู่ใน profile นี้. ไม่ตีความชื่อ tier เป็นหลักฐาน
ว่า MCP ไม่มี side effects และไม่อ้าง filesystem read-only: runtime state และพฤติกรรม MCP ยังอยู่นอก guarantee.
ไม่มี writePaths ใน role ที่ส่งมอบ. Schema ยังอธิบาย T1 ได้เพื่อเก็บ intent; implementation ปฏิเสธอย่าง explicit.

`check_m9_standalone.py` default ตรวจขอบเขต T0 (functionality + role denials + real-provider golden cases).
`--write-boundary` เป็น native diagnostic ของ T1 ที่ยังไม่ส่งมอบ: ใช้ temporary role ที่เขียนเองใน gate เท่านั้น,
ไม่ปลด guard ของ generator. หาก traversal/symlink หลุดต้อง exit 1 และรายงาน BLOCKED; ไม่มี binary exit 2.
แม้ diagnostic ผ่านในอนาคต ก็ไม่ปลด T1 อัตโนมัติ ต้อง review evidence แล้วแก้ DESIGN/implementation ก่อน.

Compatibility ของ standalone T0: คู่ที่รับรองคือ 0.116.0/`75edc48`, 0.121.0/`35ccf16`, 0.129.0/`7a27129` และ 0.138.0/`d452ece` (M12).
Compatibility ของ standalone T0 ตรวจเพิ่มบน thClaws 0.121.0 revision `35ccf16` (binary rebuilt 2026-09-09):
ใช้ native agent new/validate, named role tool subsets, CLI workflow output และ provider แบบเดียวกับ baseline
0.116.0. Native gate เป็นเงื่อนไขรับรองรุ่นเพิ่มนี้; scope เฉพาะ T0 CLI ไม่เลื่อน baseline ของ Atlas/export.
Report `thclaws_baseline` ยังหมายถึง template/catalog baseline 0.116.0; runtime ที่ตรวจจริงอยู่ใน
`static_audit.thclaws_validate.version`. รุ่นอื่นยัง fail จนตรวจใหม่. ข้อจำกัด writePaths พบแล้วบน
0.116.0 `75edc48` และ 0.121.0 `35ccf16`; gate แยกตรวจ binary ใหม่ได้โดยไม่อ้างว่า T1 รองรับ.

## 5. Capability pack

pack คือความสามารถสำเร็จรูปที่ประกอบเข้า package ได้ อยู่ใน `packs/<name>/pack.yaml` ประกาศ: MCP servers / skills / scripts ที่เพิ่ม, env ที่ต้องการ (ชื่อ), tier ต่ำสุดที่ต้องใช้, fixture สำหรับ live audit ที่รันได้โดยไม่ต้องมีข้อมูลจริง
pack แรก ๆ: `sql-readonly` (ห่อ MCP ของ project AI: metadata / query / validation ไม่รวม admin), `report-pipeline` (ห่อ build + verify ของ NT-Report), `publisher-email-sftp` (MCP ใหม่ มี `dry_run` + `idempotency_key`)
กฎ: pack ที่มี tool แบบ mutating ต้องใช้ T2; T2 ใน M1 ใช้ได้เฉพาะ `atlas-workflow` เพราะมี owner สำหรับ `human_gate`; `T2 + atlas-worker` และ `T2 + thclaws-standalone` ถูก reject จนกว่าจะมี approval story ของ target นั้น

T0 ต้องประกาศ `shell: none`, `network: none`, `write_scope: none`; ทุก tier ที่ประกาศ `shell: none` ห้ามประกาศ `Bash`, `write_scope: none` ห้ามประกาศ `Write`, `Edit`, `FetchImages` หรือ document writers, และ `network: none` ห้ามประกาศ `WebFetch`, `WebSearch`, `WebScrape`, `FetchImages` หรือ `YouTubeTranscript`
นอกจากนี้ `write_scope` ที่ไม่ใช่ `workspace` ห้ามประกาศ built-in ที่เขียนใต้ `.thclaws/` (`TodoWrite`, `Memory*`, `Kms*` writers, `GuiShell*` — ตรวจ source 2026-09-10 บน v0.121.0 revision `35ccf16`) เพราะไฟล์ที่ tool เหล่านี้เขียนไม่ได้อยู่ใน `output/` และ static audit ต้อง reject เป็น undeclared file
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
- `conformance` ของ pack ไม่ต้องใช้ provider key: harness `forge pack test <name>` (stdlib stdio JSON-RPC) จัดวาง scripts และ skills ที่ประกาศไว้ด้วย path แบบเดียวกับ generated package แล้ว start server ตาม `command`, ทำ `initialize` → `tools/list` ต้องเท่ากับ `tools` ที่ประกาศ, ยิงทุก case, บันทึก `pack-conformance.json`; ไม่มี runtime ของ server ในเครื่อง → exit 2
- ชื่อ server ต้องตรง `^[a-z0-9][a-z0-9-]*$` (ห้าม `_`) และ bare tool ต้องตรง `^[a-z0-9][a-z0-9_]*$` (ห้าม `__`) เพื่อให้ qualified name แยกกลับเป็น server/tool ได้ทางเดียว; thClaws sanitize ชื่ออื่นแบบเงียบ ๆ แต่ builder reject
- `tools` ของ pack ส่วน MCP = **ทุก** tool ที่ server advertise เพราะ thClaws ลงทะเบียนทั้งหมด (§4) และ spec ต้องประกาศ ⊇ ชุดนี้: declaration ของ spec คือ inventory ของสิ่งที่ model จะเห็นจริง ไม่ใช่รายการที่อยากใช้. server ที่มี tool แบบ admin ต้องถูก launch ในโหมดที่ไม่ expose (ผ่าน `args`) หรือ pack นั้นเป็น T2
- harness รัน server ด้วย environment สะอาด (PATH + env ที่ประกาศพร้อมค่าจาก fixture เท่านั้น) เพื่อให้ dependency ต่อ env ที่ไม่ได้ประกาศโผล่เป็น start failure ตั้งแต่ตอน test ไม่ใช่ตอน deploy
- `pack-conformance.json` เก็บ digest ของ `tools/list` (ชื่อ + input schema) ครั้งล่าสุด; server ภายนอกที่เปลี่ยน surface จะถูกจับได้ในการ test ครั้งถัดไป และ pack owner ต้องแก้ `pack.yaml` ก่อน spec ที่ใช้มันจะกลับมา `draft`
- `forge pack discover --name <server> --command <cmd> [--env NAME] [--out <file>] --args ...` (M10) ร่าง `pack.yaml` จากสิ่งที่ MCP บอกเองได้ (ชื่อ server จาก argument, tools + input schema + description จาก `tools/list`, digest ของ surface, annotations ถ้ามีเป็นข้อเสนอของ `mutating` ที่เขียนลง field พร้อม comment ให้ยืนยัน) แต่ไม่ตัดสิน `min_tier`, `network`, fixture (ปล่อย `TODO` ที่ทำให้ `validate_pack` reject) และไม่ยืนยัน `mutating`; `--env NAME` ส่งผ่านเฉพาะชื่อจาก process ปัจจุบันเพื่อให้ server start ได้และลงชื่อนั้นใน `env` ของร่าง ค่าไม่ถูกพิมพ์. server name/tool name นอก charset ถูก reject ตั้งแต่ discover. `forge pack new <name>` scaffold รูป v2 เดียวกันพร้อม `fixture/setup.py` และ README; ทั้งสองไม่ใช่ `conformant` จนกว่าผู้เขียนจะเติมส่วนที่เหลือแล้ว `forge pack test` ผ่าน (ดู `docs/packs.md`, reference `fixtures/packs/echo/`)

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
คำขอ preview ที่ระบุ `dry_run: true` และตรง contract เรียก publisher MCP ได้โดยไม่ต้องมี human approval;
การส่งจริง `dry_run: false` ยังต้องผ่าน T2 deployment/Atlas gate และห้าม model เปลี่ยน preview เป็นการส่งจริง.
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
| ต้องมีคนอนุมัติก่อน side effect | Not verified สำหรับ package ที่ generate; Enforced เฉพาะ deployment ที่ตรวจแล้ว | ต้องผ่าน Atlas `human_gate` ก่อน worker และ operator bind worker/workspace กับ T2 daemon แยกที่ยืนยันแล้ว; การเรียก `/agent/run` โดยตรงสามารถข้าม gate นี้ได้ |
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
- `.thclaws/prompt/system.md` (M7b, จาก `templates/system.md.j2`): mission-runner profile ที่แทน base prompt ของ coding assistant ตาม §4; ข้อความกลาง ไม่มีส่วนเฉพาะ pack และไม่มีวงเล็บปีกกา; โหลดเฉพาะเมื่อ daemon start จาก CWD=package เช่นเดียวกับ mcp.json
- `builder-build-report.json` (schema: `builder-build-report.schema.json`, สร้างและตรวจใน M4): target, generated files, guarantee matrix, audit result, compatibility result, deployment hints
- `atlas-register.json` (M8, `forge export`, อยู่นอก package): worker payload (`id`, `name`, `base_url`, `role` / `tags` จาก `routing`; token ไม่อยู่ในไฟล์), workspace payload (`worker_id`, `workspace_key`, deployment-time `workspace_dir`), node template (`model`, `prompt` ที่ render แล้วเป็น JSON เทียบเท่า input envelope ของ live audit, `collect_files` จาก `files.globs`, `outputs` + `output_format: json` เฉพาะ `assistant_json`; omit ทั้งคู่เมื่อเป็น `collect_files` ล้วน), edge template (`push_files: ["files.__UPSTREAM_NODE_ID__.*"]` + `policy_template.file_handoff: true` เมื่อมี input `atlas_file_handoff`), deployment list (env names, MCP servers, network/hosts, daemon CWD, `reachability_checks`, `deployment_verified: false` ซึ่ง builder ไม่เคยตั้งเป็น true) และ `package_status` ที่คัดลอกจาก build report โดยไม่เลื่อน. Export ปฏิเสธ package ที่ report ไม่บันทึก `audit.static = passed` หรือบันทึก `audit.manifest = failed`; มี binary แต่ report ยัง `manifest: skipped` → รัน `thclaws agent validate` ก่อน pack และปฏิเสธเมื่อไม่ผ่าน; `--out` ต้องอยู่นอก package เพราะ `thclaws agent pack` เดินทุกไฟล์ใน package รวม staging directory
- `atlas-workflow.json` เมื่อ target = `atlas-workflow`: workflow definition ที่ POST เข้า `/api/workflows` ได้ตรง ๆ; T2 คือ fragment ของ M7b ที่ bind worker/workspace id แล้ว, T0/T1 คือ graph node เดียวไม่มี edge
- archive `<name>-<version>.tar.gz` จาก `thclaws agent pack` พร้อม sidecar `.sha256`; ไม่มี binary → export เขียนเฉพาะไฟล์ JSON และรายงาน skip (exit 2); binary ที่ไม่ใช่ baseline `0.116.0` → fail (exit 1). พฤติกรรม pack (ตรวจ source v0.116.0 revision `75edc48`, 2026-09-09): `agent_cli.rs::pack_to_file` fuse identity จาก `.thclaws/settings.json::agent` ลง `manifest.json` (เพิ่ม `id`, `name`, `description`, `preview`, `pricing`; field เดิมคงค่า) และ `pack.rs` ตัด block `agent` ออกจาก `.thclaws/settings.json`, strip prefix `.thclaws/state/`, `.git/`, `node_modules/`, `target/`, `__pycache__/`, `.venv/`, `.next/`, `dist/`, `build/`, suffix `.env`/`.key`/`.pyc`/`.log`, path ที่มี `_secret` และ `.thclaws/audit-findings.json`/`.thclaws/usage.jsonl`; mtime ของสมาชิกมาจากไฟล์จริง archive จึงไม่ byte-stable ข้าม generate. (0.138.0/`d452ece` เพิ่ม prefix `.thclaws/bots/`, `.home/`, `.playwright-mcp/` และ exact `.thclaws/media-jobs.jsonl` → ดู §4 M12; กฎ fuse/rewrite สองไฟล์ไม่เปลี่ยน) Tree ที่แตกจาก archive จึงต่างจาก package ที่ audit เฉพาะ `manifest.json` และ `.thclaws/settings.json`: `audit.py` บน tree นั้นรายงาน `drift` เป็นเรื่องปกติ ต้องรัน audit กับ source package; `forge export` ตรวจ archive หลัง pack ทุกครั้ง: สมาชิกต้องเท่ากับ `generated_files` ของ report และ byte ตรงกับ package ยกเว้นสองไฟล์นั้น มิฉะนั้น fail (asset ของ pack หรือ generated file ที่ path มี `_secret` หรือลงท้าย `.env`/`.key`/`.log` เช่น script `client_secret.py` หรือ input ชื่อ `api_secret` → `inputs--api_secret.json` ถูก pack ตัดเงียบ ๆ และ daemon จะรันต่อโดยไม่มี MCP นั้นตาม §4); gate M8 ตรวจกรณีนี้ด้วย input `api_secret` และ start daemon จาก tree ที่แตกจาก archive
- Layout ของ package ที่ deploy (M13, ตรวจ source 0.138.0 `d452ece`, รายละเอียดใน §4 "Host + bots layout"): package และ tree ที่แตกจาก archive เป็น **single-agent workspace**
  (agent อยู่ที่ root) เสมอ; daemon-per-package start ด้วย `THCLAWS_AUTO_MIGRATE=0` ตั้งชัดเจน เพราะ `thclaws --serve` ของ desktop build จะแปลง tree เป็น host workspace
  ในที่เดิมและเขียนทับ AGENTS.md/skills/scripts (drift ต่อ audit, `collect_files` กับ identity อ้างคนละ directory). ห้ามเปิด package ที่ audit แล้วใน desktop app
  หรือรัน `thclaws bots migrate` กับมัน; ถ้าเกิดขึ้นให้ deploy ใหม่จาก archive ไม่ใช่ `unmigrate` แล้วใช้ต่อ. `thclaws agent validate`/`pack` ชี้โฟลเดอร์ที่มี `manifest.json`
  (ใน host workspace คือ `.thclaws/bots/<slug>/`). harness ของ builder ใช้ env จาก `forge/live_test.py::isolated_env` และตรวจ `check_layout` หลัง daemon พร้อม (M6/M7b/M8/M11);
  `deployment_verified` ยัง `false` และ operator ต้องตรวจ `/v1/agent/info.workspace_dir` เท่ากับ `deployment.package_root` ก่อนลงทะเบียน

Atlas registration contract (M8, ตรวจ source `atlas-control-plane` ref `daa0f4966899327fb563501435f5d6180eeef9c3` 2026-09-08; schema pinned ไว้ที่ `patterns/atlas-workflow-definition.schema.json` และ gate M8 fail เมื่อไฟล์ต้นทางต่างจากที่ pin):
- `POST /api/workers` (permission `admin`) upsert ด้วย `id` **หรือ** `base_url` (`db.py::upsert_worker`): register export ที่สองด้วย URL เดิมจะเขียนทับ worker เดิมโดยคง id เดิม และ `wrk_<name>` ของ package ที่สองไม่ถูกสร้าง → หนึ่ง daemon URL ต่อหนึ่ง worker หรือระบุ `--worker-id` ให้ตรง worker ที่มีอยู่; `base_url` ต้อง http(s) เท่านั้น; `token` คือ `THCLAWS_API_TOKEN` ของ daemon และ response ไม่คืน token. `POST /api/workspaces` ต้องมี `worker_id`, `workspace_key`, `workspace_dir` (path บนเครื่อง worker). `POST /api/workflows` validate graph, policy และ reference ตอนบันทึก: worker/workspace ที่ node pin ต้องมีอยู่ก่อน จึงต้อง register worker → workspace → workflow ตามลำดับ
- worker node field ที่ builder ใช้จำกัดตาม AGENTS.md; `output_format: json` ทำให้ Atlas `json.loads` ทั้งก้อนโดยไม่ตรวจ schema (`workflows.py::_store_output_artifact`); `collect_files` ถูก validate ตอนบันทึกด้วยกฎเดียวกับ job submit
- `push_files` เป็น glob ของ artifact key `files.<node_key>.<relpath>` จาก `collect_files` ของ node ต้นทาง ต้องเปิด `policy.file_handoff` (ตรวจทั้งตอนบันทึกและตอน run) และไฟล์ลงที่ `inputs/incoming/<run_id>/<node_key>/` ซึ่ง `{files_dir}` ใน prompt ถูกแทนค่า; node เริ่มต้นของ workflow ไม่มี edge เข้าจึงรับ push_files ไม่ได้ → exporter reject `atlas-workflow` ที่มี input `atlas_file_handoff` และให้ใช้ `atlas-worker` แล้วประกอบ edge template ต่อจาก upstream node เอง
- `render_prompt` (`workflows.py::_FIELD_RE`, `_prompt_value`) แทนเฉพาะ placeholder `{input.<name>}` ที่ `<name>` ตรง `[A-Za-z_]\w*` และ JSON-encode เฉพาะค่า object/array (string ถูกแทรกโดยไม่มี quote, boolean เป็น `True`); placeholder ที่ไม่ตรงรูปถูกปล่อยไว้ตามตัวอักษรโดยไม่ error. AgentSpec semantic check (M1, `forge/spec.py`) จึง reject Atlas spec ที่ชื่อ input หรือ `assistant_json` output (artifact key ใช้ pattern เดียวกัน) ไม่ตรง charset นี้ หรือ schema ของ input ไม่ใช่ `type: object`/`array`; ชื่อ `collect_files` output ไม่ถูกส่งให้ Atlas. เมื่อ key ระดับบนที่อ้างขาด Atlas fail node นั้นหลังสร้าง run แล้ว (ไม่ใช่ตอน submit): golden case `missing_input` ที่ละทั้ง key (fixture ทั้งสาม) จึงถูก Atlas ปฏิเสธก่อน dispatch ส่วน case ที่ส่ง key ครบแต่ค่าไม่ครบยังถึง refusal branch ของ agent. Prompt ที่ render ได้เป็น JSON เทียบเท่า `golden_cases[].input` (compact separators) ไม่ใช่ข้อความเดียวกับ live audit ซึ่งส่ง `json.dumps(input)` ล้วน: T2 มี prefix `Execute only the approved input: ` และ node ที่รับ file handoff มีบรรทัด `Handoff files directory: {files_dir}` ต่อท้าย ซึ่ง live audit ไม่ได้ทดสอบ (gate ตรวจด้วย `atlas.workflows.render_prompt` จริง)
- `interface` v1 ของ Atlas ไม่ถูก export เพราะ bounded profile ไม่มี `pattern` และ keyword อื่นที่ AgentSpec ใช้; input validation ยังเป็นของ live audit และ AGENTS.md ของ package
- หลักฐานของ gate: checkout ของ Atlas ต้องอยู่ที่ ref ที่ pin (ต่างจากนี้ = fail จนกว่าจะตรวจย่อหน้านี้ใหม่และแก้ `ATLAS_REF`; `atlas_ref` ในไฟล์ export คือ ref ที่ pin schema ไม่ใช่ revision ที่ operator ใช้จริง), Atlas in-process ยอมรับ worker/workspace/workflow จากไฟล์ export และ `POST /api/workers/<id>/poll` เห็น daemon ที่ start จาก tree ที่แตกจาก archive เป็น `online` พร้อม `agent_info.agent.mcp_servers` ตรงกับ `deployment.mcp_servers`; นี่ยืนยันว่า Atlas เห็น daemon และ MCP inventory ไม่ใช่ว่า deployment จริง isolated, มี egress control หรือผ่าน approval (`deployment_verified` ยัง `false`)

ขอบเขต generator M4: สร้าง `single-worker` สำหรับ `atlas-worker` และ `atlas-workflow` ตาม fixture ทั้งสอง; standalone pattern ยังเป็น compatibility intent และ generator ต้อง reject จนกว่าจะมี template orchestration ที่ตรวจแล้ว ห้ามสร้าง package ว่างแล้วอ้างว่ารองรับ pattern นั้น

ขอบเขต standalone generator M9 (เพิ่ม 2026-09-07): เริ่มจาก `static-pipeline` ตามโครงสร้างที่ `thclaws agent new --pattern static-pipeline` v0.116.0 สร้าง คือ `.thclaws/agents/{planner,worker,verifier}.md` + `.thclaws/agent_workflow/run.js` และ AGENTS.md ที่สั่งให้ orchestrator เรียก `WorkflowRun`; ไม่มี `routing` และไม่มี `atlas-register.json`. บน surface นี้ agent defs, hooks และ `allowed_tools` ทำงานจริง guarantee matrix อ้าง tool restriction ต่อ role ได้; writePaths มีข้อจำกัด raw path ตามหลักฐาน M9 ด้านล่าง จึงยังไม่ใช่ write boundary ที่รับรองได้. `batch-fanout` และ `dynamic` ยัง reject จนกว่าจะมี fixture และ template ของตัวเอง

Permission ต่อ role ใน standalone: spec ยังมี `permissions` ชุดเดียว (ห้ามให้ spec โต) และ generator map ลง role คงที่แบบ deterministic โดยยึดพฤติกรรมของ `subagent.rs` v0.116.0 ที่ `tools: []` หมายถึง **สืบทอด tool ทั้งหมดของ parent** และ `writePaths: []` หมายถึง **ไม่จำกัดการเขียน** ดังนั้น
- ทุก role ต้องมี `tools` ที่ไม่ว่างและเป็น subset ของ `permissions.tools` ใน spec; generator ต้อง reject ถ้า intersection ว่าง ห้ามปล่อยรายการว่างให้กลายเป็น inherit-all
- `planner` และ `verifier` เป็น T0 เสมอ ได้เฉพาะ read-only tools จาก spec (Read, Grep, Glob และ MCP read-only ที่ spec ประกาศ) ไม่มี Bash แม้ `shell: sandboxed` เพราะขัดกฎ T0 และ writePaths ไม่ scope Bash; ไม่มี `writePaths`
- `worker` ในขอบเขต M9 T0 ใช้กฎเดียวกับ planner/verifier: tools ต้องอยู่ในชุด nonmutating ที่รองรับ, shell/network/write_scope เป็น none และไม่มี writePaths. ไม่ตัด tool ที่ไม่รองรับเงียบ ๆ แต่ reject spec ที่ generator. T1 output mapping ของแผนเดิมเลื่อนไปหลัง upstream write boundary fix; T2 standalone ยังถูก schema/semantic checks ปฏิเสธตามเดิม.
- Standalone role files ฝัง snapshot ของ `AGENTS.md` ที่ generate แล้ว ดังนั้น `AGENTS.md` ไม่ใช่ editable prose สำหรับ target นี้; การ harden contract ต้องแก้ spec/template แล้ว generate ใหม่ ไม่เช่นนั้น audit ต้องจับเป็น drift.
- Gate M9 T0 ต้องบังคับเรียก Write/Edit/Bash กับทั้งสาม role บน thClaws จริงแล้วถูกปฏิเสธ. แยก `--write-boundary` สำหรับ T1 ที่ยังปิด: ต้อง fail เมื่อพบ traversal/symlink bypass ไม่ถือการพบ bug เป็น pass. ไม่ใช่ตรวจแค่เนื้อหา `agents/*.md`
- ถ้าใช้จริงแล้ว 3 role คงที่ไม่พอ ค่อยพิจารณาเพิ่ม `roles[]` ใน schema เป็น milestone แยก ไม่แก้ในระหว่าง M9

ก่อน M5 ผล generate ใช้ `package_status: unverified` และ audit แต่ละชั้นเป็น `not_run` (ยกเว้น spec ที่ตรวจแล้ว). `thclaws agent validate` ใน check M4 เป็นหลักฐานของ gate ไม่แก้ report ย้อนหลังและไม่เลื่อนสถานะ package. ถ้า pack ยังไม่มี `pack.yaml` ให้ report เป็น dependency `missing`, ไม่เดาชื่อ MCP/secret และไม่ถือว่า capability ใช้งานได้; SQL pack จริงและ live fixture ยังอยู่ใน M7. `assets_bundled` หมายถึงคัดลอก asset ตาม descriptor แล้ว ไม่ได้พิสูจน์ว่า MCP ถูกติดตั้งหรือปลอดภัย

M4 เก็บ `execution_surface` เป็นข้อความใน report ไม่ใช้เป็นชื่อไฟล์. Manifest ใช้ `filesystem_scope: workspace` ตามรูปแบบ upstream ซึ่งไม่ใช่ `permissions.write_scope` enforcement; รายละเอียด tool/network/write declaration เก็บครบใน `agentspec.json` และ report. ไม่สร้าง host allowlist จากค่า network enum ที่ไม่มีรายชื่อ host

สำหรับ `collect_files`, `files.globs` คือ output contract หลัก; thClaws snapshot ไฟล์ที่ match หลัง run จึงใช้ส่งต่อไฟล์ที่ agent สร้าง **หรือ** ไฟล์ที่มีอยู่ก่อน run ได้ และไม่เปลี่ยน `write_scope` ของ agent; `schema` ถ้ามีหมายถึง schema ของ artifact manifest ไม่ใช่ JSON body ของ assistant ส่วน `refusal.schema` เป็น contract แยกของ refusal branch และ `evaluation.golden_cases[].expect` เป็นตัวเลือก schema ที่ live audit ใช้ validate

## 9. บทบาทของ AI

ทำได้: interview 7 ข้อ, แปลงคำอธิบายเป็น mission, ร่าง AGENTS.md / SKILL.md / refusal / golden cases, แนะนำ pack, meta-audit หลัง generate (ผลเป็นข้อเสนอที่คนต้อง accept)
ห้ามตัดสินใจ: เปิด network / shell, เขียนไฟล์ที่ไหน, ส่งอีเมล / เผยแพร่, ใช้ secret / MCP ใด, ผ่าน audit หรือไม่ — ส่วนนี้มาจาก spec + audit แบบ deterministic เท่านั้น

builder-agent (M11) คือการให้ thClaws ทำหน้าที่นี้: workspace `builder/` ที่มี AGENTS.md + skills (`spec-author`, `pack-author`) และเรียก `forge generate` / `forge audit` / `forge pack test` ผ่าน Bash **ใน workspace ของ builder เท่านั้น** ไม่ใช่ใน package ที่กำลังสร้าง. ผู้ใช้พิมพ์ความต้องการ → model ถาม 7 ข้อที่ยังไม่รู้ → เขียน `spec.yaml` → รัน forge วนจนผ่าน → รายงานสถานะตาม `builder-build-report.json` เท่านั้น. block `permissions`, `env`, `network` และ `capabilities` ต้องถูกยืนยันโดยคนก่อน generate (marker `confirmed_by` ใน spec ที่ builder-agent ต้องเห็นก่อนรัน generate; forge ไม่รู้จัก marker นี้ ตัดออกก่อนส่ง); ถ้า pack ที่ต้องใช้ยังไม่มี model ต้องรายงาน `missing` ตรง ๆ ไม่ประดิษฐ์ MCP หรือชื่อ tool และงานเขียน MCP เป็นงานแยกตาม §5 ที่กลับเข้ามาทาง `forge pack test`

Implementation ของ M11 (2026-10-26): `builder/` เป็น thClaws agent package (manifest `shell_execution: sandboxed`, ไม่มี MCP, ไม่มี `.thclaws/agents/`
หรือ workflow) ที่ผ่าน `thclaws agent validate`. การเรียก forge ทุกครั้งผ่าน runner เดียว `builder/.thclaws/scripts/build.py <work/<name>/spec.yaml> --out out/<name> [--replace]`
ซึ่ง (1) ปฏิเสธ spec ที่ไม่มี block `confirmed_by: {permissions, env, capabilities}` ครบสามคีย์พอดี (network ยืนยันรวมกับ permissions)
หรือค่าว่าง/ชื่อสงวน (`model`, `assistant`, `builder` ...) โดยไม่สร้างไฟล์ใด (2) ตัด marker แล้ว `validate_spec` (3) `forge pack test` ทุก pack
ที่มี descriptor (4) `forge generate` แล้ว `forge audit --write` (5) พิมพ์ `package_status`, audit layers, สถานะ pack และ findings จาก report
และคืน exit code ของ audit (0 draft / 1 fail / 2 ไม่มี binary). `--replace` ลบได้เฉพาะ directory ที่มี `agentspec.json` + `builder-build-report.json`
ที่ runner สร้างไว้; checkout ของ forge มาจาก `AGENT_BUILDER_ROOT` หรือ repo ที่ `builder/` อยู่ และ `AGENT_BUILDER_PACKS` ใช้ได้เฉพาะ gate.
ข้อจำกัด: runner ตรวจได้เพียงว่ามี marker ถูกรูป ไม่พิสูจน์ว่าคนเป็นผู้ยืนยัน; AGENTS.md จึงห้าม model เติม marker โดยไม่มีคำยืนยัน และ gate live
ตรวจว่า model เรียก build.py ผ่าน Bash จริง (SSE `tool_use_start` ที่ input มี `build.py`) ก่อนเชื่อสรุปของมัน. เพราะ schema บังคับ `network != none`
→ T2 → `atlas-workflow` (§5) spec พยากรณ์อากาศจาก Google Weather จึงเป็น T2 เสมอจนกว่าจะมี tier/target สำหรับ MCP ที่ออก network โดยไม่มี side effect

Layout และ live gate ของ builder (M13, thClaws 0.138.0 `d452ece`; เหตุผลใน §4 "Host + bots layout"): `builder/` เป็น **host workspace v4** ใน git — root มี
`.thclaws/bots.json` (agent เดียว slug `main`) และ `.thclaws/settings.json` ของ host (`workspaceVersion: 4`), agent ทั้งหมด (AGENTS.md, manifest.json, `.thclaws/{settings,skills,scripts}`)
อยู่ที่ `builder/.thclaws/bots/main/`; `thclaws agent validate` และ `agent pack` ชี้โฟลเดอร์นั้น. ใต้ host shell ของ agent มี cwd = workspace root (ที่ `work/` และ `out/` อยู่)
ส่วนโฟลเดอร์ของ agent มาทาง `$THCLAWS_AGENT_DIR` ซึ่ง thClaws ตั้งให้ Bash เสมอแม้ไม่มี host; AGENTS.md/skills จึงเรียก runner เป็น
`python3 "$THCLAWS_AGENT_DIR/.thclaws/scripts/build.py" ...` และห้ามใช้ `python3 .thclaws/...` เปล่า (ซึ่งจะชี้ `.thclaws/` ของ host). `build.py` หา checkout โดยไล่ขึ้นจาก
ตำแหน่งไฟล์จนพบ `forge/cli.py` และมี `--list-packs` พิมพ์ pack ที่ checkout มีจริง (ชื่อ, qualified tools, env names) จาก `forge.packs` เพื่อให้ model ไม่ต้องหา `packs/` เอง
(ปัจจัยที่ทำให้ M11 live ไม่ถึง build.py: workspace ของ live ไม่มี `packs/`). การยืนยันเป็น**เทิร์นแยก**: เมื่อผู้ใช้ตอบครบแต่ยังไม่ยืนยัน model ต้องแสดง block แล้วหยุด;
เมื่อยืนยันในข้อความถัดไป model เติม `confirmed_by` และ build ในเทิร์นนั้น. Gate M11 live จึงสตาร์ท daemon จาก bot dir ด้วย `THCLAWS_WORKSPACE_ROOT=<copy root>`
(หรือ `--via-host` บน desktop build), ส่งสองเทิร์นผ่าน `session_id` (SSE event `session`), เก็บ SSE events ทันทีที่มาถึง (ไม่หายเมื่อ timeout), มี idle timeout ตรวจ stall,
บังคับระบุ `--model`, รองรับ `--rounds N` และเขียน evidence ต่อรอบ. การแก้ AGENTS.md/skill ครั้งนี้นับเป็นการแก้ prompt ต้องรัน live ซ้ำอย่างน้อย 3 รอบก่อนอ้างว่าแก้แล้ว.

## 10. นอกขอบเขต MVP (future)

- patch `agent_runtime.rs` ให้ `/agent/run` เคารพ `allowed_tools` / `disallowed_tools` / MCP allowlist / write-path (ต้อง filter ทั้ง tool definitions และ invocation จริง) — ควร upstream
- `agent_profile` บน `/agent/run` + `profiles[]` ใน `/v1/agent/info`
- auto-deploy ผ่าน `/v1/deploy` และ bundle version tracking (รวมกับ Atlas M4 Fleet)
- semantic router, marketplace, runtime framework ใหม่ — ไม่ทำ

### M9 implementation evidence (2026-09-09, thClaws 0.116.0 `75edc48`)

CLI role tool lists are enforced by `ProductionAgentFactory` before invocation. `writePaths` wraps only
Write/Edit/DocxCreate/DocxEdit/PptxCreate/PptxEdit/XlsxCreate/XlsxEdit/PdfCreate/EpubCreate/NotebookEdit.
Its `PathScopedWriteTool::check` matches the raw relative path, without canonicalization; it is not a complete
filesystem boundary (Bash/MCP are also outside that wrapper). M9 must therefore keep `output_write_scope`
Declared, describing this limitation, even after a native direct out-of-glob denial test passes. Tool allowlists
and Bash exclusion can be Enforced for named roles, not for the outer orchestrator or runtime state writes.
M9 starts with a sole assistant_json output; collect_files-only/multiple-output standalone generation is rejected.
The three roles run once in fixed order; verifier returns the final result/refusal branch, which forge validates
independently. CLI audit records workflow results, not fabricated SSE/MCP inventory evidence.

Native M9 probe ยืนยันทั้งการปฏิเสธ `outside.txt`, การเขียน `output/allowed.txt` สำเร็จ และการเขียน
`output/../traversal.txt` สำเร็จบน binary revision เดียวกัน. ห้ามเลื่อน output_write_scope เป็น Enforced
จน upstream canonicalize path หรือมี enforcement เพิ่มที่ตรวจแล้ว. `.thclaws/settings.json.model` ถูกตั้งจาก
pinned spec สำหรับ bootstrap provider ของ CLI (`config.rs::ProjectConfig.model`); ไม่เพิ่ม field ให้ AgentSpec.

M9 T0 native check 2026-09-09 บน 0.121.0 `35ccf16`: agent new/validate และ pipeline สาม role ผ่าน;
forced Write/Edit/Bash ถูกปฏิเสธครบทั้งสาม role. T1 diagnostic แยกยัง fail ทั้ง `output/../traversal.txt`
และ `output/link/escaped.txt` เมื่อ link ชี้ไป directory นอก output; positive control เขียนใน output ได้.
ดังนั้น scoped compatibility ของ T0 ไม่ใช่การรับรอง writePaths หรือยกระดับ Atlas runtime.

### Revision provenance (2026-10-26)

ตรวจ history ของ `kaebmoo/thClaws` บน GitHub (643 commits ถึง `v0.146.0` = `492bfb2`): release commit ของ `v0.116.0` คือ
`b962abc`, `v0.121.0` คือ `e45475d`, `v0.129.0` คือ `a3cb0a0`; revision ที่เอกสารนี้และ gate pin ไว้ (`75edc48`, `35ccf16`, `7a27129`)
ไม่อยู่ใน history นั้น จึงเป็น commit ใน checkout ของผู้ดูแล (thClaws คำนวณ revision จาก `git rev-parse --short HEAD` ใน
`crates/core/build.rs`). ผลต่อ gate: Atlas/export ตรวจเฉพาะ `thclaws 0.116.0` จึงรับ binary ที่ build จาก `b962abc` ได้
(M4/M5/M7b/M8/M10 native ผ่านด้วย binary นี้) ส่วน standalone T0 ตรวจคู่ version+revision จึง fail closed กับ `b962abc` ตามที่ตั้งใจ;
จะเพิ่มคู่ `0.116.0/b962abc` ได้ก็ต่อเมื่อตรวจ source ของ commit นั้นเทียบ claim ใน §4 และรัน M9 native ผ่านก่อน.
Build แบบ CLI-only ต้องมี `frontend/dist/index.html` (ไฟล์ว่างพอ) เพราะ `gui.rs`/`server.rs` `include_str!` ไฟล์นี้.

### Baseline 0.138.0 / d452ece (M12, 2026-10-27)

Source: GitHub branch `baseline-0.138.0-d452ece` (tree `bd085f15077261755d98fc3b5afcc70cd77ed14a` ตรงกับ binary บน Mac ของผู้ดูแล;
commit = `v0.138.0` `e614db6` + merge `upstream/main`). ตรวจด้วย `git diff b962abc d452ece` บนทุกไฟล์ที่ §3/§4/§7/§8 อ้างถึง แล้วเปิด source อ่านยืนยัน:

**ไม่เปลี่ยน** (diff ว่างหรือไม่แตะ claim): `api_v1/agent.rs` (field ของ `/agent/run`, ชื่อ/payload ของ SSE รวม `tool_use_start.input`),
`api_v1/info.rs` (`skills`, `mcp_servers{name,command,tool_count}`), `api_v1/mod.rs` (`THCLAWS_API_TOKEN` ไม่ตั้ง → 404),
`cloud/agent_cli.rs` (validate, pack fuse), `cloud/manifest.rs`, `cloud/agent_scaffold.rs`, `agent_defs.rs`, `net_guard.rs`,
`tools/workflow_run.rs` (summary `\n\n[workflow: N subagent turn(s), X in / Y out tokens]`), `config.rs::load_mcp_servers`
(user → project `.mcp.json`/`.thclaws/mcp.json`/`.claude/mcp.json` merge ตามชื่อ), `mcp.rs` (`MCP_NAME_SEPARATOR = "__"`, sanitize,
env literal สำหรับ stdio, `${VAR}` เฉพาะ headers, allowlist + `THCLAWS_MCP_ALLOW_ALL`, `InitializeResult.instructions` →
`# MCP server instructions`), `agent_runtime.rs::load_mcp_servers_silent` (eprintln แล้วรันต่อ), `prompts.rs::load`
(project override `.thclaws/prompt/<name>.md` ที่ CWD), `agent.rs::build_todos_reminder` (cap 80 บรรทัด/6 KB),
`tools/todo.rs` (`.thclaws/state/todos.md`), `repl.rs` (`oai/` + `OPENAI_COMPAT_*`, `run_agent_workflow` ใช้ AutoApprover +
`PermissionMode::Auto`), `/agent/run` ยังลงทะเบียน `WorkflowRun` โดยไม่มี subagent factory. รูปแบบ `--version` คือ
`thclaws 0.138.0` / `revision: d452ece (<branch>)` / `built: ...`; parser ใช้ token แรกหลัง `revision:`.

**เปลี่ยน** (ต้องรู้):
1. `agent_runtime.rs::build_runtime_with_provider` เรียก `ToolRegistry::apply_filter(allowed_tools, disallowed_tools)` หลังลงทะเบียน
   tool ทั้งหมด (upstream issue #221): `allowed` ไม่ว่าง → เหลือเฉพาะชื่อในรายการ, `disallowed` → ถอดชื่อนั้น. ค่ามาจาก `AppConfig`
   ซึ่ง `ProjectConfig` (`<cwd>/.thclaws/settings.json`) ตั้งได้ด้วย `allowedTools` / `disallowedTools` (รวม `permissions.allow/deny`
   แบบ Claude Code). ผลคือ `/agent/run` ของ daemon ที่ start จาก CWD=package **บังคับ tool allowlist ได้** เป็นครั้งแรก. M12 ยังไม่ใช้
   ช่องทางนี้: generator ไม่เขียน `allowedTools` และ guarantee matrix คง `tool_allowlist: Declared` จนกว่าจะมี milestone ที่ generate +
   พิสูจน์ด้วย native gate (tool ที่ไม่อยู่ใน allowlist ต้องหายจาก registry/ถูกปฏิเสธจริง). `apply_filter` ไม่กรอง MCP tools ที่โหลดหลังจากนั้นหรือไม่ ต้องตรวจตอนทำ
   (ตรวจแล้วใน M13: **ไม่กรอง** ทั้ง MCP และ Skill tools เพราะถูกเรียกก่อนลงทะเบียนสองกลุ่มนั้น — ดู "Host + bots layout" ข้อ 10).
2. `KmsEditTool` ถูกลงทะเบียนเพิ่มใน `/agent/run` → catalog `patterns/tools-0.138.0.json` มี 69 tools (= catalog 0.116.0 + `KmsEdit`;
   ตรวจแล้วว่าทุกชื่อใน catalog เดิมยังเป็น tool name ใน source). `KmsEdit` เป็น writer ใต้ `.thclaws/` → `WORKSPACE_STATE_TOOLS`
   (`Kms*` prefix ใน `forge/spec.py`) ครอบคลุมอยู่แล้ว.
3. `subagent.rs::PathScopedWriteTool::check` ใช้ `resolve_destination` → `sandbox::resolve_landing` (lexical normalize `.`/`..`,
   canonicalize, ตาม symlink รวม dangling symlink ทีละ component จน `MAX_SYMLINK_HOPS`) และ canonicalize cwd ก่อน match glob
   (upstream issues #204/#219). Native diagnostic `check_m9_standalone.py --write-boundary` บน `d452ece` ปฏิเสธครบ:
   `outside.txt`, `output/../traversal.txt`, `output/link/escaped.txt`, `output/dangling` และยอมรับ positive control. blocker ของ T1
   ที่บันทึกไว้สำหรับ 0.116.0–0.129.0 จึงไม่พบบน baseline นี้; **T1 ยังปิด** ตามกติกา "diagnostic ผ่านไม่ปลดอัตโนมัติ" ต้อง review
   ขอบเขต (Bash/MCP นอก wrapper, runtime state) และแก้ generator/matrix/audit ใน milestone แยก. `writePaths` ยังครอบเฉพาะ
   Write/Edit/Docx*/Pptx*/Xlsx*/PdfCreate/EpubCreate/NotebookEdit.
4. `cloud/pack.rs` strip เพิ่ม prefix `.thclaws/bots/`, `.home/`, `.playwright-mcp/` และ exact `.thclaws/media-jobs.jsonl`;
   `unpack` รองรับ directory entry และปฏิเสธ symlink/hardlink entry. ไม่กระทบ `forge export` เพราะ generated file ไม่เคยใช้ path เหล่านี้
   และ `pack_archive` ยังเทียบสมาชิกกับ `generated_files` เหมือนเดิม; `ARCHIVE_REWRITTEN` ยังเป็น `manifest.json` + `.thclaws/settings.json`.
   สิ่งที่ M12 ไม่ได้บันทึก: โฟลเดอร์ `.thclaws/bots/` มาจาก **layout migration** ที่ `--serve`/desktop ทำกับ package ในที่เดิม → ดู "Host + bots layout (M13)" ด้านล่าง.
5. `config.rs`: template settings ของ workspace ใหม่เขียน `"browserEnabled": false` แต่ `AppConfig` default ยังเป็น on เมื่อไม่มี key
   (และฉีด `browser` MCP เมื่อพบ `npx`); live harness จึงยังตั้ง `{"browserEnabled": false}` ใน `THCLAWS_CONFIG` และตรวจ inventory
   เท่าเดิม. shared workspace ปฏิเสธ browser. `apply_runtime_policy` เติม `disallowed_tools` จาก org policy (`policy/mod.rs`,
   signed policy; ไม่มี policy = ไม่มีผล; build flag `THCLAWS_POLICY_REQUIRED`). `retired_model_successor` เปลี่ยน `model` ที่ pin
   ตอน load ถ้าอยู่ใน `RETIRED_MODELS` (ตอนนี้มีเฉพาะ deepseek) → model ที่ spec pin อาจถูกแทนเงียบ ๆ ในอนาคต; live audit ควร
   บันทึก model ที่ใช้จริง (follow-up).
6. ผล MCP tool ถูก cap 256 KiB (`THCLAWS_MCP_MAX_TEXT_BYTES`, `browser_snapshot` 48 KiB) และ tool output ที่เกินถูก spill ไป
   `<sandbox root>/.thclaws/state/tool-output/` → เป็น state ที่ static audit ignore อยู่แล้ว (`.thclaws/state/` ยกเว้น `todos.md`)
   และ live copy ไม่คัดลอก; ไฟล์ spill ไม่ใช่ generated file.
7. `api_v1/messages.rs` endpoint ใหม่ และ `server.rs` เปลี่ยนมาก แต่ builder ไม่ใช้.

**การตัดสินใจ**: baseline ของ Atlas/export/catalog = `0.138.0` (binary ต้องรายงาน `thclaws 0.138.0`); standalone T0 เพิ่มคู่
`0.138.0/d452ece` และคงคู่เดิมสามคู่เป็นหลักฐานประวัติ (ไม่ได้รันซ้ำ). `patterns/tools-0.116.0.json` คงไว้เป็นหลักฐาน; static audit
ใช้ `tools-<baseline>.json`. Build แบบ CLI-only ใน container ใช้ placeholder `frontend/dist/index.html`.

**Native evidence 2026-10-27 บน build จาก `d452ece`**: M4 (scaffold compat ทั้งสาม pattern + validate 4 packages), M5 (`draft`),
M7b (`draft`), M8 (archive + sha, inventory ตรง), M9 T0 (pipeline สาม role, denial 9/9, `--write-boundary` 5/5), M10 (`draft`),
M11 (`thclaws agent validate` ของ `builder/`) ผ่าน; M6/M7a skip (ไม่มี `../AI`), M8 Atlas skip (ไม่มี `ATLAS_ROOT`), ส่วน provider
ของ M9/M11 skip (ไม่มี key). ยังไม่ได้รัน live audit ใด ๆ บน baseline นี้; evidence `candidate` เดิมทั้งหมดเป็นของ 0.116.0.

### Host + bots layout (M13, 2026-10-27, thClaws 0.138.0 `d452ece`)

พบจาก M11 live บน Mac (exit 1): binary 0.138.0 แบบ desktop build ที่เปิดด้วย `--serve` แปลงโฟลเดอร์ agent (layout เดิม = "v2", agent อยู่ที่ root)
เป็น **host workspace** (`workspaceVersion` 4) ในที่เดิม แล้ว `/agent/run` ที่ชี้ `workspace_dir` = root ได้ agent ที่ไม่มี AGENTS.md/skills.
ตรวจ source `d452ece` (path ใต้ `crates/core/src/`) และบันทึกเป็น claim ของ §4 ดังนี้ (แผนงานอยู่ที่ `docs/host-layout-plan.md`):

1. **เงื่อนไข auto-migrate** (`bin/app.rs:1017-1060`): ทำงานเมื่อ binary build ด้วย feature `gui` เท่านั้น (`#[cfg(feature = "gui")]`; `Cargo.toml` `default = []`
   → build CLI-only ไม่มีโค้ดนี้, `workspace_is_v3()` คืน false ตลอด `bin/app.rs:870-872`, และไม่มี subcommand `thclaws bots` `:354-358`), `auto_migrate_allowed()` เป็นจริง
   (`bots/migrate.rs:256-265`: `THCLAWS_AUTO_MIGRATE=0` หรือ `THCLAWS_SUPERVISED=1` → false; มิฉะนั้น true ยกเว้น `THCLAWS_INSIDE_DOCKER=1` ที่ไม่มี
   `THCLAWS_AUTO_MIGRATE=1`), ไม่ใช่ `-p`/`--cli`/`agent run`, ไม่ใช่ `--multi-tenant`, และเป็น `--serve` หรือ desktop. โฟลเดอร์เป้าหมาย = ไม่มี `.thclaws/bots.json`
   แต่มี `AGENTS.md` หรือ `manifest.json` หรือ `.thclaws/settings.json` หรือ `.thclaws/state/` (`:1237-1245`) → ทุก package ที่ generator สร้างและ `builder/` เข้าเกณฑ์.
   Mac (desktop build) กับ cloud (CLI-only build) จึงต่างกันที่ build feature ก่อน env.
2. **สิ่งที่ migration ทำ** (`bots/migrate.rs:63,79,470-532,1024-1072`): ย้ายเฉพาะ `.thclaws/`, `AGENTS.md`, `CLAUDE.md`, `manifest.json` ไป `.thclaws/bots/main/`;
   ไฟล์อื่นอยู่ root และไม่มี tombstone `AGENTS.md` ที่ root; host เขียน `.thclaws/settings.json` (`workspaceVersion: 4`, `_doc`), `.thclaws/bots.json`,
   `.thclaws/state/.gitignore`. ใน agent: `mint_identity` เติม `agent.id/name/description` ถ้าขาด, `ProjectConfig::backfill_defaults_in` เติม key ใน settings,
   และ `agent_paths::fix` (`bots/agent_paths.rs:191-260`) **เขียนทับ** `AGENTS.md`, `CLAUDE.md` และ `.md/.js/.py` ใต้ `.thclaws/{scripts,skills,workflows,agent_workflow,
   gui-shell,agents,commands,templates}`: `python3 .thclaws/x.py` → `python3 "$THCLAWS_AGENT_DIR/.thclaws/x.py"`, `Path("./.thclaws/..")`/`open(...)` → `AGENT_DIR / "..."`
   พร้อมแทรก `AGENT_DIR = Path(os.environ.get("THCLAWS_AGENT_DIR") or ".")`; ต้นฉบับสำรองที่ `.thclaws/state/pre-agent-dir/`. ผล: tree ที่ถูก migrate ไม่ byte-identical
   กับ package ที่ audit (static audit รายงาน drift ถูกต้องแล้ว) และ `thclaws bots unmigrate` (`:773`) ย้อนได้เฉพาะเมื่อมี agent เดียวชื่อ `main`.
3. **host process** (`server.rs:1067-1210`, `bots/supervisor.rs:613-660`): host ฟัง port ที่สั่ง ไม่โหลด agent/model/MCP; spawn ต่อ agent
   `thclaws --serve --bind 127.0.0.1 --port 0` ด้วย cwd = `.thclaws/bots/<slug>` และ env `THCLAWS_SERVE_TOKEN`, `THCLAWS_SERVE_ADDR_FILE`, `THCLAWS_SUPERVISED=1`,
   `THCLAWS_WORKSPACE_ROOT=<root>`; env อื่นสืบทอด (`THCLAWS_API_TOKEN`, `THCLAWS_CONFIG`, `HOME`, provider key ไปถึง agent). `/v1/*` และ `/agent/run` ถูก forward
   ไป agent ที่เลือกด้วย query `?bot=<slug>` (ไม่ระบุ = ตัวแรกใน `bots.json`) และส่ง `Authorization` ของผู้เรียกผ่านตรง (`server.rs:1369-1390`);
   `/v1/agent/info.workspace_dir` = process cwd ของ agent = bot dir (`api_v1/info.rs:128`). host ที่พบ v2 โดยไม่มีสิทธิ์ migrate จะ error "`thclaws bots migrate` first" (`server.rs:1093-1101`).
4. **`/agent/run` ใต้ host ใช้ `workspace_dir` เดียวกับสองความหมาย**: skills จาก `<workspace_dir>/.thclaws/skills` (`agent_runtime.rs:171`), AGENTS.md walk ขึ้นจาก
   `workspace_dir` และหยุดที่ shelf ไม่โหลด AGENTS.md ของ host (`context.rs:238-262`), `collect_files` snapshot เดิน `workspace_dir` โดยข้าม `.thclaws`
   (`api_v1/artifacts.rs:110-150`, `api_v1/agent.rs:460`). แต่ file tools และ Bash ทำงานที่ `THCLAWS_WORKSPACE_ROOT` (`sandbox.rs:28-45`, `workdir.rs:55-110`):
   path relative ทั่วไป → root, `.thclaws/...` ของ agent → bot dir (ยกเว้น `state/kms`, `state/logs`), Bash cwd = root และได้ `THCLAWS_AGENT_DIR` **เสมอแม้ไม่มี host**
   (`tools/bash.rs:1422-1430`); system prompt อธิบายสองโฟลเดอร์นี้ (`context.rs:112-130`). MCP config, `allowedTools`, `.thclaws/prompt/system.md`, `todos.md`
   ยังอ่านจาก cwd ของ process = bot dir. ผลบน host: `workspace_dir` = root → ไม่มี identity (อาการ M11) แต่ `collect_files` เห็นไฟล์;
   `workspace_dir` = bot dir → identity ครบ แต่ agent เขียนไฟล์ที่ root และ `collect_files` เดิน bot dir จึง**ไม่เห็น** → ไม่มีค่าใดที่ทำให้ contract `atlas-worker` สมบูรณ์.
5. **`thclaws bots add`** (`bots/install.rs:30-80` → `cloud/cmd.rs:708-723`): รับเฉพาะ slug ของ Agent Template ใน catalogue thClaws.cloud และต้อง login token;
   ไม่รับ archive หรือ path local. `bots.json` ออกแบบให้แก้มือได้ (`bots/mod.rs:34`) จึงมีทางติดตั้งมือ (แตก archive ของ `agent pack` ลง `.thclaws/bots/<slug>/` แล้วเติม `bots.json`) ที่ยังไม่ทดสอบ.
6. `thclaws agent validate`/`pack` ต้องการ `manifest.json` ที่โฟลเดอร์ที่ชี้ (`cloud/agent_cli.rs:32-36,58-63,96-104`) และ pack strip `.thclaws/bots/` → ใน host workspace ต้องชี้ bot dir;
   archive เป็น agent เดี่ยว (v2 shape) เสมอ → output ของ generator และ `forge export` ไม่เปลี่ยนรูป.
7. `thclaws -p`/`--cli` ที่ root ของ host workspace ก้าวเข้า `bots[0]` และตั้ง `THCLAWS_WORKSPACE_ROOT` (gui build, `bin/app.rs:1067-1095`); `thclaws agent run <dir>` (M9) ไม่อยู่ในเงื่อนไข migrate
   (M9 live ผ่านบนเครื่องเดียวกับที่ M11 fail).
8. host ซ่อม agent "stale" ตอนเปิด (`server.rs:1150`, `agent_paths::repair_stale`) เฉพาะ agent ที่ข้อความของตนไม่กล่าวถึง `$THCLAWS_AGENT_DIR` (`migrate.rs:565`).
9. `THCLAWS_AGENT_WORKSPACE_ROOT` จำกัดให้ `workspace_dir` ของ request อยู่ใต้ root ที่กำหนด (`agent_runtime.rs:284-325`) — ตัวเลือก hardening ของ deployment ยังไม่ใช้.
10. (ตอบคำถามที่ข้อ 1 ของรายการ M12 ทิ้งไว้) `apply_filter(allowed_tools, disallowed_tools)` ถูกเรียก**ก่อน**ลงทะเบียน Skill tools และ MCP tools
    (`agent_runtime.rs:135` เทียบ `:171-185`, `:197`) → `allowedTools` กรองเฉพาะ built-in ไม่กรอง MCP/Skill; milestone `allowedTools` ต้องรับข้อนี้.

11. Atlas (`daa0f49`) ส่งไฟล์ handoff ด้วย `POST /v1/inputs` (`atlas/thclaws_client.py:170-192`) ซึ่ง thClaws เขียนที่ `<workspace_dir>/<path>` ใต้ prefix `inputs/`
    (`api_v1/artifacts.rs:316-345,354-416`) และ Atlas render `{files_dir}` เป็น path **relative** `inputs/incoming/<run_id>/<node_key>` (`atlas/jobs.py:1247`);
    `collect_files` อ่านผ่าน `/v1/artifacts` จาก `workspace_dir` เดียวกัน (`atlas/thclaws_client.py:358`). บน host (ข้อ 4) agent อ่าน path relative ที่ workspace root
    จึงไม่พบไฟล์ที่ลงใน bot dir และไฟล์ที่ agent เขียนที่ root ไม่ถูก snapshot → package ที่มี `atlas_file_handoff` หรือ `files.globs` ใช้ host layout ไม่ได้ใน d452ece+daa0f49.
12. ติดตั้ง agent มือใน host workspace (แตก archive ของ `agent pack` ลง `.thclaws/bots/<slug>/` + เติม `bots.json`) เป็นไปได้ตาม design ของ `bots.json` แต่**ยังไม่ยืนยัน**ด้วย binary.
13. build ที่ไม่มี feature `gui` **ไม่มี `--serve` เลย**: `thclaws --serve` พิมพ์ "--serve not available — rebuild with: cargo build --features gui" แล้ว exit 1
    (`bin/app.rs:1356-1362`, ยืนยันด้วย binary ที่ build ใน cloud 2026-10-27). ดังนั้น native evidence บน cloud มีได้เฉพาะ `agent validate`, `agent pack`, `agent run` (M9)
    และ `--write-boundary`; ทุก check ที่ต้องมี daemon (`/agent/run`, `/v1/agent/info`, M6/M7b/M11 live, M8 daemon probe, M13 ส่วน layout จริง) ต้องประกาศ skip บน build นี้
    และรันบน desktop build ของ Mac. ข้อสันนิษฐานเดิมว่า "container ไม่ migrate" จึงไม่ใช่สาเหตุที่ cloud ไม่เจอปัญหา — cloud ไม่เคยสตาร์ท daemon จริงเลย.

**การตัดสินใจ (M13, scoped adoption — ผู้ใช้เลือก 2026-10-27)**: (a) deployment contract และ package ที่ generate คง layout agent เดี่ยว (v2) เท่านั้น; host layout
ไม่รองรับสำหรับ `/agent/run` ของ Atlas ตามข้อ 2, 4 และ 11 (กฎอยู่ที่ §8). (b) harness ทุกจุดที่สตาร์ท `--serve` ต้องตั้ง `THCLAWS_AUTO_MIGRATE=0` ผ่าน helper เดียว
(`forge/live_test.py::isolated_env`) และต้องตรวจผลจริงหลัง readiness (`forge/live_test.py::check_layout`: `/v1/agent/info.workspace_dir` ที่ resolve แล้วต้องเท่ากับ
directory ที่ตั้งใจ serve และต้องไม่มี `.thclaws/bots.json` โผล่ที่นั่น) ไม่เชื่อ env อย่างเดียว เพราะ default ขึ้นกับ build feature และ container. (c) `builder/` **ใช้ host layout
จริง**: เป็น host workspace v4 ใน git (`.thclaws/bots.json`, host `.thclaws/settings.json`, agent ที่ `.thclaws/bots/main/`) เพราะเป็นที่เดียวที่ผู้ใช้เปิดด้วย desktop/`--serve`
และ layout v2 ที่ git track จะถูก desktop เขียนทับ (ข้อ 2); harness serve agent ของ builder จาก bot dir ด้วย `THCLAWS_WORKSPACE_ROOT=<root>` (เท่ากับที่ host ทำกับ child, ข้อ 3)
จึงไม่ต้องพึ่ง host process และตรวจผลด้วย `check_layout` ได้ตรง ๆ; รายละเอียดอยู่ที่ §9. (d) หลาย agent ต่อ host ไม่ขัด §8 เพราะ Atlas เห็น host เป็น daemon URL เดียว (`base_url` ห้าม `?`)
จึงถึง agent default ตัวเดียว และ Atlas ไม่ต้องแก้โค้ด. (e) host deployment สำหรับ package ที่มีเฉพาะ `prompt_json`/`assistant_json` เป็นข้อเสนอ (PLAN M16) ที่ต้องพิสูจน์บน Mac
ก่อน: `/agent/run` ผ่าน host ด้วย `workspace_dir` = bot dir ครบ golden cases, ติดตั้งมือ (ข้อ 12), Atlas poll เห็น host online; export ต้องปฏิเสธ package ที่มี file handoff/`files.globs`
สำหรับ layout นั้น. (f) cloud build ของ thClaws เป็น CLI-only (ไม่มี GTK/WebKit) จึงไม่มี `--serve` (ข้อ 13): ทดสอบ daemon, migration จริง, host process และ `thclaws bots` ไม่ได้;
gate M13 จำลองโครง v4 เอง (ย้าย `AGENT_ENTRIES` + เขียน host files ตาม `install_host_files`, ไม่จำลอง `agent_paths::fix`) และประกาศชัดว่าเป็น simulated layout;
ส่วน daemon ทั้งหมดของ M13 และ M11 live เป็นงานบน desktop build ของ Mac และ gate ต้องตรวจว่า `--serve` ใช้ได้ก่อน มิฉะนั้น skip (exit 2) ไม่ใช่รายงานว่า daemon ตาย.

### M9 compatibility follow-up (2026-09-16, thClaws 0.129.0 `7a27129`)

ตรวจ source และ binary `thclaws 0.129.0` revision `7a27129` แล้ว: `subagent.rs::resolve_destination`
ยุบ `.`/`..` และตาม symlink ของ path ที่มีอยู่ รวมถึง canonicalize longest existing ancestor สำหรับ tail ใหม่
ก่อนให้ `writePaths` match. Native diagnostic ผ่าน direct out-of-scope, `output/../traversal.txt` และ symlink
ที่ชี้ไป directory นอก `output/` แต่ยังพบ bypass เมื่อ final path เป็น dangling symlink ใน `output/` ที่ชี้ไป
ไฟล์ปลายทางนอก scope ซึ่งยังไม่มีอยู่. จึงเพิ่มกรณีนี้เป็น regression และคง T1 disabled; `output_write_scope`
ยังเป็น `Declared`. Compatibility ของ standalone T0 ตรวจเป็นคู่ version+revision และไม่เปลี่ยน baseline ของ
Atlas/export.
