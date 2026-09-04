# AgentSpec

`agentspec.schema.json` คือ SSOT ของ Agent Builder ทุกไฟล์ใน package ต้อง generate จาก spec นี้ ห้ามให้ LLM เขียน `manifest.json`, permission หรือ output contract แยกเอง

## โครงสร้างหลัก

| Field | หน้าที่ |
|---|---|
| `identity` | ชื่อ, version, owner, license และขั้นต่ำของ thClaws |
| `mission` | วัตถุประสงค์เดียวและ domain |
| `target` | `thclaws-standalone`, `atlas-worker` หรือ `atlas-workflow` |
| `package_pattern` | ต้องมีเฉพาะ `thclaws-standalone`: `static-pipeline`, `batch-fanout` หรือ `dynamic` |
| `routing` | ต้องมีเฉพาะ `atlas-*`: `role` และ `tags` สำหรับ Atlas routing |
| `inputs` | input และ transport: `prompt_json`, `atlas_file_handoff` หรือ `local_workspace`; ค่าหลังคือไฟล์ที่ provision ไว้ใน worker workspace ก่อน run |
| `outputs` | output และ transport: `assistant_json` ต้องมี `schema`; `collect_files` ต้องมี `files.globs` และอาจมี schema ของ artifact manifest |
| `capabilities` | pack ที่จะประกอบเข้า package พร้อม parameters |
| `permissions` | ระดับ T0/T1/T2, declared tools/shell/network และ `write_scope` (`none`, `output`, `workspace`) |
| `refusal` | เงื่อนไข refuse/handoff และ response ที่ต้องใช้ |
| `model` | model ที่ pin หรือ policy ที่อนุญาต |
| `env` | ชื่อ environment variables ที่ต้องมีเท่านั้น ห้ามใส่ค่า secret |
| `evaluation` | golden cases อย่างน้อย normal, missing input, refusal และ malformed พร้อม `expect` branch (`result` / `refusal`) |
| `state` | standalone ใช้ `none/turn` หรือ `session/session`; Atlas ใช้ `none/turn`; `durable_memory` ยังไม่รองรับใน M1 |

## Target และ pattern

ผู้ใช้กรอก `target` และเลือก `package_pattern` เฉพาะเมื่อเป็น standalone ส่วน Atlas ไม่รับ package pattern เพราะ package เป็น single worker เสมอ Builder คำนวณ execution surface จาก compatibility matrix และไม่รับ `execution_surface` เป็น input

| Target | Package | Flow |
|---|---|---|
| `thclaws-standalone` | `static-pipeline`, `batch-fanout`, `dynamic` | workflow ภายใน package |
| `atlas-worker` | `single-worker` | ไม่มี flow ภายใน package |
| `atlas-workflow` | `single-worker` ต่อ node | Atlas workflow JSON |

`human-approval` และ `manager-loop` เป็น flow ของ Atlas ไม่ใช่ package pattern

`routing.role` และ `routing.tags` เป็นความตั้งใจด้าน routing ของ package ส่วน `worker_id`, `workspace_id`, `workspace_dir` และ `base_url` เป็น deployment-time values ที่เกิดตอน export/register ไม่อยู่ใน AgentSpec
`tags: []` หมายถึง route ด้วย `role` อย่างเดียวและเป็นค่าที่ถูกต้อง

## Permission และ guarantee

`permissions` เป็น declared capability ไม่ใช่หลักฐานว่า runtime enforce ได้แล้ว ทุก build ต้องสร้าง guarantee matrix แยกต่างหากว่า claim แต่ละข้ออยู่ในสถานะ `Declared`, `Enforced`, `Evidence` หรือ `Not guaranteed` และบอก enforcement mechanism

ตัวอย่างเช่น `/agent/run` ของ thClaws ปัจจุบัน register built-in tools รวมถึง Bash ดังนั้น `shell: none` ยังห้ามประกาศเป็น enforced หากไม่มี daemon boundary หรือ runtime policy อื่นมาบังคับ

M1 ตรวจ tier boundary แบบ deterministic: T0 ต้องเป็น `shell: none`, `network: none`, `write_scope: none`; T1 ต้องเป็น `shell: none`, `network: none`, `write_scope: output`; T2 ต้องเป็น `write_scope: workspace` แต่ใน M1 ใช้ได้เฉพาะ `atlas-workflow` ที่มี owner สำหรับ `human_gate`. ทุก tier ที่เป็น `shell: none` ห้ามประกาศ `Bash`, `write_scope: none` ห้ามประกาศ built-in write tools และ `network: none` ห้ามประกาศ `WebFetch`, `WebSearch`, `WebScrape`, `FetchImages` หรือ `YouTubeTranscript`

## Model และ input transport

ถ้าต้องการ reproducible package ให้ใช้:

```json
"model": {"mode": "pinned", "id": "provider-model-id"}
```

สำหรับไฟล์ input ของ Atlas ไม่ให้ผู้เขียนระบุ landing `path` เอง เพราะ Atlas สร้าง path เป็น `inputs/incoming/<run>/<node>/`. ถ้า agent T1 สร้างไฟล์ output ก็ระบุ glob ที่จะส่งกลับได้ เช่น:

```json
{
  "name": "brief",
  "transport": "collect_files",
  "files": {"globs": ["output/*.md"]}
}
```

`local_workspace` ใช้ได้กับทุก target และหมายถึงไฟล์ที่ operator หรือ deployment เตรียมไว้ใน worker workspace ก่อน dispatch; ไม่ใช่ artifact ที่ส่งผ่าน edge ของ Atlas

AgentSpec หนึ่งตัวประกาศ input หลาย transport ร่วมกันได้ เช่น `prompt_json` สำหรับคำสั่งและ `atlas_file_handoff` สำหรับไฟล์. Generator จะ map แต่ละ input ตาม transport ของมัน; Atlas ยังคงเป็นผู้กำหนด landing path ของ file handoff.

`push_files` เป็น field ของ **workflow edge** ที่เลือก artifact key จาก upstream node ไม่ใช่ field ของ worker node โดยตรง ส่วน `collect_files` เป็น field ของ node

`collect_files` ขอให้ thClaws snapshot ไฟล์ที่ match หลัง run จึงเก็บได้ทั้งไฟล์ที่ agent สร้างและไฟล์ที่มีอยู่ก่อน run; ไม่ใช่หลักฐานว่า agent ได้เขียนไฟล์ และไม่เปลี่ยน `write_scope`. `collect_files.schema` ถ้ามีหมายถึง schema ของ artifact manifest ไม่ใช่ JSON body ของ assistant ส่วน `refusal.schema` เป็น output contract แยกสำหรับ refusal branch; live audit เลือก schema จาก `golden_cases[].expect`

`identity.thclaws_min_version` ต้องมี minimum หรือ exact numeric version และอาจเพิ่ม upper compatibility bound ได้ เช่น `>=0.116.0,<0.120.0`; range ที่ขัดกันจะถูก reject ไม่รองรับ prerelease, `!=`, `~` หรือ `^`

## Golden cases

`evaluation.golden_cases[].input` เป็น mapping จาก `inputs[].name` ไปยังค่าของ input นั้น ห้ามมี key ที่ไม่ได้ประกาศ เพื่อให้ generator และ live audit map payload ได้แน่นอน. `kind` บอกลักษณะของ input ส่วน `expect` บอก branch ที่ live audit ต้อง validate: `result` ใช้ sole `assistant_json` schema, `refusal` ใช้ `refusal.schema`. M6 ยังไม่รองรับ result ที่มีหลาย output หรือ `collect_files` อย่างเดียว และจะรายงาน response ที่ผ่านทั้งสอง branch schema ว่า ambiguous.

`normal` ต้องมี `expect: result` และ `refusal` ต้องมี `expect: refusal`; `missing_input` กับ `malformed` เลือก branch ที่คาดหวังได้เอง. `normal` และ `refusal` ต้องให้ input ที่ประกาศครบและผ่าน schema ของ input (หากประกาศ schema). `missing_input` ต้องขาด input ที่ประกาศอย่างน้อยหนึ่งตัว ส่วน `malformed` ต้องให้ input ครบแต่มีอย่างน้อยหนึ่งตัวที่ไม่ผ่าน schema. จนกว่า AgentSpec จะมี field สำหรับ optional input, M2 ถือว่า input ที่ประกาศทุกตัวจำเป็นใน normal/refusal/malformed. Input ที่ไม่มี schema เป็น unconstrained แต่ fixture ต้องมีอย่างน้อยหนึ่ง input schema จึงจะสร้าง malformed case ได้.

`refusal.conditions` เป็น prompt material ไม่ใช่ test matrix; ไม่จำเป็นต้องมี golden case ต่อทุก condition. `expect` คือ contract ที่ deterministic ว่า live audit ต้องยอมรับ branch ใด.
