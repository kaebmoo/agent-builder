# Plan — agent-builder MVP

กติกาเดียวกับ Atlas: **หนึ่ง milestone = หนึ่ง check script** ใน `scripts/check_<milestone>.py` ที่ fail เมื่อพฤติกรรมพัง
ห้าม tick milestone ก่อน check ผ่าน commit ต่อ milestone เมื่อ gate เขียว

ลำดับตาม baseline ที่ตกลง: schema → fixtures → matrix → generator → thclaws validate → static audit → SQL pack ขั้นต่ำ (M7a) → live audit → publisher pack (M7b) → export → standalone generator (M9, เพิ่ม 2026-09-07 ต่อท้าย MVP เพื่อไม่เลื่อนสาย Atlas)

| M | ชื่อ | ผลลัพธ์ | Definition of done | check |
|---|---|---|---|---|
| M0 | Repo + design | README, DESIGN, PLAN, AGENTS.md, โครงโฟลเดอร์, pyproject | push ขึ้น GitHub ได้ | `check_m0_layout.py` (ไฟล์/โฟลเดอร์บังคับครบ) |
| M1 | AgentSpec schema | `agentspec.schema.json` + `docs/agentspec.md` อธิบายทุก field | target/pattern/routing ถูกบังคับตาม target, embedded JSON Schema ถูกตรวจ, refusal schema, golden kinds และ expected branch ครบ, Atlas model/output arity ถูกจำกัด, tier/write boundary และ `shell: none` / `network: none` tool consistency ถูกตรวจ, T2 standalone/atlas-worker ถูก reject, invalid transport/state/model/path/unsatisfiable version ถูก reject | `check_m1_spec.py` |
| M2 | Fixtures | `fixtures/invoice-reviewer/spec.yaml` (target atlas-worker, `atlas_file_handoff`, Atlas เป็นคนกำหนด landing path), `fixtures/sql-reader/spec.yaml` (target atlas-worker, `prompt_json`, pack `sql-readonly`) + golden cases 4 ประเภทต่อ fixture | ทุก fixture ผ่าน M1 และ golden case มีครบ normal / missing / refusal / malformed พร้อม branch ที่คาดหวัง | `check_m2_fixtures.py` |
| M3 | Compatibility matrix | `patterns/matrix.yaml` + `forge/compat.py` คำนวณ execution surface และ pattern ที่อนุญาตจาก target | ทุก combination ใน DESIGN §3 ให้ผลตรงตาราง, combination นอกตารางถูก reject | `check_m3_matrix.py` |
| M4 | Generator | `forge/generate.py` + `templates/` สร้าง package จาก spec แบบ deterministic (รันสองครั้งได้ไฟล์เหมือนกันทุก byte) | output ของทั้ง 2 fixtures ผ่าน `thclaws agent validate` โดยไม่มี error; script ของ pack อยู่ใน `.thclaws/scripts/`; `builder-build-report.schema.json` และ `builder-build-report.json` มี guarantee matrix | `check_m4_generate.py` (ต้องมี `thclaws` ใน PATH; ถ้าไม่มีให้ skip ส่วน validate พร้อมประกาศชัด) |
| M5 | Static audit | `forge/audit.py` กฎใน DESIGN §7 + wrapper `audit.py` / `studio.py` ในทุก package | package ที่ถูกต้องได้สถานะ `draft`; package ที่ถูกแก้ให้ผิด (ลบ section Refuse, ใส่ admin MCP ใน T0, env หาย) ถูก fail พร้อมเหตุผลอ่านได้; unknown embedded-schema keyword warn ใน draft และ fail ก่อน shippable; กฎที่อิง single-worker (ไม่มี `.thclaws/agents/`, ไม่มี `WorkflowRun` ใน AGENTS.md) ผูกกับ target ที่เป็น Atlas ไม่ใช่กฎสากล เพื่อรองรับ M9 | `check_m5_static_audit.py` |
| M6 | Live audit | `forge/live_test.py` start daemon ชั่วคราว (`thclaws --serve` บน port สุ่ม, CWD = package), ยิง `/agent/run` `stream:true` เก็บ SSE, validate output กับ branch ที่ถูกต้อง, สรุป evidence | fixture `sql-reader` กับ SQLite ตัวอย่างใน pack ผ่านทั้ง 4 golden case → สถานะ `candidate`; ทุก case validate ตาม `expect` (`result` → sole `assistant_json` schema, `refusal` → `refusal.schema`) และ fail เมื่อ schema สอง branch ซ้อนกัน; ไม่มี API key → คงสถานะ `draft` และ report บอกเหตุผล; รองรับเฉพาะ `/agent/run` ใน M6 ส่วน standalone (`thclaws agent run`) เป็นของ M9; ต้องมี M7a (SQL pack ขั้นต่ำ) ก่อน เพราะ generator ยังรายงาน `sql-readonly` เป็น `missing` | `check_m6_live.py` (ต้องการ thClaws + provider key; skip ได้แต่ต้องประกาศ) |
| M7a | Pack `sql-readonly` ขั้นต่ำ (ก่อน M6) | `packs/sql-readonly/pack.yaml` + MCP ของ project AI + SQLite fixture ที่ live audit ใช้ได้ | generator ของ `sql-reader` รายงาน pack เป็น `assets_bundled` ไม่ใช่ `missing`; MCP start ได้กับ SQLite fixture ในเครื่อง | `check_m7a_sql_pack.py` (ต้องมี MCP runtime; ไม่มีให้ skip exit 2) |
| M7b | Pack `publisher-email-sftp` | `packs/publisher-email-sftp/` (MCP ใหม่ dry_run + idempotency_key) | T0 กับ T2 ให้ guarantee matrix ต่างกันจริง; T2 บังคับ daemon แยกและ `human_gate` ใน node template | `check_m7b_packs.py` |
| M8 | Export + Atlas registration | `forge/atlas_export.py` ออก `atlas-register.json` + flow template (target `atlas-workflow`), `thclaws agent pack` เป็น archive | node export ใช้ `collect_files`; `output_format: json` มีเฉพาะ `assistant_json`; edge export ใช้ `push_files` และเปิด `policy.file_handoff`; ไฟล์ export ผ่าน workflow-definition.schema.json ของ Atlas และ register worker/workspace ใน Atlas ทดสอบได้ด้วยมือ | `check_m8_export.py` |
| M9 | Standalone generator (`static-pipeline`) | `fixtures/<name>-standalone/spec.yaml` (target `thclaws-standalone`, `package_pattern: static-pipeline`), templates `.thclaws/agents/{planner,worker,verifier}.md` + `.thclaws/agent_workflow/run.js`, generator เลิก reject pattern นี้; ไม่มี routing, ไม่มี `atlas-register.json` | output ตรงโครงสร้าง `thclaws agent new --pattern static-pipeline` v0.116.0 และผ่าน `thclaws agent validate`; permission ต่อ role ใน `agents/*.md` มาจาก spec ตาม DESIGN §8: ทุก role มี `tools` ไม่ว่างและเป็น subset ของ spec, planner/verifier ไม่มี Bash/Write, worker `write_scope: none` ไม่มี Write/Edit; negative test บน thClaws จริงว่าเขียนนอก `writePaths` และเรียก tool นอก subset ไม่ได้; guarantee matrix ระบุว่า tool/write restriction ต่อ role บังคับได้บน surface นี้ (ต่างจาก `/agent/run`); live audit ใช้ `thclaws agent run <pkg> --workflow .thclaws/agent_workflow/run.js --args` ตาม DESIGN §7 โดย `__wf_result` เป็น JSON ของ output/refusal branch ตรง schema, ตัด token summary ก่อน parse, ห้ามใช้ `verifier.ok` เป็นหลักฐาน; `batch-fanout` และ `dynamic` ยัง reject จนกว่าจะมี fixture ของตัวเอง | `check_m9_standalone.py` (ไม่มี `thclaws` → skip exit 2; มี binary แต่ไม่มี provider key → skip ส่วน live exit 2 พร้อมประกาศ) |

## นอกแผน (ทำหลัง MVP เมื่อมีเหตุผล)

- Discovery interview mode (`forge/discover.py`) + builder-agent เป็น thClaws package (รันใน GUI/CLI ที่ subagent/hooks ทำงาน)
- LLM meta-audit
- pack `report-pipeline` (NT-Report)
- patch `agent_runtime.rs` (allowed/disallowed tools, hooks, MCP allowlist) → upstream
- auto-deploy `/v1/deploy` + bundle version ต่อ worker (ผูกกับ Atlas M4)

## ความเสี่ยงที่รู้แล้ว

- thClaws release รายสัปดาห์: pin `thclaws_min_version` และมี compatibility fixture (M3/M4) ที่เทียบ output ของ `thclaws agent new` กับ template ของเรา
- live audit ต้องใช้ provider key: ทุก check ที่ต้องการต้อง skip แบบประกาศชัด ไม่ปล่อยให้ผ่านเงียบ
- exit code ของ check ทุก milestone: `0` = pass, `1` = fail, `2` = explicit skip; CI ต้องแสดง skip และ fail เฉพาะเมื่อมี `1`
- credential ห้ามอยู่ใน package หรือ fixture (`.gitignore` กัน data/ และ *.db แล้ว)
- ห้ามให้ spec โต: ความต้องการใหม่ไปโตที่ pack ไม่ใช่ที่ schema

## ขั้นถัดไป

M3 เสร็จ 2026-09-06: `patterns/matrix.yaml`, `forge/compat.py` และ `scripts/check_m3_matrix.py` ผ่าน gate แล้ว; ตรวจ M1/M2 ผ่านก่อนเริ่มงาน

M4 เสร็จ 2026-09-07: Atlas single-worker generator, build report schema/guarantee matrix และ `check_m4_generate.py` ผ่าน native gate ด้วย thClaws v0.116.0 revision `75edc48`. ตรวจทั้งสอง fixture, asset ของ pack สังเคราะห์, byte determinism และ explicit skip. แก้ baseline ของ dynamic ให้ไม่มี run.js จากหลักฐาน `agent new` จริง

M5: ทำ static audit และ wrappers; missing pack ต้องไม่ผ่าน static audit. M4 ยังไม่ติดตั้ง SQL MCP (M7), ไม่ export flow/human_gate (M8) และ reject standalone generation จนกว่าจะมี orchestration templates ที่ตรวจแล้ว (M9) ดู [generator.md](generator.md)

ลำดับที่ปรับ 2026-09-07 หลัง review: M7 แยกเป็น M7a (SQL pack ขั้นต่ำ) ที่ต้องเสร็จก่อน M6 เพราะ live audit ของ `sql-reader` ใช้ SQL MCP กับ SQLite fixture ซึ่ง M4 ยังรายงานเป็น `missing`; M7b (publisher) คงอยู่หลัง M6

M9 (เพิ่ม 2026-09-07, ปรับหลัง review วันเดียวกัน): standalone generator เริ่มจาก `static-pipeline` เท่านั้น ใช้ 3 role คงที่โดยไม่ขยาย schema; ต้องเพิ่ม fixture standalone ก่อน และ M5/M6 ต้องผูกกฎ single-worker กับ target Atlas ไว้ล่วงหน้า. ข้อบังคับก่อนเริ่ม implementation: permission mapping ห้ามให้ `tools`/`writePaths` ว่างกลายเป็น inherit-all (DESIGN §8), live audit ใช้ `thclaws agent run` ไม่ใช่ `/agent/run` หรือ WebSocket และ `__wf_result` ต้องเป็น output/refusal branch ตรง schema ไม่ใช่ `{ok, steps}` (DESIGN §7)
