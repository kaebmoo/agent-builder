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

- [docs/DESIGN.md](docs/DESIGN.md) — design baseline (AgentSpec, target/pattern matrix, guarantee matrix, audit 2 ชั้น)
- [docs/PLAN.md](docs/PLAN.md) — milestones M0–M11 พร้อม definition of done และ check script ต่อ milestone
- [AGENTS.md](AGENTS.md) — กติกาสำหรับ coding agent ที่ทำงานใน repo นี้
- [docs/generator.md](docs/generator.md) — M4 generator, CLI, build report และข้อจำกัด
- [docs/audit.md](docs/audit.md) — M5 static audit: กฎ, สถานะ, wrappers และ gate
- [docs/export.md](docs/export.md) — M8 `forge export`: atlas-register.json, workflow, archive และลำดับลงทะเบียน
- [docs/packs.md](docs/packs.md) — M10 pack contract สำหรับผู้เขียน MCP ภายนอก: `forge pack new` / `discover` / `test`
- [builder/README.md](builder/README.md) — M11 builder-agent workspace: interview, `confirmed_by`, `build.py` และ gate

## สถานะ

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
`.thclaws/scripts/build.py` ที่บังคับ `confirmed_by` และรายงานผลจาก forge เท่านั้น; ส่วน live (ต้องมี provider key) ยังไม่ผ่าน ดู [builder/README.md](builder/README.md).
M13 (2026-10-27): รองรับ host + bots layout ของ thClaws 0.138.0 — `builder/` เป็น host workspace (agent ที่ `.thclaws/bots/main/`), harness ตั้ง
`THCLAWS_AUTO_MIGRATE=0` และตรวจ layout จริงทุกครั้งที่สตาร์ท `--serve`, ส่วน package ที่ deploy กับ Atlas คง single-agent layout (DESIGN §4/§8).
M16 (2026-10-28): package ที่มีเฉพาะ `prompt_json`/`assistant_json` deploy เป็น bot ตัวเดียวของ thClaws host ได้ด้วย `forge export --layout host`
(พิสูจน์บน desktop build + Atlas `daa0f49`; package และ archive ยัง single-agent) ดู [docs/export.md](docs/export.md).
M12 (2026-10-27) ย้าย baseline thClaws ไป 0.138.0/`d452ece` หลังตรวจ DESIGN §4 ซ้ำกับ source จริง; native gate ทุกตัวผ่านบน binary นี้ และ `--write-boundary` ของ T1 ผ่านครบแต่ T1 ยังปิดรอ review.

```bash
python3 scripts/check_m7b_packs.py
python3 scripts/check_m8_export.py
python3 scripts/check_m10_pack_authoring.py
python3 scripts/check_m11_builder_agent.py
python3 scripts/check_m13_host_layout.py
python3 scripts/check_m14_standalone_t1.py
python3 scripts/check_m12_baseline.py
```

ดู [Publisher pack](packs/publisher-email-sftp/README.md) และ [SQL pack](packs/sql-readonly/README.md)
สำหรับ runtime, fixture และ exit code 0/1/2.

## ข้อกำหนดขั้นต่ำ

- Python 3.11+
- thClaws baseline `0.138.0` revision `d452ece` สำหรับ Atlas/export/catalog (M12, 2026-10-27; เดิม 0.116.0); standalone T0 รองรับคู่ version+revision 0.116.0/`75edc48`, 0.121.0/`35ccf16`, 0.129.0/`7a27129`, 0.138.0/`d452ece` (รุ่นอื่น fail จนกว่าจะตรวจ DESIGN §4 ใหม่); `thclaws agent validate` / `thclaws agent pack` ต้องอยู่ใน PATH หรือ `THCLAWS_BIN` สำหรับ M4 ขึ้นไป
- Atlas control plane สำหรับ M8: `forge export` ทำงานได้โดยไม่ต้องมี Atlas; ส่วน native ของ `check_m8_export.py` ใช้ checkout ที่
  `../atlas-control-plane` หรือ `ATLAS_ROOT` (ไม่พบ → skip exit 2)

## License

Apache-2.0 (ดู [LICENSE](LICENSE) และ [NOTICE](NOTICE)) — ครอบเฉพาะโค้ดของ builder; package ที่ generate ออกมาเป็นของผู้ใช้ ใช้ license ตาม `identity.license` ใน AgentSpec

Live audit (M6): `forge live-test out/sql-reader --write` หรือ `forge audit out/sql-reader --live --write`.
ต้องมี thClaws baseline (0.138.0) และ provider key ใน environment (`OPENAI_API_KEY` สำหรับ fixture SQL).
ไม่มี key จะรายงาน SKIP (exit 2) และคง `draft`; ผ่าน golden cases และหลักฐาน MCP จึงเป็น `candidate`.
รายละเอียดและขอบเขตการตรวจอยู่ใน [docs/audit.md](docs/audit.md).
