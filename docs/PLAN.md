# Plan — agent-builder MVP

กติกาเดียวกับ Atlas: **หนึ่ง milestone = หนึ่ง check script** ใน `scripts/check_<milestone>.py` ที่ fail เมื่อพฤติกรรมพัง
ห้าม tick milestone ก่อน check ผ่าน commit ต่อ milestone เมื่อ gate เขียว

ลำดับตาม baseline ที่ตกลง: schema → fixtures → matrix → generator → thclaws validate → static audit → SQL pack ขั้นต่ำ (M7a) → live audit → publisher pack (M7b) → export → standalone generator (M9, เพิ่ม 2026-09-07 ต่อท้าย MVP เพื่อไม่เลื่อนสาย Atlas) → pack authoring workflow (M10) → builder-agent (M11) (เพิ่ม 2026-09-07 หลัง MVP; M7a ขยายเป็น pack contract ในวันเดียวกัน) → baseline 0.138.0 (M12, เพิ่ม 2026-10-27 เพราะ binary ที่ผู้ดูแลใช้จริงไม่ใช่ 0.116.0 อีกแล้ว) → host + bots layout (M13, เพิ่ม 2026-10-27 หลัง M7b/M11 live บน Mac fail เพราะ `--serve` ของ desktop build แปลง package ในที่เดิม; ประเด็นใน `docs/thclaws-host-bots-brief.md`)

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
| M9 | Standalone generator (`static-pipeline`, T0) | fixture `text-reviewer-standalone`, role planner/worker/verifier, CLI live audit; T1 disabled | deterministic package ผ่าน native validate, ทุก role ใช้ nonempty Read/Grep/Glob หรือ pack-declared nonmutating MCP subset; Write/Edit/Bash เรียกไม่ได้จริง; T1 generate และ legacy audit ถูก reject; CLI คืน result/refusal ที่ forge validate; provider golden cases ผ่านด้วย model ที่ระบุ; ไม่รับรอง filesystem isolation | `check_m9_standalone.py` (ไม่มี binary/key → explicit skip 2); `--write-boundary` ตรวจ blocker T1 แยกและคืน 1 เมื่อพบ bypass |
| M10 | Pack authoring workflow | `forge pack new <name>` scaffold (`pack.yaml` v2 + `fixture/setup.py` + cases + README ของ contract), `forge pack discover --command <cmd> [--args ...]` ร่าง `pack.yaml` จาก `tools/list` (tools, input schema, annotations เป็นข้อเสนอของ `mutating`) โดยไม่ตัดสิน tier/network/env/fixture, `docs/packs.md` สำหรับผู้เขียน MCP ภายนอก, skill `pack-author` (SKILL.md ที่ session ของ thClaws ใช้เขียน MCP ตาม contract ได้), reference server `fixtures/packs/echo/` (stdlib stdio MCP ~50 บรรทัด) เป็น fixture ของ gate | scaffold → implement echo → `forge pack test` ผ่านครบวงจรโดยไม่ใช้ LLM; `discover` จาก echo server ได้ `pack.yaml` ที่ผ่าน `forge pack test` หลังผู้เขียนเติม tier/fixture; harness รัน server ด้วย env สะอาด (PATH + env ที่ประกาศ) และ server ที่ต้องการ env ที่ไม่ได้ประกาศ fail พร้อมเหตุผล; ชื่อ server/tool นอก charset ของ contract ถูก reject; server ที่ advertise tool นอกที่ประกาศหรือขาด tool fail พร้อมเหตุผล; pack ที่มี `mutating` แต่ `min_tier: T0` ถูก reject; `forge generate` กับ spec ที่ใช้ echo pack ได้ `.thclaws/mcp.json` ที่ไม่มีค่า secret และ `forge audit` ได้ `draft` | `check_m10_pack_authoring.py` (ไม่ต้องใช้ key; ไม่มี `thclaws` → skip ส่วน validate exit 2) |
| M11 | builder-agent (thClaws เป็นผู้สร้าง) | workspace `builder/` (AGENTS.md, skills `spec-author` + `pack-author`, ไม่มี subagent) ที่ interview 7 ข้อ, เขียน `spec.yaml`, รัน `forge generate` / `forge audit` / `forge pack test` ผ่าน Bash ใน workspace ของ builder, รายงานสถานะจาก `builder-build-report.json`; marker `confirmed_by` สำหรับ block permissions/env/network/capabilities ตาม DESIGN §9 | offline: workspace ผ่าน `thclaws agent validate`, AGENTS.md ห้าม model ตัดสิน permission/network/secret และห้ามประดิษฐ์ pack, `spec.yaml` ที่ไม่มี `confirmed_by` ต้องถูก skill ปฏิเสธก่อน generate; live (ต้องมี key): prompt fixture "agent พยากรณ์อากาศจาก Google Weather" ได้ spec ที่ `forge generate` รับ และ report บอก pack `google-weather` เป็น `missing` ตรง ๆ; ผล audit ทุกชั้นมาจาก forge ไม่ใช่คำสรุปของ model | `check_m11_builder_agent.py` (ไม่มี `thclaws` → skip exit 2; ไม่มี provider key → skip ส่วน live exit 2 พร้อมประกาศ) |
| M12 | thClaws baseline 0.138.0 (เพิ่ม 2026-10-27) | ตรวจ DESIGN §4 ซ้ำกับ source `d452ece` (branch `baseline-0.138.0-d452ece`), `patterns/tools-0.138.0.json`, `THCLAWS_BASELINE` + คู่ standalone `0.138.0/d452ece`, docs | ทุก claim ใน §4 มีสถานะ ไม่เปลี่ยน/เปลี่ยน พร้อมผลต่อ builder; native gate M4–M11 ผ่านบน binary จาก `d452ece`; `--write-boundary` บันทึกผล; T1/allowedTools ยังไม่เปิด | `check_m12_baseline.py` (ไม่มี binary → exit 2; binary ไม่ตรงคู่ → exit 1) |
| M14 | ปลด T1 standalone (เพิ่ม 2026-10-28) | DESIGN §4 "Standalone T1 rollout" (review writePaths ของ `d452ece`: ครอบ Write/Edit/office writers ไม่ครอบ Bash/MCP, resolve ปลายทางผ่าน sandbox) + §6/§8; generator สร้าง T1 `static-pipeline` ที่ worker ได้ `Write`/`Edit` พร้อม `writePaths: output/**` และ planner/verifier คง T0; `STANDALONE_T1_THCLAWS_COMPATIBILITY` เฉพาะ `0.138.0/d452ece`; static audit ถือ `output/**` เป็น runtime output เฉพาะ T1 และ validate ตามคู่ของ tier; live audit standalone ทุก tier ทำ tree diff ต่อ case; fixture `note-writer-standalone`; matrix T1 `output_write_scope: Enforced` (เฉพาะ Write/Edit ของ worker), `no_file_writes: Not applicable`; skill `spec-author` บอกขอบเขต T1 | package T1 deterministic ผ่าน validate; writer อื่น/T1 ไม่มี writer/pattern อื่น/T2 ถูก reject; boundary drift และไฟล์นอก output/ เป็น finding; บน binary คู่ T1: probe 14 รายการบน package จริง (Write/Edit ใต้ output ผ่าน, direct/traversal/symlink/dangling/Edit นอก output/Bash และ planner/verifier Write/Edit/Bash ถูกปฏิเสธ), pipeline 4 cases ผ่าน provider จำลองโดยเขียนเฉพาะ `output/note.md`; provider จริงผ่าน golden cases; M9/M11 live ไม่ถดถอย (M11 3 รอบเพราะแก้ skill); ไม่อ้าง filesystem isolation ของ MCP/runtime state | `check_m14_standalone_t1.py` (ไม่มี binary/key → 2; binary ไม่ใช่คู่ T1 → 1) |
| M13 | thClaws host + bots layout, scoped adoption (เพิ่ม 2026-10-27) | DESIGN §4 "Host + bots layout" (ข้อเท็จจริง `d452ece` + Atlas `daa0f49`) และกฎ deployment ใน §8/§9; `forge/live_test.py::isolated_env` (`THCLAWS_AUTO_MIGRATE=0`) + `check_layout` ทุกจุดที่สตาร์ท `--serve` (M6/M7b/M8/M11) และ `request()` ที่เก็บ events ทันที/ตรวจ stall; `builder/` เป็น host workspace v4 (agent ที่ `.thclaws/bots/main/`, runner ผ่าน `"$THCLAWS_AGENT_DIR/.thclaws/scripts/build.py"`, `build.py` หา checkout เอง + `--list-packs`); export registration, deployment hints, docs บอก operator ตั้ง env และห้ามเปิด package ใน desktop; gate M11 live เป็นสองเทิร์น (`weather-request.md` ไม่ยืนยัน → `weather-confirm.md` ใน `session_id` เดิม), `--model` บังคับ, `--rounds`, evidence ต่อรอบ | deployment contract และ output ของ generator คง single-agent layout (ไม่มี claim ใหม่ใน guarantee matrix); harness fail ชัดเมื่อ tree ถูกแปลง (negative case ใน M6, control case บน desktop build); `thclaws agent validate` ผ่านที่ agent dir; daemon ที่สตาร์ทจาก bot dir ด้วย `THCLAWS_WORKSPACE_ROOT` รายงาน `.thclaws/bots/main` พร้อม skills ครบ (ทุก build) และ desktop build เปิด `builder/` เป็น host แล้วไฟล์ที่ track ไม่เปลี่ยน; M7b `--live` ผ่าน 3 รอบบน harness ใหม่; M0–M12 ไม่ถดถอย | `check_m13_host_layout.py` (ไม่มี binary → exit 2; binary CLI-only ไม่มี `thclaws bots` → ส่วน host skip exit 2; desktop build → 0) |

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

M8 เสร็จ 2026-09-08: `forge/atlas_export.py` + `forge export <package> --out <dir> --base-url <url> --workspace-dir <path>`
(optional `--worker-id` / `--workspace-id` / `--workspace-key`) เขียน `atlas-register.json` (worker/workspace payload ไม่มี token,
`node_template` ที่ prompt render เป็น JSON เทียบเท่า input envelope ของ live audit, `collect_files` จาก `files.globs`, `outputs` + `output_format: json`
เฉพาะ `assistant_json`, `edge_template` `push_files` + `policy_template.file_handoff` เฉพาะ input `atlas_file_handoff`, deployment list
และ `package_status` ที่คัดลอกจาก build report), `atlas-workflow.json` เฉพาะ target `atlas-workflow` (T2 = fragment ของ M7b ที่ bind
worker/workspace id แล้ว; T0/T1 = node เดียว) และ archive จาก `thclaws agent pack` พร้อม sidecar `.sha256`. Schema ของ Atlas pin ไว้ที่
`patterns/atlas-workflow-definition.schema.json` จาก ref `daa0f4966899327fb563501435f5d6180eeef9c3` และบันทึก contract ใน DESIGN §8.
`check_m8_export.py` ผ่าน exit 0 บน thClaws v0.116.0 revision `75edc48` กับ Atlas checkout ref เดียวกัน: export ซ้ำได้ byte เดียวกัน
(รวม archive) และไฟล์ JSON เท่ากันหลัง generate ใหม่, template ผ่าน `$defs` ของ schema ที่ pin, T2 ไม่เหลือ placeholder, refusal ครบ
(ปลายทางมีอยู่แล้ว, base_url/workspace_dir/id ผิด, package drift, report stale หรืออ้าง candidate โดยไม่มี live evidence,
`atlas-workflow` ที่มี file handoff), archive มี inventory ครบและ sha ตรง, Atlas in-process ยอมรับ worker → workspace → workflow
ทั้งสามรูป (templates ที่ประกอบต่อ upstream node, T0 node เดียว, T2 gate flow), `render_prompt` ของ Atlas คืน envelope ตรง golden
case และ fail closed เมื่อ input ขาด, และ `POST /api/workers/<id>/poll` เห็น daemon ของ package publisher `online` พร้อม
`mcp_servers` ตรงที่ประกาศ. ไม่มี `thclaws` → exit 2 (ไม่มี archive/daemon probe); ไม่มี checkout Atlas → exit 2.
ข้อจำกัด: ไม่ export `interface` v1 ของ Atlas (profile ไม่มี `pattern`), `atlas-workflow` node เดียวรับ push_files ไม่ได้จึง reject,
`deployment_verified` เป็น `false` เสมอและ export ไม่เลื่อนสถานะ; ไม่ได้ deploy จริง, ไม่ตรวจ egress/isolation/approval,
`human_approval` ยัง `Not verified`. Refactor `forge/generate.py` (`input_envelope`, `worker_outputs`) ทำให้ T2 fragment ใช้
output name จาก spec แทนค่าคงที่ `publication`; package publisher ยังเหมือนเดิมทุก byte. Regression M0–M5/M7a/M7b native, M6 offline
(ไม่มี key → exit 2) และ Ruff ผ่าน; ไม่มี commit/push. ถัดไป M9.

M8 review follow-up 2026-09-09: review พบว่า export รับ package ที่ยังไม่ audit หรือ report บันทึก manifest fail แล้ว pack ด้วย exit 0,
`{input.<name>}` ที่ชื่อมี `-`/ขึ้นต้นด้วยตัวเลขหรือ schema เป็น scalar ทำให้ Atlas render prompt ผิดโดยไม่ error, `--out` ใน package
ทำให้ archive มี staging directory, base_url ตัวพิมพ์ใหญ่/port ผิด/`#` ผ่าน, T2 binding แทนค่าแบบ text สองรอบ, และเอกสารไม่บันทึกว่า
`thclaws agent pack` เปลี่ยน `manifest.json`/`.thclaws/settings.json`. แก้: export ต้องมี `audit.static = passed` และ `manifest != failed`
(มี binary แต่ report ยัง skipped → validate ใหม่ก่อน pack), semantic check ของ AgentSpec (M1) reject Atlas spec ที่ชื่อ input/`assistant_json` output หรือ schema ของ input ที่ Atlas render ไม่ได้ (reviewer ยืนยันกับ Atlas จริงว่า render เป็น literal และ `str()` โดยไม่ fail closed),
reject `--out` ใน package, normalize/ตรวจ base_url เข้มขึ้น, bind T2 แบบ structural, `deployment.verify` เป็น `reachability_checks`
พร้อมประโยคว่า builder ไม่เคยตั้ง `deployment_verified`, gate ตรวจ archive byte ต่อ byte ยกเว้นสองไฟล์ที่ pack เขียนใหม่, start daemon
จาก tree ที่แตกจาก archive, fail เมื่อ checkout ของ Atlas ไม่ใช่ ref ที่ pin, และสแกนคำต้องห้าม (`read-only` ด้วย) เฉพาะข้อความที่ builder
เขียน. DESIGN §8 บันทึกพฤติกรรม pack ที่ revision `75edc48`, กฎ `_FIELD_RE`/`_prompt_value`, การ upsert worker ด้วย `base_url` และ
ขอบเขตของ `missing_input`. `check_m8_export.py` ผ่าน exit 0 (native) / exit 2 (offline), M0–M5/M7a/M7b native exit 0, M6 offline exit 2,
Ruff และ `git diff --check` ผ่าน; ไม่มี commit/push. ข้อจำกัดเดิม (ไม่ deploy จริง, `deployment_verified` false, `human_approval`
`Not verified`) ไม่เปลี่ยน. รอบ 2 (2026-09-09): reviewer ยืนยันว่า `thclaws agent pack` ตัดไฟล์ที่ path มี `_secret` หรือลงท้าย
`.log`/`.key`/`.env` เงียบ ๆ (`client_secret.py` หายจาก archive โดย export ไม่ error) → `pack_archive` เปิด archive ตรวจ inventory
เทียบ `generated_files` และ byte เทียบ package (ยกเว้นสองไฟล์ที่ pack เขียนใหม่) แล้ว fail; gate M8 เพิ่ม negative case input
`api_secret` (มี binary) และ input ไม่มี schema; base_url reject port 0; ข้อความ reject input ที่ไม่มี schema แยกจากกรณี type ผิด.
Gate ทุกตัวผ่านเหมือนเดิม; ไม่มี commit/push.


M9 implementation 2026-09-09 — **ยังไม่ปิด milestone**: เพิ่ม fixture `text-reviewer-standalone`, generator
static-pipeline พร้อม role tools ที่เป็น nonempty subset, model pin, workflow planner → worker → verifier
ที่คืน result/refusal โดยตรง; `forge live-test` เลือก CLI สำหรับ standalone, แยก temporary workspace ต่อ case,
ตัด token summary และ validate branch ด้วย forge. Static audit ตรวจ role/workflow drift และคำสั่ง WorkflowRun.

`check_m9_standalone.py` offline ผ่าน; native thClaws v0.116.0 revision `75edc48` ผ่าน validate, pipeline
สาม role × สี่ synthetic cases, wrong-branch rejection, planner/verifier เรียก Write ไม่ได้, worker เรียก Bash
นอก subset ไม่ได้, เขียน `outside.txt` ไม่ได้ และเขียน `output/allowed.txt` ได้. Native provider เป็น localhost
scripted HTTP response ไม่ใช่ model จริง; ไม่ใช้ผลนี้รับรอง real-provider live audit.

พบ baseline limitation: `output/../traversal.txt` เขียนนอก output ได้ เพราะ writePaths match raw path;
บันทึกใน DESIGN และ matrix คง `output_write_scope: Declared`. DoD เรื่อง complete output write boundary
ยังไม่รองรับบน upstream รุ่นนี้; ไม่ patch runtime ใน builder หรืออ้าง guarantee เกินหลักฐาน.

Gate M9 จบ exit 2 เพราะยังไม่ได้รัน provider จริง. Automatic approval review ปฏิเสธการใช้ credential จาก
`.env` ส่ง synthetic golden prompts ไป provider ภายนอกโดยไม่มีอนุมัติปลายทาง/ข้อมูลเฉพาะ; ต้องได้รับอนุมัติก่อนรัน
ด้วย model ที่เลือก (fixture pin gpt-5.4; model override ไม่เปลี่ยน source). Regression M0–M5/M7a/M7b/M8 native
ผ่าน exit 0; M6 offline ผ่านและ live skip exit 2. ยังไม่มี commit/push ของ M9.


M9 authorized live follow-up 2026-09-09: ผู้ใช้อนุมัติการใช้ credential เดิมส่งสี่ synthetic golden cases
ไป `https://aigateway.ntictsolution.com` ด้วย `oai/gpt-5.4-mini` แล้ว. รัน `check_m9_standalone.py --model oai/gpt-5.4-mini --provider-key-env OPENAI_COMPAT_API_KEY` ผ่าน **exit 0**: offline/native checks ผ่าน,
provider จริงผ่าน normal / missing-input / refusal / malformed ครบ 4/4 และทุก case มีสาม subagent turns.
Report ของ package ทดสอบเป็น `candidate` และผ่าน report schema; evidence ที่ redact แล้วอยู่ใน local ignored
`out/m9-live/report.json` พร้อม `summary.json`. ผลนี้รับรองเฉพาะ model override ของรอบนี้; fixture ต้นฉบับ
ยัง pin `gpt-5.4` และไม่ใช้ผลนี้อ้างว่า model นั้นผ่าน. ข้อรออนุมัติ/provider live ปิดแล้ว.

M9 ยังไม่ปิดข้อกำหนด complete output write boundary: upstream `75edc48` ยังคงยอมรับ `output/../`;
`output_write_scope` เป็น `Declared` ตามเดิม. Gate exit 0 ไม่ได้แปลว่าแก้ช่องโหว่ upstream แล้ว.
ยังไม่มี commit/push ของ M9.


M9 T0 ส่งมอบ 2026-09-09 — **ปิดเฉพาะขอบเขต T0 ที่ผู้ใช้อนุมัติ**: generator รองรับ static-pipeline T0
และ reject T1 ก่อนสร้าง directory. Legacy T1 package ถูก static/live audit block ด้วย render เดียวกัน.
ทุก role ใช้ nonempty Read/Grep/Glob หรือ MCP ที่ pack ประกาศ nonmutating; unknown และ tools ที่อาจเขียน/เรียก
orchestration อื่นถูก reject. T0 ไม่มี writePaths; no_file_writes ยัง Not guaranteed เพราะ runtime state/MCP.
เพิ่ม compatibility เฉพาะ standalone T0 สำหรับ thClaws 0.121.0; Atlas/export ยัง baseline 0.116.0.

ผลรอบนี้บน native thClaws 0.121.0 revision `35ccf16`: M9 T0 gate **exit 0**, native pipeline + denied
Write/Edit/Bash ครบ 3 roles ผ่าน และ provider จริง `oai/gpt-5.4-mini` ผ่านทั้ง 4 golden cases; report candidate.
Evidence ที่ redact แล้วอยู่ local ignored `out/m9-t0-0121/report.json` และ `summary.json` (ไม่ใช้ผลนี้อ้าง
ว่า source fixture ที่ pin gpt-5.4 ผ่าน provider นั้น). Ruff และ git diff --check ผ่าน.
Regression M0–M3 exit 0; M4–M8 มีส่วน offline ที่ยังผ่าน แต่เมื่อมี binary 0.121.0 บน PATH ต้อง fail exit 1
เพราะ version gate ของ Atlas/export ยังรับรองเฉพาะ 0.116.0; exit 2 เกิดเฉพาะเมื่อไม่มี binary ให้รันส่วน native.
ไม่ปลด version gate ของ Atlas เพื่อให้ทดสอบผ่าน.

งานค้างที่ปิดการใช้งาน: **M9 T1 write boundary**. `check_m9_standalone.py --write-boundary` คืน **exit 1**
บน `35ccf16` เพราะ traversal และ symlink หลุดได้จริง ขณะที่ direct out-of-scope write ถูกปฏิเสธและ positive
control ผ่าน. นี่คือ blocker ของ T1 ไม่ใช่ failure ของ T0 ที่ส่งมอบ. ก่อนปลด T1 ต้องตรวจ upstream fix,
ให้ diagnostic ปฏิเสธทั้งสองกรณี, review ขอบเขตและแก้ DESIGN/guard อย่าง explicit. ถัดไป M10 ทำต่อได้.
ยังไม่มี commit/push ของ M9.

M9 review follow-up 2026-09-10: ปิด audit bypass ที่เพิ่ม role/workflow ใหม่ใต้ `.thclaws/agents/` หรือ
`.thclaws/agent_workflow/` แล้ว static audit ยังผ่าน; inventory ตอนนี้จับไฟล์เพิ่มทั้งสองที่ทุก target และ gate
M9 มี regression ทั้ง `rogue.md` และ `evil.js`. แก้ `ignorable()` ให้ตรงกับสิ่งที่ `thclaws agent pack` strip จริง:
ignore เฉพาะ `.thclaws/state/`, `.git/`, root `__pycache__/` และ `*.pyc`; nested `__pycache__` payload,
`.thclaws/skills/__pycache__/SKILL.md` และ `.DS_Store` เป็น finding. Standalone `AGENTS.md` ไม่ถือว่า editable
หลัง generate เพราะ role files ฝัง snapshot ของ contract; ต้องแก้ spec/template แล้ว regenerate. Native M9 T0 บน
0.121.0 `35ccf16` ยังผ่าน denial 9/9; `--write-boundary` ยัง exit 1 ตาม T1 blocker เดิม.

M9 review follow-up 2 (2026-09-10): `.thclaws/state/todos.md` ถูก thClaws โหลดเข้า system prompt จาก CWD
ก่อน run จึงไม่ใช่ state ที่ source package จะ ignore ได้ แม้ pack จะ strip prefix นี้. Static audit ตอนนี้จับ
`todos.md` เฉพาะไฟล์นี้แต่ยัง ignore run-state อื่นใต้ `.thclaws/state/`; Atlas live audit copy ตัด `state`
เหมือน standalone เพื่อไม่ยก source state เข้า candidate run. แก้ `studio.py` docstring ให้ไม่บอกว่า
standalone แก้ `AGENTS.md` ได้หลัง generate.

M9 review follow-up 3 (2026-09-10): ปิด false positive ที่เกิดจาก follow-up 2. `TodoWrite` อยู่ใน tool catalog และ spec Atlas ประกาศได้แม้ `write_scope: none` แต่ `todo.rs` เขียน `.thclaws/state/todos.md` relative to CWD การรัน daemon ตาม deployment hint (CWD=package) จึงทิ้งไฟล์ที่ static audit reject โดยที่ operator แก้ตาม ข้อความ finding ไม่ได้. Semantic validation ตอนนี้ reject built-in ที่เขียนใต้ `.thclaws/` (`TodoWrite`, `Memory*`, `Kms*` writers, `GuiShell*`) เมื่อ `write_scope` ไม่ใช่ `workspace`; gate M1 มี regression ครบทุก tool ทั้ง scope `none` และ `output`. `forge/live_test.py` รวม ignore set ของ copytree ทั้งสองที่เป็น `COPY_IGNORE` เดียว จึง drift ไม่ได้ และ gate M6 offline ตรวจว่า source `state/` ไม่เข้า run directory (เดิมไม่มี gate ไหนจับเลยเพราะ M6 native ยังติด version gate 0.121.0). DESIGN §4 ระบุ version/revision ของ claim `build_todos_reminder` แล้ว. ไม่แตะสอง low items เดิม: role binding ของ `Provider.records == 12` และ MCP subset runtime evidence.

M9 review follow-up 4 (2026-09-10): `ignorable()` จับชื่อ `todos.md` แบบ case-insensitive เฉพาะ basename เพื่อปิดช่องบน filesystem แบบ case-insensitive โดยยังให้ `.thclaws/State/` fail closed. `COPY_IGNORE` เปลี่ยนจาก name match เป็น parent-path match จึงตัดเฉพาะ `.thclaws/state/` และไม่ตัด skill ที่ชื่อ `state`; M5/M6 มี regression ของทั้งสองขอบเขต.

M9 compatibility follow-up 2026-09-16: ตรวจ thClaws `0.129.0` revision `7a27129` จาก source/binary จริง.
เพิ่ม standalone T0 compatibility แบบ pin ทั้ง version และ revision; M9 T0 native pipeline, validate และ denial
probes ผ่าน. `--write-boundary` ปฏิเสธ `..` และ symlink ที่มี target แล้ว แต่พบว่า final dangling symlink
ยังเขียนนอก `output/` ได้ จึงคง T1 disabled และ `output_write_scope: Declared` ตาม DESIGN; ห้ามเลื่อนสถานะจาก
diagnostic นี้จน upstream แก้ edge case ดังกล่าว.

M10 เสร็จ 2026-10-26: `forge pack new <name>` scaffold (`pack.yaml` v2 ที่ทุกจุดตัดสินเป็น `TODO`, `fixture/setup.py`,
README, โฟลเดอร์ `scripts/`), `forge pack discover --name <server> --command <cmd> [--env NAME] [--out <file>] --args ...`
ร่าง `pack.yaml` จาก `initialize` → `tools/list` ของ server จริงด้วย env สะอาด (tools ครบ, inputSchema/description เป็น comment,
digest ของ surface, `mutating` เป็นข้อเสนอจาก annotations) โดยปล่อย tier/network/fixture เป็น `TODO`; `forge pack test` รับ
`--packs-dir`. ทั้ง scaffold และร่างตั้งใจให้ `forge pack test` fail พร้อมเหตุผล ("invalid min_tier or network") จนผู้เขียนเติม.
Harness รายงานเหตุผลเมื่อ server ต้องการ env ที่ไม่ได้ประกาศ. เพิ่ม `docs/packs.md`, skill `builder/.thclaws/skills/pack-author/SKILL.md`
(ที่ M11 จะใช้ต่อ) และ reference pack `fixtures/packs/echo/` (stdlib stdio MCP สอง tool, ไม่มี env/network, พร้อม `spec.yaml` ที่ใช้ pack นี้).
`check_m10_pack_authoring.py` ผ่าน: scaffold → implement echo → conformant แบบ deterministic; discover → เติม → conformant;
annotations destructive ถูกเสนอเป็น `mutating` และ T0 ที่คงข้อเสนอนั้นถูก reject; server ที่อ่าน env นอกประกาศ, tool เกิน/ขาด,
tools/list แบ่งหน้า, negative case ที่ server ไม่ปฏิเสธ, ชื่อ server/tool นอก charset ถูก fail/reject พร้อมเหตุผล; CLI exit code
0/1 และปฏิเสธเขียนทับ; spec echo ได้ `.thclaws/mcp.json` ที่มีแค่ command/args, static audit ผ่าน, dependency `conformant`,
conformance ที่ลบ → `assets_bundled`, ที่ failed → static fail. ไม่ใช้ provider key.

สภาพแวดล้อมรอบนี้ (cloud container): build thClaws จาก GitHub `kaebmoo/thClaws` tag `v0.116.0` = commit `b962abc` (placeholder
`frontend/dist/index.html` สำหรับ CLI-only build). Revision ที่ gate pin ไว้ (`75edc48`, `35ccf16`, `7a27129`) **ไม่อยู่ใน history
บน GitHub** (643 commits) จึงเป็น commit ใน checkout ของผู้ดูแลเท่านั้น. ผลด้วย binary นี้: M10 **exit 0** (`echo-reader` เป็น `draft`
บน `thclaws 0.116.0 revision: b962abc`), M4/M5/M7b native exit 0, M8 archive ผ่านแต่ skip Atlas (exit 2), M7a/M6 skip เพราะไม่มี
checkout AI (exit 2), M9 **exit 1 ตามที่ออกแบบ** (revision ไม่ใช่คู่ที่รับรอง; fail closed ไม่ใช่ skip) — ต้องรัน M9 บนเครื่องที่มี
revision ที่ pin หรือ push commit เหล่านั้นขึ้น GitHub แล้วตรวจ DESIGN §4 ใหม่ก่อนเพิ่มคู่ `0.116.0/b962abc`. Ruff 0.16.9 และ
`git diff --check` ผ่าน; ไม่มี commit/push ใน session นี้จนผู้ใช้สั่ง. ถัดไป M11.

M10 macOS follow-up 2026-10-26: ผู้ใช้รัน gate บน Mac แล้ว M10 fail ด้วย `PermissionError` ที่ case server อ่าน env นอกประกาศ.
สาเหตุ: server ตายก่อนอ่าน stdin → `write` ได้ `BrokenPipeError` แล้ว `os.killpg` บน process group ที่เหลือแต่ zombie คืน `EPERM`
บน macOS ทับ exception เดิม. แก้ `forge/pack_test.py` ให้ `send` แปลง OSError เป็นข้อความ `MCP exited before replying; ...` เดียวกันทุก platform
และให้ `close` (รวม `forge/live_test.py` สองจุด) ทน `PermissionError` แล้ว fallback เป็น `Popen.kill`. ยังไม่มี Mac ใน session นี้ให้ยืนยันซ้ำ;
ผู้ใช้ต้องรัน `check_m10_pack_authoring.py` บน Mac อีกครั้ง. ผล M9 บน Mac: binary เป็น `0.138.0` revision `d452ece` (ไม่อยู่บน GitHub เช่นกัน)
จึง fail closed ตามออกแบบ; จะรองรับรุ่นนี้ต้องตรวจ DESIGN §4 (writePaths/traversal/symlink, todos.md, MCP naming) กับ source รุ่นนั้นก่อนเพิ่มคู่ version+revision.

M11 เสร็จ 2026-10-26 (offline + native): workspace `builder/` (AGENTS.md ที่มี Mission/Interview/Must/Refuse/Output, manifest `sandboxed`,
settings identity, skills `spec-author` + `pack-author`, ไม่มี subagent/workflow/MCP) ผ่าน `thclaws agent validate` บน `0.116.0/b962abc`.
runner `.thclaws/scripts/build.py` บังคับ `confirmed_by` และรัน `forge pack test` → `forge generate` → `forge audit --write` แล้วพิมพ์ผลจาก report
(DESIGN §9). Fixture `fixtures/builder/weather-spec.yaml` (T2/atlas-workflow เพราะ network allowlist, pack `google-weather`) ผ่าน forge generate
และ report บอก `google-weather: missing`; `fixtures/builder/weather-request.md` คือ prompt ที่ตอบ 7 ข้อครบสำหรับ live. `check_m11_builder_agent.py`
offline ผ่าน: layout/ข้อห้าม/7 คำถาม, forge ปฏิเสธ marker ที่ไม่ถูกตัด, build.py ปฏิเสธ spec ที่ไม่มี/ไม่ครบ/ค่าสงวน marker โดยไม่สร้างไฟล์,
strip marker ออกจาก `agentspec.json`, rebuild `--replace` ได้ byte เดิมและไม่ลบ directory ที่ไม่ใช่ของ runner, spec ผิดกฎ M1 fail ที่ forge,
checkout หายรายงานชัด, echo pack ผ่าน runner เป็น `conformant` และ `draft` เมื่อมี binary. **Live ยังไม่รัน**: ไม่มี provider key ใน session นี้
→ exit 2 ประกาศชัด; ผู้ใช้รัน `python3 scripts/check_m11_builder_agent.py --model oai/gpt-5.4-mini --provider-key-env OPENAI_COMPAT_API_KEY`
บนเครื่องที่มี thclaws 0.116.0 และ key (gate ตรวจว่า model เขียน spec ที่ forge รับ, เรียก build.py ผ่าน Bash จริง, report บอก missing และข้อความสรุป
ไม่อ้าง candidate/shippable/deploy-ready; evidence redact ลง `out/m11-live/report.json`). ผล live ไปใน DoD ของ M11 รอบถัดไป.
Regression: M0–M3 exit 0; M4/M5/M7b/M10 native exit 0; M8 archive ผ่าน skip Atlas; M6/M7a skip (ไม่มี AI checkout); M9 exit 1 กับ `b962abc`
ตามออกแบบ; โหมด CI ไม่มี binary ทุก gate เป็น 0/2. Ruff และ `git diff --check` ผ่าน.

M12 เสร็จ 2026-10-27 (ยกเว้นส่วน live): ผู้ดูแล push `d452ece` เป็น branch `baseline-0.138.0-d452ece` (tree `bd085f15` ตรง binary บน Mac,
= `v0.138.0` + merge upstream). ตรวจ diff `b962abc → d452ece` ทุกไฟล์ที่ DESIGN อ้าง และบันทึกใน §4 "Baseline 0.138.0 / d452ece":
ไม่เปลี่ยน — API/SSE ของ `/agent/run`, `/v1/agent/info`, validate/pack fuse, scaffold, MCP config/naming/env/instructions, system.md override,
todos reminder, CLI workflow summary, `oai/` provider; เปลี่ยน — (1) `/agent/run` ใช้ `apply_filter(allowedTools/disallowedTools)` จาก
project settings ได้แล้ว (ยังไม่ adopt; `tool_allowlist` คง Declared) (2) `KmsEdit` เพิ่ม → catalog 69 tools (3) writePaths resolve
ปลายทางผ่าน `sandbox::resolve_landing` → `--write-boundary` ผ่าน 5/5 เป็นครั้งแรก แต่ T1 ยังปิดรอ review (4) `agent pack` strip เพิ่ม
`.thclaws/bots/`, `.home/`, `.playwright-mcp/`, `media-jobs.jsonl` (5) browserEnabled template off/AppConfig default on, org policy
denied tools, retired-model repoint (deepseek เท่านั้น) (6) MCP text cap 256 KiB และ spill ลง `.thclaws/state/tool-output/`.
โค้ด: `THCLAWS_BASELINE = "0.138.0"`, คู่ standalone เพิ่ม `0.138.0/d452ece` (คงสามคู่เดิม), `patterns/tools-0.138.0.json`,
gate M6 ใช้ baseline จาก forge แทน literal, comments/docs. ผลบน build จาก `d452ece` (CLI-only, placeholder frontend): M0–M5, M7b, M10
**exit 0** (M4 scaffold compat สาม pattern + validate; M5/M7b/M10 `draft`), M8 archive ผ่าน/Atlas skip, M9 T0 pipeline + denial 9/9
ผ่าน/provider skip, M11 validate ผ่าน/live skip, M6/M7a skip (ไม่มี `../AI`), `check_m12_baseline.py` **exit 0**
(`thclaws 0.138.0 revision d452ece`, builder validates, write-boundary 5/5). โหมด CI ไม่มี binary ทุก gate 0/2; Ruff/`git diff --check` ผ่าน.
ค้าง: live audit ทุกตัวบน baseline ใหม่ (M6 SQL, M7b publisher 3 รอบ, M9 provider, M11 interview) ต้องรันบน Mac ด้วย key —
evidence `candidate` เดิมเป็นของ 0.116.0 ทั้งหมด; `docs/local-test-plan.md` ปรับให้ใช้ binary 0.138.0 ของเครื่องแล้ว.
ข้อเสนอ milestone ถัดไป (เลขเลื่อนเมื่อเพิ่ม M13): M14 ปลด T1 standalone (ทำแล้ว 2026-10-28 ดูย่อหน้า "M14 ทำใน cloud"),
M15 adopt `allowedTools` ใน `.thclaws/settings.json` เพื่อยก `tool_allowlist` บน `/agent/run` จาก Declared เป็น Enforced พร้อม native proof
(ต้องรับข้อค้นพบ M13 ว่า `apply_filter` ถูกเรียกก่อนลงทะเบียน Skill/MCP tools จึงไม่กรองสองกลุ่มนั้น), และ M16 (มีเงื่อนไข) host deployment สำหรับ
package ที่มีเฉพาะ `prompt_json`/`assistant_json` หลังพิสูจน์บน Mac ตาม DESIGN §4 การตัดสินใจ (e).

ผล live บน Mac 2026-10-27 (checkout `0837eef`, binary 0.138.0 `d452ece` desktop build, `oai/gpt-5.4-mini`; รายละเอียดใน `docs/thclaws-host-bots-brief.md`):
M9 live exit 0 และ `--write-boundary` exit 0; M7b `--live` fail 3/3 ตามคำสั่งในแผน แต่ผ่าน 4/4 เมื่อตั้ง `THCLAWS_AUTO_MIGRATE=0` ผ่าน launcher นอก repo;
M11 live fail ทุกรอบ — ไม่ตั้ง env: `--serve` แปลง copy ของ `builder/` เป็น host workspace จึงไม่เห็น AGENTS.md/skills; ตั้ง env: model ไม่ถึง build.py เพราะ
workspace ไม่มี `packs/` (`ls packs` ENOENT), กฎยืนยันขัดกับ request ที่ยืนยันล่วงหน้าในเทิร์นเดียว, stall ของ provider ที่ harness เก็บ events ไม่ได้เมื่อ timeout;
M8 ผ่านเมื่อ `ATLAS_ROOT` ชี้ worktree ที่ pin `daa0f49`; M6 live/M7a ยังไม่ได้รัน (venv ไม่มี `mcp`, `../AI` อยู่ที่ `aae9456` ไม่ใช่ `d95dc28`); M10 macOS exit 0.

M13 ทำใน cloud 2026-10-27 (branch `claude/adoring-feynman-kqs8us`): ตรวจ source `d452ece` (clone branch `baseline-0.138.0-d452ece`) และ Atlas `daa0f49` แล้วบันทึก
DESIGN §4 "Host + bots layout" (12 ข้อ + การตัดสินใจ scoped adoption), §8 กฎ deployment, §9 layout ของ builder; โค้ด: `isolated_env`/`check_layout`/`request(sink, idle_timeout)`
ใน `forge/live_test.py` และใช้ใน M6/M7b/M8/M11, M6 negative case `host=True`, `builder/` → v4 (`git mv` agent ไป `.thclaws/bots/main/`, `bots.json`, host settings),
`build.py` walk-up + `--list-packs`, AGENTS.md/skills/README ใช้ `$THCLAWS_AGENT_DIR` และกฎยืนยันเทิร์นแยก, fixture `weather-request.md` ตัดคำยืนยัน + `weather-confirm.md`,
gate M11 สองเทิร์น/`--rounds`/`--model` บังคับ/`--turn-timeout`/`--idle-timeout`/`--via-host`, gate M12 validate agent dir, deployment hint และ registration text, docs,
gate ใหม่ `check_m13_host_layout.py`. Build thClaws CLI-only จาก branch baseline ใน cloud ได้ (`thclaws 0.138.0` revision `d452ece`; ต้องติดตั้ง `libdbus-1-dev` ให้ crate
`keyring`; ไม่มี GTK/WebKit จึงไม่มี feature `gui`) แต่พบว่า build นี้**ไม่มี `--serve`** (DESIGN §4 host + bots layout ข้อ 13) จึงให้ native evidence ได้เฉพาะ validate/pack/
`agent run`/write-boundary; gate M11/M13 ตรวจและประกาศ skip เมื่อ `--serve` ใช้ไม่ได้.
ผลใน cloud 2026-10-27: CI mode (ไม่มี binary) M0–M3 exit 0, M4–M13 exit 2 ไม่มี 1; native ด้วย binary CLI-only จาก `d452ece`: M0–M5, M7b, M10, M12 exit 0
(M12 = validate agent dir ใหม่ + write-boundary 5/5), M11 exit 2 (offline + validate `.thclaws/bots/main` ผ่าน, live skip ไม่มี key), M13 exit 2 (offline สามส่วนผ่าน
+ validate ผ่าน, ส่วน daemon skip เพราะ `--serve` ไม่มีใน build นี้), M6/M7a/M8/M9 exit 2 ตามเหตุเดิม; Ruff และ `git diff --check` ผ่าน.
**ค้างบน Mac (desktop build + key, session แยกตาม `docs/local-test-plan.md`)**: M13 native exit 0 (control migration, host เปิด `builder/`), M7b `--live` ×3,
M11 live `--rounds 3`, M8 กับ `ATLAS_ROOT` ที่ pin, optional M6/M7a; เมื่อผ่านครบจึงบันทึก "M12 live"/"M11 live"/"M13 เสร็จ" และ CHANGELOG.

M13 เสร็จ (Mac) 2026-10-28 (session บน Mac เริ่มที่ checkout `f5382ee`, branch `claude/adoring-feynman-kqs8us`; binary thclaws `0.138.0` revision `d452ece`
desktop build — `thclaws --help` มี `bots`; model `oai/gpt-5.4-mini` ผ่าน MATCHA `OPENAI_COMPAT_*`; evidence redact แล้วอยู่ที่ `out/m13-evidence/` ซึ่ง gitignored;
ตรวจแล้วว่า log/evidence ทั้งหมดไม่มีค่าจาก `.env`). ระหว่างรันพบห้าเรื่องและแก้ตามที่ผู้ใช้อนุมัติทีละข้อ (ไม่แตะ pin, เกณฑ์ audit, golden cases หรือ fixture):
(1) M13 native exit 1 "serving a v2 package with THCLAWS_AUTO_MIGRATE=0 changed files outside runtime state: ['.thclaws/settings.json']" — thClaws stamp
`workspaceVersion: 2` ทุกครั้งที่เปิด project (`ProjectConfig::migrate_workspace_if_needed`, ไม่ขึ้นกับ flag, มีตั้งแต่ 0.116.0); กรณี agent ของ builder และ host
ได้ไฟล์เดียวกันที่ `.thclaws/bots/main/.thclaws/settings.json` → บันทึก DESIGN §4 "Host + bots layout" ข้อ 14 และ §8 ก่อนแก้โค้ด, generator เขียน
`"workspaceVersion": 2` ใน `.thclaws/settings.json` ทุก package, agent ของ `builder/` ประกาศเหมือนกัน; ไม่เพิ่ม path ใดเป็น runtime state ใน gate.
(2) M11 live: model เขียน `permissions.network` เป็น object และซ้อน `routing` ใต้ `target` → skill `spec-author` บอกชนิดและ top-level shape.
(3) gate M11 นับ "blocks shippable" ใน finding ของ forge ที่ model คัดลอกตามที่ skill สั่งเป็นคำอ้าง → gate สแกน `FORBIDDEN_CLAIMS` เฉพาะคำของ model
(`own_words`: ตัดบรรทัดที่คัดลอกทั้งบรรทัดจากผลของ build.py call) พร้อม offline self-check และ DESIGN §9. (4) model เรียก `AskUserQuestion` ในเทิร์นแรกแล้วค้างจน
`case deadline exceeded` → settings ของ agent `"disallowedTools": ["AskUserQuestion"]` (ยืนยันด้วย stub provider: `/agent/run` ส่ง 42 tools ไม่มี
`AskUserQuestion`, ไม่ตั้งได้ 43) และ DESIGN §9. (5) model หยุดขออนุญาตก่อนทำงาน และอ่าน "Target: Atlas" เป็น `atlas-worker` ทั้งที่ network ≠ none → AGENTS.md:
ทำงานโดยไม่ขออนุญาตแล้วหยุดเฉพาะเพื่อรอคำยืนยัน, Q2 ระบุว่า Atlas + network/T2 = `atlas-workflow`, และเมื่อ build.py fail ด้วยกฎที่ skill กำหนดโดยไม่มี confirmed block
เปลี่ยนให้แก้ spec แล้ว rebuild ในเทิร์นเดียวกัน.
ผล (tree สุดท้ายของ commit นี้):
CI mode: m0=0 m1=0 m2=0 m3=0 m4=2 m5=2 m6=1 m7a=1 m7b=2 m8=1 m9=2 m10=2 m11=2 m12=2 m13=2
native mode: m0=0 m1=0 m2=0 m3=0 m4=0 m5=0 m6=1 m7a=1 m7b=0 m8=1 m9=2 m10=0 m11=2 m12=0 m13=0
m6/m7a=1 ทั้งสองโหมดเพราะติดตั้ง `mcp` แล้ว gate ไม่ skip แต่ fail closed ที่ "SQL source revision differs from source.ref" (`../AI` อยู่ที่ `e79ee5f` ไม่ใช่ `d95dc28`);
m8=1 เพราะ default `../atlas-control-plane` อยู่ที่ `375ac85` ไม่ใช่ `daa0f49` — ทั้งสามเป็น pin ของ checkout ข้างเคียงบนเครื่องนี้ ไม่ใช่ regression (ผู้ใช้รับทราบ).
M13 native: exit=0 (control migration เกิด bots.json: yes; host เปิด builder/ แล้วไฟล์ที่ track เปลี่ยน: none หลังแก้ข้อ 1).
M7b live: exit=0 x3 (`candidate`, 4/4 golden cases ต่อรอบ, harness ตั้ง `THCLAWS_AUTO_MIGRATE=0` เองโดยไม่ใช้ launcher นอก repo), evidence `out/m13-evidence/m7b-live/`.
M9 live: exit=0 write-boundary: exit=0 (รันซ้ำเพราะ generator เปลี่ยน), evidence `out/m13-evidence/m9/`.
optional: m7a/m6 ไม่รัน (`../AI` ไม่อยู่ที่ `d95dc28`), m8=0 (`ATLAS_ROOT` = worktree ที่ pin `daa0f49`), M11 `--via-host` 1 รอบ exit=0
(`out/m13-evidence/m11-via-host/round-1-20261028T073041.json`). Ruff และ `git diff --check` ผ่าน.

M11 live 2026-10-28: exit=0 rounds passed=3/3 (`oai/gpt-5.4-mini`, binary `0.138.0`/`d452ece`, `--rounds 3` แบบ agent-dir), evidence
`out/m13-evidence/m11-live-6/round-{1,2,3}-20261028T0728*.json`; ต่อรอบ: turn1 หยุดถาม yes, turn2 build.py yes (หนึ่งครั้ง), draft `target: atlas-workflow` ตั้งแต่แรก,
รายงาน `unverified` และ `google-weather: missing` ตาม report. ก่อนผ่านมี 5 run ที่ fail ตามลำดับ (evidence `out/m13-evidence/m11-live*/`): spec shape, gate false positive,
`AskUserQuestion` ค้าง, 2/3 (รอบ 3 หยุดขออนุญาต), 1/3 (รอบ 2 `atlas-worker` + T2 แล้วหยุดหลัง build fail) — แก้ตามข้อ 2–5 ข้างบน. ข้อสังเกต: ทั้งสามรอบที่ผ่านไม่ได้รัน
`--list-packs` (gate ไม่ตรวจ; pack ที่ขาดถูกประกาศเป็น `missing` ถูกต้อง). DoD ส่วน live ของ M11 ปิด.

M12 live 2026-10-28: live audit บน baseline `0.138.0`/`d452ece` ด้วย `oai/gpt-5.4-mini` — M7b publisher `candidate` 3/3, M9 provider 4/4 + denial 9/9 + write-boundary 5/5,
M11 interview 3/3; ค้างเฉพาะ M6 SQL live (ต้อง `../AI` ที่ `d95dc28`) ซึ่ง evidence `candidate` ยังเป็นของ 0.116.0. pinned `gpt-5.4` ของ fixture ยังไม่ได้ทดสอบ (MATCHA ไม่มี).

M14 ทำใน cloud 2026-10-28 (branch `claude/adoring-feynman-kqs8us`, ฐาน `4970dfe`): review `writePaths` ของ `d452ece` แล้วบันทึก DESIGN §4 "Standalone T1 rollout"
(8 ข้อเท็จจริง + การตัดสินใจ (a)–(g)), §6 แถว T1, §8 permission mapping; โค้ด: `STANDALONE_T1_THCLAWS_COMPATIBILITY`/`T1_WRITE_TOOLS`/`standalone_write_paths` ใน
`forge/generate.py`, `standalone_roles` รองรับ T1 (worker ได้ Write/Edit; planner/verifier read-only; writer อื่นและ T1 ไร้ writer ถูก reject), `role.md.j2` เขียน
`writePaths`, `AGENTS.md.j2` บอก write boundary สำหรับ T1, matrix T1 (`output_write_scope: Enforced`, `no_file_writes: Not applicable`), `forge/audit.py`
(`ignorable(path, spec)` ถือ `output/**` เป็น runtime output เฉพาะ T1, `thclaws_validate(standalone_tier=...)` ใช้ตารางคู่ตาม tier),
`forge/live_test.py` (`tree_snapshot`/`written_files`; `run_standalone` ทำ tree diff ต่อ case ทุก tier และบันทึก event `files_written`), fixture
`fixtures/note-writer-standalone/` (T1, Read/Write/Edit, เขียน `output/note.md`), gate ใหม่ `check_m14_standalone_t1.py`, M9 gate ตัด assertion "T1 is disabled"
(legacy-T1 drift ยังต้อง fail) และข้อความ `--write-boundary` เป็น diagnostic, M2 รับ fixture standalone, M12 ตรวจว่าตาราง T1 มีเฉพาะ `d452ece`, docs/README/skill `spec-author`.
ผลใน cloud: CI mode M0–M3 exit 0, M4–M14 exit 2 ไม่มี 1; native ด้วย binary CLI-only `d452ece`: M14 native **PASS** (validate ไม่มี warning writePaths; probe 14 รายการ
รวม `Edit` ภายใต้ wrapper ที่ไม่เคยตรวจมาก่อน; pipeline 4 cases เขียนเฉพาะ `output/note.md` ใน case normal และ package เป็น `candidate` ภายใต้ provider จำลอง) แล้ว skip provider;
M9 native ผ่านพร้อม tree diff (T0 ไม่เขียนไฟล์ใด). **ค้างบน Mac**: `check_m14_standalone_t1.py --model oai/gpt-5.4-mini ...` provider live, M9 live ซ้ำ, และ M11 live `--rounds 3`
(skill เปลี่ยน) — ตาม `docs/local-test-plan.md` ข้อ 4/6.

M14 เสร็จ (Mac) 2026-10-28 (checkout `63e89d7` บน `main`; binary thclaws `0.138.0` revision `d452ece` desktop build — `thclaws --help` มี `bots`; model
`oai/gpt-5.4-mini` ผ่าน MATCHA `OPENAI_COMPAT_*`; evidence อยู่ที่ `out/m14-evidence/{step1-ci,step2-native,step3-m14-provider,step4-m9-live,step5-m11-live,step6-m8-atlas}/`
ซึ่ง gitignored; ตรวจแล้วว่า log/evidence ทั้งหมดไม่มีค่า key/URL จาก `.env`). ไม่แก้โค้ด, gate, pin, เกณฑ์ audit, golden cases หรือ fixture; ไฟล์ที่ track ไม่เปลี่ยนหลังรันครบ.
CI mode: m0=0 m1=0 m2=0 m3=0 m4=2 m5=2 m6=1 m7a=1 m7b=2 m8=1 m9=2 m10=2 m11=2 m12=2 m13=2 m14=2
native mode: m0=0 m1=0 m2=0 m3=0 m4=0 m5=0 m6=1 m7a=1 m7b=0 m8=1 m9=2 m10=0 m11=2 m12=0 m13=0 m14=2
m6/m7a/m8=1 ทั้งสองโหมดด้วยเหตุเดิมของ M13 (pin ของ checkout ข้างเคียง: `../AI` ที่ `e79ee5f` ≠ `d95dc28`, `../atlas-control-plane` ที่ `375ac85` ≠ `daa0f49`) ไม่ใช่ regression.
M14 native บน macOS (`/tmp` resolve เป็น `/private/tmp`) ตรงกับ cloud: probe 14/14 (worker Write/Edit `output/allowed.txt` allowed; Write นอก output, `output/../traversal.txt`,
symlink directory, dangling symlink, Edit `outside-existing.txt`, worker Bash และ planner/verifier Write/Edit/Bash denied) และ "T1 pipeline wrote output/note.md for the normal case only".
M14 provider: exit=0 cases=4/4 — normal `files={'output': ['output/note.md'], 'outside_output': []}`, missing-input/refusal/malformed `files` ว่าง.
M9 live: exit=0 (provider 4/4, denial 9/9; tree diff ผ่านทุก case จึงยืนยันว่า T0 ไม่เขียนไฟล์ใด).
M11 live: exit=0 rounds passed=3/3 (`--rounds 3` แบบ agent-dir เพราะ skill `spec-author` เปลี่ยน), evidence `out/m11-live/round-{1,2,3}-20261028T1834*.json`
(สำเนาใน `out/m14-evidence/step5-m11-live/`); ต่อรอบ: turn1 หยุดถาม yes, turn2 build.py yes, draft `target: atlas-workflow`, รายงาน `google-weather` missing.
optional: m8=0 (`ATLAS_ROOT` = worktree ที่ pin `daa0f49`); m6/m7a ไม่รัน (`../AI` ไม่อยู่ที่ `d95dc28`). ส่วน live ของ M14 ปิด.

M6/M7a live (Mac) 2026-10-28 (checkout `dc8a2f3`; binary thclaws `0.138.0` revision `d452ece` desktop build; model `oai/gpt-5.4-mini` ผ่าน MATCHA
`OPENAI_COMPAT_*`; evidence `out/m6-m7a-evidence/{m7a_native.log,m6_live.log,pack-conformance.json}` ซึ่ง gitignored และตรวจแล้วว่าไม่มีค่าจาก `.env`).
พบสองเรื่องที่ทำให้ M6/M7a รันไม่ได้แม้มี AI ที่ `d95dc28`: (1) `packs/sql-readonly/fixture/setup.py` หา AI ที่ `parents[4]/'AI'` (sibling ของ checkout) เท่านั้น
ไม่มี env override จึงใช้ worktree ของ AI ที่อื่นตรง ๆ ไม่ได้ — ไม่แก้ fixture แต่รัน gate จากโฟลเดอร์คู่ที่มี worktree `AI`@`d95dc28` กับ `agent-builder`@`dc8a2f3`
วางข้างกัน (gate ใส่ ROOT ของตัวเองหน้า `sys.path`) แล้ว provenance `matched: True`; (2) `.venv` ได้ mcp 2.3.0 จาก extra `sql-pack = ["mcp>=1.0"]` ที่ไม่มีเพดาน
ซึ่งลบ `mcp.server.fastmcp` ที่ทั้ง `server.py` ของ pack และ `nt_query_mcp.py` ของ AI ใช้ → server ตายก่อนตอบ ("MCP exited before replying");
แก้ `pyproject.toml` เป็น `mcp>=1.26,<2` (ตาม `requirements.txt` ของ AI) และ `docs/local-test-plan.md` ข้อ 7 ให้สร้างโฟลเดอร์คู่ + venv `.[sql-pack]`.
`SQL_READONLY_DSN` ไม่ต้องตั้ง: `setup.py` สร้าง SQLite สังเคราะห์ (invoice 3 แถว) ใน temp ของ harness เอง.
ผล (venv แยกที่มี mcp 1.30.0): M7a native exit=0 (SQL conformance 11/11 cases, database ไม่เปลี่ยน, native validate และ `sql-reader` draft);
M6 live exit=0 (`package_status=candidate`) — แทน evidence SQL `candidate` ของ 0.116.0; live audit ทุกตัวบน baseline `d452ece` จึงครบ (เหลือ pinned `gpt-5.4` ของ fixture ที่ MATCHA ไม่มี).
