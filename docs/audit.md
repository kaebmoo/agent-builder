# Static audit (M5)

ตรวจ package ที่ generate แล้วแบบ deterministic ตามกฎ DESIGN §7 โดยไม่เรียก LLM, ไม่ start daemon และไม่รัน script/MCP ของ pack. ผลถูกบันทึกใน `builder-build-report.json` (field `audit`, `static_audit`, `package_status`)

## ใช้งาน

```bash
forge audit out/invoice-reviewer            # ตรวจอย่างเดียว ไม่แก้ไฟล์ใด ๆ
forge audit out/invoice-reviewer --write    # ตรวจแล้วบันทึกผลลง builder-build-report.json
forge audit out/invoice-reviewer --strict   # warning นับเป็น fail (เกณฑ์ก่อน shippable)
```

ใน package มี wrapper สองตัวที่ generate มาให้: `python3 audit.py` เท่ากับ `forge audit <package>` และ `python3 studio.py` เท่ากับ `forge audit <package> --write`. ทั้งคู่เป็น runner บาง ๆ ที่ import `forge.audit` จึงต้องติดตั้ง builder (`pip install -e`) หรือตั้ง `PYTHONPATH` ไปที่ checkout ก่อน. กฎทั้งหมดอยู่ที่ `forge/audit.py` ที่เดียว

Exit code: `0` = package เป็น `draft`, `1` = มี error (กฎ static หรือ `thclaws agent validate` ไม่ผ่าน), `2` = กฎ static ผ่านแต่ไม่มี `thclaws` จึงคง `unverified` แบบประกาศชัด. ใช้ `THCLAWS_BIN` เลือก binary ได้ (ค่าเริ่มต้นหาใน PATH)

## กฎ

| rule | ตรวจอะไร | severity |
|---|---|---|
| `spec` | `agentspec.json` ผ่าน M1 (schema + semantic) และ M3 (compatibility) และ generator render ได้ | error |
| `packs` | descriptor ของทุก pack ต้องมี (`missing` = fail), `min_tier` ไม่เกิน tier ของ spec, `tools`/`env` ที่ pack ต้องการถูกประกาศใน spec, ชื่อ asset และ symlink ถูกกฎ M4 | error |
| `inventory` | ไฟล์ที่ generate ต้องมีครบ; ไฟล์ใด ๆ ในทั้ง package ที่ไม่ได้มาจาก spec หรือ pack ที่ประกาศ = fail (เช่น `secrets.env` หรือ README ที่ใส่เพิ่ม) และ symlink ทุกชนิด = fail; ยกเว้นเฉพาะสิ่งที่ runtime/VCS สร้างและ `thclaws agent pack` ตัดทิ้ง: `.thclaws/state/`, `.git/`, `__pycache__/`, `*.pyc`, `.DS_Store`; `.thclaws/agents/` และ `.thclaws/agent_workflow/` เป็นของกฎ `single_worker` | error |
| `drift` | ไฟล์ที่ไม่ใช่ prose ต้องตรงกับที่ render จาก `agentspec.json` ทุก byte: `manifest.json`, `.thclaws/settings.json`, schemas, golden cases, script/skill ของ pack, `audit.py`/`studio.py`, report schema; ไฟล์ JSON บอกตำแหน่งที่ต่าง เช่น `requires.mcp_servers: expected [] found ["sql-admin"]` | error |
| `agents_md` | มี section `## Mission` / `## Must` / `## Refuse` / `## Output`, มีประโยค "ตอบ JSON ล้วน" เมื่อ output เป็น `assistant_json`, และ block `permissions` / `model` / `state` ตรงกับ spec | error |
| `refusal` | ทุกข้อใน `refusal.conditions` และ `refusal.response` ต้องอยู่ใน AGENTS.md (`refusal.json` ตรวจผ่าน `drift`) | error |
| `schema_vocabulary` | keyword ใน embedded schema (inputs / outputs / refusal) ที่อยู่นอก JSON Schema 2020-12 vocabularies เพราะ validator ไม่สนใจ keyword นั้น | warning: `draft` ผ่าน, `--strict` fail |
| `single_worker` | เฉพาะ target Atlas: `.thclaws/agents/` และ `.thclaws/agent_workflow/` ต้องว่าง และ AGENTS.md ห้ามสั่ง `WorkflowRun` เพราะ `/agent/run` ไม่มี subagent factory | error |
| manifest layer | `thclaws agent validate` ผ่าน; binary ต้องเป็น baseline `0.116.0` (ต่างจากนี้ = fail จนกว่าจะตรวจ DESIGN §4 ใหม่); รันโดยตั้ง `PYTHONPYCACHEPREFIX` นอก package จึงไม่ทิ้ง bytecode | passed / failed / skipped |

`single_worker` ผูกกับ `TARGET_RULES` ใน `forge/audit.py` (ปัจจุบัน `atlas-worker`, `atlas-workflow`) ไม่ใช่กฎสากล; `rules_for(target)` บอกชุดกฎของ target นั้น เพื่อให้ M9 เพิ่ม `.thclaws/agents/` และ `run.js` ของ standalone ได้โดยไม่ต้องแก้กฎ Atlas

## ไฟล์ที่แก้ได้หลัง generate

`AGENTS.md` และ `.thclaws/skills/<identity.name>/SKILL.md` เท่านั้น (ขั้น "LLM customize เฉพาะส่วนข้อความ") ตราบใดที่เนื้อหาบังคับตามกฎ `agents_md` / `refusal` ยังอยู่. ไฟล์อื่นเป็นผลของ AgentSpec: ต้องแก้ที่ spec แล้ว generate ใหม่ มิฉะนั้น `drift` fail

## สถานะ

- `unverified` → `draft` เมื่อไม่มี finding ระดับ error และ `thclaws agent validate` ผ่าน
- ไม่มี binary: กฎ static ผ่านแต่ `audit.manifest = skipped` → คง `unverified` ตาม DESIGN §7 และ report ระบุเหตุผล
- warning ไม่กัน `draft` แต่ถูกบันทึกใน `static_audit.findings`; ต้องแก้ก่อน `shippable` (milestone ถัดไปใช้ `--strict`)
- `builder-build-report.schema.json` บังคับว่า `draft` ต้องมี `audit.spec` / `manifest` / `static = passed` และมี `static_audit`; `audit.manifest` ต้องเท่ากับ `static_audit.thclaws_validate.status`; `audit.static = passed` ต้องไม่มี finding ระดับ error และถ้า `strict` ต้องไม่มี finding เลย; report ที่อ้าง `draft` หรือ `shippable` โดยหลักฐานไม่รองรับจะไม่ผ่าน schema (gate M5 ทดสอบ report ปลอมทั้ง 8 แบบ)
- audit ไม่แก้ไฟล์ใด ๆ นอกจาก report เมื่อ `--write`; รันซ้ำได้ report เหมือนเดิมทุก byte บนเครื่องเดียวกัน (`thclaws_validate.version` มาจาก binary ที่ใช้)
- report สะท้อนผลครั้งล่าสุดที่รัน `studio.py`; แก้ไฟล์แล้วต้องรันใหม่ (`audit.py` จะเตือนเมื่อ report ไม่ตรงกับผลปัจจุบัน)
- ถ้า `agentspec.json` render ไม่ได้ audit จะบันทึก `audit.spec = failed` ลง report เดิม; ถ้าไม่มี report ให้ใช้ audit จะ fail พร้อมเหตุผลโดยไม่เขียนอะไร

## สิ่งที่ M5 ไม่ตรวจ

- MCP ถูกติดตั้งบน daemon จริงหรือไม่ และ agent เรียก skill/MCP จริงหรือไม่ (live audit M6)
- permission ถูกบังคับที่ runtime: guarantee matrix ไม่เปลี่ยนจาก M4 เพราะ `/agent/run` ยังไม่ enforce (DESIGN §4, §6)
- `sql-reader` ยัง fail ที่ `packs` จนกว่า M7a จะเพิ่ม `packs/sql-readonly/pack.yaml`; ห้ามใส่ descriptor หลอกเพื่อให้ผ่าน
- standalone package: generator ยัง render ไม่ได้จน M9 จึง fail ที่ `spec`

## Gate

```bash
python3 scripts/check_m5_static_audit.py
```

- `0`: offline checks และส่วน native (`draft` จริงบน thClaws `0.116.0`, wrappers รันเป็น subprocess) ผ่าน
- `1`: verdict, ข้อความ finding, warning/strict, target binding, schema ของ report หรือ wrapper ผิด
- `2`: offline ผ่านแต่ไม่มี `thclaws` ใน PATH จึงประกาศ skip ส่วน native

offline ตรวจ: package ถูกต้องไม่มี finding และไม่ถูกเลื่อนสถานะ, `sql-reader` fail เพราะ pack missing, package ที่แก้ให้ผิด (ลบ `## Refuse`, ใส่ admin MCP ใน manifest ของ T0, pack T2 ใน spec T0, env หาย, model ใน `agentspec.json` ไม่ตรง AGENTS.md, ไฟล์เกิน/หาย/ถูกแก้, spec ผิดกฎ M1) fail พร้อมข้อความ, unknown keyword เป็น warning และ fail เมื่อ `--strict`, `single_worker` ผูกกับ Atlas เท่านั้น, report schema ปฏิเสธ status ที่ไม่มีหลักฐาน. บนเครื่องที่ binary อยู่ใน source checkout:

```bash
PATH="/path/to/thClaws/target/debug:$PATH" python3 scripts/check_m5_static_audit.py
```
