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
| identity | `name`, `version`, `owner`, `thclaws_min_version` |
| mission | วัตถุประสงค์หนึ่งย่อหน้า + domain |
| target | `thclaws-standalone` / `atlas-worker` / `atlas-workflow` (ดู §3) |
| inputs | array ของ `{name, transport, path?, mime_types?, schema?}` โดย `transport` ∈ `prompt_json` / `atlas_file_handoff` / `local_workspace` / `mixed` |
| outputs | array ของ `{name, transport, schema}` โดย `transport` ∈ `assistant_json` / `collect_files` / `mixed` |
| capabilities | รายชื่อ **pack** + พารามิเตอร์ (ดู §5) |
| permissions | `tier` ∈ `T0` (read-only, JSON) / `T1` (เขียนเฉพาะ `output/**`) / `T2` (side effect ภายนอก) + declared `tools`, `shell`, `network` |
| refusal | เงื่อนไขที่ต้องปฏิเสธหรือ hand off พร้อมรูปแบบคำตอบเมื่อปฏิเสธ |
| model | model id ที่ pin หรือ model policy |
| env | **ชื่อ** env var ที่ต้องมี (ไม่มีค่า) |
| evaluation | golden cases: normal / missing input / refusal / malformed |
| state (อนาคต) | `{mode: session, scope: workspace}` ใช้ได้เมื่อ target รองรับ (ดู §3) |

หมายเหตุ: `execution_surface` และ pattern ที่อนุญาต **คำนวณจาก `target`** ผ่าน compatibility matrix ผู้ใช้ไม่กรอกเอง เพื่อไม่ให้เกิดคู่ที่ขัดกัน

## 3. Target / pattern compatibility matrix

| target | execution surface | package pattern ที่อนุญาต | flow อยู่ที่ไหน |
|---|---|---|---|
| `thclaws-standalone` | thClaws GUI / CLI / catalog | `static-pipeline`, `batch-fanout`, `dynamic` (ตาม `thclaws agent new`) | `.thclaws/agent_workflow/run.js` ใน package |
| `atlas-worker` | `POST /agent/run` | `single-worker` เท่านั้น | ไม่มี flow ใน package |
| `atlas-workflow` | Atlas workflow engine | `single-worker` หนึ่ง package ต่อ node | Atlas workflow JSON ที่ builder export |

เหตุผล: บน `/agent/run` (`agent_runtime.rs::build_runtime_for_workspace`) ไม่มี subagent factory และไม่ register `Task` tool
ดังนั้น `.thclaws/agents/*.md` (tools / writePaths / output_schema) และ `thclaws.subagent()` ใน run.js **ไม่ทำงาน** บน path นี้
planner → worker → verifier สำหรับ Atlas จึงต้องเป็น node หลายตัวใน Atlas ไม่ใช่ subagent ใน package

`human-approval` และ `manager-loop` เป็น **flow pattern ของ Atlas** (node `human_gate`, node `manager`) ไม่ใช่ package pattern

`state.mode: session` ใช้ `session_id` ของ `/agent/run` ได้ (`api_v1/agent.rs`) แต่ Atlas ปัจจุบันไม่ส่ง `session_id` ต่อเนื่องระหว่าง node → spec ต้อง reject ถ้าเลือก session state กับ target ที่เป็น Atlas จนกว่า Atlas จะรองรับ

## 4. สิ่งที่ `/agent/run` ใช้จริง vs ไม่ใช้ (thClaws v0.116.0)

ใช้จริง: `AGENTS.md` (walk ขึ้นจาก `workspace_dir`), skills ใน `<workspace_dir>/.thclaws/skills/`, MCP servers จาก settings ของ **daemon** (ไม่ใช่ต่อ workspace), `model` / `max_tokens` / `system` (ต่อท้าย) / `session_id` / `collect_files`, Bash sandbox ระดับ OS

ไม่ใช้: agent defs, hooks (`pre_tool_use`), `allowed_tools` / `disallowed_tools`, permission mode (ใช้ `AutoApprover`), workflow subagent

ผลที่ตามมา: การจำกัด tool/shell ต่อ role บน Atlas worker **ยังบังคับไม่ได้** สิ่งที่บังคับได้คือ MCP ที่ daemon มี/ไม่มี, daemon แยกต่อ tier, sandbox, Atlas `human_gate`, และ schema check ตอน audit

## 5. Capability pack

pack คือความสามารถสำเร็จรูปที่ประกอบเข้า package ได้ อยู่ใน `packs/<name>/pack.yaml` ประกาศ: MCP servers / skills / scripts ที่เพิ่ม, env ที่ต้องการ (ชื่อ), tier ต่ำสุดที่ต้องใช้, fixture สำหรับ live audit ที่รันได้โดยไม่ต้องมีข้อมูลจริง
pack แรก ๆ: `sql-readonly` (ห่อ MCP ของ project AI: metadata / query / validation ไม่รวม admin), `report-pipeline` (ห่อ build + verify ของ NT-Report), `publisher-email-sftp` (MCP ใหม่ มี `dry_run` + `idempotency_key`)
กฎ: pack ที่มี tool แบบ mutating ห้ามถูกเลือกใน spec ที่ประกาศ T0; T2 บังคับ daemon แยกและ node template ต้องมี `human_gate` นำหน้า

## 6. Guarantee matrix (บังคับต้องมีในทุก build report)

| Claim | สถานะบน `/agent/run` วันนี้ | บังคับด้วย |
|---|---|---|
| ใช้ tool ตาม allowlist | Declared | prompt / package เท่านั้น |
| ห้ามเขียนไฟล์ | Enforced เฉพาะกรณี | daemon แยก + ไม่มี write MCP + sandbox (Write/Edit ยังมี) |
| ห้าม shell | Not guaranteed | runtime register Bash เสมอ |
| network เฉพาะ host | Not guaranteed | policy allowlist ครอบเฉพาะ URL ตอนติดตั้ง plugin/skill/MCP config; `net_guard` กันแค่ private IP ของ WebFetch/WebScrape/FetchImages → ต้องใช้ egress control ของเครื่อง |
| ต้องมีคนอนุมัติก่อน side effect | Enforced | Atlas `human_gate` + T2 daemon แยก |
| output เป็น JSON ตาม schema | Enforced ตอน audit | schema validation ใน live audit (Atlas เองทำแค่ `json.loads`) |
| ต้องเรียก skill/MCP X | Evidence | SSE tool events ใน live audit (บังคับเฉพาะเมื่อ spec ระบุ must-call) |

`audit` ห้ามพิมพ์คำว่า read-only ถ้า package มี mutating MCP หรือ runtime ยัง expose Bash

## 7. Audit สองชั้น + สถานะ package

static audit: schema ของ spec, manifest ที่ generate, permission declaration vs pack, env names, MCP/skill มีอยู่จริง, refusal surface ครบ, AGENTS.md มี section บังคับ (Mission / Must / Refuse / Output) และประโยค "ตอบ JSON ล้วน" เมื่อ output เป็น `assistant_json`, `thclaws agent validate` ผ่าน
live audit: start daemon ชั่วคราวแบบ isolated, ยิง `/agent/run` จริงด้วย golden cases (normal / missing input / refusal / malformed), validate output กับ schema, เก็บ SSE tool/skill events เป็น evidence
สถานะ: `draft` (static ผ่าน) → `candidate` (live ผ่าน) → `shippable` (compatibility + security ผ่าน) ไม่มี API key = อยู่ที่ draft ห้ามบอกว่า deploy-ready
`audit.py` / `studio.py` ใน package เป็น runner บาง ๆ ที่เรียก builder logic ตัวเดียว

## 8. ผลลัพธ์ของ builder

- package โฟลเดอร์ตาม format `thclaws agent new` (manifest.json, AGENTS.md, `.thclaws/{settings.json,skills/,schemas/,scripts/,agents/}`) — script ของ pack วางใน `.thclaws/scripts/` เพราะเป็นที่เดียวที่ `thclaws agent validate` syntax-check python
- `builder-build-report.json` (schema: `builder-build-report.schema.json`): target, generated files, guarantee matrix, audit result, compatibility result, deployment hints
- `atlas-register.json`: worker `role` / `tags` / `workspace_dir`, node template (`output_format`, `model`, `collect_files`, `push_files`), flow template เมื่อ target = `atlas-workflow`
- archive จาก `thclaws agent pack`

## 9. บทบาทของ AI

ทำได้: interview 7 ข้อ, แปลงคำอธิบายเป็น mission, ร่าง AGENTS.md / SKILL.md / refusal / golden cases, แนะนำ pack, meta-audit หลัง generate (ผลเป็นข้อเสนอที่คนต้อง accept)
ห้ามตัดสินใจ: เปิด network / shell, เขียนไฟล์ที่ไหน, ส่งอีเมล / เผยแพร่, ใช้ secret / MCP ใด, ผ่าน audit หรือไม่ — ส่วนนี้มาจาก spec + audit แบบ deterministic เท่านั้น

## 10. นอกขอบเขต MVP (future)

- patch `agent_runtime.rs` ให้ `/agent/run` เคารพ `allowed_tools` / `disallowed_tools` / MCP allowlist / write-path (ต้อง filter ทั้ง tool definitions และ invocation จริง) — ควร upstream
- `agent_profile` บน `/agent/run` + `profiles[]` ใน `/v1/agent/info`
- auto-deploy ผ่าน `/v1/deploy` และ bundle version tracking (รวมกับ Atlas M4 Fleet)
- semantic router, marketplace, runtime framework ใหม่ — ไม่ทำ
