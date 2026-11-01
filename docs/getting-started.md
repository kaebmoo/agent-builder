# เริ่มต้นใช้ agent-builder (คู่มือสำหรับมือใหม่)

คู่มือนี้พาทำตั้งแต่ติดตั้งจนได้ agent package ตัวแรก อ่านจบแล้วจะรู้ว่า
ได้อะไรออกมา เอาไปใช้ที่ไหน และลองอะไรต่อได้ ทุกคำสั่งในหน้านี้รันทดสอบแล้วบน macOS
(Python 3.14, thClaws 0.146.0) เอกสารเชิงลึกอยู่ที่ [README](../README.md), [agentspec.md](agentspec.md),
[generator.md](generator.md), [audit.md](audit.md), [export.md](export.md)
ฉบับภาษาอังกฤษ: [getting-started.en.md](getting-started.en.md)

## 1. agent-builder คืออะไร ใน 1 นาที

คุณเขียน **AgentSpec** (ไฟล์ YAML ไฟล์เดียวที่บอกว่า agent ทำอะไร รับ input อะไร ตอบ JSON หน้าตาไหน
ใช้ tool อะไร ห้ามทำอะไร) แล้วให้คำสั่ง `forge` สร้าง **thClaws agent package** (โฟลเดอร์ที่ thClaws รันได้)
พร้อม **build report** ที่บอกชัดว่าตรวจอะไรผ่านแล้ว อะไรยังไม่ได้ตรวจ

```
spec.yaml  →  forge generate  →  package/  →  forge audit  →  (thClaws รัน / Atlas ลงทะเบียน)
```

สิ่งที่ builder ไม่ทำ: ไม่ให้ LLM ตัดสินเรื่อง permission หรือ secret, ไม่ deploy ให้, ไม่รับรองสิ่งที่ยังไม่ได้พิสูจน์
สถานะของ package จึงไต่ขึ้นทีละขั้นตามหลักฐานจริง

| สถานะ | ได้เมื่อ | ความหมาย |
|---|---|---|
| `unverified` | generate เสร็จ | ยังไม่ได้ตรวจอะไรเลย (หรือไม่มี thClaws ให้ตรวจ) |
| `draft` | static audit ผ่าน + `thclaws agent validate` ผ่าน | โครงสร้างถูกต้อง thClaws ยอมรับ แต่ยังไม่เคยรันกับ model จริง |
| `candidate` | live audit ผ่าน golden cases ครบ | รันกับ model จริงแล้วตอบตรง schema ทุกกรณี |
| `shippable` | live + security audit ผ่านในรอบเดียวกัน | พร้อมส่งต่อให้ operator deploy (ยังไม่ใช่ "deploy แล้ว") |

## 2. เตรียมเครื่อง เลือกได้ 3 ระดับ

| ระดับ | ต้องมี | ทำได้ถึงไหน |
|---|---|---|
| A | Python 3.11+ | generate package, static audit, อ่าน report (สถานะสูงสุด `unverified`) |
| B | A + binary `thclaws` 0.146.0 | `draft`, รัน agent จริงด้วย `thclaws agent run`, export ให้ Atlas |
| C | B + provider key ของ model | live audit → `candidate`, security audit → `shippable` |

เริ่มที่ระดับ A ได้เลย ไม่ต้องมี key หรือ thClaws

### ระดับ A: ติดตั้ง builder

```bash
git clone https://github.com/kaebmoo/agent-builder.git
cd agent-builder
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/forge --help
```

ถ้าเห็นรายการคำสั่ง `generate`, `audit`, `live-test`, `export`, `pack` แปลว่าพร้อมแล้ว
(ถ้าเครื่องมี `python`/`pip` ที่ชี้ไป Python รุ่นเก่า ให้ใช้ `.venv/bin/python -m pip` ตามด้านบนเสมอ)

### ระดับ B: มี thClaws

ต้องเป็น desktop build ของ fork `kaebmoo/thClaws` ที่ revision `9f69995` (version 0.146.0)
build ด้วย `cargo build --features gui --bin thclaws` แล้วชี้ให้ builder เห็น:

```bash
export THCLAWS_BIN="$HOME/Documents/GitHub/thClaws/target/debug/thclaws"
export PATH="$(dirname "$THCLAWS_BIN"):$PATH"
export THCLAWS_DISABLE_KEYCHAIN=1   # กัน binary ที่ path ใหม่ค้างรอ dialog ของ macOS keychain
thclaws --version                    # ต้องได้ thclaws 0.146.0 / revision: 9f69995
```

รุ่นอื่นใช้ไม่ได้โดยตั้งใจ: builder ตรวจคู่ version+revision แล้ว fail ชัดจนกว่าจะมีคนตรวจพฤติกรรมรุ่นใหม่ (ดู [DESIGN §4](DESIGN.md))

ตั้งแต่ M22 ถ้ามี thClaws รุ่นอื่น ใช้ `--allow-unverified-runtime` กับ `forge audit` / `forge live-test` / `forge export` ได้:
builder ยังตรวจจริง แต่บันทึก `runtime: unverified`, ลดแถว `tool_allowlist`/`no_shell`/`output_write_scope`/`collect_files`
ที่เป็น `Enforced` ให้เป็น `Declared` และสถานะไม่เกิน `candidate` (ไม่ถึง `shippable`) — ดู [audit.md](audit.md#unverified-runtime-m22).

รายละเอียดการ build และข้อควรระวังอยู่ใน [local-test-plan.md](local-test-plan.md)

### ระดับ C: มี provider key

ตั้ง environment variable ตาม provider ของ model ที่ spec pin ไว้ เช่น model `gpt-5.4` ใช้ `OPENAI_API_KEY`
ถ้าใช้ gateway แบบ OpenAI-compatible ให้ pin model เป็น `oai/<model>` แล้วตั้ง `OPENAI_COMPAT_API_KEY` กับ `OPENAI_COMPAT_BASE_URL`
(ดู [audit.md: OpenAI-compatible endpoint](audit.md#openai-compatible-endpoint)) ห้ามใส่ค่า key ลงใน spec หรือ package เด็ดขาด

## 3. ลองครั้งแรกใน 5 นาที (ระดับ A)

ใช้ตัวอย่างที่มากับ repo: agent ที่รับข้อความแล้วส่งกลับตามเดิม

```bash
.venv/bin/forge generate fixtures/text-reviewer-standalone/spec.yaml --out out/text-reviewer
```

ผลที่คาด:

```
Generated 19 files in .../out/text-reviewer; package_status=unverified
Manifest, static, live and security audits have not run.
```

ดูว่าได้อะไรมา:

```bash
find out/text-reviewer -type f | sort
```

| ไฟล์ | คืออะไร |
|---|---|
| `AGENTS.md` | คำสั่งที่ agent อ่าน: mission, สิ่งที่ต้องทำ (Must), สิ่งที่ต้องปฏิเสธ (Refuse), รูปแบบ output |
| `agentspec.json` | spec ของคุณในรูป JSON ทุกไฟล์อื่น render จากไฟล์นี้ |
| `manifest.json`, `.thclaws/settings.json` | สิ่งที่ thClaws ใช้: identity, model, tool allowlist |
| `.thclaws/schemas/*.json` | JSON Schema ของ input, output และ refusal |
| `.thclaws/agents/*.md`, `.thclaws/agent_workflow/run.js` | (standalone) role planner/worker/verifier และ workflow ที่เรียกตามลำดับ |
| `evaluation/golden-cases.json` | 4 กรณีทดสอบ: normal, missing input, refusal, malformed |
| `builder-build-report.json` | สถานะ, guarantee matrix และผล audit ทุกชั้น |
| `audit.py`, `studio.py` | runner สั้น ๆ: ตรวจอย่างเดียว / ตรวจแล้วบันทึกผลลง report |

ตรวจ package:

```bash
.venv/bin/forge audit out/text-reviewer
```

ไม่มี thClaws จะได้แบบนี้ (exit code 2 = ข้ามแบบประกาศชัด ไม่ใช่ error):

```
thclaws agent validate: skipped
thclaws is not on PATH (THCLAWS_BIN); manifest validation not run, package stays unverified
package_status=unverified (SKIP)
```

มี thClaws (ระดับ B) ให้ใส่ `--write` เพื่อบันทึกผลลง report:

```
thclaws agent validate: passed (thclaws 0.146.0 revision: 9f69995 (main))
builder-build-report.json updated
package_status=draft (PASS)
```

ความหมายของ exit code ทุกคำสั่งและทุก gate ใน repo เหมือนกัน: `0` ผ่าน, `1` ไม่ผ่าน, `2` ข้ามเพราะขาดของภายนอก (binary หรือ key)

## 4. เขียน spec ของตัวเอง

ก่อนเขียน ตอบ 7 ข้อนี้กับตัวเอง (ชุดเดียวกับที่ builder-agent ใช้สัมภาษณ์):

1. agent ทำอะไร หนึ่งย่อหน้า และชื่อสั้น ๆ แบบ kebab-case
2. รันที่ไหน: `thclaws-standalone` (รันเองบนเครื่อง) หรือ Atlas (`atlas-worker` / `atlas-workflow`)
3. input/output: ชื่อ, transport และ JSON Schema; output เป็น `assistant_json` ตัวเดียว
4. ต้องใช้ capability pack ไหม (เช่น SQL อ่านอย่างเดียว) ถ้าไม่ต้องให้ `capabilities: []`
5. permission: tier (`T0` อ่านอย่างเดียว ปลอดภัยสุด), tools, shell, network, write_scope และ **ชื่อ** env ที่ต้องมี
6. ต้องปฏิเสธเมื่อไร และตอบกลับอย่างไร
7. model ที่ pin และ golden cases 4 แบบ

ตัวอย่างเต็มที่ generate และ audit ผ่านแล้ว (บันทึกเป็น `work/my-summarizer/spec.yaml`):

```yaml
identity:
  name: my-summarizer
  version: 0.1.0
  owner: your-name
  license: Apache-2.0
  thclaws_min_version: ">=0.146.0"
mission:
  purpose: Summarize the supplied Thai or English text into at most three sentences and report the language detected.
  domain: text-summary
target: thclaws-standalone
package_pattern: static-pipeline
inputs:
  - name: request
    transport: prompt_json
    schema:
      type: object
      additionalProperties: false
      required: [text]
      properties:
        text: {type: string, minLength: 1, maxLength: 20000}
outputs:
  - name: summary
    transport: assistant_json
    schema:
      type: object
      additionalProperties: false
      required: [summary, language]
      properties:
        summary: {type: string, minLength: 1}
        language: {type: string, enum: [th, en, other]}
capabilities: []
permissions:
  tier: T0
  tools: [Read]
  shell: none
  network: none
  write_scope: none
refusal:
  conditions:
    - A required input is missing or malformed.
    - The request asks to send messages, browse the web or perform any side effect.
  response: Return a JSON refusal with the reason.
  schema:
    type: object
    additionalProperties: false
    required: [refused, reason]
    properties:
      refused: {const: true}
      reason: {type: string}
model:
  mode: pinned
  id: gpt-5.4
env: []
evaluation:
  golden_cases:
    - id: normal
      kind: normal
      expect: result
      input:
        request: {text: "agent-builder แปลง AgentSpec เป็น package ของ thClaws ที่ตรวจสอบได้ แล้วส่งต่อให้ Atlas ลงทะเบียนเป็น worker"}
    - id: missing-input
      kind: missing_input
      expect: refusal
      input: {}
    - id: refusal
      kind: refusal
      expect: refusal
      input:
        request: {text: "Email this summary to the whole company right now."}
    - id: malformed
      kind: malformed
      expect: refusal
      input:
        request: {text: 42}
state:
  mode: none
  scope: turn
```

```bash
.venv/bin/forge generate work/my-summarizer/spec.yaml --out out/my-summarizer
.venv/bin/forge audit out/my-summarizer --write
```

สิ่งที่มักติดตอนแรก (builder จะบอกเหตุผลเป็นข้อความ ไม่ปล่อยผ่านเงียบ):

- `T0` ห้ามมี `Bash`, tool เขียนไฟล์ หรือ tool ที่ออก network (`WebFetch`, `WebSearch`, ...) ถ้าต้องการ web ให้ไปที่ tier สูงขึ้นและเข้าใจว่า egress ไม่ถูกบังคับที่ runtime
- ชื่อ tool ต้องเป็นชื่อที่ thClaws รู้จัก (built-in เช่น `Read`, `Grep`, `Glob` หรือ MCP tool รูป `<server>__<tool>` จาก pack) ชื่อนอก catalog เป็น warning ที่กัน `shippable`
- `permissions.tools` ต้องไม่ว่าง; golden cases ต้องมีครบ 4 แบบ; output ต้องมี `schema`
- `env` ใส่ได้เฉพาะ **ชื่อ** ตัวแปร ห้ามใส่ค่า
- แก้ไฟล์ใน package ด้วยมือแล้ว audit จะฟ้อง `drift` ให้แก้ที่ spec แล้ว generate ใหม่ (ยกเว้น Atlas package ที่แก้ `AGENTS.md` และ `SKILL.md` ได้ตราบที่เนื้อหาบังคับยังอยู่)

## 5. รัน agent จริง (ระดับ B + key)

package แบบ standalone รันบนเครื่องได้ทันทีด้วย thClaws CLI (ต้องตั้ง key ของ model ที่ pin ไว้ก่อน):

```bash
thclaws agent run out/my-summarizer --workflow .thclaws/agent_workflow/run.js \
  --args '{"request":{"text":"agent-builder แปลง AgentSpec เป็น package ของ thClaws ที่ตรวจสอบได้"}}'
```

stdout เป็น JSON ตาม schema ของ output (หรือ refusal) ต่อด้วยสรุปจำนวน turn ของ subagent
ถ้า package เป็น Atlas worker จะไม่รันด้วยคำสั่งนี้ แต่ start เป็น daemon แล้วให้ Atlas เรียก (ข้อ 7)

## 6. ให้ builder พิสูจน์ด้วย live audit (ระดับ C)

live audit ยิง golden cases ทั้ง 4 เข้า agent จริงแบบ isolated (คัดลอก package ไป temp, HOME แยก, ไม่แตะเครื่องคุณ)
แล้ว validate คำตอบกับ schema ทีละ branch:

```bash
.venv/bin/forge live-test out/my-summarizer --write                      # ผ่าน → candidate
.venv/bin/forge audit out/my-summarizer --live --security --write        # live + security ผ่านในรอบเดียวกัน → shippable
```

model ที่ pin เป็น `oai/...` ให้เพิ่ม `--provider-key-env OPENAI_COMPAT_API_KEY` ไม่มี key ได้ exit 2 และสถานะคง `draft`

`--security` ตรวจแบบ deterministic 4 ข้อ: static audit แบบ strict ไม่มี warning, ไม่มีค่า secret หรือ path ชื่อแบบ secret ในไฟล์,
pack ทุกตัวผ่าน `forge pack test`, และ `network: allowlist` ต้องประกาศ host ถ้าผ่านแต่ไม่มี live ในรอบเดียวกันจะเห็นแบบนี้:

```
security: passed (scanned 18 files)
package_status=draft (PASS)
```

อ่าน report ตรง ๆ ได้ที่ `out/my-summarizer/builder-build-report.json` ส่วนสำคัญคือ `package_status`, `audit` (ผลแต่ละชั้น)
และ `guarantee_matrix` ที่บอกทีละข้อว่า claim นั้น Enforced, Declared, Evidence หรือ Not guaranteed
ตัวอย่างใน T0 standalone: `tool_allowlist` และ `no_shell` เป็น Enforced แต่ `network_hosts` เป็น Not guaranteed
นี่คือสิ่งที่พูดได้จริงเกี่ยวกับ package ห้ามอ้างเกินกว่าตารางนี้ (ไม่มีสถานะใดแปลว่า "deploy-ready")

## 7. เอาไปใช้งาน

**แบบ 1: รันเองบนเครื่อง (standalone)** ใช้คำสั่งในข้อ 5 จาก script หรือ cron ของคุณ input เป็น JSON ตาม schema, output เป็น JSON ตาม schema
เหมาะกับงาน batch หรือ pipeline ที่ไม่ต้องมี orchestrator
หรือติดตั้ง package เป็น **bot ของ thClaws** ไว้คุยผ่าน CLI/desktop: upload archive จาก `forge export` ไป catalogue (ทำใน session ด้วย `/cloud publish`; CLI ไม่มีคำสั่งนี้แล้ว)
แล้วผู้ใช้สั่ง `thclaws bots add <slug>` หรือติดตั้งจาก desktop ลง `.thclaws/bots/<slug>/` ข้อควรรู้: ถ้าเครื่องที่ติดตั้งไม่มี credential ของ provider ที่ spec pin
thClaws จะแทน model ด้วย model ของ host ตอนติดตั้ง และต้อง audit กับ source package ไม่ใช่ tree ที่ติดตั้ง (DESIGN §4 "Bot channel")

**แบบ 2: เป็น worker ของ Atlas** เขียน spec ให้ `target: atlas-worker` (มี `routing.role`/`tags` และไม่มี `package_pattern`)
แล้ว export หลัง audit:

```bash
.venv/bin/forge export out/<pkg> --out out/<pkg>-export \
  --base-url http://<daemon-host>:4317 \
  --workspace-dir /srv/atlas/<pkg>
```

ได้ `atlas-register.json` (payload worker/workspace, node template, รายการ env และ reachability checks), `atlas-workflow.json` (เฉพาะ `atlas-workflow`)
และ archive `.tar.gz` + `.sha256` จากนั้น operator ทำ 5 ขั้นด้วยมือตาม [export.md](export.md#ลำดับลงทะเบียน-ทำด้วยมือ):
แตก archive บนเครื่อง worker → start `thclaws --serve` ด้วย `THCLAWS_AUTO_MIGRATE=0` และ env ตามชื่อใน report →
`POST /api/workers` และ `/api/workspaces` → ใส่ node template ลง workflow → poll ให้ `online`
ข้อควรระวังสำคัญ: ห้ามเปิดโฟลเดอร์ package ด้วย thClaws desktop app เพราะมันจะแปลงโครงสร้างในที่เดิมจน Atlas เรียกไม่ตรง
บน host จริงใช้ [deploy-runbook.md](deploy-runbook.md) (M18a): prerequisites, launcher systemd/launchd ที่ `scripts/render_launcher.py` render จาก `atlas-register.json`
(ชื่อ env เท่านั้น ไม่มีค่า), ขั้นตรวจ daemon/Atlas, rollback และ checklist ที่ operator ติ๊ก `deployment_verified` เองนอก builder

**แบบ 3: ให้ thClaws สร้าง agent ให้** คุยกับ thClaws เป็นภาษาคนแล้วให้มันเขียน spec และรัน builder แทนคุณ ดูข้อ 8 ถัดไป

## 8. ให้ thClaws สร้าง agent ให้ (builder-agent)

โฟลเดอร์ `builder/` ใน repo นี้คือ workspace ของ thClaws ที่เตรียมไว้ให้ model เป็น "ผู้สัมภาษณ์และคนกดปุ่ม build"
ส่วนการตัดสินว่า package ใช้ได้หรือไม่ยังเป็นของ `forge` ทั้งหมด (model ห้ามตัดสินเรื่อง permission, network, secret หรือบอกว่าผ่าน audit เอง)

**ขั้นตอน**

1. เปิดโฟลเดอร์ `builder/` ด้วย thClaws desktop build (หรือ `thclaws --serve` จากโฟลเดอร์นั้น) โฟลเดอร์นี้เป็น host workspace อยู่แล้ว
   thClaws จะเห็น agent ที่ `.thclaws/bots/main/` เอง ห้ามรัน `thclaws bots migrate` กับโฟลเดอร์นี้
2. พิมพ์ความต้องการ เช่น "ต้องการ agent สรุปรายงานประจำวันจากไฟล์ Markdown แล้วตอบเป็น JSON"
3. model ถามเฉพาะข้อที่ยังไม่รู้จาก 7 ข้อในข้อ 4 แล้วเขียน `work/<name>/spec.yaml` ให้
4. model หยุดและแสดง block `permissions` / `env` / `capabilities` ให้คุณยืนยันในเทิร์นถัดไป กติกาคือ model ห้ามเติม marker `confirmed_by` เอง
   คนต้องเป็นผู้ยืนยัน (ตอบว่ายืนยัน พร้อมชื่อของคุณ)
5. หลังยืนยัน model รัน `build.py` ซึ่งเรียก `forge pack test` → `forge generate` → `forge audit --write` แล้วรายงานสถานะจาก
   `builder-build-report.json` ได้ package ที่ `builder/out/<name>/` ใช้ต่อตามข้อ 5–7 ได้เหมือน package ที่สร้างเอง

**สิ่งที่ thClaws ทำไม่ได้ในขั้นนี้ (ตั้งใจออกแบบ)**

- ประดิษฐ์ pack หรือ MCP ขึ้นเอง ใช้ได้เฉพาะ pack ที่คำสั่ง `--list-packs` พิมพ์ (วันนี้คือ 5 pack ตามตารางในข้อ 9 ไม่รวม `echo`)
  ขอความสามารถที่ไม่มี เช่น Google Weather report จะบอก `missing` ตรง ๆ ไม่เดาชื่อ tool
- ตัดสินหรืออ้างผล audit เอง ทุกบรรทัดสถานะคัดลอกจากผลของ forge
- live audit, export และ deploy ให้ Atlas ยังเป็นขั้นที่คุณสั่งเองตามข้อ 6–7

**ทางลัดไม่ใช้ LLM** ถ้าเขียน spec เองได้แล้ว รัน runner เดียวกันจาก terminal ธรรมดา:

```bash
python3 builder/.thclaws/bots/main/.thclaws/scripts/build.py --list-packs
python3 builder/.thclaws/bots/main/.thclaws/scripts/build.py work/my-summarizer/spec.yaml --out out/my-summarizer
```

spec ต้องมี block `confirmed_by` (ดู [builder/README.md](../builder/README.md)) exit code เหมือน forge: `0` = `draft`, `1` = ถูกปฏิเสธหรือชั้นใดไม่ผ่าน,
`2` = ไม่มี `thclaws` จึงคง `unverified`

**ความพร้อมจริง** การสัมภาษณ์สองเทิร์นผ่าน gate M11 live 3/3 รอบบน thClaws 0.138.0 (2026-10-28) และ 4/6 รอบบน 0.146.0 (2026-10-31)
ด้วย `gpt-5.4-mini` รอบที่ไม่ผ่านเกิดจาก model จบเทิร์นก่อนตอบครบ ไม่ใช่โค้ดของ builder model ที่แรงกว่าจะนิ่งกว่า
ต้องเป็น desktop build เพราะ CLI-only build ไม่มี `--serve`

## 9. ลองต่อได้อีก

- **ใช้ความสามารถภายนอกผ่าน pack**: ใส่ชื่อ pack ใน `capabilities` แล้ว builder เติม MCP config, tool และชื่อ env ให้เอง วันนี้มี pack ให้ใช้ดังนี้

| pack | ทำอะไร | tier/network | tools (`<server>__<tool>`) | env ที่ operator ต้องตั้ง | ตัวอย่าง spec |
|---|---|---|---|---|---|
| `sql-readonly` | query ฐานข้อมูล SQLite แบบอ่านอย่างเดียว (ดู metadata, ตรวจ SQL, รัน query) ห่อ MCP ของโปรเจ็กต์ `kaebmoo/AI` ไม่มี admin tool | T0, none | `sql-readonly__metadata`, `__query`, `__validate` | `SQL_READONLY_AI_ROOT`, `SQL_READONLY_DSN` | `fixtures/sql-reader/spec.yaml` |
| `publisher-email-sftp` | ส่ง email หรือ upload ไฟล์ทาง SFTP ไปปลายทางที่ operator กำหนดไว้ล่วงหน้า ทุก call ต้องระบุ `dry_run` และ `idempotency_key` ส่งจริงต้องผ่าน `human_gate` ของ Atlas | T2, general-http | `publisher-email-sftp__send_email`, `__upload_file` | `PUBLISHER_CONFIG`, `PUBLISHER_INPUT_ROOT`, `PUBLISHER_STATE_DIR` | `fixtures/publisher/spec.yaml` |
| `sqlite-readonly` | อ่าน SQLite ไฟล์ใดก็ได้ด้วย `mode=ro` + authorizer | T0, none | `sqlite-readonly__tables`, `__schema`, `__query` | `SQLITE_READONLY_DB` | `fixtures/sqlite-reader/spec.yaml` |
| `csv-readonly` | ดูไฟล์, header, preview และ aggregate CSV/TSV แบบอ่านอย่างเดียวภายในโฟลเดอร์ที่กำหนด | T0, none | `csv-readonly__files`, `__columns`, `__preview`, `__aggregate` | `CSV_READONLY_ROOT` | `fixtures/csv-reader/spec.yaml` |
| `datetime-tools` | ดูเวลา, แปลง timezone, หาระยะเวลา และบวกวันทำการ | T0, none | `datetime-tools__now`, `__convert`, `__diff`, `__add_business_days` | ไม่มี | `fixtures/datetime-helper/spec.yaml` |
| `echo` (ใน `fixtures/packs/`) | ตัวอย่างอ้างอิงเล็กสุด: echo/reverse ข้อความ ไม่มี env ไม่มี network ใช้เป็นต้นแบบเขียน pack และใช้ใน gate ไม่ใช่ความสามารถสำหรับงานจริง | T0, none | `echo__echo`, `echo__reverse` | ไม่มี | `fixtures/packs/echo/spec.yaml` (ต้องใช้ `--packs-dir fixtures/packs`) |

  ทั้งสาม pack ใหม่เป็นตัวอย่างอิสระ (M21, stdlib เท่านั้น ใช้กับ target ใดก็ได้); งาน email/SFTP ใช้ `publisher-email-sftp` กับงานทั่วไปได้ โดย operator ตั้งปลายทางและ credentials ใน config ตามการออกแบบ เพื่อให้ agent ไม่เป็นผู้เลือกผู้รับ.

  `sql-readonly` ต้องมี checkout ของ `kaebmoo/AI` ที่ revision `d95dc28` วางข้าง agent-builder ชื่อ `AI` และติดตั้ง `pip install -e ".[sql-pack]"` (`mcp>=1.26,<2`)
  ส่วน `publisher-email-sftp` ต้องมี OpenSSH `sftp` บน PATH เมื่อส่งจริง ดูรายละเอียดใน README ของแต่ละ pack
- **เขียน pack ของตัวเอง** เมื่อมี MCP server อยู่แล้ว: `forge pack new <name>` → `forge pack discover --name <name> --command <cmd>` → เติม tier/network/fixture → `forge pack test <name>`
  ดู [packs.md](packs.md) ตัวอย่างเล็กสุดอยู่ที่ `fixtures/packs/echo/`
- **T1 standalone ที่เขียนไฟล์ได้เฉพาะ `output/`**: ดู `fixtures/note-writer-standalone/spec.yaml` (`write_scope: output`, tools เพิ่ม `Write`/`Edit`)
- **ตรวจว่า builder เองยังทำงานถูก**: `python3 scripts/check_m5_static_audit.py` และ gate อื่นใน `scripts/` (exit 2 เมื่อไม่มี binary/key เป็นเรื่องปกติ)

## 10. ถาม-ตอบสั้น

- **ทำไมได้แค่ `unverified`** สองสาเหตุ: (1) ไม่มี `thclaws` ให้ validate (exit 2) ตั้ง `THCLAWS_BIN` ตามข้อ 2 แล้วรัน `forge audit --write` ใหม่; (2) มี `thclaws` แต่ไม่ใช่ baseline `0.146.0`/`9f69995` (exit 1, บรรทัด `is not the verified baseline`) ถ้าต้องใช้รุ่นนั้นให้เพิ่ม `--allow-unverified-runtime` (M22) จะได้ `draft` โดย report บันทึก `runtime: unverified`, แถว guarantee ที่พึ่ง runtime เป็น `Declared` และสถานะไม่เกิน `candidate` ดู [audit.md](audit.md#unverified-runtime-m22)
- **ทำไม `candidate` ไม่กลายเป็น `shippable` เอง** security audit ต้องรันในรอบเดียวกับ live (`--live --security`) builder ไม่เชื่อสถานะเก่าใน report
- **แก้ package ตรง ๆ ได้ไหม** standalone: ไม่ได้ ให้แก้ spec แล้ว generate ใหม่; Atlas: แก้ได้เฉพาะ `AGENTS.md` และ `.thclaws/skills/<name>/SKILL.md`
- **key เก็บที่ไหน** ใน environment ของเครื่องที่รัน daemon หรือ CLI เท่านั้น spec ประกาศแค่ชื่อตัวแปร และ security audit จะฟ้องถ้าพบค่าในไฟล์
- **`shippable` แปลว่า deploy ได้เลยไหม** ไม่ใช่ แปลว่าหลักฐานใน builder ครบ การ deploy, egress control และ human approval เป็นงานของ operator และ Atlas และ `deployment_verified` ใน export เป็น `false` เสมอจนกว่า operator จะตรวจเอง

## 11. ข้อจำกัดและสิ่งที่ต้องทำเอง

สรุปจาก [DESIGN §6](DESIGN.md) (guarantee matrix คือคำรับรองเดียวของ builder), [export.md](export.md) และ [PLAN](PLAN.md)
ส่วน "สถานะ 2026-10-31" แยกเป็นสองกลุ่ม: ข้อจำกัดของ agent-builder เองที่ใช้กับทุกคน และข้อจำกัดที่เกิดจาก project หรือ dependency ภายนอกที่ builder ผูกอยู่

### 11.1 ข้อจำกัดทั่วไปของ agent-builder

| ข้อจำกัด | ถ้าต้องการ ต้องทำอะไร / อย่างไร |
|---|---|
| ไม่ deploy ให้: ไม่มี launcher, secret provisioning, monitoring; `deployment_verified` ใน export เป็น `false` เสมอ | ทำ 5 ขั้นใน [export.md](export.md#ลำดับลงทะเบียน-ทำด้วยมือ) ด้วยมือ: แตก archive, start `thclaws --serve` ด้วย `THCLAWS_AUTO_MIGRATE=0` + env ตามชื่อใน report, POST worker/workspace, ใส่ node template, poll ให้ `online`; เขียน launchd/systemd unit เองและบันทึกผลตรวจ deployment นอก builder (M18 ที่เสนอยังไม่ทำ) |
| LLM ไม่ตัดสินเรื่อง permission, network, secret, MCP หรือผล audit (ตั้งใจออกแบบ) | เขียนส่วนเหล่านี้ใน spec เองเสมอ; builder-agent (ข้อ 8) ทำได้แค่ร่างแล้วให้คนยืนยันด้วย `confirmed_by` |
| `shippable` แปลว่าหลักฐานใน builder ครบ ไม่ใช่ deploy-ready; ต้องมี live + security ในรอบเดียวกัน และ builder ไม่เชื่อสถานะเก่าใน report | รัน `forge audit --live --security --write` ทุกครั้งก่อนส่งต่อ แล้วให้ operator ตรวจ egress, approval และ deployment เอง |
| M22: runtime ที่ไม่ใช่ baseline ใช้ได้ด้วย `--allow-unverified-runtime` แต่แถว guarantee ที่พึ่ง runtime เป็นแค่ Declared และไม่มีทางถึง `shippable` | review DESIGN §4 กับ source ของรุ่นนั้นแล้ว re-baseline แบบ M17 ถ้าต้องการคำรับรองเต็ม |
| network egress เป็น Not guaranteed ที่ runtime: `network: none` ตรวจแค่ static ว่าไม่มี web tool, `allowlist` ตรวจแค่ว่าประกาศ host | ใช้ firewall หรือ egress proxy ของเครื่อง/เครือข่ายให้ตรงกับ `deployment.network`/`hosts` ใน `atlas-register.json` |
| `no_file_writes` Not guaranteed สำหรับ writer ที่ประกาศ, MCP และ runtime state; Skill/MCP tools อยู่นอก allowlist claim; T1 `output/**` Enforced เฉพาะ standalone `static-pipeline` | รัน daemon แยกต่อ package ด้วย user หรือ sandbox ของ OS, ไม่ใส่ MCP ที่เขียนไฟล์ถ้าไม่จำเป็น; การบังคับใน runtime ต้อง patch thClaws upstream (DESIGN §10) |
| human approval ของ T2 เป็น Not verified: package มี approval fragment แต่ไม่พิสูจน์ว่า runtime ผูก approval กับ tool arguments; เรียก `/agent/run` ตรงจะข้าม gate | deploy T2 บน daemon แยก ให้ทุก run เข้าทาง Atlas `human_gate` และไม่ expose daemon ให้เรียกตรง (M20 ที่เสนอยังไม่ทำ) |
| output schema บังคับเฉพาะใน live audit; Atlas ทำแค่ `json.loads` | validate output ด้วย `.thclaws/schemas/*.json` ฝั่งผู้บริโภคเอง |
| standalone รองรับเฉพาะ `static-pipeline` (`batch-fanout`/`dynamic` ถูก reject); Atlas package เป็น node เดียว; `--layout host` รับเฉพาะ `prompt_json`/`assistant_json` และหนึ่ง package ต่อ host | งานหลายขั้นให้แยก spec ต่อ node แล้วประกอบ workflow ใน Atlas เอง; pattern dynamic ยังไม่มีทางทำ |
| แก้ไฟล์ใน package ตรงไม่ได้ (audit ฟ้อง `drift`) ยกเว้น `AGENTS.md`/`SKILL.md` ของ Atlas package; ยังไม่มี `must_call` ใน schema | แก้ spec แล้ว generate ใหม่; ถ้าต้องยืนยันว่า tool ถูกเรียก ดูจาก SSE evidence ของ live audit |
| หลักฐาน native/live อยู่ใน `out/` (gitignored) บนเครื่องที่รัน; GitHub CI ตรวจจริงแค่ M0–M3 + ruff | รัน gate ใน `scripts/` เองด้วย `THCLAWS_BIN` ก่อนเชื่อสถานะ หรือตั้ง self-hosted runner ที่มี binary และ key |
| pack ตัวอย่างอิสระ 3 ตัว (M21) ไม่ใช่ service ที่ harden: ไม่มี OS sandbox, ไม่มี row-level access control, `csv-readonly` อ่านทั้งไฟล์เข้า memory | ใช้กับข้อมูลที่ให้ agent อ่านได้ทั้งหมดอยู่แล้ว; ถ้าต้อง harden ให้ copy pack ไปแก้แล้ว `forge pack test` ใหม่ |

### 11.2 ข้อจำกัดที่ผูกกับ project หรือ dependency ภายนอก

| project | ข้อจำกัด | ถ้าต้องการ ต้องทำอะไร / อย่างไร |
|---|---|---|
| thClaws (fork `kaebmoo/thClaws`) | ต้องเป็น desktop build ที่ baseline `0.146.0`/`9f69995` สำหรับ daemon, native gate และ export archive; version อื่น fail 1 ตามออกแบบ เว้นแต่เปิด `--allow-unverified-runtime` (M22) ซึ่งลด guarantee และหยุดที่ `candidate`; CLI-only build ไม่มี `--serve` | build fork ที่ revision นั้นด้วย `cargo build --features gui --bin thclaws`; จะย้าย version ต้อง review DESIGN §4 ทุก claim กับ source ก่อน (แบบ M12/M17) |
| thClaws | desktop app และ `--serve` แปลงโฟลเดอร์ที่เปิดเป็น host workspace ในที่เดิม (auto-migrate) ทำให้ package ของ Atlas ไม่ใช่ agent อีกต่อไป | start daemon ด้วย `THCLAWS_AUTO_MIGRATE=0` เท่านั้น ห้ามเปิด package ใน desktop app หรือรัน `thclaws bots migrate`; ถ้าเกิดขึ้นให้ deploy ใหม่จาก archive |
| thClaws | allowlist ของ built-in tool fail-open ถ้า `.thclaws/settings.json` หายหรือตั้ง `THCLAWS_PROJECT_ROOT`; MCP ของ pack ต้องอยู่ใน `~/.config/thclaws/mcp_allowlist.json` ทั้ง command line เว้นแต่ตั้ง `THCLAWS_MCP_ALLOW_ALL=1`; engine ฉีด MCP เพิ่มเองเมื่อเครื่องมี office app หรือ AI Server hub | start daemon ด้วย CWD = โฟลเดอร์ package, ไม่ตั้ง `THCLAWS_PROJECT_ROOT`, provision mcp_allowlist ตาม `.thclaws/mcp.json`, ตรวจว่า `agent_info.agent.mcp_servers` ตรงกับ `deployment.mcp_servers` |
| Atlas control plane (`atlas-control-plane`) | builder ใช้แค่ `POST /agent/run`; schema pin ที่ revision `daa0f49`; `output_format: json` ไม่ตรวจ schema; upsert worker ด้วย `base_url` ซ้ำจะทับ worker เดิม | gate M8/M16 ต้องใช้ checkout ที่ `daa0f49` (`ATLAS_ROOT`); ใช้ URL แยกต่อ daemon หรือ `--worker-id` ให้ตรง worker เดิม; validate output เองตามข้อ 11.1 |
| repo `kaebmoo/AI` (pack `sql-readonly`) | pack ห่อ MCP ของ AI ที่ revision `d95dc28` และต้องใช้ `mcp>=1.26,<2`; AI รุ่นปัจจุบันห่างกว่า 300 commits ไม่เคยทดสอบ | วาง checkout ของ AI ที่ `d95dc28` ชื่อ `AI` ข้าง agent-builder แล้ว `pip install -e ".[sql-pack]"`; งาน SQLite ทั่วไปใช้ `sqlite-readonly` แทนได้โดยไม่ต้องมี AI |
| model provider / gateway | live audit ต้องมี key ของ provider แบบ OpenAI-compatible; fixture pin `gpt-5.4` แต่หลักฐานทั้งหมดใช้ `oai/gpt-5.4-mini`; builder-agent ผ่าน 4/6 รอบเพราะ model จบเทิร์นก่อนตอบครบ | ตั้ง `OPENAI_API_KEY` หรือ `OPENAI_COMPAT_API_KEY` + `OPENAI_COMPAT_BASE_URL` ใน environment; pin `model.id` เป็นรุ่นที่ gateway มีจริงแล้วรัน live 3 รอบ; model ที่แรงกว่าจะนิ่งกว่า |
| เครื่องที่รัน (OS) | `datetime-tools` ต้องมี tz database ของเครื่อง; `publisher-email-sftp` ต้องมี OpenSSH `sftp` และ known_hosts ที่ operator provision; keychain ของ macOS อาจค้าง dialog ตอนเรียก binary ที่ path ใหม่ | ติดตั้ง tzdata/OpenSSH ตามเครื่อง; ตั้ง `THCLAWS_DISABLE_KEYCHAIN=1` ตอนรัน CLI |

Milestone ที่เสนอเพื่อปิดข้อจำกัดเหล่านี้ (M18 deployment จริง, M20 T2 side-effect proof) อยู่ใน [PLAN](PLAN.md) และยังไม่เริ่ม
