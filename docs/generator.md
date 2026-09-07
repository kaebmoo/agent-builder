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

Atlas ไม่มี subagent definition หรือ run.js ใน package. `atlas-workflow` ได้ package ของ node เดียว; flow/export และ `human_gate` ที่ใช้งานจริงอยู่ใน M8. Standalone generator ยังไม่รองรับและคืน error ก่อนเขียนไฟล์ แม้ pattern จะผ่าน compatibility matrix

## Capability assets

M4 รับ descriptor `packs/<name>/pack.yaml` รูปแบบขั้นต่ำนี้:

```yaml
min_tier: T0
tools: [Read]
env: []
mcp_servers: []
scripts: [example.py]
skills: [guide]
```

ต้องมีทั้ง 6 field และไม่รับ field อื่น. `scripts` อ้างไฟล์ `.py` ตรงใต้ `scripts/` ของ pack; `skills` อ้าง `<name>/SKILL.md` ตรงใต้ `skills/`. ชื่อ asset ต้องเป็น lowercase/digits/underscore/hyphen และไม่รับ symlink หรือ path traversal

script ถูกคัดลอกเป็น `.thclaws/scripts/<pack>--<file>.py`; skill เป็น `.thclaws/skills/<pack>--<skill>/SKILL.md`. Prefix กันการทับชื่อข้าม pack และ collision จะ fail. ไม่มีการ execute asset ตอน generate

spec ต้องประกาศ tools/env ที่ descriptor ต้องการ และ tier ต้องไม่น้อยกว่า `min_tier`. M4 ยังไม่แปลง `params` ของ pack ที่มี descriptor จึง reject ถ้าไม่ว่าง. MCP เป็นเพียงชื่อ requirement ใน manifest; operator ต้องติดตั้งบน daemon เอง

ถ้าไม่มี descriptor ผล build ระบุ dependency `missing` และ CLI พิมพ์ `UNRESOLVED pack`. ไม่สมมติชื่อ server, command หรือ secret เพื่อให้ดูเหมือนใช้งานได้. `sql-readonly` ใน fixture ยังอยู่ในกรณีนี้จนกว่า M7 จะเพิ่ม implementation จริง. Gate ใช้ pack สังเคราะห์ใน temporary directory เพื่อทดสอบตำแหน่งและ byte ของ assets

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

Report ที่ generate มีสถานะ `unverified`; manifest/static/live/security เป็น `not_run`. ผล validator ใน gate เป็นหลักฐานแยกจาก report ที่สร้างและไม่แก้ไฟล์ package. การ validate package format ผ่านไม่ใช่หลักฐานว่า SQL MCP ทำงาน, permission ถูกบังคับ หรือ package ผ่าน audit. `draft` ต้องรอ static audit M5; live audit และ security ยังไม่รันใน M4
