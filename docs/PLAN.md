# Plan — agent-builder MVP

กติกาเดียวกับ Atlas: **หนึ่ง milestone = หนึ่ง check script** ใน `scripts/check_<milestone>.py` ที่ fail เมื่อพฤติกรรมพัง
ห้าม tick milestone ก่อน check ผ่าน commit ต่อ milestone เมื่อ gate เขียว

ลำดับตาม baseline ที่ตกลง: schema → fixtures → matrix → generator → thclaws validate → static audit → SQL pack ขั้นต่ำ (M7a) → live audit → publisher pack (M7b) → export → standalone generator (M9, เพิ่ม 2026-09-07 ต่อท้าย MVP เพื่อไม่เลื่อนสาย Atlas) → pack authoring workflow (M10) → builder-agent (M11) (เพิ่ม 2026-09-07 หลัง MVP; M7a ขยายเป็น pack contract ในวันเดียวกัน)

| M | ชื่อ | ผลลัพธ์ | Definition of done | check |
|---|---|---|---|---|
| M0 | Repo + design | README, DESIGN, PLAN, AGENTS.md, โครงโฟลเดอร์, pyproject | push ขึ้น GitHub ได้ | `check_m0_layout.py` (ไฟล์/โฟลเดอร์บังคับครบ) |
| M1 | AgentSpec schema | `agentspec.schema.json` + `docs/agentspec.md` อธิบายทุก field | target/pattern/routing ถูกบังคับตาม target, embedded JSON Schema ถูกตรวจ, refusal schema, golden kinds และ expected branch ครบ, Atlas model/output arity ถูกจำกัด, tier/write boundary และ `shell: none` / `network: none` tool consistency ถูกตรวจ, ชื่อ tool ต้องเป็นชื่อที่ runtime เห็น (provider regex `^[A-Za-z0-9_-]+$`; MCP เป็น `<server>__<tool>` ตาม DESIGN §4, เพิ่ม 2026-09-07), T2 standalone/atlas-worker ถูก reject, invalid transport/state/model/path/unsatisfiable version ถูก reject | `check_m1_spec.py` |
| M2 | Fixtures | `fixtures/invoice-reviewer/spec.yaml` (target atlas-worker, `atlas_file_handoff`, Atlas เป็นคนกำหนด landing path), `fixtures/sql-reader/spec.yaml` (target atlas-worker, `prompt_json`, pack `sql-readonly`) + golden cases 4 ประเภทต่อ fixture | ทุก fixture ผ่าน M1 และ golden case มีครบ normal / missing / refusal / malformed พร้อม branch ที่คาดหวัง | `check_m2_fixtures.py` |
| M3 | Compatibility matrix | `patterns/matrix.yaml` + `forge/compat.py` คำนวณ execution surface และ pattern ที่อนุญาตจาก target | ทุก combination ใน DESIGN §3 ให้ผลตรงตาราง, combination นอกตารางถูก reject | `check_m3_matrix.py` |
| M4 | Generator | `forge/generate.py` + `templates/` สร้าง package จาก spec แบบ deterministic (รันสองครั้งได้ไฟล์เหมือนกันทุก byte) | output ของทั้ง 2 fixtures ผ่าน `thclaws agent validate` โดยไม่มี error; script ของ pack อยู่ใน `.thclaws/scripts/`; `builder-build-report.schema.json` และ `builder-build-report.json` มี guarantee matrix | `check_m4_generate.py` (ต้องมี `thclaws` ใน PATH; ถ้าไม่มีให้ skip ส่วน validate พร้อมประกาศชัด) |
| M5 | Static audit | `forge/audit.py` กฎใน DESIGN §7 + wrapper `audit.py` / `studio.py` ในทุก package | package ที่ถูกต้องได้สถานะ `draft`; package ที่ถูกแก้ให้ผิด (ลบ section Refuse, ใส่ admin MCP ใน T0, env หาย) ถูก fail พร้อมเหตุผลอ่านได้; unknown embedded-schema keyword warn ใน draft และ fail ก่อน shippable; กฎที่อิง single-worker (ไม่มี `.thclaws/agents/`, ไม่มี `WorkflowRun` ใน AGENTS.md) ผูกกับ target ที่เป็น Atlas ไม่ใช่กฎสากล เพื่อรองรับ M9 | `check_m5_static_audit.py` |
| M6 | Live audit | `forge/live_test.py` start daemon ชั่วคราว (`thclaws --serve` บน port สุ่ม, CWD = package), ยิง `/agent/run` `stream:true` เก็บ SSE, validate output กับ branch ที่ถูกต้อง, สรุป evidence | fixture `sql-reader` กับ SQLite ตัวอย่างใน pack ผ่านทั้ง 4 golden case → สถานะ `candidate`; ทุก case validate ตาม `expect` (`result` → sole `assistant_json` schema, `refusal` → `refusal.schema`) และ fail เมื่อ schema สอง branch ซ้อนกัน; ไม่มี API key → คงสถานะ `draft` และ report บอกเหตุผล; รองรับเฉพาะ `/agent/run` ใน M6 ส่วน standalone (`thclaws agent run`) เป็นของ M9; ต้องมี M7a (SQL pack ขั้นต่ำ) ก่อน เพราะ generator ยังรายงาน `sql-readonly` เป็น `missing`; daemon isolated ตาม DESIGN §4 (`THCLAWS_CONFIG`, `HOME`/`XDG_CONFIG_HOME` ชั่วคราว, `THCLAWS_MCP_ALLOW_ALL=1`, `THCLAWS_API_TOKEN`, CWD = package จึงได้ `.thclaws/mcp.json` ของ package) โดย env ของ fixture มาจาก `fixture.setup` ของ pack; evidence ต้องมี SSE tool event ชื่อ `<server>__<tool>` ของ pack และ `/v1/agent/info` เห็น server นั้น. Note จาก review M7a (2026-09-07) ที่ M6 ต้องเก็บ: (1) `pack-conformance.json` ที่ fail/skip/stale ปัจจุบันกัน `forge generate` ทั้งก้อน แต่ลบไฟล์แล้ว render ได้ `assets_bundled` ให้เปลี่ยนเป็น finding ระดับ error ใน static audit (กัน `draft`) โดยไม่กัน generate; (2) provenance: harness/`fixture/setup.py` บันทึก `git rev-parse HEAD` ของ `SQL_READONLY_AI_ROOT` ลง evidence และ fail เมื่อไม่ตรง `source.ref` เพราะ `server.py` monkeypatch `_db_adapter` ของ upstream; (3) `setup.py` probe `mcp` ด้วย interpreter เดียวกับที่ server ใช้ (`python3` บน PATH) ไม่ใช่ `sys.executable` ของ harness เพื่อให้ได้ exit 2 ไม่ใช่ 1; (4) env pattern ใน `forge/packs.py` (`[A-Z_][A-Z0-9_]*`) ให้ตรง schema ของ AgentSpec (`^[A-Z][A-Z0-9_]*$`) | `check_m6_live.py` (ต้องการ thClaws + provider key; skip ได้แต่ต้องประกาศ) |
| M7a | Pack contract + `sql-readonly` ขั้นต่ำ (ก่อน M6) | pack contract v2 ตาม DESIGN §5 (`network`/`hosts`, `mcp_servers[]` พร้อม `tools`/`mutating`, `fixture.setup` + `fixture.cases`, `source` optional); `forge pack test <name>` harness stdio JSON-RPC ด้วย stdlib (`initialize` → `tools/list` → cases → `pack-conformance.json`); generator เขียน `.thclaws/mcp.json` (command/args เท่านั้น ไม่มีค่า secret); static audit เพิ่ม tool catalog `patterns/tools-0.116.0.json` จาก `ToolRegistry::with_builtins`, qualified MCP names และ `network` ของ spec ไม่ต่ำกว่า pack; `packs/sql-readonly/` ห่อ MCP ของ project AI + SQLite fixture ที่ advertise `metadata`/`query`/`validate` ตรงกับ fixture `sql-reader` (ใช้ชื่อ `sql-readonly__*` แล้วตั้งแต่ M1 follow-up); migration ต้องอยู่ใน milestone เดียวกัน: `load_pack`/`pack_assets` ใน `forge/generate.py` เปลี่ยนจาก descriptor v1 หกฟิลด์เป็น v2 ทั้งชุด (ไม่ต้องรองรับย้อนหลัง เพราะยังไม่มี pack จริงและ gate M4 ใช้ pack สังเคราะห์), กฎ `packs` ของ `forge/audit.py`, `dependencies[].status` เพิ่ม `conformant` ใน `builder-build-report.schema.json` และแก้ gate M4/M5 ให้ใช้ v2 | `forge pack test sql-readonly` ผ่านทั้ง smoke และ negative case (mutation ถูกปฏิเสธที่ server); generator รายงาน pack เป็น `assets_bundled` และ `sql-reader` ได้ `draft` จาก `forge audit`; tool ที่ไม่อยู่ใน catalog หรือ pack เป็น warning ใน draft และ fail เมื่อ `--strict`; pack ที่ `mutating` ไม่ว่างแต่ `min_tier` ต่ำกว่า T2 ถูก reject; ไม่มีค่า secret ในไฟล์ใดของ package | `check_m7a_sql_pack.py` (ไม่มี runtime ของ MCP → skip exit 2; ไม่ต้องใช้ provider key) |
| M7b | Pack `publisher-email-sftp` | `packs/publisher-email-sftp/` (MCP ใหม่ dry_run + idempotency_key) | T0 กับ T2 ให้ guarantee matrix ต่างกันจริง; T2 บังคับ daemon แยกและ `human_gate` ใน node template | `check_m7b_packs.py` |
| M8 | Export + Atlas registration | `forge/atlas_export.py` ออก `atlas-register.json` + flow template (target `atlas-workflow`), `thclaws agent pack` เป็น archive | node export ใช้ `collect_files`; `output_format: json` มีเฉพาะ `assistant_json`; edge export ใช้ `push_files` และเปิด `policy.file_handoff`; ไฟล์ export ผ่าน workflow-definition.schema.json ของ Atlas และ register worker/workspace ใน Atlas ทดสอบได้ด้วยมือ | `check_m8_export.py` |
| M9 | Standalone generator (`static-pipeline`) | `fixtures/<name>-standalone/spec.yaml` (target `thclaws-standalone`, `package_pattern: static-pipeline`), templates `.thclaws/agents/{planner,worker,verifier}.md` + `.thclaws/agent_workflow/run.js`, generator เลิก reject pattern นี้; ไม่มี routing, ไม่มี `atlas-register.json` | output ตรงโครงสร้าง `thclaws agent new --pattern static-pipeline` v0.116.0 และผ่าน `thclaws agent validate`; permission ต่อ role ใน `agents/*.md` มาจาก spec ตาม DESIGN §8: ทุก role มี `tools` ไม่ว่างและเป็น subset ของ spec, planner/verifier ไม่มี Bash/Write, worker `write_scope: none` ไม่มี Write/Edit; negative test บน thClaws จริงว่าเขียนนอก `writePaths` และเรียก tool นอก subset ไม่ได้; guarantee matrix ระบุว่า tool/write restriction ต่อ role บังคับได้บน surface นี้ (ต่างจาก `/agent/run`); live audit ใช้ `thclaws agent run <pkg> --workflow .thclaws/agent_workflow/run.js --args` ตาม DESIGN §7 โดย `__wf_result` เป็น JSON ของ output/refusal branch ตรง schema, ตัด token summary ก่อน parse, ห้ามใช้ `verifier.ok` เป็นหลักฐาน; `batch-fanout` และ `dynamic` ยัง reject จนกว่าจะมี fixture ของตัวเอง | `check_m9_standalone.py` (ไม่มี `thclaws` → skip exit 2; มี binary แต่ไม่มี provider key → skip ส่วน live exit 2 พร้อมประกาศ) |
| M10 | Pack authoring workflow | `forge pack new <name>` scaffold (`pack.yaml` v2 + `fixture/setup.py` + cases + README ของ contract), `forge pack discover --command <cmd> [--args ...]` ร่าง `pack.yaml` จาก `tools/list` (tools, input schema, annotations เป็นข้อเสนอของ `mutating`) โดยไม่ตัดสิน tier/network/env/fixture, `docs/packs.md` สำหรับผู้เขียน MCP ภายนอก, skill `pack-author` (SKILL.md ที่ session ของ thClaws ใช้เขียน MCP ตาม contract ได้), reference server `fixtures/packs/echo/` (stdlib stdio MCP ~50 บรรทัด) เป็น fixture ของ gate | scaffold → implement echo → `forge pack test` ผ่านครบวงจรโดยไม่ใช้ LLM; `discover` จาก echo server ได้ `pack.yaml` ที่ผ่าน `forge pack test` หลังผู้เขียนเติม tier/fixture; harness รัน server ด้วย env สะอาด (PATH + env ที่ประกาศ) และ server ที่ต้องการ env ที่ไม่ได้ประกาศ fail พร้อมเหตุผล; ชื่อ server/tool นอก charset ของ contract ถูก reject; server ที่ advertise tool นอกที่ประกาศหรือขาด tool fail พร้อมเหตุผล; pack ที่มี `mutating` แต่ `min_tier: T0` ถูก reject; `forge generate` กับ spec ที่ใช้ echo pack ได้ `.thclaws/mcp.json` ที่ไม่มีค่า secret และ `forge audit` ได้ `draft` | `check_m10_pack_authoring.py` (ไม่ต้องใช้ key; ไม่มี `thclaws` → skip ส่วน validate exit 2) |
| M11 | builder-agent (thClaws เป็นผู้สร้าง) | workspace `builder/` (AGENTS.md, skills `spec-author` + `pack-author`, ไม่มี subagent) ที่ interview 7 ข้อ, เขียน `spec.yaml`, รัน `forge generate` / `forge audit` / `forge pack test` ผ่าน Bash ใน workspace ของ builder, รายงานสถานะจาก `builder-build-report.json`; marker `confirmed_by` สำหรับ block permissions/env/network/capabilities ตาม DESIGN §9 | offline: workspace ผ่าน `thclaws agent validate`, AGENTS.md ห้าม model ตัดสิน permission/network/secret และห้ามประดิษฐ์ pack, `spec.yaml` ที่ไม่มี `confirmed_by` ต้องถูก skill ปฏิเสธก่อน generate; live (ต้องมี key): prompt fixture "agent พยากรณ์อากาศจาก Google Weather" ได้ spec ที่ `forge generate` รับ และ report บอก pack `google-weather` เป็น `missing` ตรง ๆ; ผล audit ทุกชั้นมาจาก forge ไม่ใช่คำสรุปของ model | `check_m11_builder_agent.py` (ไม่มี `thclaws` → skip exit 2; ไม่มี provider key → skip ส่วน live exit 2 พร้อมประกาศ) |

## นอกแผน (ทำหลัง MVP เมื่อมีเหตุผล)

- Discovery interview mode แบบไม่ใช้ LLM (`forge/discover.py`) ถ้า builder-agent (M11) ไม่พอ
- pack `google-weather` (MCP เรียก Google Weather API, `network: allowlist`) เป็น pack แรกที่ทดสอบ network requirement จริง ทำเมื่อมีคนเขียน MCP ตาม contract M7a/M10; builder ไม่เขียน MCP ให้
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

M5 เสร็จ 2026-09-07: `forge/audit.py` + `forge audit` + wrappers `audit.py`/`studio.py` ในทุก package, report schema รองรับ `draft` และ `static_audit`, `check_m5_static_audit.py` ผ่าน native gate บน thClaws v0.116.0 revision `75edc48`. `invoice-reviewer` เป็น `draft`; `sql-reader` fail ที่ `packs` เพราะ `sql-readonly` ยัง missing (รอ M7a). M4 ยังไม่ติดตั้ง SQL MCP (M7), ไม่ export flow/human_gate (M8) และ reject standalone generation จนกว่าจะมี orchestration templates ที่ตรวจแล้ว (M9) ดู [generator.md](generator.md), [audit.md](audit.md)

ลำดับที่ปรับ 2026-09-07 หลัง review: M7 แยกเป็น M7a (SQL pack ขั้นต่ำ) ที่ต้องเสร็จก่อน M6 เพราะ live audit ของ `sql-reader` ใช้ SQL MCP กับ SQLite fixture ซึ่ง M4 ยังรายงานเป็น `missing`; M7b (publisher) คงอยู่หลัง M6

M9 (เพิ่ม 2026-09-07, ปรับหลัง review วันเดียวกัน): standalone generator เริ่มจาก `static-pipeline` เท่านั้น ใช้ 3 role คงที่โดยไม่ขยาย schema; ต้องเพิ่ม fixture standalone ก่อน และ M5/M6 ต้องผูกกฎ single-worker กับ target Atlas ไว้ล่วงหน้า. ข้อบังคับก่อนเริ่ม implementation: permission mapping ห้ามให้ `tools`/`writePaths` ว่างกลายเป็น inherit-all (DESIGN §8), live audit ใช้ `thclaws agent run` ไม่ใช่ `/agent/run` หรือ WebSocket และ `__wf_result` ต้องเป็น output/refusal branch ตรง schema ไม่ใช่ `{ok, steps}` (DESIGN §7)

วางแผนเพิ่ม 2026-09-07 หลังคำถาม "ให้ thClaws เป็นผู้สร้าง agent": ตรวจ source thClaws v0.116.0 revision `75edc48` แล้วบันทึกใน DESIGN §4 ว่า daemon โหลด `.thclaws/mcp.json` ของ CWD ตอน start, tool ของ MCP ชื่อ `<server>__<tool>`, secret ต้องมาจาก env ของ daemon และ isolation ใช้ `THCLAWS_CONFIG` / `HOME` / `THCLAWS_MCP_ALLOW_ALL` / `THCLAWS_API_TOKEN`. ผลคือ (1) M7a ขยายเป็น pack contract v2 + `forge pack test` เพราะเป็นรอยต่อเดียวที่งานภายนอก (MCP, script, skill) จะเข้ามาทดสอบและผนวกได้โดยไม่ใช้ LLM (DESIGN §5) (2) M10 pack authoring workflow ให้คนหรือ thClaws เขียน pack ตาม contract ได้เอง (3) M11 builder-agent ที่ให้ thClaws interview/ร่าง spec/รัน forge โดยไม่ตัดสินเรื่อง permission/secret (DESIGN §9). ตัดสินใจใช้ qualified name ในวันเดียวกัน: schema จำกัด `permissions.tools` ด้วย provider regex, semantic check บังคับรูป `<server>__<tool>`, fixture `sql-reader` เปลี่ยนเป็น `sql-readonly__*` แล้ว (M1/M2 follow-up ไม่รอ M7a) ทำให้ M7a เหลือแค่สร้าง pack ที่ advertise ชื่อเดียวกัน

M7a เสร็จ 2026-09-07: contract v2, `forge pack test`, MCP config generation, pinned tool catalog และ migration gate M4/M5 ครบ. SQL wrapper ใช้ query MCP / shared validation ของ project AI ref `d95dc28628673d7adef1d6f1958116922fea1946`, จำกัด SQLite พร้อม upstream mode=ro และ authorizer; ไม่เปิด admin tools. `check_m7a_sql_pack.py` ผ่าน conformance, negative protocol cases, SQL totals/unchanged database และ native draft บน thClaws v0.116.0 revision `75edc48` โดยไม่ใช้ provider key. ถัดไป M6; pack conformance ยังไม่ใช่หลักฐานว่า agent เรียก MCP หรือ runtime บังคับ permission ของ spec.

งานติดตามจาก review M7a (ไม่รวมใน DoD รอบนี้): พิจารณาย้าย failed/stale conformance evidence จาก generation error ไปเป็น static-audit finding เพื่อแยก render ออกจากผล test; ตรวจและบันทึก AI source revision เทียบ `source.ref` ใน M6/security audit; ให้ fixture ตรวจ MCP dependency ด้วย interpreter เดียวกับ command ของ server; ทำ env-name pattern ของ pack ให้ตรง AgentSpec. Gate M7a เขียน conformance evidence ลง source tree แบบ gitignored ตามพฤติกรรมปัจจุบัน.

M6 เสร็จ 2026-09-07: `forge/live_test.py`, `forge live-test`, `forge audit --live`, report evidence/status `candidate` และ follow-up M7a ทั้ง 4 จุดครบ. `check_m6_live.py --model oai/gpt-5.4-mini --provider-key-env OPENAI_COMPAT_API_KEY` ผ่าน exit 0 บน thClaws v0.116.0 revision `75edc48` โดยใช้ MATCHA OpenAI-compatible endpoint กับ TLS verification ปกติ. ผ่านทั้ง 4 golden cases, SSE ของ SQL MCP และ daemon inventory; SQL source provenance ตรง ref ที่ pin. Offline negative checks และ M4/M5 native ผ่านด้วย. รอบเดิมตรวจ Ruff 0.15.20 ผ่าน แต่ review ด้วย Ruff 0.16.6 พบ 13 errors จึงยังใช้ผล lint เดิมยืนยัน CI ไม่ได้ (แก้ใน follow-up ด้านล่าง). Fixture ต้นฉบับยัง pin `gpt-5.4` ซึ่ง MATCHA ไม่ advertise; ผล live นี้รับรอง package ที่ pin `oai/gpt-5.4-mini` เท่านั้น. ครั้งแรกพบ refusal/malformed ไม่ตรง schema จึงปรับ generated instructions ให้แสดง input/refusal contract จาก SSOT โดยตรงและตรวจ input ก่อน tool call; ไม่เปลี่ยน golden cases หรือเกณฑ์ audit. ไม่มี key ยัง exit 2 และไม่มีการเลื่อนสถานะ. Security audit ยังไม่รัน; candidate ไม่ใช่ shippable.

M6 review follow-up 2026-09-07: แก้ lint ทั้ง 13 จุดและตรวจ `ruff 0.16.6 check .` ผ่าน; gate executable, subprocess ระบุ check=False และ negative JSON cases ตรวจเหตุผลแยกชัดเจน. ตรวจ source thClaws `75edc48` แล้วเพิ่ม baseline `browserEnabled` ใน DESIGN §4 ก่อนแก้ harness ให้ปิด browser และตรวจ MCP inventory เท่ากับ declared ทั้งก่อนและหลัง golden cases. เพิ่ม regression ที่ server `browser` เกินต้อง fail ก่อนมี model call. Gate เต็มผ่าน exit 0 อีกครั้งด้วย `oai/gpt-5.4-mini` ผ่าน MATCHA หลังแก้ isolation; ขอบเขต candidate/model และข้อจำกัด tool allowlist/shell/write-path ตามย่อหน้าก่อนยังเหมือนเดิม.

M7b เสร็จ 2026-09-08: `publisher-email-sftp` MCP (Python stdlib + native OpenSSH sftp), dry run,
persistent idempotency ledger, fixed operator destinations และ fixture `publisher` target `atlas-workflow`.
T2 generate มี `atlas-node-template.json` ที่เริ่ม `human_gate` และไป worker เฉพาะ human approve;
require daemon แยกพร้อม worker/workspace binding และ static audit reject การถอด gate/edge/isolation.
`check_m7b_packs.py` ผ่าน exit 0 บน thClaws v0.116.0 revision `75edc48`: MCP conformance, negative
arguments/path cases, retry/restart/concurrency/unknown outcome, transport adapters ด้วย test doubles,
byte determinism และ native draft audit. Template ผ่าน native schema/graph/prompt ของ Atlas ref
`daa0f4966899327fb563501435f5d6180eeef9c3`. ไม่ได้ส่ง email/SFTP จริงหรือ provision daemon;
human_approval ยัง `Not verified`, publisher เป็น `draft` ไม่ใช่ candidate/shippable. ถัดไป M8.
Regression M0–M5/M7a และ Ruff 0.16.6 ผ่าน; M6 offline ผ่าน ส่วน live ใช้ key ว่างโดยตั้งใจและรายงาน skip exit 2.

M7b live follow-up 2026-09-08: reviewer พบ publisher normal case ถูกปฏิเสธทั้งที่ input ตรง schema.
รันซ้ำผ่าน MATCHA ด้วย `oai/gpt-5.4-mini` แล้วยืนยัน failure; local provider probe ยืนยันว่า native thClaws
ส่ง input และ publisher tool schemas ครบ. แก้ instructions ให้ JSON run prompt เป็นคำขอทำ AgentSpec mission
แทน workflow ทั่วไปสำหรับงาน coding, แยก input envelope กับ schema ของ value, ระบุ preview ที่ไม่ต้องมี approval
และให้คืน output schema value โดยไม่ครอบชื่อ artifact. ไม่เปลี่ยน golden cases หรือเกณฑ์ audit.
เพิ่ม `check_m7b_packs.py --live --model ... --provider-key-env ...` สำหรับ opt-in live regression;
default gate ยังตรวจถึง draft ตาม DoD เดิม. เคยมีรอบที่ package ซึ่ง pin `oai/gpt-5.4-mini` ผ่านทั้ง 4 cases
และมี SSE `publisher-email-sftp__send_email` จาก preview จริง ได้ `candidate`; fixture ต้นฉบับยัง pin `gpt-5.4`.
แต่ review รอบถัดไปรายงานว่าผ่านเพียง 2 จาก 5 ครั้ง โดย normal case ตีความว่า preview ต้องใช้ approved email
จึงยังไม่ถือว่าปิดปัญหา: candidate report เดิมเป็นหลักฐานเฉพาะรอบ ไม่ใช่หลักฐานว่าผลทำซ้ำได้.
ผลนี้ไม่ยืนยัน actual delivery, SFTP live, daemon isolation ของ deployment หรือ human approval;
`human_approval` ยัง `Not verified`. ข้อจำกัด network enum/hosts ว่างยังรับไว้ตาม README ไม่ได้แก้ contract.
ตรวจซ้ำด้วย M7b opt-in live gate ผ่าน exit 0; SQL M6 offline/live regression ผ่าน exit 0 ด้วย model/endpoint เดียวกัน.
M2/M4/M5 native gates, Ruff 0.16.6 และ `git diff --check` ผ่าน; M7b `--live` ไม่มี key ยัง skip exit 2 ตามเดิม.

M7b stability follow-up 2026-09-08 — **ผลก่อนแก้ช่องทาง MCP instructions**: แก้ `mission.purpose` ของ fixture ให้ preview
ไม่ติดเงื่อนไข approved และลบ block เฉพาะ publisher ออกจาก template กลาง. Source สุดท้ายไม่เปลี่ยน
permissions, golden cases, schema, `forge/`, server implementation หรือ tool descriptions.
ทดลองแต่ละ variant บน package เดียวกัน 3 รอบ ตรวจ hash ว่า package ไม่เปลี่ยนระหว่างรอบ และเก็บทุกผล
โดยไม่เลือกเฉพาะรอบที่ผ่าน. ทุก trial ยกเว้น `gpt-4.1` ใช้ `oai/gpt-5.4-mini` ผ่าน MATCHA เดิม.

| Trial | สิ่งที่ทดสอบ | ผ่านครบทั้ง 4 cases + MCP evidence |
|---|---|---|
| `mission-v2` | mission ที่แยก preview/approved delivery; ลบ block เฉพาะ pack | 0/3 |
| `t2-scope` | ปรับ T2 Must ให้ approval ผูกกับ external side effect | 1/3 |
| `inline-skills` | แนบ skill body ใน AGENTS ของ package ทดสอบ | 0/3 |
| `tool-contract` | อธิบาย preview/approval/digest ใน MCP tool description | 2/3 |
| `explicit-mission` | ระบุ subject/body และ dry_run ใน mission ของ package ทดสอบ | 2/3 |
| `refusal-first` | ระบุให้ใช้ refusal conditions ก่อน preview ใน mission ทดสอบ | 1/3 |
| `gpt-4.1` | เปลี่ยนเฉพาะ model ทดสอบเป็น oai/gpt-4.1 | 1/3 |
| `skill-loader` | เพิ่ม Skill ใน declared tools ของ package ทดสอบเท่านั้น | 2/3 |

`normal` บางรอบปฏิเสธ input ที่ตรง schema หรือสร้าง digest โดยไม่เรียก MCP; บาง variant ยังผิด refusal
หรือเติม arguments เองใน missing-input. Audit เดิมจับ failures เหล่านี้ จึงไม่มี trial ที่ผ่านเงื่อนไขครบ 3 รอบ.
Diagnostic หนึ่ง provider turn (ไม่ execute tool calls) บางแบบเลือก MCP ถูกต้อง แต่ผลนั้นไม่ยืนยัน full audit.
Native events ยืนยันว่า skill body ไม่ได้อยู่ใน initial catalog และบางรอบเรียก Skill แม้ source ไม่ได้ประกาศ;
การทดลองเพิ่ม Skill อย่างเดียวก็ยังไม่แก้ความไม่นิ่ง จึงไม่เปลี่ยน permissions ของ source.

ถอน T2/tool-description variants และไม่ย้าย mission/model/permission variants จาก package ทดสอบเข้า source.
Source สุดท้าย render ตรงกับ `mission-v2/package` ทุก byte ก่อนบันทึก failed round-3 report เป็น `draft`;
ทั้ง 3 รอบของ variant นี้ normal fail และอีก 3 cases ผ่าน. หลักฐานอยู่ใน local ignored
`out/publisher-stability/<trial>/summary.json` และ `round-1.json` ถึง `round-3.json`; ไม่มี credential ใน report.
`candidate` ของรอบอื่นยังเป็นหลักฐานเฉพาะรอบ ไม่ใช่หลักฐานว่าปิดปัญหา. ต้องผ่านครบอย่างน้อย 3 รอบบน
package เดียวกันก่อนปิดข้อ 4; `human_approval` ยัง `Not verified` และไม่ได้ส่ง email/SFTP จริง.

M0–M5/M7a/M7b native gates และ Ruff 0.16.6 ผ่าน; M6 offline ผ่านและไม่มี key skip exit 2;
M7b --live ไม่มี key skip exit 2 คง draft. SQL M6 live regression ผ่าน exit 0 ด้วย `oai/gpt-5.4-mini`
หลังแก้ template กลาง (การทดลอง T2 ไม่มีผลต่อ SQL ซึ่งเป็น T0). MATCHA inventory ไม่มี gpt-5.4 ตัวเต็ม;
ไม่ได้เปลี่ยน source model ที่ยัง pin gpt-5.4. ไม่มี commit/push ใน follow-up นี้.

M7b MCP guidance follow-up: trace source thClaws `75edc48` พบช่องทาง
`InitializeResult.instructions` ที่ถูกส่งเข้า system prompt ตั้งแต่ request แรก แต่ publisher ยังไม่ส่ง field นี้.
Diagnostic ใช้ skill body เดิมผ่านช่องทางนี้โดยไม่เปลี่ยน permissions/golden cases/audit criteria และผ่าน
3/3 full audits (`mcp-instructions`). Native localhost provider capture (`mcp-wire/provider-probe.json`)
ยืนยันว่าได้ skill body เดิมครบหนึ่งชุดใน `# MCP server instructions` และ input/tool schemas ครบ.

นำลง source โดย server อ่าน body จาก skill ไฟล์เดิมเพื่อไม่คัดลอกคำแนะนำไว้ใน template กลาง. พบ conformance
harness stage เฉพาะ scripts จึงเพิ่มการ stage skills ที่ pack ประกาศด้วย path ของ generated package;
เพิ่ม M7b gate ที่แก้ skill ใน temporary copy แล้วตรวจ initialize ทั้ง source/generated layouts.
Source trial `mcp-source` ได้ 2/3: ทุก case ถูก branch แต่ normal รอบหนึ่งสร้าง digest เองโดยไม่มี MCP.
จึงระบุข้อเท็จจริงใน skill ว่า digest รวม operator-configured destination ซึ่งไม่มีใน prompt และต้องได้จาก tool.
ผลก่อนหน้านี้ยังเป็นหลักฐานประจำ variant ไม่ใช้แทนผลของ source ล่าสุด.

M7b stability result 2026-09-08 (ปิดข้อ 4 ในขอบเขต gpt-5.4-mini): ประโยค digest ใน skill ทำให้ `mcp-digest` เหลือ 1/3
(normal refuse หนึ่งรอบ และ missing-input เรียก tool ด้วย placeholder หนึ่งรอบ) จึงถอนออก. Trace `prompts.rs::load` ของ
`75edc48` พบว่า daemon ใช้ `.thclaws/prompt/system.md` ใน CWD แทน base prompt ของ coding assistant (บันทึกใน DESIGN §4)
จึง generate mission-runner profile จาก `templates/system.md.j2` ให้ทุก target โดยไม่มีข้อความเฉพาะ pack และไม่แตะ
golden cases, permissions, schema หรือเกณฑ์ audit. Source ปัจจุบันผ่าน full live audit 6/6 รอบใน 2 ชุด
(`system-profile`, `system-profile-2`; package hash เท่ากันทุกไฟล์) ทุกรอบ `candidate` มี SSE `send_email` จาก normal
และไม่มี tool call ใน 3 refusal cases. Native provider capture (`system-profile/provider-probe.json`) ยืนยันว่า system
message เริ่มด้วย profile, ไม่มี coding base prompt, และยังมี `# MCP server instructions` พร้อม skill body ครั้งเดียว.

ยังเปิดอยู่: (1) fixture pin `gpt-5.4` ซึ่ง MATCHA ไม่ advertise ผลทั้งหมดรับรองเฉพาะ `oai/gpt-5.4-mini`; ก่อนเลื่อน model
ใน source ต้องรัน 3 รอบด้วย model นั้น. (2) `human_approval` ยัง `Not verified`, ไม่ได้ส่ง email/SFTP จริง, และไม่ได้
provision daemon แยกของ deployment; `candidate` ไม่ใช่ shippable. (3) profile ถูกโหลดเฉพาะเมื่อ daemon start จาก package
CWD; harness ทำเช่นนั้น แต่ deployment จริงต้องตรวจเอง (deployment hint ระบุแล้ว). (4) ความนิ่งวัดจากรอบที่รันบน package byte เดียวกันเท่านั้น (source ปัจจุบันมี 3/3; skill รุ่นก่อน 6/6);
ถ้าเปลี่ยน template กลาง, skill หรือ thClaws version ให้รัน `out/publisher_stability.py` ใหม่ก่อนอ้างผลเดิม.
Ruff และ `git diff --check` ผ่าน; ไม่มี commit/push.

M7b review fixes 2026-09-08: (1) skill ของ pack ไม่ผูก input shape ของ fixture อีกต่อไป (derive `subject`/`body`/`dry_run`/
`idempotency_key` ตาม input contract และ mission ของ spec นั้น) และ description เป็น "Preview or publish"; (2) server ระบุ
ceiling ของ hardcoded skill layout ด้วย `ponytail:` comment; (3) M7b gate assert ว่า profile ไม่มีวงเล็บปีกกาที่
`apply_template` จะแทน; (4) `docs/audit.md` เพิ่ม `--live` และ profile, DESIGN §8 เพิ่ม `.thclaws/prompt/system.md`,
CHANGELOG ย้าย profile ไป Added. การแก้ skill ทำให้ `pack-conformance.json` stale จริงตามที่ review คาด (static audit block
live จน `forge pack test` ใหม่). Source หลังแก้ผ่าน live 3/3 (`skill-generic`) ในเงื่อนไขเดิม; M7b gate exit 0,
Ruff และ `git diff --check` ผ่าน. ข้อเปิด 4 ข้อในย่อหน้าก่อนไม่เปลี่ยน.

M7b review round 3 (2026-09-08): แก้ README ของ pack และข้อเปิด (4) ให้ตรงข้อเท็จจริงว่า source ปัจจุบันมีหลักฐาน 3/3
ส่วน 6/6 เป็นของ skill รุ่นก่อน; skill ระบุว่า `idempotency_key`/`dry_run` ต้องมาจาก input เสมอ ห้าม compose (compose ได้
เฉพาะ subject/body เมื่อ mission สั่ง) เพื่อรักษา stable key ตอน retry; `docs/audit.md` เลิกชี้ไฟล์ ignored และอธิบายกติกา
3 รอบแทน. หลังแก้ skill รัน `forge pack test` ใหม่และ live 3/3 (`skill-key`) บน package byte เดียวกับ source ปัจจุบัน.
