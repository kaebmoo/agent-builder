# agent-builder

Compiler/factory ที่แปลง "ความต้องการของคน" ให้เป็น **thClaws agent package** ที่ตรวจสอบได้ มี version และนำไปลงทะเบียนเป็น worker ของ **Atlas** ได้อย่างปลอดภัย

```
User goal → Discovery (7 questions) → AgentSpec (SSOT)
  → เลือก target / pattern → Generate package (deterministic)
  → LLM customize (เฉพาะส่วนข้อความ) → Static audit + Live audit
  → thclaws agent validate / pack → Export package + Atlas registration data
```

หลัก 3 ข้อ

1. `target` เป็นตัวกำหนดว่าอะไร execute ที่ไหน (thClaws standalone หรือ Atlas worker)
2. ทุก guarantee ต้องบอกได้ว่า enforce ด้วยอะไร (Declared / Enforced / Not guaranteed)
3. Builder สร้าง package ไม่สร้าง runtime ใหม่ (ใช้ `thclaws agent validate/pack` ไม่ reimplement)

## ตำแหน่งในระบบ

| ชั้น | หน้าที่ | repo |
|---|---|---|
| Atlas | routing, workflow, queue, approval, audit, artifact handoff | atlas-control-plane |
| thClaws | runtime ของ worker (`thclaws --serve`) tools, MCP, sandbox, workspace | thClaws (fork kaebmoo) |
| **agent-builder** | สร้าง/ตรวจ/ทดสอบ specialist package | repo นี้ |
| Specialist package | AGENTS.md + skills + schemas + manifest ต่อหนึ่งหน้าที่ | ผลลัพธ์ของ builder |

## เอกสาร

- [docs/getting-started.md](docs/getting-started.md) — คู่มือเริ่มต้นสำหรับมือใหม่: ติดตั้ง, generate/audit ครั้งแรก, เขียน spec เอง, รันจริง, เอาไปใช้กับ Atlas
- [docs/DESIGN.md](docs/DESIGN.md) — design baseline (AgentSpec, target/pattern matrix, guarantee matrix, audit 2 ชั้น)
- [docs/PLAN.md](docs/PLAN.md) — milestones M0–M17 และ M19 (M18/M20 เสนอ) พร้อม definition of done และ check script ต่อ milestone
- [AGENTS.md](AGENTS.md) — กติกาสำหรับ coding agent ที่ทำงานใน repo นี้
- [docs/generator.md](docs/generator.md) — M4 generator, CLI, build report และข้อจำกัด
- [docs/audit.md](docs/audit.md) — M5 static audit และ M19 security audit: กฎ, สถานะ, wrappers และ gate
- [docs/export.md](docs/export.md) — M8 `forge export`: atlas-register.json, workflow, archive และลำดับลงทะเบียน
- [docs/packs.md](docs/packs.md) — M10 pack contract สำหรับผู้เขียน MCP ภายนอก: `forge pack new` / `discover` / `test`
- [builder/README.md](builder/README.md) — M11 builder-agent workspace: interview, `confirmed_by`, `build.py` และ gate

## สถานะ

M19 (2026-10-31): `forge audit --security` ตรวจ deterministic rules `strict`/`secrets`/`packs`/`egress`;
`shippable` ต้องมี live evidence ในรอบเดียวกัน พร้อม gate `check_m19_security_audit.py`.
ผล native ที่ reviewer รันบน thclaws 0.146.0/`9f69995`: gate exit 0, ถึง `shippable` ผ่าน scripted provider,
golden cases 4/4 และเรียก `echo__reverse`; ไม่ใช้ provider key (ดู evidence ใน PLAN). หลักฐาน synthetic/scripted นี้ไม่ใช่การรัน model จริง.

M7b (publisher email/SFTP pack) — เสร็จ 2026-09-08. MCP รองรับ dry run, persistent idempotency key
และ fixed destination จาก env ของ operator. Package T2 มี approval fragment ที่บังคับ `human_gate`
กับ requirement ของ daemon แยก; static/native audit ผ่านเป็น `draft` แต่ยังไม่ยืนยัน deployment หรือส่งจริง.
M8 export/registration เสร็จ 2026-09-08: `forge export` ออก `atlas-register.json`, `atlas-workflow.json` และ archive จาก
`thclaws agent pack` ตาม [docs/export.md](docs/export.md); gate ลงทะเบียนกับ Atlas in-process และ poll daemon ของ package ได้จริง
แต่ `deployment_verified` ยัง `false`. M9 เปิด standalone static-pipeline T0; M14 (2026-10-28) ปลด T1 (`write_scope: output`, worker ได้ Write/Edit ใต้ native `writePaths: output/**`)
เฉพาะบน thClaws 0.138.0/`d452ece` พร้อม gate `check_m14_standalone_t1.py` ที่พิสูจน์บน package จริง. ดู [PLAN](docs/PLAN.md) และ [generator](docs/generator.md).
M10 (pack authoring) เสร็จ 2026-10-26: `forge pack new` / `forge pack discover` ร่าง pack ที่ตั้งใจให้ยังไม่ conformant จนผู้เขียนเติม tier/network/fixture
แล้ว `forge pack test` ตัดสิน; reference pack `fixtures/packs/echo/` ผ่านครบวงจรโดยไม่ใช้ LLM ดู [docs/packs.md](docs/packs.md).
M11 (builder-agent) offline/native เสร็จ 2026-10-26: workspace `builder/` ให้ thClaws interview และร่าง spec แต่ build ได้ทางเดียวผ่าน
`.thclaws/scripts/build.py` ที่บังคับ `confirmed_by` และรายงานผลจาก forge เท่านั้น; ส่วน live (ต้องมี provider key) ผ่าน 3/3 รอบ 2026-10-28 ดู [builder/README.md](builder/README.md).
M13 (2026-10-27): รองรับ host + bots layout ของ thClaws 0.138.0 — `builder/` เป็น host workspace (agent ที่ `.thclaws/bots/main/`), harness ตั้ง
`THCLAWS_AUTO_MIGRATE=0` และตรวจ layout จริงทุกครั้งที่สตาร์ท `--serve`, ส่วน package ที่ deploy กับ Atlas คง single-agent layout (DESIGN §4/§8).
M16 (2026-10-28): package ที่มีเฉพาะ `prompt_json`/`assistant_json` deploy เป็น bot ตัวเดียวของ thClaws host ได้ด้วย `forge export --layout host`
(พิสูจน์บน desktop build + Atlas `daa0f49`; package และ archive ยัง single-agent) ดู [docs/export.md](docs/export.md).
M12 (2026-10-27) ย้าย baseline thClaws ไป 0.138.0/`d452ece` หลังตรวจ DESIGN §4 ซ้ำกับ source จริง; native gate ทุกตัวผ่านบน binary นี้ และ `--write-boundary` ของ T1 ผ่านครบแต่ T1 ยังปิดรอ review.
M15 (2026-10-28): package ที่ deploy กับ Atlas ประกาศ `allowedTools` = `permissions.tools` ใน `.thclaws/settings.json` และ pin `browserEnabled`/`halEnabled`/`imageToolsEnabled` = false
จึงบังคับ allowlist ของ built-in บน `/agent/run` ได้จริงบน thClaws 0.138.0/`d452ece` (`tool_allowlist` และ `no_shell` เมื่อ `shell: none` เป็น Enforced; Skill และ MCP tools
อยู่นอก claim) พร้อม gate `check_m15_tool_allowlist.py`; package Atlas ที่ generate ก่อน M15 จะ drift ที่ `.thclaws/settings.json` ต้อง generate ใหม่ ดู DESIGN §4/§6.
M17 (2026-10-31) ย้าย baseline thClaws ไป 0.146.0/`9f69995` หลังตรวจ DESIGN §4 ทุก claim ซ้ำกับ source ("Baseline 0.146.0 / 9f69995": catalog เพิ่ม `DocConvert`, write landing #223,
host root โหลด `AGENTS.md` เข้าทุก bot, MCP allowlist keyed ทั้ง command line, office/RAG MCP ที่ engine ฉีดเอง, keychain ตอน start); คู่ standalone T0/T1 เพิ่ม `0.146.0/9f69995`
และ gate `check_m17_baseline.py`; pin `"officeApps": []` ถูก adopt ในทุก package (รวม standalone) เมื่อ 2026-10-31 หลังตรวจ source ซ้ำ (DESIGN §4 M17 ข้อ 5 follow-up; gate M15).

```bash
python3 scripts/check_m7b_packs.py
python3 scripts/check_m8_export.py
python3 scripts/check_m10_pack_authoring.py
python3 scripts/check_m11_builder_agent.py
python3 scripts/check_m13_host_layout.py
python3 scripts/check_m14_standalone_t1.py
python3 scripts/check_m15_tool_allowlist.py
python3 scripts/check_m16_host_deploy.py
python3 scripts/check_m19_security_audit.py
python3 scripts/check_m12_baseline.py
```

ดู [Publisher pack](packs/publisher-email-sftp/README.md) และ [SQL pack](packs/sql-readonly/README.md)
สำหรับ runtime, fixture และ exit code 0/1/2.

## ข้อกำหนดขั้นต่ำ

- Python 3.11+
- thClaws baseline `0.146.0` revision `9f69995` สำหรับ Atlas/export/catalog (M17, 2026-10-31; เดิม 0.138.0/`d452ece` ใน M12 และ 0.116.0 ก่อนนั้น); standalone T0 รองรับคู่ version+revision 0.116.0/`75edc48`, 0.121.0/`35ccf16`, 0.129.0/`7a27129`, 0.138.0/`d452ece`, 0.146.0/`9f69995` (รุ่นอื่น fail จนกว่าจะตรวจ DESIGN §4 ใหม่); `thclaws agent validate` / `thclaws agent pack` ต้องอยู่ใน PATH หรือ `THCLAWS_BIN` สำหรับ M4 ขึ้นไป
- standalone T1 (M14) รับรองคู่ 0.138.0/`d452ece` และ 0.146.0/`9f69995` (M17) (`STANDALONE_T1_THCLAWS_COMPATIBILITY`); คู่ T0 รุ่นเก่ายังเป็น T0 เท่านั้น
- gate ส่วนที่ต้องสตาร์ท daemon (`thclaws --serve`: M6, M7b live, M8 poll, M11 live, M13, M15, M16) ต้องใช้ desktop build ของ thClaws (`thclaws --help` มี `bots`);
  build แบบ CLI-only ไม่มี `--serve` (DESIGN §4 "Host + bots layout" ข้อ 13) จึงตรวจส่วนนั้นไม่ได้
- Atlas control plane สำหรับ M8 และ M16: `forge export` ทำงานได้โดยไม่ต้องมี Atlas; ส่วน native ของ `check_m8_export.py` และ `check_m16_host_deploy.py`
  ใช้ checkout ที่ `../atlas-control-plane` หรือ `ATLAS_ROOT` ซึ่งต้องอยู่ที่ revision ที่ pin `daa0f49` (ไม่พบ → skip exit 2; อยู่คนละ revision → fail exit 1)
- SQL pack สำหรับ M6/M7a: `pip install -e ".[sql-pack]"` (`mcp>=1.26,<2`; mcp 2.x ไม่มี `mcp.server.fastmcp`) และ checkout ของ `kaebmoo/AI` ที่ `d95dc28`
  ชื่อ `AI` วางข้าง checkout ของ agent-builder (ไม่พบ → skip exit 2; อยู่คนละ revision → fail exit 1); ใช้โฟลเดอร์คู่ worktree ตาม `docs/local-test-plan.md` ข้อ 7

## License

Apache-2.0 (ดู [LICENSE](LICENSE) และ [NOTICE](NOTICE)) — ครอบเฉพาะโค้ดของ builder; package ที่ generate ออกมาเป็นของผู้ใช้ ใช้ license ตาม `identity.license` ใน AgentSpec

Live audit (M6): `forge live-test out/sql-reader --write` หรือ `forge audit out/sql-reader --live --write`.
ต้องมี thClaws baseline (0.146.0) และ provider key ใน environment (`OPENAI_API_KEY` สำหรับ fixture SQL).
ไม่มี key จะรายงาน SKIP (exit 2) และคง `draft`; ผ่าน golden cases และหลักฐาน MCP จึงเป็น `candidate`.
รายละเอียดและขอบเขตการตรวจอยู่ใน [docs/audit.md](docs/audit.md).
