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

## สถานะ

M7b (publisher email/SFTP pack) — เสร็จ 2026-09-08. MCP รองรับ dry run, persistent idempotency key
และ fixed destination จาก env ของ operator. Package T2 มี approval fragment ที่บังคับ `human_gate`
กับ requirement ของ daemon แยก; static/native audit ผ่านเป็น `draft` แต่ยังไม่ยืนยัน deployment หรือส่งจริง.
M6 SQL live audit เสร็จแล้วตาม [PLAN](docs/PLAN.md); ถัดไป M8 export/registration.

```bash
python3 scripts/check_m7b_packs.py
```

ดู [Publisher pack](packs/publisher-email-sftp/README.md) และ [SQL pack](packs/sql-readonly/README.md)
สำหรับ runtime, fixture และ exit code 0/1/2.

## ข้อกำหนดขั้นต่ำ

- Python 3.11+
- thClaws >= 0.116.0 (`thclaws agent validate` / `thclaws agent pack` ต้องอยู่ใน PATH สำหรับ M4 ขึ้นไป)
- Atlas control plane สำหรับ M8 (export registration data ทำงานได้โดยไม่ต้องมี Atlas)

## License

Apache-2.0 (ดู [LICENSE](LICENSE) และ [NOTICE](NOTICE)) — ครอบเฉพาะโค้ดของ builder; package ที่ generate ออกมาเป็นของผู้ใช้ ใช้ license ตาม `identity.license` ใน AgentSpec

Live audit (M6): `forge live-test out/sql-reader --write` หรือ `forge audit out/sql-reader --live --write`.
ต้องมี thClaws v0.116.0 และ provider key ใน environment (`OPENAI_API_KEY` สำหรับ fixture SQL).
ไม่มี key จะรายงาน SKIP (exit 2) และคง `draft`; ผ่าน golden cases และหลักฐาน MCP จึงเป็น `candidate`.
รายละเอียดและขอบเขตการตรวจอยู่ใน [docs/audit.md](docs/audit.md).
