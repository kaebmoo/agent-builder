# builder — thClaws เป็นผู้สร้าง agent (M11)

workspace นี้เป็น **host workspace** ตาม layout ของ thClaws 0.138.0 (DESIGN §4 "Host + bots layout"): root มี `.thclaws/bots.json`
และ `.thclaws/settings.json` ของ host ส่วน agent ตัวเดียวอยู่ที่ `.thclaws/bots/main/` (ผ่าน `thclaws agent validate` ที่โฟลเดอร์นั้น,
ไม่มี subagent หรือ workflow): `AGENTS.md` + skills `spec-author` และ `pack-author` + runner `.thclaws/scripts/build.py`.
Model ทำได้แค่ interview 7 ข้อ, ร่าง `work/<name>/spec.yaml`, ขอให้คนยืนยัน block `permissions` / `env` /
`capabilities` (marker `confirmed_by`) ในเทิร์นถัดไป, แล้วเรียก build.py; ผล audit ทุกชั้นมาจาก forge (DESIGN §9).

## ใช้งาน

```bash
# เปิด thClaws (desktop หรือ thclaws --serve) ที่โฟลเดอร์ builder/ นี้: desktop build เห็น .thclaws/bots.json จึงเปิดเป็น host
# และ supervise agent ใน .thclaws/bots/main/ โดยไม่ migrate อะไร; thclaws -p ที่ root ก็ก้าวเข้า agent ตัวนี้เอง
# พิมพ์ความต้องการ เช่น "ต้องการ agent พยากรณ์อากาศจาก Google Weather"
# model จะถามข้อที่ยังไม่รู้ เขียน work/<name>/spec.yaml ที่ root ของ workspace แล้วหยุดรอให้คุณยืนยันก่อน build

# รัน build โดยตรงได้เช่นกัน (ไม่ต้องใช้ LLM) — shell ของ agent ทำงานที่ root ของ workspace จึงอ้าง runner ผ่าน $THCLAWS_AGENT_DIR
python3 "$THCLAWS_AGENT_DIR/.thclaws/scripts/build.py" --list-packs
python3 "$THCLAWS_AGENT_DIR/.thclaws/scripts/build.py" work/<name>/spec.yaml --out out/<name> [--replace]
# จาก terminal ธรรมดา (ไม่มี thClaws) ใช้ path เต็ม: python3 .thclaws/bots/main/.thclaws/scripts/build.py ...
```

build.py หา checkout ของ agent-builder เองโดยไล่ขึ้นจากตำแหน่งไฟล์จนพบ `forge/cli.py` (หรือตั้ง `AGENT_BUILDER_ROOT`);
`--list-packs` พิมพ์ pack ที่ checkout มีจริง (ชื่อ, qualified tools, env names) เพื่อให้ model ไม่ต้องหา `packs/` เอง.
`work/`, `out/`, `.thclaws/state/` และ `.thclaws/bots/main/.thclaws/{state,sessions}/` เป็น local ignored.
Exit 0 = `draft`, 1 = refused หรือชั้นใดไม่ผ่าน, 2 = ไม่มี `thclaws` จึงคง `unverified`.

CLI-only build ของ thClaws (ไม่มี feature `gui`) ไม่มี `--serve` เลย (exit 1 พร้อมข้อความให้ build ด้วย `--features gui`) จึงใช้ workspace นี้ได้เฉพาะ
`thclaws agent validate .thclaws/bots/main` และการรัน build.py ตรง ๆ; gate M11 live สตาร์ท daemon จาก `.thclaws/bots/main/` พร้อม
`THCLAWS_WORKSPACE_ROOT=<path ของ builder/>` (สิ่งที่ host ทำกับ child ของมัน) บน desktop build และประกาศ skip บน CLI-only build.
ห้ามรัน `thclaws bots migrate`/`unmigrate` กับโฟลเดอร์นี้ เพราะ tree ถูก git track ไว้ใน layout นี้แล้ว. gate ทุกตัวที่สตาร์ท `--serve`
(รวม M11 live) ตั้ง `THCLAWS_AUTO_MIGRATE=0` และตรวจ `/v1/agent/info.workspace_dir` เองผ่าน `forge/live_test.py::isolated_env`/`check_layout`.

## confirmed_by

```yaml
confirmed_by:
  permissions: seal   # รวม network
  env: seal
  capabilities: seal
```

build.py ปฏิเสธเมื่อไม่มี block นี้, มี key ไม่ครบ/เกิน, ค่าว่าง หรือค่าเป็นชื่อสงวน (`model`, `assistant`, `builder` ...)
และตัด marker ออกก่อนส่งให้ forge. runner ตรวจได้เพียงว่ามี marker ที่ถูกรูป ไม่สามารถพิสูจน์ได้ว่าคนเป็นผู้ยืนยันจริง;
AGENTS.md จึงห้าม model เติม marker เองโดยไม่มีคำยืนยัน และ gate M11 ตรวจทั้งข้อความห้ามและพฤติกรรม runner.

## Gate

```bash
python3 scripts/check_m11_builder_agent.py
python3 scripts/check_m11_builder_agent.py --model oai/gpt-5.4-mini --provider-key-env OPENAI_COMPAT_API_KEY --rounds 3
python3 scripts/check_m13_host_layout.py      # layout ของ workspace นี้และ harness (DESIGN §4 host + bots layout)
```

offline: layout (host + agent dir), ข้อความห้ามใน AGENTS.md, พฤติกรรม build.py (refuse/strip/report/`--list-packs`) กับ fixture `fixtures/builder/`;
native: `thclaws agent validate` ของ `.thclaws/bots/main/` (ไม่มี binary → exit 2); live: daemon isolated ที่สตาร์ทจาก agent dir ด้วย
`THCLAWS_WORKSPACE_ROOT` (หรือ `--via-host` บน desktop build) รับสองเทิร์น: `fixtures/builder/weather-request.md` (คำตอบ 7 ข้อ ไม่มีคำยืนยัน →
model ต้องแสดง block แล้วหยุด) และ `fixtures/builder/weather-confirm.md` ใน `session_id` เดิม (→ model เติม `confirmed_by` และรัน build.py)
แล้วตรวจว่า spec ที่ model เขียนผ่าน forge และ report บอก `google-weather` เป็น `missing`; `--rounds N` ต้องผ่านทุกรอบ, evidence ต่อรอบใน
`out/m11-live/` (ไม่มี key → exit 2; ต้องระบุ `--model` เมื่อมี key).
