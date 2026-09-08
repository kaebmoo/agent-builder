# Generator (M4)

สร้าง package สำหรับ Atlas `single-worker` จาก AgentSpec ที่ผ่าน schema และ semantic checks ของ M1 ใช้ compatibility resolver ของ M3 แล้ว render แบบ deterministic โดยไม่เรียก LLM และไม่รัน script/MCP ของ pack

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

- `manifest.json` และ `.thclaws/settings.json`: identity/catalog fields ตาม thClaws v0.116.0. ส่งต่อให้ `thclaws agent validate` ตรวจ ไม่สร้าง manifest validator อีกชุด
- `AGENTS.md` และ `.thclaws/skills/<identity.name>/SKILL.md`: mission, permission declarations, refusal และ output จาก spec
- `agentspec.json`: source contract ที่ serialize แบบ canonical JSON
- `.thclaws/schemas/inputs--<name>.json`, `outputs--<name>.json`, `refusal.json`: schema ตาม spec. collect_files ที่ไม่มี schema จะไม่สร้าง schema สมมติ
- `evaluation/golden-cases.json`: cases และ expected branch ครบจาก spec
- `builder-build-report.json` และ schema: inventory, compatibility, guarantee matrix, audit status และ deployment hints
- `audit.py` และ `studio.py`: runner บาง ๆ ของ static audit (M5) คัดลอกจาก `templates/` ทุก byte; ดู [audit.md](audit.md)
- `atlas-node-template.json` เฉพาะ T2 (M7b): requirement ของ daemon แยก และ fragment `human_gate` → worker ผ่าน choice `approve` เท่านั้น; worker/workspace เป็น placeholder ที่ operator ต้อง bind ใน deployment copy และ static audit ห้ามแก้ template ต้นฉบับ

Atlas ไม่มี subagent definition หรือ run.js ใน package. `atlas-workflow` ได้ package ของ node เดียว; M7b เพิ่ม approval fragment สำหรับ T2 และ M8 export ไฟล์ลงทะเบียนกับ flow ที่ bind แล้วผ่าน `forge export` ([export.md](export.md)); การ provision deployment ยังเป็นงานของ operator. Standalone generator ยังไม่รองรับและคืน error ก่อนเขียนไฟล์ แม้ pattern จะผ่าน compatibility matrix

## Capability assets

M7a แทน descriptor เดิมด้วย contract v2 ตาม [DESIGN §5](DESIGN.md) ทั้งชุด ไม่รองรับ v1.
ดูตัวอย่างจริงที่ `packs/sql-readonly/pack.yaml` และ [คู่มือ SQL pack](../packs/sql-readonly/README.md).

`scripts` อ้างไฟล์ `.py` ใต้ `scripts/`; `skills` อ้าง `<name>/SKILL.md` ใต้ `skills/`.
ไม่รับ symlink หรือ path traversal. Asset ถูกคัดลอกเป็น `.thclaws/scripts/<pack>--<file>.py`
และ `.thclaws/skills/<pack>--<skill>/SKILL.md`; argument ที่ตรง `scripts/<file>.py`
จะถูกเปลี่ยนเป็น path ของ asset ใน package. Argument อื่นคงเดิม ไม่มี shell หรือ env interpolation.

Generator สร้าง `.thclaws/mcp.json` จาก server command/args และ `manifest.requires.mcp_servers`.
ห้าม server name ซ้ำข้าม pack. ค่า secret ไม่อยู่ใน MCP config; operator ตั้ง env ตามชื่อใน spec
แล้ว start daemon โดยใช้ CWD=package. การติดตั้ง runtime, daemon isolation และ egress เป็นงาน deployment.

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

Native gate ตรวจ thClaws v0.116.0; ถ้า version เปลี่ยนต้องตรวจและปรับ DESIGN ก่อนเปลี่ยน baseline. บนเครื่องที่ binary อยู่ใน source checkout ใช้ PATH เฉพาะ command เช่น:

```bash
PATH="/path/to/thClaws/target/debug:$PATH" python3 scripts/check_m4_generate.py
```

`agent new` ถูกเรียกทั้ง static-pipeline, batch-fanout และ dynamic เพื่อตรวจรูปแบบ scaffold โดย dynamic ต้องไม่มี run.js. `agent validate` ตรวจ package จริง รวมถึง synthetic pack script โดยให้ bytecode cache อยู่ภายนอก package

Report ที่ generate มีสถานะ `unverified`; manifest/static/live/security เป็น `not_run`. ผล validator ใน gate เป็นหลักฐานแยกจาก report ที่สร้างและไม่แก้ไฟล์ package. การ validate package format ผ่านไม่ใช่หลักฐานว่า SQL MCP ทำงาน, permission ถูกบังคับ หรือ package ผ่าน audit. `draft` มาจาก static audit (M5, `studio.py` หรือ `forge audit --write`); live audit และ security ยังไม่รัน
