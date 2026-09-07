# Plan — agent-builder MVP

กติกาเดียวกับ Atlas: **หนึ่ง milestone = หนึ่ง check script** ใน `scripts/check_<milestone>.py` ที่ fail เมื่อพฤติกรรมพัง
ห้าม tick milestone ก่อน check ผ่าน commit ต่อ milestone เมื่อ gate เขียว

ลำดับตาม baseline ที่ตกลง: schema → fixtures → matrix → generator → thclaws validate → static audit → live audit → export

| M | ชื่อ | ผลลัพธ์ | Definition of done | check |
|---|---|---|---|---|
| M0 | Repo + design | README, DESIGN, PLAN, AGENTS.md, โครงโฟลเดอร์, pyproject | push ขึ้น GitHub ได้ | `check_m0_layout.py` (ไฟล์/โฟลเดอร์บังคับครบ) |
| M1 | AgentSpec schema | `agentspec.schema.json` + `docs/agentspec.md` อธิบายทุก field | target/pattern/routing ถูกบังคับตาม target, embedded JSON Schema ถูกตรวจ, refusal schema, golden kinds และ expected branch ครบ, Atlas model/output arity ถูกจำกัด, tier/write boundary และ `shell: none` / `network: none` tool consistency ถูกตรวจ, T2 standalone/atlas-worker ถูก reject, invalid transport/state/model/path/unsatisfiable version ถูก reject | `check_m1_spec.py` |
| M2 | Fixtures | `fixtures/invoice-reviewer/spec.yaml` (target atlas-worker, `atlas_file_handoff`, Atlas เป็นคนกำหนด landing path), `fixtures/sql-reader/spec.yaml` (target atlas-worker, `prompt_json`, pack `sql-readonly`) + golden cases 4 ประเภทต่อ fixture | ทุก fixture ผ่าน M1 และ golden case มีครบ normal / missing / refusal / malformed พร้อม branch ที่คาดหวัง | `check_m2_fixtures.py` |
| M3 | Compatibility matrix | `patterns/matrix.yaml` + `forge/compat.py` คำนวณ execution surface และ pattern ที่อนุญาตจาก target | ทุก combination ใน DESIGN §3 ให้ผลตรงตาราง, combination นอกตารางถูก reject | `check_m3_matrix.py` |
| M4 | Generator | `forge/generate.py` + `templates/` สร้าง package จาก spec แบบ deterministic (รันสองครั้งได้ไฟล์เหมือนกันทุก byte) | output ของทั้ง 2 fixtures ผ่าน `thclaws agent validate` โดยไม่มี error; script ของ pack อยู่ใน `.thclaws/scripts/`; `builder-build-report.schema.json` และ `builder-build-report.json` มี guarantee matrix | `check_m4_generate.py` (ต้องมี `thclaws` ใน PATH; ถ้าไม่มีให้ skip ส่วน validate พร้อมประกาศชัด) |
| M5 | Static audit | `forge/audit.py` กฎใน DESIGN §7 + wrapper `audit.py` / `studio.py` ในทุก package | package ที่ถูกต้องได้สถานะ `draft`; package ที่ถูกแก้ให้ผิด (ลบ section Refuse, ใส่ admin MCP ใน T0, env หาย) ถูก fail พร้อมเหตุผลอ่านได้; unknown embedded-schema keyword warn ใน draft และ fail ก่อน shippable | `check_m5_static_audit.py` |
| M6 | Live audit | `forge/live_test.py` start daemon ชั่วคราว (`thclaws --serve` บน port สุ่ม, CWD = package), ยิง `/agent/run` `stream:true` เก็บ SSE, validate output กับ branch ที่ถูกต้อง, สรุป evidence | fixture `sql-reader` กับ SQLite ตัวอย่างใน pack ผ่านทั้ง 4 golden case → สถานะ `candidate`; ทุก case validate ตาม `expect` (`result` → sole `assistant_json` schema, `refusal` → `refusal.schema`) และ fail เมื่อ schema สอง branch ซ้อนกัน; ไม่มี API key → คงสถานะ `draft` และ report บอกเหตุผล | `check_m6_live.py` (ต้องการ thClaws + provider key; skip ได้แต่ต้องประกาศ) |
| M7 | Pack `sql-readonly` และ `publisher-email-sftp` | `packs/sql-readonly/` (MCP ของ project AI + SQLite fixture), `packs/publisher-email-sftp/` (MCP ใหม่ dry_run + idempotency_key) | T0 กับ T2 ให้ guarantee matrix ต่างกันจริง; T2 บังคับ daemon แยกและ `human_gate` ใน node template | `check_m7_packs.py` |
| M8 | Export + Atlas registration | `forge/atlas_export.py` ออก `atlas-register.json` + flow template (target `atlas-workflow`), `thclaws agent pack` เป็น archive | node export ใช้ `collect_files`; `output_format: json` มีเฉพาะ `assistant_json`; edge export ใช้ `push_files` และเปิด `policy.file_handoff`; ไฟล์ export ผ่าน workflow-definition.schema.json ของ Atlas และ register worker/workspace ใน Atlas ทดสอบได้ด้วยมือ | `check_m8_export.py` |

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

M5: ทำ static audit และ wrappers; missing pack ต้องไม่ผ่าน static audit. M4 ยังไม่ติดตั้ง SQL MCP (M7), ไม่ export flow/human_gate (M8) และ reject standalone generation จนกว่าจะมี orchestration templates ที่ตรวจแล้ว ดู [generator.md](generator.md)
