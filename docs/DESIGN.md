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
| permissions | `tier` ∈ `T0` (read-only agent behavior) / `T1` (เขียนเฉพาะ `output/**`) / `T2` (side effect ภายนอก) + declared `tools`, `shell`, `network`, `write_scope` |
| refusal | เงื่อนไขที่ต้องปฏิเสธหรือ hand off พร้อมรูปแบบคำตอบเมื่อปฏิเสธ |
| model | model id ที่ pin หรือ model policy |
| env | **ชื่อ** env var ที่ต้องมี (ไม่มีค่า) |
| evaluation | golden cases: normal / missing input / refusal / malformed พร้อม expected output branch (`result` / `refusal`) |
| state | ต้องประกาศ `{mode, scope}`; M1 ให้ standalone ใช้ `none/turn` หรือ `session/session`, Atlas ใช้ `none/turn`; `durable_memory` เลื่อนไปหลัง M1 |

หมายเหตุ: `execution_surface` คำนวณจาก `target` ผ่าน compatibility matrix ผู้ใช้ไม่กรอกเอง ส่วน `package_pattern` เป็น input เฉพาะ standalone เพราะ target เดียวมีหลาย pattern ที่ถูกต้อง

## 3. Target / pattern compatibility matrix

| target | execution surface | package pattern ที่อนุญาต | flow อยู่ที่ไหน |
|---|---|---|---|
| `thclaws-standalone` | thClaws GUI / CLI / catalog | `static-pipeline`, `batch-fanout`, `dynamic` (ผู้ใช้ต้องเลือก) | `.thclaws/agent_workflow/run.js` ใน package |
| `atlas-worker` | `POST /agent/run` | `single-worker` เท่านั้น | ไม่มี flow ใน package |
| `atlas-workflow` | Atlas workflow engine | `single-worker` หนึ่ง package ต่อ node | Atlas workflow JSON ที่ builder export |

เหตุผล: บน `/agent/run` (`agent_runtime.rs::build_runtime_for_workspace`) ไม่มี subagent factory และไม่ register `Task` tool
ดังนั้น `.thclaws/agents/*.md` (tools / writePaths / output_schema) และ `thclaws.subagent()` ใน run.js **ไม่ทำงาน** บน path นี้
planner → worker → verifier สำหรับ Atlas จึงต้องเป็น node หลายตัวใน Atlas ไม่ใช่ subagent ใน package

`human-approval` และ `manager-loop` เป็น **flow pattern ของ Atlas** (node `human_gate`, node `manager`) ไม่ใช่ package pattern

`routing.role` และ `routing.tags` เป็น package intent สำหรับ Atlas routing ส่วน `worker_id`, `workspace_id`, `workspace_dir` และ `base_url` เป็น deployment-time values ที่ exporter รับจาก operator

`state.mode: session` ใช้ `session_id` ของ `/agent/run` ได้ (`api_v1/agent.rs`) ดังนั้น standalone ใช้คู่ `session/session` ได้ แต่ Atlas ปัจจุบันไม่ส่ง `session_id` ต่อเนื่องระหว่าง node → spec ต้อง reject ถ้าเลือก state ที่ไม่ใช่ `none` กับ target ที่เป็น Atlas จนกว่า Atlas จะรองรับ `durable_memory` ยังไม่อยู่ใน M1 ทุก target

## 4. สิ่งที่ `/agent/run` ใช้จริง vs ไม่ใช้ (thClaws v0.116.0)

ใช้จริง: `AGENTS.md` (walk ขึ้นจาก `workspace_dir`), skills ใน `<workspace_dir>/.thclaws/skills/`, MCP servers จาก settings ของ **daemon** (ไม่ใช่ต่อ workspace), `model` / `max_tokens` / `system` (ต่อท้าย) / `session_id` / `collect_files`, Bash sandbox ระดับ OS

ไม่ใช้: agent defs, hooks (`pre_tool_use`), `allowed_tools` / `disallowed_tools`, permission mode (ใช้ `AutoApprover`), workflow subagent

ผลที่ตามมา: การจำกัด tool/shell ต่อ role บน Atlas worker **ยังบังคับไม่ได้** สิ่งที่บังคับได้คือ MCP ที่ daemon มี/ไม่มี, daemon แยกต่อ tier, sandbox, Atlas `human_gate`, และ schema check ตอน audit

`thclaws_min_version` คือ minimum version ที่อาจมี upper compatibility bound: clause เป็น `<`, `<=`, `==`, `>`, `>=` + numeric `x.y.z` และต่อด้วย comma เช่น `>=0.116.0,<0.120.0` หรือ `0.116.0`; ต้องมี minimum/exact clause, bare version ห้ามปนกับ clause อื่น และ semantic gate reject range ที่ไม่มีทาง satisfy ได้ ไม่รองรับ prerelease, `!=`, `~`, `^`

การตรวจชื่อ tool ใช้สามระดับ: built-in catalog ที่ pin ตาม thClaws version, MCP tools จาก pack/daemon จริง และ unknown tool เป็น warning ใน `draft` แต่ fail เมื่อจะเป็น `shippable` ห้ามสร้าง enum ปิดตายสำหรับ MCP tools

## 5. Capability pack

pack คือความสามารถสำเร็จรูปที่ประกอบเข้า package ได้ อยู่ใน `packs/<name>/pack.yaml` ประกาศ: MCP servers / skills / scripts ที่เพิ่ม, env ที่ต้องการ (ชื่อ), tier ต่ำสุดที่ต้องใช้, fixture สำหรับ live audit ที่รันได้โดยไม่ต้องมีข้อมูลจริง
pack แรก ๆ: `sql-readonly` (ห่อ MCP ของ project AI: metadata / query / validation ไม่รวม admin), `report-pipeline` (ห่อ build + verify ของ NT-Report), `publisher-email-sftp` (MCP ใหม่ มี `dry_run` + `idempotency_key`)
กฎ: pack ที่มี tool แบบ mutating ต้องใช้ T2; T2 ใน M1 ใช้ได้เฉพาะ `atlas-workflow` เพราะมี owner สำหรับ `human_gate`; `T2 + atlas-worker` และ `T2 + thclaws-standalone` ถูก reject จนกว่าจะมี approval story ของ target นั้น

T0 ต้องประกาศ `shell: none`, `network: none`, `write_scope: none`; ทุก tier ที่ประกาศ `shell: none` ห้ามประกาศ `Bash`, `write_scope: none` ห้ามประกาศ `Write`, `Edit`, `FetchImages` หรือ document writers, และ `network: none` ห้ามประกาศ `WebFetch`, `WebSearch`, `WebScrape`, `FetchImages` หรือ `YouTubeTranscript`
T1 ต้องประกาศ `shell: none`, `network: none`, `write_scope: output`; นี่เป็น declared boundary ที่ builder ใช้ตรวจและแสดงใน guarantee matrix ยังไม่ใช่ runtime write-path enforcement
T2 ต้องประกาศ `write_scope: workspace`; การเลือก tier อย่างเดียวไม่สร้าง approval ให้ target ที่ไม่มี `human_gate`

## 6. Guarantee matrix (บังคับต้องมีในทุก build report)

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
`audit.py` / `studio.py` ใน package เป็น runner บาง ๆ ที่เรียก builder logic ตัวเดียว

M1 ตรวจว่า embedded schema เป็น JSON Schema ที่ถูกต้อง แต่ตามมาตรฐาน JSON Schema unknown keyword เป็น annotation ที่ผ่านได้; M5 static audit จะ warn ตอน `draft` และ fail ก่อน `shippable` เมื่อ keyword ไม่อยู่ใน vocabulary ที่ builder รองรับ

ผลลัพธ์ของ check ใช้ exit code เดียวกันทุก milestone: `0` = pass, `1` = fail, `2` = explicit skip เพราะ dependency ภายนอกยังไม่มี; CI ต้องรายงาน skip แยกและ fail เมื่อมี `1`

## 8. ผลลัพธ์ของ builder

- package โฟลเดอร์ตาม format `thclaws agent new` (manifest.json, AGENTS.md, `.thclaws/{settings.json,skills/,schemas/,scripts/,agents/}`) — script ของ pack วางใน `.thclaws/scripts/` เพราะเป็นที่เดียวที่ `thclaws agent validate` syntax-check python
- `builder-build-report.json` (schema: `builder-build-report.schema.json`, สร้างและตรวจใน M4): target, generated files, guarantee matrix, audit result, compatibility result, deployment hints
- `atlas-register.json`: worker `role` / `tags` จาก `routing`, deployment-time `workspace_dir`, node template (`model`, `collect_files`, และ `output_format: json` เฉพาะ `assistant_json`; omit เมื่อเป็น `collect_files` ล้วน) และ edge template (`push_files` + `policy.file_handoff`), flow template เมื่อ target = `atlas-workflow`
- archive จาก `thclaws agent pack`

สำหรับ `collect_files`, `files.globs` คือ output contract หลัก; thClaws snapshot ไฟล์ที่ match หลัง run จึงใช้ส่งต่อไฟล์ที่ agent สร้าง **หรือ** ไฟล์ที่มีอยู่ก่อน run ได้ และไม่เปลี่ยน `write_scope` ของ agent; `schema` ถ้ามีหมายถึง schema ของ artifact manifest ไม่ใช่ JSON body ของ assistant ส่วน `refusal.schema` เป็น contract แยกของ refusal branch และ `evaluation.golden_cases[].expect` เป็นตัวเลือก schema ที่ live audit ใช้ validate

## 9. บทบาทของ AI

ทำได้: interview 7 ข้อ, แปลงคำอธิบายเป็น mission, ร่าง AGENTS.md / SKILL.md / refusal / golden cases, แนะนำ pack, meta-audit หลัง generate (ผลเป็นข้อเสนอที่คนต้อง accept)
ห้ามตัดสินใจ: เปิด network / shell, เขียนไฟล์ที่ไหน, ส่งอีเมล / เผยแพร่, ใช้ secret / MCP ใด, ผ่าน audit หรือไม่ — ส่วนนี้มาจาก spec + audit แบบ deterministic เท่านั้น

## 10. นอกขอบเขต MVP (future)

- patch `agent_runtime.rs` ให้ `/agent/run` เคารพ `allowed_tools` / `disallowed_tools` / MCP allowlist / write-path (ต้อง filter ทั้ง tool definitions และ invocation จริง) — ควร upstream
- `agent_profile` บน `/agent/run` + `profiles[]` ใน `/v1/agent/info`
- auto-deploy ผ่าน `/v1/deploy` และ bundle version tracking (รวมกับ Atlas M4 Fleet)
- semantic router, marketplace, runtime framework ใหม่ — ไม่ทำ
