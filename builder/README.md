# builder — thClaws เป็นผู้สร้าง agent (M11)

workspace นี้เป็น thClaws agent package (ผ่าน `thclaws agent validate`) ที่ไม่มี subagent หรือ workflow:
`AGENTS.md` + skills `spec-author` และ `pack-author` + runner `.thclaws/scripts/build.py`.
Model ทำได้แค่ interview 7 ข้อ, ร่าง `work/<name>/spec.yaml`, ขอให้คนยืนยัน block `permissions` / `env` /
`capabilities` (marker `confirmed_by`), แล้วเรียก build.py; ผล audit ทุกชั้นมาจาก forge (DESIGN §9).

## ใช้งาน

```bash
# เปิด thClaws โดยใช้ workspace นี้ (GUI/CLI) แล้วพิมพ์ความต้องการ เช่น
#   "ต้องการ agent พยากรณ์อากาศจาก Google Weather"
# model จะถามข้อที่ยังไม่รู้ เขียน work/<name>/spec.yaml แล้วขอยืนยันก่อน build

# รัน build โดยตรงได้เช่นกัน (ไม่ต้องใช้ LLM)
python3 .thclaws/scripts/build.py work/<name>/spec.yaml --out out/<name> [--replace]
```

build.py ต้องเห็น checkout ของ agent-builder: ค่าเริ่มต้นคือ repo ที่ `builder/` อยู่ หรือตั้ง `AGENT_BUILDER_ROOT`.
`work/` และ `out/` เป็น local ignored. Exit 0 = `draft`, 1 = refused หรือชั้นใดไม่ผ่าน, 2 = ไม่มี `thclaws` จึงคง `unverified`.

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
python3 scripts/check_m11_builder_agent.py --model oai/gpt-5.4-mini --provider-key-env OPENAI_COMPAT_API_KEY
```

offline: layout, ข้อความห้ามใน AGENTS.md, พฤติกรรม build.py (refuse/strip/report) กับ fixture `fixtures/builder/`;
native: `thclaws agent validate` ของ workspace นี้ (ไม่มี binary → exit 2); live: ส่ง `fixtures/builder/weather-request.md`
เข้า `/agent/run` บน daemon isolated แล้วตรวจว่า spec ที่ model เขียนผ่าน forge และ report บอก `google-weather` เป็น `missing`
(ไม่มี key → exit 2).
