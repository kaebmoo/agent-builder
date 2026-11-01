# Pack authoring (M10) — คู่มือสำหรับผู้เขียน MCP ภายนอก

MCP server, script และ skill ที่ agent ต้องใช้ **ไม่อยู่ใน builder และไม่อยู่ใน AgentSpec** (DESIGN §5). เขียนที่ไหนก็ได้
(repo แยก, session ของ thClaws ที่ทำหน้าที่ coding agent, มนุษย์) แล้วเข้ามาทาง `packs/<name>/pack.yaml` ซึ่งเป็น
contract v2 เพียงรอยต่อเดียว. เอกสารนี้บอกว่า pack ต้องมีอะไร, คำสั่งช่วยร่างทำอะไรและ **ไม่** ทำอะไร, และ harness ตัดสินอย่างไร

## ขั้นตอน

```bash
forge pack new <name>                                   # 1. scaffold packs/<name>/ (TODO ทุกจุดที่ผู้เขียนต้องตัดสิน)
forge pack discover --name <name> --command <cmd> [--env NAME ...] [--out packs/<name>/pack.yaml] --args <argv...>
                                                        # 1'. หรือร่าง pack.yaml จาก tools/list ของ server ที่มีอยู่แล้ว
#    เติม min_tier / network / hosts / env / mutating / fixture แล้ว implement scripts/server.py
forge pack test <name>                                  # 2. conformance โดยไม่ใช้ LLM และไม่ใช้ provider key
```

`forge pack test` ผ่าน (exit 0) = pack **conformant**; `pack-conformance.json` (gitignored) บันทึก digest ของ descriptor/assets
และของ `tools/list` ครั้งล่าสุด. exit 1 = contract/protocol/case fail พร้อมเหตุผล; exit 2 = runtime ของ server ไม่มีในเครื่อง
(`fixture/setup.py` คืน 2 หรือ `command` ไม่อยู่ใน PATH). สถานะเป็นผลของ harness ไม่ใช่ field ที่เขียนเอง.

ตัวอย่างสมบูรณ์ที่เล็กที่สุดอยู่ที่ [`fixtures/packs/echo/`](../fixtures/packs/echo/README.md): stdlib stdio MCP สอง tool,
ไม่มี env, ไม่มี network. ตัวอย่างจริงดู [`packs/sql-readonly`](../packs/sql-readonly/README.md) (T0, wrap runtime ภายนอก)
และ [`packs/publisher-email-sftp`](../packs/publisher-email-sftp/README.md) (T2, mutating tools),
รวมถึงตัวอย่างอิสระ [`packs/sqlite-readonly`](../packs/sqlite-readonly/README.md) (T0, SQLite ไฟล์ใดก็ได้ อ่านอย่างเดียวด้วย `mode=ro` + authorizer),
[`packs/csv-readonly`](../packs/csv-readonly/README.md) (T0, CSV/TSV ใต้โฟลเดอร์เดียว) และ
[`packs/datetime-tools`](../packs/datetime-tools/README.md) (T0, ไม่มี env) — ทั้งสามใช้ stdlib เท่านั้น ไม่ผูกกับ repo อื่น (M21).
แต่ละ pack ถือสำเนา MCP stdio shell (`handle` + loop อ่าน stdin) ของตัวเองโดยตั้งใจ ไม่มี shared module
เพื่อให้ copy pack เดียวไปใช้ได้ทันที จึงยกเว้นไฟล์ `server.py` ทั้งสามจาก copy-paste detection ของ SonarCloud
ใน `.sonarcloud.properties` (bug และ code smell ยังถูกตรวจตามปกติ).

## สิ่งที่ `forge pack new` และ `forge pack discover` ทำ และไม่ทำ

| | ทำ | ไม่ทำ (ผู้เขียนตัดสิน) |
|---|---|---|
| `new <name>` | สร้าง `pack.yaml` รูป v2 ที่มี `TODO`, `fixture/setup.py` skeleton, `README.md`, โฟลเดอร์ `scripts/`; ปฏิเสธชื่อนอก charset และ directory ที่มีอยู่แล้ว | เลือก tier/network/env/tools/cases; เขียน server |
| `discover` | spawn server ด้วย env สะอาด (PATH + `--env NAME` ที่ระบุ), `initialize` → `tools/list`, เขียน `tools` ครบตามที่ server advertise, แนบ description/inputSchema เป็น comment, บันทึก digest ของ surface, เสนอ `mutating` จาก annotations (`readOnlyHint: false` หรือ `destructiveHint: true`) | ตัดสิน `min_tier` / `network` / `hosts` / fixture (ปล่อย `TODO`); ยืนยัน `mutating` (thClaws ไม่อ่าน annotations); เขียนทับไฟล์ที่มีอยู่ |

ร่างจาก `discover` และ scaffold จาก `new` ตั้งใจให้ **ไม่ผ่าน** `forge pack test` จนกว่าจะเติม: `min_tier: TODO` → "invalid min_tier or network",
`cases: []` → "each server requires smoke and negative fixture cases". ไม่มีทางที่ร่างจะกลายเป็น conformant โดยไม่มีคนตัดสิน.
`--env NAME` ส่งผ่าน **ชื่อ** จาก process ปัจจุบันเพื่อให้ server start ได้ และลงชื่อนั้นใน `env:` ของร่าง; ค่าไม่ถูกพิมพ์หรือเขียนลงไฟล์.

## Contract ที่ harness บังคับ

- `pack.yaml` มี field ครบชุด v2 (`min_tier, network, hosts, env, tools, mcp_servers, scripts, skills, fixture`; `source` optional) ไม่มี field อื่น
- ชื่อ server `^[a-z0-9][a-z0-9-]*$` (ห้าม `_`), bare tool `^[a-z0-9][a-z0-9_]*$` (ห้าม `__`); qualified name ที่ model เห็นคือ `<server>__<tool>`
  thClaws sanitize ชื่ออื่นแบบเงียบ ๆ แต่ builder reject ทั้งใน descriptor และตอน `discover`
- `tools` ของ server = **ทุก** tool ที่ `tools/list` advertise ไม่ขาดไม่เกิน (thClaws ลงทะเบียนทั้งหมด); ต้องส่งครบในหน้าเดียว (ไม่มี `nextCursor`)
  และ `inputSchema` ต้องเป็น JSON Schema ที่ถูกต้อง; surface ที่เปลี่ยนโดย descriptor ไม่เปลี่ยนจะ fail จนผู้เขียน review `pack.yaml`
- `mutating` ⊆ `tools`; ไม่ว่าง → `min_tier: T2` และทุก mutating tool ต้องมี input `dry_run` และ `idempotency_key`
- `network: allowlist` ต้องมี `hosts`; `network: none` ต้องไม่มี `hosts`; spec ที่ใช้ pack ต้องประกาศ network ไม่ต่ำกว่า pack
- `env` คือชื่อ (`^[A-Z][A-Z0-9_]*$`) ที่ process ของ daemon ต้องมี; **ไม่มีค่า secret ในไฟล์ใด** ของ pack หรือ package; config ส่งทาง `args` และ env เท่านั้น
- harness รัน `fixture/setup.py` และ server ด้วย **environment สะอาด**: PATH + ชื่อใน `env` พร้อมค่าจาก fixture เท่านั้น
  server ที่อ่าน env ที่ไม่ได้ประกาศจึง fail ตั้งแต่ตอน test ("MCP exited before replying; the harness passes only PATH and the env names declared in pack.yaml")
- `fixture/setup.py <workdir>` สร้างข้อมูลสังเคราะห์ใต้ `workdir` (ไม่ใช้ข้อมูลจริง ไม่ใช้ network) แล้วพิมพ์ JSON `{ENV_NAME: value}` ที่ key ตรง `env` พอดี;
  คืน exit 2 เมื่อ runtime ของ MCP ไม่มี (probe ด้วย interpreter เดียวกับ `command`) ไม่ใช่ 1
- `fixture.cases` ต้องมีอย่างน้อยหนึ่ง `expect: ok` และหนึ่ง `expect: error` ต่อ server; `expect: error` หมายถึง server ตอบ `isError: true`
  ด้วยตัวเอง (reject mutation/argument ผิดที่ server ไม่ใช่ที่ prompt); case ที่ไม่ตรงคาด fail ทั้ง pack
- `scripts/<file>.py` ถูกคัดลอกเป็น `.thclaws/scripts/<pack>--<file>.py` และ argument ที่ตรง `scripts/<file>.py` ถูกเปลี่ยนตาม;
  `skills/<skill>/SKILL.md` เป็น `.thclaws/skills/<pack>--<skill>/SKILL.md`; harness จัดวาง asset แบบเดียวกับ generated package ก่อน start server
- ห้าม symlink และ path traversal ใน asset

## หลัง conformant

spec ประกาศ `capabilities: [{pack: <name>, params: {}}]`, `permissions.tools` ⊇ qualified tools ทั้งหมดของ pack (+ `tools` built-in ของ pack),
`env` ⊇ `env` ของ pack, tier ≥ `min_tier`, network ≥ `network` → `forge generate` bundle asset, เขียน `.thclaws/mcp.json` (command/args เท่านั้น)
และ `manifest.requires.mcp_servers`; `forge audit` ตรวจว่า spec รองรับ pack และ conformance ไม่ failed/skipped/stale.
Conformant ไม่ใช่หลักฐานว่า agent เรียก tool หรือ runtime บังคับ permission: นั่นเป็นเรื่องของ live audit (M6/M9) และ guarantee matrix (DESIGN §6).

## ข้อควรระวัง

- `setup.py` และ `server.py` เป็นโค้ดที่รันจริงบนเครื่องผู้ทดสอบ; harness แยก env และ working directory แต่ไม่ใช่ OS/network sandbox. review pack ก่อนรัน
- pack ที่ต้องเรียก API ภายนอก (เช่น Google Weather) ใช้ recorded response หรือ mock endpoint ใน fixture และประกาศ `network: allowlist` + `hosts` จริงสำหรับ deployment
- ถ้าต้องให้คำแนะนำการใช้ tool ตั้งแต่เริ่ม run ให้ส่งผ่าน `InitializeResult.instructions` (DESIGN §4) แทนการพึ่ง template กลางของ builder
