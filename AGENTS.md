# agent-builder — กติกาสำหรับ coding agent

อ่าน `docs/DESIGN.md` และ `docs/PLAN.md` ก่อนแก้อะไร

## ห้าม

- reimplement manifest validation / packing ของ thClaws — เรียก `thclaws agent validate` และ `thclaws agent pack` เท่านั้น
- สร้าง schema ที่ซ้อนกับ `manifest.json` ของ thClaws — SSOT คือ `agentspec.schema.json` และ generate manifest จากมัน
- ให้ LLM ตัดสินเรื่อง network / shell / write path / secret / MCP / ผ่าน audit — ส่วนนี้มาจาก spec + deterministic audit
- อ้างว่า package "read-only" หรือ "deploy-ready" ถ้า guarantee matrix หรือสถานะไม่รองรับ
- commit ข้อมูลจริง, credential, หรือ *.db

## ต้อง

- หนึ่ง milestone หนึ่ง `scripts/check_<m>.py` และรันให้ผ่านก่อนบอกว่าเสร็จ
- generator ต้อง deterministic (รันซ้ำได้ไฟล์เหมือนเดิมทุก byte)
- check ที่ต้องพึ่ง thClaws binary หรือ provider key ให้ skip แบบประกาศชัด ไม่ผ่านเงียบ
- exit code ของ check: `0` = pass, `1` = fail, `2` = explicit skip; CI ต้องรายงาน skip แยกและ fail เมื่อมี `1`
- เมื่อพฤติกรรม thClaws ที่อ้างใน DESIGN §4 เปลี่ยน ให้แก้ DESIGN ก่อนแก้โค้ด และระบุ version ที่ตรวจ

## บริบท

- thClaws source: fork `kaebmoo/thClaws`, ดู `crates/core/src/{agent_runtime.rs, api_v1/agent.rs, agent_defs.rs, cloud/agent_scaffold.rs, cloud/agent_cli.rs}`
- Atlas: `atlas-control-plane` ใช้ `POST /agent/run` เท่านั้น; node field ที่รองรับ: `role, tags, workspace_id, model, output_format, collect_files`; edge field `push_files` ใช้ได้เมื่อเปิด `policy.file_handoff`; `output_format: json` ทำ `json.loads` ทั้งก้อน ไม่ตรวจ schema
- Python 3.11+, dependencies เฉพาะใน `pyproject.toml`
