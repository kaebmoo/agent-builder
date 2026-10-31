# Static audit (M5)

ตรวจ package ที่ generate แล้วแบบ deterministic ตามกฎ DESIGN §7 โดยไม่เรียก LLM, ไม่ start daemon และไม่รัน script/MCP ของ pack. ผลถูกบันทึกใน `builder-build-report.json` (field `audit`, `static_audit`, `package_status`)

## ใช้งาน

```bash
forge audit out/invoice-reviewer            # ตรวจอย่างเดียว ไม่แก้ไฟล์ใด ๆ
forge audit out/invoice-reviewer --write    # ตรวจแล้วบันทึกผลลง builder-build-report.json
forge audit out/invoice-reviewer --strict   # warning นับเป็น fail (เกณฑ์ก่อน shippable)
forge audit out/<pkg> --security           # M19: strict + deterministic security checks
```

ใน package มี wrapper สองตัวที่ generate มาให้: `python3 audit.py` เท่ากับ `forge audit <package>` และ `python3 studio.py` เท่ากับ `forge audit <package> --write`. ทั้งคู่เป็น runner บาง ๆ ที่ import `forge.audit` จึงต้องติดตั้ง builder (`pip install -e`) หรือตั้ง `PYTHONPATH` ไปที่ checkout ก่อน. กฎทั้งหมดอยู่ที่ `forge/audit.py` ที่เดียว

Exit code: `0` = package เป็น `draft`, `1` = มี error (กฎ static หรือ `thclaws agent validate` ไม่ผ่าน), `2` = กฎ static ผ่านแต่ไม่มี `thclaws` จึงคง `unverified` แบบประกาศชัด. ใช้ `THCLAWS_BIN` เลือก binary ได้ (ค่าเริ่มต้นหาใน PATH)

## กฎ

| rule | ตรวจอะไร | severity |
|---|---|---|
| `spec` | `agentspec.json` ผ่าน M1 (schema + semantic) และ M3 (compatibility) และ generator render ได้ | error |
| `packs` | descriptor ของทุก pack ต้องมี (`missing` = fail), `min_tier` ไม่เกิน tier ของ spec, `tools`/`env` ที่ pack ต้องการถูกประกาศใน spec, network ไม่ต่ำกว่า pack, contract v2 / conformance digest / asset paths ถูกต้อง | error |
| `inventory` | ไฟล์ที่ generate ต้องมีครบ; ไฟล์ใด ๆ ในทั้ง package ที่ไม่ได้มาจาก spec หรือ pack ที่ประกาศ = fail (เช่น `secrets.env`, README ที่ใส่เพิ่ม, role เพิ่มใน `.thclaws/agents/`, workflow เพิ่มใน `.thclaws/agent_workflow/`, nested `__pycache__` payload, `.thclaws/state/todos.md` หรือ `.DS_Store`) และ symlink ทุกชนิด = fail; ยกเว้นเฉพาะสิ่งที่ runtime/VCS สร้างและ `thclaws agent pack` ตัดทิ้งโดยไม่ถูกโหลดกลับเป็น prompt: `.thclaws/state/` ยกเว้น `todos.md` ทุก case, `.git/`, root `__pycache__/`, `*.pyc` | error |
| `drift` | ไฟล์ที่ไม่ใช่ prose ต้องตรงกับที่ render จาก `agentspec.json` ทุก byte: `manifest.json`, `.thclaws/settings.json`, schemas, golden cases, script/skill ของ pack, `audit.py`/`studio.py`, report schema; ไฟล์ JSON บอกตำแหน่งที่ต่าง เช่น `requires.mcp_servers: expected [] found ["sql-admin"]` | error |
| `agents_md` | มี section `## Mission` / `## Must` / `## Refuse` / `## Output`, มีประโยค "ตอบ JSON ล้วน" เมื่อ output เป็น `assistant_json`, และ block `permissions` / `model` / `state` ตรงกับ spec | error |
| `refusal` | ทุกข้อใน `refusal.conditions` และ `refusal.response` ต้องอยู่ใน AGENTS.md (`refusal.json` ตรวจผ่าน `drift`) | error |
| `schema_vocabulary` | keyword ใน embedded schema (inputs / outputs / refusal) ที่อยู่นอก JSON Schema 2020-12 vocabularies เพราะ validator ไม่สนใจ keyword นั้น | warning: `draft` ผ่าน, `--strict` fail |
| `tools` | ชื่อ tool อยู่ใน catalog `patterns/tools-0.146.0.json` (baseline ปัจจุบัน; `tools-0.138.0.json` และ `tools-0.116.0.json` คงไว้เป็นหลักฐาน) หรือเป็น qualified MCP tool จาก pack; catalog รวม tool แบบ conditional ไม่ได้ยืนยันว่าเปิดอยู่บน daemon | warning: `draft` ผ่าน, `--strict` fail |
| `single_worker` | เฉพาะ target Atlas: `.thclaws/agents/` และ `.thclaws/agent_workflow/` ต้องว่าง และ AGENTS.md ห้ามสั่ง `WorkflowRun` เพราะ `/agent/run` ไม่มี subagent factory | error |
| manifest layer | `thclaws agent validate` ผ่าน; binary ต้องเป็น baseline `0.146.0` (M17; M12 เดิม 0.138.0) ยกเว้น standalone T0 ที่รับรองคู่ version+revision เพิ่มเติม (รุ่นอื่น = fail จนกว่าจะตรวจ DESIGN §4 ใหม่); รันโดยตั้ง `PYTHONPYCACHEPREFIX` นอก package จึงไม่ทิ้ง bytecode | passed / failed / skipped |

`single_worker` ผูกกับ `TARGET_RULES` ใน `forge/audit.py` (ปัจจุบัน `atlas-worker`, `atlas-workflow`) ไม่ใช่กฎสากล; `rules_for(target)` บอกชุดกฎของ target นั้น เพื่อให้ M9 มี `.thclaws/agents/` และ `run.js` ที่ generate จาก spec ได้โดยไม่ต้องแก้กฎ Atlas. ไฟล์ role/workflow ที่เพิ่มเองยังถูกกฎ `inventory` จับทุก target

ข้อยกเว้นของ manifest layer สำหรับ standalone T0 ที่ตรวจแล้วคือ version+revision `0.116.0/75edc48`,
`0.121.0/35ccf16`, `0.129.0/7a27129`, `0.138.0/d452ece` และ `0.146.0/9f69995`; version หรือ revision อื่นยัง fail closed ตาม DESIGN §4.

## ไฟล์ที่แก้ได้หลัง generate

สำหรับ Atlas package: `AGENTS.md` และ `.thclaws/skills/<identity.name>/SKILL.md` แก้ได้หลัง generate (ขั้น "LLM customize เฉพาะส่วนข้อความ") ตราบใดที่เนื้อหาบังคับตามกฎ `agents_md` / `refusal` ยังอยู่. สำหรับ standalone T0: role ทั้งสามฝัง snapshot ของ `AGENTS.md` ไว้ จึงแก้ `AGENTS.md` หลัง generate ไม่ได้; ต้องแก้ spec/template แล้ว generate ใหม่. ไฟล์อื่นเป็นผลของ AgentSpec: ต้องแก้ที่ spec แล้ว generate ใหม่ มิฉะนั้น `drift` fail

## สถานะ

- `unverified` → `draft` เมื่อไม่มี finding ระดับ error และ `thclaws agent validate` ผ่าน
- ไม่มี binary: กฎ static ผ่านแต่ `audit.manifest = skipped` → คง `unverified` ตาม DESIGN §7 และ report ระบุเหตุผล
- warning ไม่กัน `draft` แต่ถูกบันทึกใน `static_audit.findings`; ต้องแก้ก่อน `shippable` (M19 `--security` รัน static audit ด้วย `--strict` ในรอบเดียวกัน)
- `builder-build-report.schema.json` บังคับว่า `draft` ต้องมี `audit.spec` / `manifest` / `static = passed` และมี `static_audit`; `audit.manifest` ต้องเท่ากับ `static_audit.thclaws_validate.status`; `audit.static = passed` ต้องไม่มี finding ระดับ error และถ้า `strict` ต้องไม่มี finding เลย; report ที่อ้าง `draft` หรือ `shippable` โดยหลักฐานไม่รองรับจะไม่ผ่าน schema (gate M5 ทดสอบ report ปลอมทั้ง 8 แบบ)
- audit ไม่แก้ไฟล์ใด ๆ นอกจาก report เมื่อ `--write`; รันซ้ำได้ report เหมือนเดิมทุก byte บนเครื่องเดียวกัน (`thclaws_validate.version` มาจาก binary ที่ใช้)
- report สะท้อนผลครั้งล่าสุดที่รัน `studio.py`; แก้ไฟล์แล้วต้องรันใหม่ (`audit.py` จะเตือนเมื่อ report ไม่ตรงกับผลปัจจุบัน)
- ถ้า `agentspec.json` render ไม่ได้ audit จะบันทึก `audit.spec = failed` ลง report เดิม; ถ้าไม่มี report ให้ใช้ audit จะ fail พร้อมเหตุผลโดยไม่เขียนอะไร

## สิ่งที่ M5 ไม่ตรวจ

- MCP ถูกติดตั้งบน daemon จริงหรือไม่ และ agent เรียก skill/MCP จริงหรือไม่ (live audit M6)
- permission ถูกบังคับที่ runtime: static audit ตรวจเฉพาะว่า `.thclaws/settings.json` (รวม `allowedTools` และ flag browser/HAL/media ของ target Atlas, M15) ตรงกับ render ทุก byte; การที่ `/agent/run` ถอด built-in นอก allowlist จริงพิสูจน์ใน `check_m15_tool_allowlist.py` ไม่ใช่ใน audit (DESIGN §4 "Tool allowlist บน `/agent/run`", §6)
- `sql-reader` ผ่าน static audit ได้ด้วย SQL pack v2 ของ M7a; การมี descriptor ไม่ยืนยันว่า runtime ถูกติดตั้ง ต้องใช้ `forge pack test` และ live audit ตามลำดับ
- standalone package: static-pipeline T0 (M9) และ T1 (M14); role/workflow drift เป็น error (รวม `writePaths` ของ worker ใน T1) และ AGENTS.md ต้องระบุ WorkflowRun กับ path ที่ generate; สำหรับ package T1 inventory ถือ `output/**` เป็น runtime output ไม่ใช่ไฟล์ undeclared (ยังเป็น finding ใน T0/T2) และ `thclaws agent validate` ถูกรับรองตามคู่ version+revision ของ tier (T1 เฉพาะ `0.138.0/d452ece` และ `0.146.0/9f69995`)

## Gate

```bash
python3 scripts/check_m5_static_audit.py
```

- `0`: offline checks และส่วน native (`draft` จริงบน thClaws baseline `0.146.0`, wrappers รันเป็น subprocess) ผ่าน
- `1`: verdict, ข้อความ finding, warning/strict, target binding, schema ของ report หรือ wrapper ผิด
- `2`: offline ผ่านแต่ไม่มี `thclaws` ใน PATH จึงประกาศ skip ส่วน native

offline ตรวจ: package ถูกต้องไม่มี finding และไม่ถูกเลื่อนสถานะ, package สังเคราะห์ที่ไม่มี pack descriptor ต้อง fail, package ที่แก้ให้ผิด (ลบ `## Refuse`, ใส่ admin MCP ใน manifest ของ T0, pack T2 ใน spec T0, env หาย, model ใน `agentspec.json` ไม่ตรง AGENTS.md, ไฟล์เกิน/หาย/ถูกแก้, spec ผิดกฎ M1) fail พร้อมข้อความ, unknown keyword เป็น warning และ fail เมื่อ `--strict`, `single_worker` ผูกกับ Atlas เท่านั้น, report schema ปฏิเสธ status ที่ไม่มีหลักฐาน. บนเครื่องที่ binary อยู่ใน source checkout:

```bash
PATH="/path/to/thClaws/target/debug:$PATH" python3 scripts/check_m5_static_audit.py
```

## Live audit (M6)

```bash
forge pack test sql-readonly
forge generate fixtures/sql-reader/spec.yaml --out out/sql-reader
forge live-test out/sql-reader --write
# หรือใช้ runner เดิม
forge audit out/sql-reader --live --write
python3 out/sql-reader/studio.py --live
```

`--timeout 120` กำหนด deadline ต่อ golden case เป็นวินาที; `--provider-key-env OPENAI_API_KEY` เลือก **ชื่อ** environment variable ของ provider ที่ตรงกับ pinned model (ไม่เปลี่ยน model และไม่รับค่า key ผ่าน CLI). ค่าเริ่มต้นตรงกับ fixture `sql-reader` ที่ใช้ OpenAI. Harness ไม่ค้น credential จากไฟล์หรือ keychain ของผู้ใช้.

ก่อน live ต้องผ่าน static audit และ native validate ทุกครั้ง จึงไม่ใช้สถานะ candidate เก่าแทนหลักฐานใหม่. Live audit ต้องมี output เดียวเป็น `assistant_json` และ pinned model; target Atlas ใช้ daemon (M6), standalone ใช้ CLI (M9). Fixture ข้อมูลมาจาก `fixture.setup` ของแต่ละ pack และไม่ใช้ข้อมูลจริง. คัดลอก package ไป temporary directory, start daemon ด้วย CWD ของสำเนา พร้อม HOME/XDG_CONFIG_HOME/THCLAWS_CONFIG ใหม่และ `browserEnabled: false` เพื่อปิด Playwright MCP ที่ runtime เพิ่มเอง, API token สุ่ม และ `THCLAWS_MCP_ALLOW_ALL=1`. ส่งต่อเฉพาะ PATH, provider key ที่เลือก และ env ที่ fixture ประกาศ; env ชนกับ isolation หรือ pack อื่นเป็น error. ปิด process group ของ daemon และลบไฟล์ชั่วคราวเมื่อจบหรือเกิดข้อผิดพลาด. Package ต้นทางเปลี่ยนเฉพาะ report เมื่อระบุ `--write`.

แต่ละ case ส่ง input เป็น JSON ใน `prompt` ของ `/agent/run` พร้อม `stream:true` โดยไม่ใช้ session ร่วมกัน. ตรวจ SSE ว่ามี `result` และ `[DONE]`, ไม่มี error/denied event, แล้ว parse JSON ของ assistant turn สุดท้ายหลัง tool calls. ไม่รับ Markdown fences, duplicate JSON keys หรือ NaN. Validate กับ branch ตาม `expect` และต้องไม่ผ่าน schema ของอีก branch พร้อมกัน. เก็บ SSE events (รวม skill events) ลง `live_audit.cases`; แทนค่า provider key, daemon token และ fixture env ที่ทราบด้วย `<redacted>` ก่อนบันทึก. ไม่บันทึก daemon logs หรือ HTTP error body.

ทุก pack ที่มี MCP ต้องมีอย่างน้อยหนึ่งคู่ SSE `tool_use_start` / `tool_use_result` ที่ id/name ตรงกันและ status `ok` ของ tool ที่ประกาศ และ `/v1/agent/info` ต้องมีชุด server ตรงกับ pack ที่ประกาศทุกตัว ไม่มีตัวเกินหรือขาด ทั้งก่อนส่ง case แรกและหลังจบ; inventory ไม่ตรงก่อนเริ่มจะไม่เรียก model. หลักฐานนี้ไม่พิสูจน์ tool allowlist, shell หรือ write-path enforcement. SQL provenance บันทึก `git rev-parse HEAD` ของ `SQL_READONLY_AI_ROOT` เทียบกับ `source.ref` ทั้งใน conformance และ live evidence; mismatch เป็น fail. Fixture probe `mcp` ด้วย `python3` บน PATH ซึ่งเป็น interpreter ของ server.

ผลและ exit code:

- `0`: ทุก case และหลักฐาน MCP ผ่าน → `candidate`; security ยัง `not_run`.
- `1`: static/native/live/provenance ผิด → ไม่เป็น candidate; ถ้า static/native ผ่านแล้วคง `draft`.
- `2`: ไม่มี provider key หรือ fixture runtime → `draft` เมื่อ static/native ผ่าน; ไม่มี thClaws → `unverified`. Report ระบุเหตุผล skip ชัดเจน.

Conformance ที่ failed/skipped/stale ไม่กัน generate อีกต่อไป: ยัง bundle assets ได้ แต่ static audit มี finding ระดับ error ที่กัน `draft`. ไม่มีไฟล์ conformance หมายถึงยังไม่รันทดสอบ จึงเป็น `assets_bundled` ตาม contract เดิม.

Gate: `python3 scripts/check_m6_live.py` รัน parser/schema/isolated HTTP daemon จำลองและ negative cases ก่อน แล้วรัน SQL conformance และ native live จริง. การทดสอบ daemon จำลองไม่ใช่หลักฐาน candidate ของ package จริง; ไม่มี provider key ต้องจบด้วย exit 2 แม้ offline checks ผ่านทั้งหมด.

### OpenAI-compatible endpoint

ตาม thClaws v0.116.0 ใช้ model `oai/<upstream-model-id>`, `OPENAI_COMPAT_API_KEY` และ `OPENAI_COMPAT_BASE_URL` (รับทั้ง `/v1` และ `/v1/chat/completions`). Live harness ส่ง endpoint เฉพาะเมื่อ spec pin model `oai/`; key-env ต้องเป็น `OPENAI_COMPAT_API_KEY`. ตั้ง mapping จาก secret store หรือ environment ของ operator เช่น `MATCHA_API_KEY` → `OPENAI_COMPAT_API_KEY` และ `MATCHA_API_URL` → `OPENAI_COMPAT_BASE_URL`; ไม่เก็บค่าเหล่านี้ใน spec/package. `MATCHA_VERIFY_SSL` ไม่มี native mapping ใน thClaws baseline นี้; harness ใช้ TLS verification ปกติ.

Gate รองรับ model override แบบ explicit โดยสร้างเฉพาะ package ทดสอบ ไม่แก้ fixture ต้นฉบับ:

```bash
python3 scripts/check_m6_live.py --model oai/gpt-5.4-mini --provider-key-env OPENAI_COMPAT_API_KEY
```

Generated instructions แสดง input และ refusal schema จาก AgentSpec โดยตรง และสั่งให้ตรวจ input ก่อนเรียก tool; ผลผ่าน/ไม่ผ่านยังตัดสินด้วย deterministic audit เท่านั้น.

ทุก package มี `.thclaws/prompt/system.md` (จาก `templates/system.md.j2`) ซึ่ง thClaws ใช้แทน base prompt ของ coding assistant เมื่อ daemon start จาก CWD=package (DESIGN §4); static audit ตรวจ drift/inventory ของไฟล์นี้เหมือน generated file อื่น และ M7b gate ตรวจว่าไม่มีวงเล็บปีกกาที่ `apply_template` ของ thClaws จะแทนค่า. Pack ส่งคำแนะนำการใช้ MCP ผ่าน `InitializeResult.instructions`; harness ยืนยันด้วย provider capture ไม่ใช่จากการมีไฟล์.

Publisher (M7b) มี live regression แบบ opt-in ที่ใช้ harness M6 เดียวกันกับ golden cases ของ fixture `publisher`:

```bash
python3 scripts/check_m7b_packs.py --live --model oai/gpt-5.4-mini --provider-key-env OPENAI_COMPAT_API_KEY
```

ไม่มี key → skip exit 2 และคง `draft`; ผ่านครบ 4 cases พร้อม SSE `publisher-email-sftp__send_email` → `candidate` โดย `human_approval` ยัง `Not verified` และไม่มีการส่งจริง. ผลรอบเดียวไม่ใช่หลักฐานความนิ่ง; กติกาคือผ่านครบอย่างน้อย 3 รอบติดบน package ที่ hash เท่ากันทุกไฟล์ (ดู PLAN; หลักฐานรอบต่อรอบเก็บใน `out/` ซึ่ง ignored ไม่อยู่ใน clone).


## Standalone live audit (M9)

`forge live-test` เลือก `thclaws agent run --workflow .thclaws/agent_workflow/run.js --args` จาก target.
แต่ละ golden case ใช้สำเนา package ใหม่, HOME/XDG_CONFIG_HOME/THCLAWS_CONFIG แยก, ปิด browser injection,
ส่งต่อเฉพาะ provider key และ fixture env; ปิดทั้ง process group เมื่อจบ/timeout.
ตัด native token summary จาก stdout แล้วตรวจ result/refusal กับ expected branch และ reject กรณี match ทั้งสอง branch.
ต้องมีอย่างน้อยสาม subagent turns; evidence เก็บ `workflow_result` พร้อมค่า JSON, turn count และ stderr ที่ redact แล้ว.
ไม่ใช้ verifier.ok เป็น audit verdict และไม่สร้าง SSE หรือ MCP inventory ปลอม; skill_mcp_calls ยัง No evidence.

Live audit ของ standalone ทุก tier snapshot tree ของ case directory ก่อน/หลังรัน (ยกเว้น `.thclaws/state/`, `.thclaws/sessions/`): ไฟล์ใหม่หรือเปลี่ยนนอก `output/`
ทำให้ case fail, T0 ต้องไม่เขียนไฟล์ใดเลย และ evidence มี event `files_written` ต่อ case (M14).

`python3 scripts/check_m9_standalone.py` เป็น gate ของ **T0 ที่ส่งมอบ**: ตรวจ deterministic output,
legacy T1 audit rejection (package T0 ที่ agentspec ถูกเขียนเป็น T1 ต้อง drift), safe tool subsets, role drift, native agent new/validate,
pipeline และ wrong-branch rejection ผ่าน HTTP provider จำลองบน localhost รวมถึงบังคับ Write/Edit/Bash
กับทุก role แล้วต้องถูกปฏิเสธ. จากนั้นรัน provider จริงเมื่อมี key; ไม่มี binary หรือ key = exit 2.
`--model oai/gpt-5.4-mini --provider-key-env OPENAI_COMPAT_API_KEY` ใช้ model override โดยไม่แก้ fixture ต้นฉบับ.
Native synthetic results ไม่ใช่หลักฐานว่า model จริงผ่าน golden cases.

```bash
# Gate ของ T0 ที่เปิดใช้งาน
python3 scripts/check_m9_standalone.py

# Upstream writePaths diagnostic บน role ที่ gate เขียนเอง (ใช้ประเมิน binary รุ่นใหม่): ไม่มี binary = 2, พบ bypass = 1, ปฏิเสธครบ = 0
python3 scripts/check_m9_standalone.py --write-boundary

# Gate ของ tool allowlist บน /agent/run (M15): ไม่มี binary, build CLI-only หรือไม่มี key = 2, binary ที่ไม่ใช่ baseline ปัจจุบัน (0.146.0) = 1
python3 scripts/check_m15_tool_allowlist.py
python3 scripts/check_m15_tool_allowlist.py --model oai/gpt-5.4-mini --provider-key-env OPENAI_COMPAT_API_KEY

# Gate ของ T1 ที่ส่งมอบ (M14): package จริงจาก generator; ไม่มี binary หรือ key = 2, binary ที่ไม่ใช่คู่ T1 = 1
python3 scripts/check_m14_standalone_t1.py
python3 scripts/check_m14_standalone_t1.py --model oai/gpt-5.4-mini --provider-key-env OPENAI_COMPAT_API_KEY
```

โหมด write-boundary ใช้ temporary native worker ที่เพิ่ม Write/writePaths ใน test เท่านั้น ไม่ bypass guard
ของ generator และไม่เรียก audit เพื่อรับรอง package ทดลองนั้น; ผลของมันไม่ใช่การรับรอง T1 — การรับรองคือ `check_m14_standalone_t1.py`
ซึ่ง offline ตรวจ deterministic T1 package, worker `writePaths: output/**`, planner/verifier read-only, matrix rows, การ reject writer ที่ไม่รองรับ,
boundary drift, inventory `output/**` เฉพาะ T1 และตารางคู่ T1; native ตรวจ `thclaws agent validate` (ไม่มี warning writePaths), probe 14 รายการบน package จริง
(worker Write/Edit ใต้ `output/` สำเร็จ; Write นอก output, `output/../traversal.txt`, symlink directory, dangling symlink, Edit ไฟล์นอก output และ Bash ถูกปฏิเสธ;
planner/verifier Write/Edit/Bash ถูกปฏิเสธ) และ pipeline 4 golden cases ผ่าน provider จำลองที่ worker เขียน `output/note.md` จริง (tree diff ยืนยัน);
provider จริงรัน golden cases ของ `note-writer-standalone` เมื่อมี key.


## Security audit (M19)

`--security` ตรวจแบบ deterministic ตาม DESIGN §7 บน strict static audit ในรอบเดียวกัน ไม่เรียก LLM
และไม่ต้องใช้ provider key สำหรับ security checks:

```bash
forge audit <pkg> --security
forge audit <pkg> --live --security --write
# รันจาก directory ของ package; ต้องติดตั้ง builder หรือตั้ง PYTHONPATH
python3 studio.py --security
```

| rule | ตรวจอะไร | severity |
|---|---|---|
| `strict` | static audit ต้องไม่มี finding ใด รวม warning `tools` และ `schema_vocabulary` | error |
| `secrets` | สแกน content ด้วย fixed secret patterns และค่า env ที่ spec ประกาศ; กัน sensitive paths ที่ `thclaws agent pack` ตัดเงียบ (`.env`, `.key`, `.pem`, `.p12`, `_secret`, `id_rsa`); finding ระบุ path/line/pattern โดยไม่แสดงค่า | error |
| `packs` | ทุก dependency ต้อง `conformant`; `assets_bundled`/`missing` ไม่พอ และ static conformance ตรวจ provenance เทียบ `source.ref` | error |
| `egress` | `network: allowlist` ต้องมี host จาก pack ที่ประกาศอย่างน้อยหนึ่ง; `general-http` คงขอบเขต Not guaranteed ตาม matrix | error |

Content scan ตรวจ regular files ยกเว้น `.thclaws/state/`, `.git/`, `__pycache__/`, `*.pyc`
และ `builder-build-report.json`; ข้าม symlink และ content ที่ decode UTF-8 ไม่ได้ โดย static inventory ยังตรวจอยู่.
รายละเอียด regex อยู่ใน DESIGN §7; การไม่พบ pattern ไม่ใช่หลักฐานว่าไม่มี secret ทุกรูปแบบ.

`shippable` ต้องมี live evidence ในรอบเดียวกัน (`--live --security`): live ผ่านเป็น `candidate`,
`compatibility.status: matched`, strict static ไม่มี finding และ security ผ่าน จึงเลื่อนสถานะได้.
ไม่ใช้ `candidate` ที่บันทึกไว้จากรอบก่อน; `--security` อย่างเดียวได้สูงสุด `draft`
หรือคง `unverified` เมื่อไม่มี binary. Live ยังต้องมี provider key ตาม M6/M9.

Report ใช้ `audit.security: not_run | passed | failed` และมี `security_audit` เมื่อและเฉพาะเมื่อ
security ถูกรัน: `{status, strict: true, findings[], scanned_files, detail}`.
Schema กัน `shippable` ที่ไม่มี live/security/strict/compatibility evidence และกัน security passed ที่มี finding.
Exit code ของ `forge audit --security`: `0` = การตรวจที่รันผ่าน, `1` = static/manifest/live/security ใด failed,
`2` = manifest skipped หรือขอ live แล้ว skipped; skip ไม่เลื่อนเป็น `shippable`.

Security audit ไม่พิสูจน์ egress enforcement, runtime isolation, deployment, human approval หรือ
MCP behaviour นอก pack conformance; ไม่เปลี่ยนแถวใดใน guarantee matrix (DESIGN §6)
และไม่ตั้ง `deployment_verified` ใน export.

```bash
python3 scripts/check_m19_security_audit.py
```

Gate ตรวจ offline negative cases, report schema, determinism/ไม่เขียน package, synthetic promotion rule,
CLI และ studio; native ตรวจ baseline validation และ live golden cases 4/4 ผ่าน scripted provider บน
`127.0.0.1` พร้อม `echo__reverse` แล้วเลื่อนเป็น `shippable` ทั้ง Python API และ CLI โดยไม่ใช้ key จริง.
`--timeout` กำหนดวินาทีต่อ live case (default 180).
Exit `0` = offline + native ผ่านทั้งหมด; `1` = assertion/การตรวจล้มเหลว (รวม binary ไม่ตรง baseline);
`2` = offline ผ่านแต่ไม่มี thclaws หรือเป็น CLI-only build ที่ไม่มี `--serve` จึงประกาศ SKIP.
Synthetic promotion เป็นหลักฐานของกฎเท่านั้น; scripted provider ไม่ใช่หลักฐานพฤติกรรมของ model จริง.
