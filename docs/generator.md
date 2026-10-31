# Generator (M4 / M9)

สร้าง package สำหรับ Atlas `single-worker` และ standalone `static-pipeline` จาก AgentSpec ที่ผ่าน schema และ semantic checks ของ M1 ใช้ compatibility resolver ของ M3 แล้ว render แบบ deterministic โดยไม่เรียก LLM และไม่รัน script/MCP ของ pack

## ใช้งานจาก source checkout

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
forge generate fixtures/invoice-reviewer/spec.yaml --out out/invoice-reviewer
forge generate fixtures/sql-reader/spec.yaml --out out/sql-reader
```

หรือใช้ `python3 -m forge.cli generate ...` จาก repo เมื่อมี dependencies แล้ว. M4 รองรับ source checkout/editable install; wheel distribution ที่ bundle resources ยังไม่อยู่ใน milestone นี้

`--out` ต้องเป็น path ใหม่ แม้ directory เดิมจะว่างก็ไม่เขียนทับ. Generator render และตรวจ report ก่อนเขียนลง staging directory แล้ว rename ไปยังปลายทาง ไม่มี timestamp, absolute source path, UUID หรือค่า environment ในไฟล์ที่สร้าง. spec เดียวกันกับ templates/packs รุ่นเดียวกันให้ไฟล์เหมือนกันทุก byte

## ไฟล์ที่ได้

- `manifest.json` และ `.thclaws/settings.json`: identity/catalog fields ตาม thClaws v0.116.0 (ไม่เปลี่ยนถึง 0.138.0/`d452ece` M12 และ 0.146.0/`9f69995` M17). ส่งต่อให้ `thclaws agent validate` ตรวจ ไม่สร้าง manifest validator อีกชุด
- `.thclaws/settings.json` ของ target Atlas (M15) มี `allowedTools` = `permissions.tools` เรียงตามชื่อ และ `browserEnabled`/`halEnabled`/`imageToolsEnabled` = `false`: daemon ที่ start ด้วย CWD=package ถอด built-in อื่นทั้งหมดก่อนส่ง tool list ให้ model; Skill/SkillList/SkillSearch และ MCP tools ไม่ถูกกรอง (DESIGN §4 "Tool allowlist บน `/agent/run`"). Spec Atlas ที่ประกาศ HAL/media tools (`WebScrape`, `YouTubeTranscript`, `TextToImage` ฯลฯ) ถูก reject เพราะกลุ่มนี้ลงทะเบียนหลัง filter และถูก pin ปิด. Standalone ไม่มี key เหล่านี้
- `.thclaws/settings.json` ของทุก target (รวม standalone) pin `"officeApps": []`: thClaws 0.144.0 ขึ้นไปฉีด office-app MCP servers (`gridcraft`/`wordcraft`/`deckcraft`) ทุกครั้งที่โหลด config (daemon และ CLI) เมื่อพบ craft binary บน host และ `[]` เป็นค่า opt-out เดียวที่ใช้ได้ (`false` ทำให้ทั้งไฟล์ถูกทิ้ง); revision เก่าละเลย key นี้. env `THCLAWS_OFFICE_APPS` ของ operator ทับได้ และ `aiserver-rag` ไม่มี setting ระดับ package (DESIGN §4 M17 ข้อ 5 follow-up)
- `AGENTS.md` และ `.thclaws/skills/<identity.name>/SKILL.md`: mission, permission declarations, refusal และ output จาก spec
- `agentspec.json`: source contract ที่ serialize แบบ canonical JSON
- `.thclaws/schemas/inputs--<name>.json`, `outputs--<name>.json`, `refusal.json`: schema ตาม spec. collect_files ที่ไม่มี schema จะไม่สร้าง schema สมมติ
- `evaluation/golden-cases.json`: cases และ expected branch ครบจาก spec
- `builder-build-report.json` และ schema: inventory, compatibility, guarantee matrix, audit status และ deployment hints
- `audit.py` และ `studio.py`: runner บาง ๆ ของ static audit (M5) คัดลอกจาก `templates/` ทุก byte; ดู [audit.md](audit.md)
- `atlas-node-template.json` เฉพาะ T2 (M7b): requirement ของ daemon แยก และ fragment `human_gate` → worker ผ่าน choice `approve` เท่านั้น; worker/workspace เป็น placeholder ที่ operator ต้อง bind ใน deployment copy และ static audit ห้ามแก้ template ต้นฉบับ

Atlas ไม่มี subagent definition หรือ run.js ใน package. `atlas-workflow` ได้ package ของ node เดียว; M7b เพิ่ม approval fragment สำหรับ T2 และ M8 export ไฟล์ลงทะเบียนกับ flow ที่ bind แล้วผ่าน `forge export` ([export.md](export.md)); การ provision deployment ยังเป็นงานของ operator. Standalone รองรับ `static-pipeline` เท่านั้น; `batch-fanout` และ `dynamic` ยังคืน error ก่อนเขียนไฟล์

## Capability assets

M7a แทน descriptor เดิมด้วย contract v2 ตาม [DESIGN §5](DESIGN.md) ทั้งชุด ไม่รองรับ v1.
ดูตัวอย่างจริงที่ `packs/sql-readonly/pack.yaml` และ [คู่มือ SQL pack](../packs/sql-readonly/README.md);
ผู้เขียน pack ใหม่เริ่มที่ [packs.md](packs.md) (`forge pack new` / `discover` / `test`, M10) และ reference `fixtures/packs/echo/`.

`scripts` อ้างไฟล์ `.py` ใต้ `scripts/`; `skills` อ้าง `<name>/SKILL.md` ใต้ `skills/`.
ไม่รับ symlink หรือ path traversal. Asset ถูกคัดลอกเป็น `.thclaws/scripts/<pack>--<file>.py`
และ `.thclaws/skills/<pack>--<skill>/SKILL.md`; argument ที่ตรง `scripts/<file>.py`
จะถูกเปลี่ยนเป็น path ของ asset ใน package. Argument อื่นคงเดิม ไม่มี shell หรือ env interpolation.

Generator สร้าง `.thclaws/mcp.json` จาก server command/args และ `manifest.requires.mcp_servers`.
ห้าม server name ซ้ำข้าม pack. ค่า secret ไม่อยู่ใน MCP config; operator ตั้ง env ตามชื่อใน spec
แล้ว start daemon โดยใช้ CWD=package และ `THCLAWS_AUTO_MIGRATE=0` (package ต้องคงเป็น single-agent workspace; ห้ามเปิดใน thClaws desktop
หรือรัน `thclaws bots migrate` กับมัน — DESIGN §4 "Host + bots layout"). การติดตั้ง runtime, daemon isolation และ egress เป็นงาน deployment.

Spec ต้องประกาศ tools ครบทุกตัวที่ server advertise รวม built-ins ที่ pack ต้องใช้, env ครบ,
tier ไม่น้อยกว่า min_tier และ network ไม่น้อยกว่า pack. `mutating` ต้องเป็น subset ของ server tools
และใช้ T2. `params` ยังไม่รองรับเมื่อมี descriptor.

ไม่มี descriptor = `missing`; มี descriptor/assets = `assets_bundled`; มีหลักฐานจาก
`forge pack test` ที่ผ่านและ digest ตรง descriptor/assets = `conformant` พร้อมอ้าง evidence ใน report.
ผล test ที่ fail/skip หรือ stale ไม่กัน generation แต่กัน static audit จนกว่าจะ test ใหม่ผ่าน (M6).
Generator ไม่รัน MCP และไม่เลื่อนสถานะ package จาก `unverified` เอง.

## Gate และสถานะ

```bash
python3 scripts/check_m4_generate.py
```

Gate ตรวจซ้ำทั้งสอง fixture แบบ byte-for-byte, report schema/inventory, permission guarantees, input preservation, pack asset paths และ failure cases ก่อนตรวจ `thclaws` ใน PATH:

- `0`: offline checks และ `thclaws agent new`/`agent validate` ผ่านทั้งหมด
- `1`: generation, contract, compatibility baseline หรือ native validation ล้มเหลว
- `2`: offline checks ผ่าน แต่ไม่มี `thclaws` จึงประกาศ skip native checks

Native gate ตรวจ thClaws baseline `0.146.0` (M17; เดิม 0.138.0 ใน M12 และ 0.116.0 ก่อนนั้น); ถ้า version เปลี่ยนต้องตรวจและปรับ DESIGN ก่อนเปลี่ยน baseline. บนเครื่องที่ binary อยู่ใน source checkout ใช้ PATH เฉพาะ command เช่น:

```bash
PATH="/path/to/thClaws/target/debug:$PATH" python3 scripts/check_m4_generate.py
```

`agent new` ถูกเรียกทั้ง static-pipeline, batch-fanout และ dynamic เพื่อตรวจรูปแบบ scaffold โดย dynamic ต้องไม่มี run.js. `agent validate` ตรวจ package จริง รวมถึง synthetic pack script โดยให้ bytecode cache อยู่ภายนอก package

Report ที่ generate มีสถานะ `unverified`; manifest/static/live/security เป็น `not_run`. ผล validator ใน gate เป็นหลักฐานแยกจาก report ที่สร้างและไม่แก้ไฟล์ package. การ validate package format ผ่านไม่ใช่หลักฐานว่า SQL MCP ทำงาน, permission ถูกบังคับ หรือ package ผ่าน audit. `draft` มาจาก static audit (M5, `studio.py` หรือ `forge audit --write`); live audit (M6/M9) และ security audit (M19, `forge audit --security`) เป็นขั้นแยกที่เลื่อนสถานะเป็น `candidate` / `shippable` ได้ตามหลักฐานในรอบเดียวกัน


## Standalone T0 (M9)

```bash
forge generate fixtures/text-reviewer-standalone/spec.yaml --out out/text-reviewer-standalone
forge audit out/text-reviewer-standalone --write
thclaws agent run out/text-reviewer-standalone --workflow .thclaws/agent_workflow/run.js --args '{"request":{"text":"Hello world"}}'
forge live-test out/text-reviewer-standalone --write
```

ต้อง provision provider key ของ model ที่ pin ไว้ก่อนรัน. Package เพิ่ม `.thclaws/agents/{planner,worker,verifier}.md`,
`.thclaws/agent_workflow/run.js` และ schema `branch.json` (`oneOf` result/refusal จาก spec).
Pinned model ถูกเขียนทั้ง settings และ role definitions; model policy สืบทอด model ของ operator และยังทำ live audit ไม่ได้.
ไม่มี routing หรือ Atlas export. Standalone รับ output เดียวแบบ `assistant_json` เท่านั้น และรองรับ T0 (M9) กับ T1 (M14).

T0: ทุก role ได้เฉพาะ Read/Grep/Glob และ qualified MCP tools ที่ pack ประกาศว่าไม่ mutating ตาม spec.
Tools อื่น (รวม KMS/Memory writers, Task/WorkflowRun และ unknown tools) ถูก reject โดยไม่ตัดสิทธิ์เงียบ ๆ.
ทุก role ต้องมี tools ไม่ว่าง; generator reject แทน inherit-all และไม่สร้าง writePaths สำหรับ T0.
Role definitions และ workflow เป็นไฟล์ deterministic ที่แก้เองแล้ว static audit fail ด้วย drift.

T1 (M14, DESIGN §4 "Standalone T1 rollout", fixture `fixtures/note-writer-standalone/`): `permissions.write_scope: output` กับ tools ที่เพิ่มได้เฉพาะ
`Write`/`Edit` (ต้องมีอย่างน้อยหนึ่ง; Docx/Pptx/Xlsx/Pdf/Epub/NotebookEdit/FetchImages/DocConvert ถูก reject ชัดว่ายังไม่รองรับ). planner/verifier ยังเป็น T0 (read-only)
ส่วน `worker.md` ได้ Write/Edit พร้อม `writePaths: output/**` ซึ่ง thClaws บังคับที่ runtime โดย resolve ปลายทางผ่าน sandbox (traversal, symlink และ dangling symlink
ถูกปฏิเสธบน 0.138.0 `d452ece` และ 0.146.0 `9f69995`). Package ไม่ ship `output/` (Write สร้าง parent เอง) และ static audit ถือ `output/**` เป็น runtime output เฉพาะ package T1.
Guarantee matrix ของ T1: `output_write_scope: Enforced` (ครอบเฉพาะ Write/Edit ของ worker; Bash ไม่มีในทุก role; MCP tools และ runtime state อยู่นอก boundary),
`no_file_writes: Not applicable`. T1 รับรองคู่ runtime `0.138.0/d452ece` และ `0.146.0/9f69995` (M17) (`STANDALONE_T1_THCLAWS_COMPATIBILITY`) เพราะ writePaths ของ 0.116.0 `75edc48`
ตรวจ raw glob (`output/../traversal.txt` หลุด) และ 0.129.0 `7a27129` ยังหลุดกรณี dangling symlink; คู่เก่ายังรับรอง T0 เท่านั้น.
Live audit ของ standalone ทุก tier ทำ tree diff ของ case directory: ไฟล์นอก `output/` (ยกเว้น `.thclaws/state/`, `.thclaws/sessions/`) ทำให้ case fail และ T0 ต้องไม่เขียนไฟล์ใดเลย.
T0 ที่ส่งมอบยังมี `output_write_scope: Not applicable` และ `no_file_writes: Not guaranteed`: ไม่มี write tools ใน role แต่ runtime state/MCP ไม่ใช่ filesystem isolation.
ดูหลักฐานและขอบเขตใน [DESIGN](DESIGN.md).


รองรับ native validation ของ standalone T0 บน thClaws 0.116.0 (`75edc48`), 0.121.0 (`35ccf16`),
0.129.0 (`7a27129`), 0.138.0 (`d452ece`) และ 0.146.0 (`9f69995`) เท่านั้น; ต้องตรงทั้ง version และ revision. Atlas/export pin 0.146.0 (M17).
`thclaws_baseline` ใน report คือ template/catalog baseline;
ดู runtime ที่ใช้จริงใน `static_audit.thclaws_validate.version`.
