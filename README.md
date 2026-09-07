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

M5 (static audit) — เสร็จ 2026-09-07; `forge audit` / `audit.py` / `studio.py` ตรวจ package ตามกฎ DESIGN §7 และ `thclaws agent validate` v0.116.0. `invoice-reviewer` ได้สถานะ `draft`; `sql-reader` ยัง `unverified` เพราะ pack `sql-readonly` ยังไม่มี (M7a). ถัดไปคือ M7a แล้ว live audit (M6)

รัน gate ด้วย `python3 scripts/check_m4_generate.py` และ `python3 scripts/check_m5_static_audit.py` — ถ้าไม่มี `thclaws` ใน PATH จะประกาศ skip ส่วน native และคืน exit 2

## ข้อกำหนดขั้นต่ำ

- Python 3.11+
- thClaws >= 0.116.0 (`thclaws agent validate` / `thclaws agent pack` ต้องอยู่ใน PATH สำหรับ M4 ขึ้นไป)
- Atlas control plane สำหรับ M8 (export registration data ทำงานได้โดยไม่ต้องมี Atlas)

## License

Apache-2.0 (ดู [LICENSE](LICENSE) และ [NOTICE](NOTICE)) — ครอบเฉพาะโค้ดของ builder; package ที่ generate ออกมาเป็นของผู้ใช้ ใช้ license ตาม `identity.license` ใน AgentSpec
