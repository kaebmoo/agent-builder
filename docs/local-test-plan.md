# แผนทดสอบบนเครื่อง local (Mac) สำหรับ Claude Code

จุดประสงค์: ปิดส่วนที่ cloud session ทำไม่ได้ เพราะไม่มี provider key, ไม่มี checkout ของ AI/Atlas และไม่ใช่ macOS
งานทั้งหมดอยู่บน branch `claude/adoring-feynman-kqs8us` (PR kaebmoo/agent-builder#1). อ่าน `AGENTS.md`, `CLAUDE.md`, `docs/PLAN.md`
ย่อหน้า "M10 macOS follow-up", "M11 เสร็จ" และ "M12 เสร็จ" ก่อนเริ่ม

ตั้งแต่ M12 baseline คือ thClaws `0.138.0` revision `d452ece` ซึ่งเป็น binary ที่มีอยู่แล้วที่
`~/Documents/GitHub/thClaws/target/debug/thclaws` (ตรวจด้วย `thclaws --version`). **ไม่ต้อง build รุ่นอื่น**; ถ้า binary ถูก rebuild
จาก commit อื่น ทุก gate ที่ใช้ binary จะ fail exit 1 ตามออกแบบ จนกว่าจะตรวจ DESIGN §4 ใหม่

## สิ่งที่ต้องยืนยัน

| # | สิ่งที่ตรวจ | ทำไมต้องทำบน local | ผลที่คาด |
|---|---|---|---|
| 1 | gate ทุกตัวในโหมด CI (ไม่มี `thclaws` ใน PATH) | ยืนยัน checkout ตรงกับ cloud และยืนยัน fix macOS ของ M10 (เกิดในส่วน offline) | ทุกตัว exit 0 หรือ 2 ไม่มี 1; `check_m10_pack_authoring.py` ต้องเป็น 2 ไม่ใช่ 1 |
| 2 | gate ทุกตัวแบบ native ด้วย binary ของเครื่อง | ยืนยันผล M12 บน macOS (cloud ตรวจบน Linux build จาก commit เดียวกัน) | M0–M5, M7b, M10, M12 exit 0; M6/M7a exit 2 ถ้าไม่มี `../AI`; M8 exit 2 ถ้าไม่มี Atlas; M9/M11 exit 2 (รอ key) |
| 3 | `check_m11_builder_agent.py` live (ต้องมี key) | DoD ส่วน live ของ M11 ยังไม่เคยรัน | exit 0 และ evidence ใน `out/m11-live/report.json` |
| 4 | `check_m9_standalone.py` live (ต้องมี key) | evidence provider เดิมเป็นของ 0.116.0 | exit 0; report `candidate` ของ package ทดสอบ |
| 5 | (optional) M7a/M6 กับ `../AI`, M8 กับ `ATLAS_ROOT`, M7b `--live` 3 รอบ | evidence `candidate` ของ SQL/publisher ทั้งหมดเป็นของ 0.116.0 | exit 0 หรือ skip 2 พร้อมเหตุผล |

## เตรียมเครื่อง

```bash
cd ~/Documents/GitHub/agent-builder
git fetch origin claude/adoring-feynman-kqs8us
git checkout claude/adoring-feynman-kqs8us
git pull --ff-only origin claude/adoring-feynman-kqs8us
python3 -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
~/Documents/GitHub/thClaws/target/debug/thclaws --version      # ต้องได้ thclaws 0.138.0 / revision: d452ece
export THCLAWS_PATH="$HOME/Documents/GitHub/thClaws/target/debug"
```

Provider: ใช้ mapping เดิมจาก `.env` (ไม่พิมพ์ค่าออก terminal): `MATCHA_API_KEY` → `OPENAI_COMPAT_API_KEY`,
`MATCHA_API_URL` → `OPENAI_COMPAT_BASE_URL`; model ที่เคยผ่านคือ `oai/gpt-5.4-mini`

## ลำดับการรัน

```bash
# 1. โหมด CI (ไม่มี thclaws ใน PATH)
for f in scripts/check_*.py; do printf "%-45s " "$f"; python3 "$f" > /tmp/ci_$(basename $f .py).log 2>&1; echo "exit=$?"; done

# 2. native ทุก gate ด้วย binary ของเครื่อง
for f in scripts/check_*.py; do printf "%-45s " "$f"; PATH="$THCLAWS_PATH:$PATH" python3 "$f" > /tmp/native_$(basename $f .py).log 2>&1; echo "exit=$?"; done

# 3. M11 live
set -a; . ./.env; set +a; export OPENAI_COMPAT_API_KEY="$MATCHA_API_KEY" OPENAI_COMPAT_BASE_URL="$MATCHA_API_URL"
PATH="$THCLAWS_PATH:$PATH" python3 scripts/check_m11_builder_agent.py \
  --model oai/gpt-5.4-mini --provider-key-env OPENAI_COMPAT_API_KEY --timeout 900

# 4. M9 live (T0) และ diagnostic ของ T1
PATH="$THCLAWS_PATH:$PATH" python3 scripts/check_m9_standalone.py --model oai/gpt-5.4-mini --provider-key-env OPENAI_COMPAT_API_KEY
PATH="$THCLAWS_PATH:$PATH" python3 scripts/check_m9_standalone.py --write-boundary     # คาด exit 0 บน d452ece

# 5. optional
PATH="$THCLAWS_PATH:$PATH" python3 scripts/check_m7a_sql_pack.py
PATH="$THCLAWS_PATH:$PATH" python3 scripts/check_m6_live.py --model oai/gpt-5.4-mini --provider-key-env OPENAI_COMPAT_API_KEY
PATH="$THCLAWS_PATH:$PATH" python3 scripts/check_m7b_packs.py --live --model oai/gpt-5.4-mini --provider-key-env OPENAI_COMPAT_API_KEY
ATLAS_ROOT=../atlas-control-plane PATH="$THCLAWS_PATH:$PATH" python3 scripts/check_m8_export.py
```

## กติกาสำหรับ Claude Code บน local

- ห้ามแก้ gate, pin ของ version/revision, เกณฑ์ audit หรือ golden cases เพื่อให้ผ่าน; ถ้า gate fail ให้วาง output เต็มและหยุดถามก่อน
- ห้าม commit ค่า key, `.env`, `out/`, `pack-conformance.json` หรือ `builder/work`/`builder/out` (ทั้งหมด gitignored อยู่แล้ว)
- ถ้า M10 ยัง fail บน Mac ให้เก็บ `report['detail']` และ `type(exc).__name__` จาก `forge/pack_test.py` ก่อนเสนอแก้; อย่าแก้ harness โดยไม่รู้สาเหตุ
- ถ้า M11 live fail ให้อ่าน `out/m11-live/report.json` (`final_text`, assertion ที่ fail) แล้วรายงาน; การแก้ prompt/skill ของ `builder/` ต้องรัน live ซ้ำอย่างน้อย 3 รอบตามกติกา M7b ก่อนอ้างว่าแก้แล้ว
- ถ้า binary ไม่ใช่ `0.138.0/d452ece` ให้หยุดและรายงาน ไม่ build รุ่นอื่นเองและไม่เพิ่มคู่ version+revision
- เมื่อผ่านครบ ให้บันทึกผลลง `docs/PLAN.md` (ย่อหน้า "M12 live" ใหม่: วันที่, model, binary version+revision, exit code ทุก gate, path evidence) และ `CHANGELOG.md` แล้วค่อย commit เมื่อผู้ใช้สั่งตาม `CLAUDE.md`

## รูปแบบรายงานผล

```
checkout: <git rev-parse --short HEAD>   thclaws: <version> revision <rev>   model: <id>
CI mode:     m0=0 m1=0 ... m12=2
native mode: m0=0 ... m12=0
M11 live: exit=<n>  evidence: out/m11-live/report.json  final_text มีคำว่า missing/unverified: yes|no
M9 live: exit=<n>   write-boundary: exit=<n>
optional: m7a=<n> m6=<n> m7b-live=<n>x3 m8=<n>
สิ่งที่ fail และ output เต็ม: ...
```

## Prompt สำหรับวางใน Claude Code บน Mac

```
อ่าน docs/local-test-plan.md แล้วทำตามลำดับทั้งหมดบนเครื่องนี้ ห้ามแก้ gate/pin/เกณฑ์ audit เพื่อให้ผ่าน
ถ้า step ใด fail ให้วาง output เต็ม หยุด แล้วถามก่อนแก้ ใช้ key จาก .env ตาม mapping ในแผนโดยไม่พิมพ์ค่าออกมา
จบด้วยรายงานตาม "รูปแบบรายงานผล" ในแผน และยังไม่ commit จนกว่าผมสั่ง
```
