# Export + Atlas registration (M8)

`forge export` แปลง package ที่ผ่าน static audit แล้วเป็นชุดไฟล์สำหรับ operator ลงทะเบียนกับ Atlas
โดยไม่เรียก Atlas, ไม่ start daemon และไม่เลื่อนสถานะ package. กติกาและข้อเท็จจริงของ Atlas และ `thclaws agent pack` อยู่ใน
[DESIGN §8](DESIGN.md)

## ใช้งาน

```bash
forge generate fixtures/publisher/spec.yaml --out out/publisher
forge audit out/publisher --write
forge export out/publisher --out out/publisher-export \
  --base-url http://publisher-daemon.internal:4317 \
  --workspace-dir /srv/atlas/publisher
```

`forge audit --write` (หรือ `studio.py`) เป็น precondition จริง: export อ่าน `builder-build-report.json` และปฏิเสธเมื่อ
`audit.static` ไม่ใช่ `passed` หรือ `audit.manifest` เป็น `failed`. ถ้า report ถูกเขียนบนเครื่องที่ไม่มี `thclaws`
(`manifest: skipped`, `unverified`) แต่ตอน export มี binary export จะรัน `thclaws agent validate` เองก่อน pack และปฏิเสธเมื่อไม่ผ่าน.

ค่าที่ operator ให้คือ deployment-time binding (DESIGN §3) ไม่มี token หรือค่า secret ใด ๆ:

- `--base-url`: URL ของ daemon ที่ Atlas เรียก; รับเฉพาะ `http://`/`https://` ตัวพิมพ์เล็ก, host และ port ที่ถูกต้อง (ไม่รับ 0), path ได้,
  ห้าม credential, query (`?`) หรือ fragment (`#`); ช่องว่างหัวท้ายและ `/` ท้ายถูกตัด. หนึ่ง URL ต่อหนึ่ง worker (ดูข้อ 3 ด้านล่าง)
- `--workspace-dir`: directory ของ package บนเครื่อง worker ซึ่ง daemon ต้อง start จากที่นั่น; ต้องเป็น absolute POSIX path ไม่มี `..`
- `--worker-id` / `--workspace-id` / `--workspace-key` (optional; ค่าเริ่มต้น `wrk_<name>`, `wsp_<name>`, `<name>`): ตัวแรกเป็น
  ตัวอักษร/ตัวเลข ตามด้วย `[A-Za-z0-9_.-]` ยาวไม่เกิน 128
- `--out`: path ใหม่ที่อยู่นอก package (ใน package จะทำให้ `thclaws agent pack` เก็บ staging directory เข้า archive)

ไฟล์ที่ได้:

- `atlas-register.json`: worker/workspace payload, `node_template`, `edge_template` (เฉพาะ input `atlas_file_handoff`),
  `policy_template`, `deployment` (env names, MCP servers, network/hosts, daemon CWD, `reachability_checks`,
  `deployment_verified: false`), `registration` steps และ `package.package_status` / `package.audit` ที่คัดลอกจาก
  `builder-build-report.json`; `atlas_ref` คือ ref ของ Atlas ที่ pin schema ไม่ใช่ revision ที่ operator ใช้จริง. ข้อจำกัด: export ตรวจว่า report ไม่ stale
  เฉพาะ `dependencies` / `deployment_hints` / `generated_files` เทียบกับ render ปัจจุบัน ส่วน `package_status` และ `audit`
  เชื่อตาม report ที่ผ่าน schema (schema ผูก status กับหลักฐาน audit ไว้แล้ว) ไม่ได้รัน static audit หรือ live audit ซ้ำ
- `atlas-workflow.json` เฉพาะ target `atlas-workflow`: POST เข้า `/api/workflows` ได้ตรง ๆ หลัง register worker/workspace
- `<name>-<version>.tar.gz` + `.sha256` จาก `thclaws agent pack` (binary จาก PATH หรือ `THCLAWS_BIN`); ไม่มี binary → ข้าม
  archive และ exit 2; binary ที่ไม่ใช่ baseline `0.116.0` → exit 1. หลัง pack export เปิด archive ตรวจว่าสมาชิกเท่ากับ
  `generated_files` ของ report และ byte ตรงกับ package ยกเว้น `manifest.json`/`.thclaws/settings.json`; `thclaws agent pack`
  ตัด path ที่มี `_secret` และไฟล์ `.env`/`.key`/`.log`/`.pyc` ออกเงียบ ๆ ดังนั้น pack script ชื่อ `client_secret.py` หรือ input
  ชื่อ `api_secret` (ได้ `inputs--api_secret.json`) ทำให้ export fail แทนที่จะได้ archive ที่ daemon รันต่อโดยไม่มี MCP

Export ปฏิเสธเมื่อ static audit มี error (เช่นไฟล์ใน package ถูกแก้), report ไม่ตรง schema / ไม่ตรงกับ render ปัจจุบัน /
ไม่บันทึก static audit ที่ผ่าน, binding ไม่ถูกต้อง, target ไม่ใช่ Atlas, หรือ `atlas-workflow` มี input แบบ file handoff
(node เดียวไม่มี edge ให้ push_files). AgentSpec validation (M1) ปฏิเสธ Atlas spec ที่ Atlas render ไม่ได้ตั้งแต่ก่อน generate: ชื่อ input และ
`assistant_json` output ต้องตรง `[A-Za-z_][A-Za-z0-9_]*` และ schema ของ input ต้องเป็น `type: object` หรือ `array` (DESIGN §8). Export ซ้ำบน package เดิมได้ไฟล์เหมือนเดิมทุก byte;
ไฟล์ JSON เหมือนเดิมแม้ generate package ใหม่ ส่วน archive ขึ้นกับ mtime ของไฟล์ใน package ตามพฤติกรรม `thclaws agent pack`

Prompt ของ node เมื่อ Atlas render แล้วเป็น JSON เทียบเท่า input ของ golden case (compact) ไม่ใช่ข้อความเดียวกับที่ live audit ส่ง:
T2 มี prefix `Execute only the approved input: ` และ node ที่รับ file handoff มีบรรทัด `Handoff files directory: {files_dir}`
ซึ่ง live audit ไม่ได้ทดสอบ. `routing.tags` (เช่น `read-only`) ถูกคัดลอกเป็น routing intent ของ Atlas ไม่ใช่คำรับรองของ builder;
guarantee matrix ใน build report คือคำรับรองเดียว

## ลำดับลงทะเบียน (ทำด้วยมือ)

1. แตก archive ที่ `deployment.package_root` บนเครื่อง worker ตรวจ sha256 แล้ว start `thclaws --serve` จาก directory นั้น
   พร้อม `THCLAWS_API_TOKEN` และ env ตามชื่อใน `deployment.env`. Tree ที่แตกได้ต่างจาก package ที่ audit เฉพาะ `manifest.json`
   (identity ที่ fuse) และ `.thclaws/settings.json` (block `agent` ถูกตัด) จึงรัน `audit.py`/`forge audit` กับ source package
   ไม่ใช่ tree ที่แตก
2. `POST /api/workers` ด้วย `worker` + `token`; `POST /api/workspaces` ด้วย `workspace`
3. Atlas upsert worker ด้วย `id` หรือ `base_url`: URL ที่ลงทะเบียนให้ worker อื่นแล้วจะเขียนทับ worker นั้นแทนการสร้าง
   `wrk_<name>` และ workflow ของ package ใหม่จะอ้าง worker ที่ไม่มี → ใช้ URL แยกต่อ daemon หรือระบุ `--worker-id` ให้ตรง worker เดิม
4. `atlas-workflow` → `POST /api/workflows` ด้วย `atlas-workflow.json`; `atlas-worker` → ประกอบ `node_template` /
   `edge_template` / `policy_template` เข้า workflow ของตนเอง โดยแทน `__UPSTREAM_NODE_ID__` ด้วย node ที่มี `collect_files`
5. `deployment.reachability_checks`: `POST /api/workers/<id>/poll` ต้องได้ `status: online` และ `agent_info.agent.mcp_servers`
   ตรงกับ `deployment.mcp_servers`. นี่พิสูจน์แค่ว่า Atlas เข้าถึง daemon และเห็น MCP inventory

สถานะยังเป็นของ build report: `draft`/`candidate` ไม่ใช่ shippable. `deployment_verified` เป็น `false` เสมอและ builder ไม่เคยตั้งเป็น
true; operator ต้องยืนยัน daemon isolation, egress และ approval path ของ deployment เองนอกไฟล์นี้

## Gate

```bash
python3 scripts/check_m8_export.py
PATH="/path/to/thClaws/target/debug:$PATH" ATLAS_ROOT=/path/to/atlas-control-plane python3 scripts/check_m8_export.py
```

- `0`: offline checks, archive จาก thClaws (สมาชิกเท่ากับ package ทุก byte ยกเว้น `manifest.json`/`.thclaws/settings.json` ที่ pack
  เขียนใหม่ตาม DESIGN §8) และ Atlas in-process (checkout อยู่ที่ ref ที่ pin, schema ตรงที่ pin, graph/policy/prompt validation,
  register worker/workspace/workflow, poll เห็น daemon ที่ start จาก tree ที่แตกจาก archive online พร้อม MCP inventory) ผ่านทั้งหมด
- `1`: export ไม่ deterministic, template ไม่ตรง schema/DoD, refusal ไม่ทำงาน (รวม package ที่ยังไม่ audit, report ที่ manifest
  fail, `--out` ใน package, base_url/ชื่อ input/schema ที่ Atlas render ไม่ได้, archive ที่ pack ตัดไฟล์ออก), archive มีไฟล์อื่นต่างจาก package, checkout หรือ
  schema ของ Atlas ต่างจากที่ pin, หรือ Atlas ปฏิเสธการลงทะเบียน
- `2`: offline ผ่านแต่ไม่มี `thclaws` (ไม่มี archive/daemon probe) หรือไม่พบ checkout ของ Atlas (`ATLAS_ROOT` หรือ `../atlas-control-plane`)

Offline ใช้ fixture `invoice-reviewer` (atlas-worker, file handoff), `publisher` (atlas-workflow T2) และ spec สังเคราะห์สำหรับ
`collect_files`, `atlas-workflow` T0, ชื่อ input ที่มี `-` และ schema แบบ scalar. ไม่ต้องใช้ provider key และไม่ส่งอะไรออกนอกเครื่อง
