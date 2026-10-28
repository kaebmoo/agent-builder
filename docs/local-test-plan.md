# แผนทดสอบบนเครื่อง local (Mac) สำหรับ Claude Code

จุดประสงค์: ปิดส่วนที่ cloud session ทำไม่ได้ เพราะไม่มี provider key, ไม่มี checkout ของ AI/Atlas และไม่ใช่ macOS
งานทั้งหมดอยู่บน branch `claude/adoring-feynman-kqs8us` (PR kaebmoo/agent-builder#1). อ่าน `AGENTS.md`, `CLAUDE.md`, `docs/PLAN.md`
ย่อหน้า "M10 macOS follow-up", "M11 เสร็จ", "M12 เสร็จ", "ผล live บน Mac 2026-10-27" และ "M13 ทำใน cloud", DESIGN §4 "Host + bots layout" และ
`docs/thclaws-host-bots-brief.md` ก่อนเริ่ม. รอบนี้ (M13) สิ่งที่ cloud ทำไม่ได้คือทุกอย่างที่ต้องมี daemon เพราะ binary CLI-only ไม่มี `--serve`

ตั้งแต่ M12 baseline คือ thClaws `0.138.0` revision `d452ece` ซึ่งเป็น binary ที่มีอยู่แล้วที่
`~/Documents/GitHub/thClaws/target/debug/thclaws` (ตรวจด้วย `thclaws --version`). **ไม่ต้อง build รุ่นอื่น**; ถ้า binary ถูก rebuild
จาก commit อื่น ทุก gate ที่ใช้ binary จะ fail exit 1 ตามออกแบบ จนกว่าจะตรวจ DESIGN §4 ใหม่

## สิ่งที่ต้องยืนยัน

| # | สิ่งที่ตรวจ | ทำไมต้องทำบน local | ผลที่คาด |
|---|---|---|---|
| 1 | gate ทุกตัวในโหมด CI (ไม่มี `thclaws` ใน PATH) | ยืนยัน checkout ตรงกับ cloud และยืนยัน fix macOS ของ M10 (เกิดในส่วน offline) | ทุกตัว exit 0 หรือ 2 ไม่มี 1; `check_m10_pack_authoring.py` ต้องเป็น 2 ไม่ใช่ 1 |
| 2 | gate ทุกตัวแบบ native ด้วย binary ของเครื่อง | ยืนยันผล M12 บน macOS (cloud ตรวจบน Linux build จาก commit เดียวกัน) | M0–M5, M7b, M10, M12 exit 0; M6/M7a exit 2 ถ้าไม่มี `../AI`; M8 exit 2 ถ้าไม่มี Atlas; M9/M11 exit 2 (รอ key) |
| 3 | `check_m13_host_layout.py` native (desktop build) | cloud build CLI-only ไม่มี host/migration จึงตรวจ control case และการเปิด `builder/` เป็น host ไม่ได้ | exit 0; ถ้า "changed files outside runtime state" ให้วางรายการไฟล์แล้วหยุด (DESIGN ต้องแก้ก่อน ไม่เพิ่ม ignore) |
| 4 | `check_m11_builder_agent.py` live `--rounds 3` (ต้องมี key) | DoD ส่วน live ของ M11 ยังไม่ผ่าน; gate เป็นสองเทิร์นและ prompt/skill เปลี่ยน จึงต้อง 3 รอบ | exit 0; evidence `out/m11-live/round-<n>-<ts>.json` ครบ 3 ไฟล์ |
| 5 | `check_m7b_packs.py --live` 3 รอบ (ต้องมี key) | ครั้งก่อนผ่านด้วย launcher นอก repo; ต้องยืนยันว่า harness เองตั้ง `THCLAWS_AUTO_MIGRATE=0` แล้วพอ | exit 0 ทั้ง 3 รอบ |
| 6 | `check_m9_standalone.py` live (ต้องมี key) — ผ่านแล้ว 2026-10-27 ไม่ต้องรันซ้ำเว้นแต่ checkout เปลี่ยน | evidence provider เดิมเป็นของ 0.116.0 | exit 0 |
| 6b | `check_m15_tool_allowlist.py` native + provider (desktop build, key) | ต้องมี daemon `--serve`; cloud build CLI-only ไม่มี | exit 0 (probe บน package จริง + golden cases 4/4); M7b `--live` 3 รอบซ้ำเพราะ tool list ของ publisher เปลี่ยน |
| 7 | (optional) M7a/M6 จากโฟลเดอร์คู่ worktree `AI`@`d95dc28` + `agent-builder` ด้วย venv `.[sql-pack]` (`mcp>=1.26,<2`), M8 กับ `ATLAS_ROOT` worktree ที่ pin `daa0f49` | M6/M7a ผ่านบน `d452ece` แล้ว 2026-10-28 (รันซ้ำเมื่อ pack/generator/baseline เปลี่ยน); `fixture/setup.py` หา AI ที่ sibling เท่านั้น; M8 default `../atlas-control-plane` อยู่คนละ revision | exit 0 หรือ skip 2 พร้อมเหตุผล |

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

# 3. M13 native บน desktop build (ไม่ต้องใช้ key): control case ของ migration + เปิด builder/ เป็น host
PATH="$THCLAWS_PATH:$PATH" python3 scripts/check_m13_host_layout.py

# 4. M11 live สองเทิร์น 3 รอบ (harness ตั้ง THCLAWS_AUTO_MIGRATE=0 เองแล้ว ไม่ต้องใช้ launcher นอก repo; --model บังคับระบุ)
set -a; . ./.env; set +a; export OPENAI_COMPAT_API_KEY="$MATCHA_API_KEY" OPENAI_COMPAT_BASE_URL="$MATCHA_API_URL"
PATH="$THCLAWS_PATH:$PATH" python3 scripts/check_m11_builder_agent.py \
  --model oai/gpt-5.4-mini --provider-key-env OPENAI_COMPAT_API_KEY --rounds 3
# ถ้ารอบใด fail: evidence ของรอบนั้นอยู่ที่ out/m11-live/round-<n>-<ts>.json (events เก็บไว้แม้ timeout) — วาง detail และ final_text ของแต่ละ turn
# (optional) ผ่าน host จริงของ desktop build แทนการสตาร์ทจาก agent dir:
PATH="$THCLAWS_PATH:$PATH" python3 scripts/check_m11_builder_agent.py \
  --model oai/gpt-5.4-mini --provider-key-env OPENAI_COMPAT_API_KEY --rounds 1 --via-host

# 5. M7b live 3 รอบ (ก่อนหน้า fail 3/3 เพราะ migration; harness ใหม่ต้องผ่านเองโดยไม่ตั้ง env ภายนอก)
for i in 1 2 3; do PATH="$THCLAWS_PATH:$PATH" python3 scripts/check_m7b_packs.py --live --model oai/gpt-5.4-mini --provider-key-env OPENAI_COMPAT_API_KEY; echo "round $i exit=$?"; done

# 6. M14 (T1 standalone) native + provider live, แล้ว M9 live (T0) ซ้ำเพราะ run_standalone เพิ่ม tree diff (M14)
PATH="$THCLAWS_PATH:$PATH" python3 scripts/check_m14_standalone_t1.py --model oai/gpt-5.4-mini --provider-key-env OPENAI_COMPAT_API_KEY
PATH="$THCLAWS_PATH:$PATH" python3 scripts/check_m9_standalone.py --model oai/gpt-5.4-mini --provider-key-env OPENAI_COMPAT_API_KEY
PATH="$THCLAWS_PATH:$PATH" python3 scripts/check_m9_standalone.py --write-boundary     # คาด exit 0 บน d452ece

# 6b. M15 (allowedTools บน /agent/run) native + provider live; tool list ของทุก package Atlas เปลี่ยน จึงรัน M7b live ข้อ 5 ซ้ำด้วย
PATH="$THCLAWS_PATH:$PATH" python3 scripts/check_m15_tool_allowlist.py --model oai/gpt-5.4-mini --provider-key-env OPENAI_COMPAT_API_KEY

# 7. optional
# M6/M7a: packs/sql-readonly/fixture/setup.py หา AI ที่ `<โฟลเดอร์แม่ของ agent-builder>/AI` เท่านั้น (ไม่มี env override) จึงใช้ worktree ของ AI
# ที่อื่นตรง ๆ ไม่ได้ และห้ามแก้ fixture: สร้างโฟลเดอร์คู่ที่มี AI@d95dc28 กับ agent-builder@HEAD วางข้างกัน แล้วรัน gate จาก agent-builder ในนั้น
# (gate ใส่ ROOT ของตัวเองหน้า sys.path จึงใช้ forge ของ worktree). mcp ต้องเป็น v1 ตาม `.[sql-pack]` (mcp>=1.26,<2):
# mcp 2.x ลบ `mcp.server.fastmcp` ทำให้ gate fail ที่ "MCP exited before replying". SQL_READONLY_DSN ไม่ต้องตั้ง: setup.py สร้าง SQLite สังเคราะห์เอง
PAIR="$HOME/Documents/GitHub/sql-pin-d95dc28"    # ชื่อใดก็ได้ ขอให้ภายในมี AI/ และ agent-builder/ ข้างกัน
git -C ~/Documents/GitHub/AI worktree add --detach "$PAIR/AI" d95dc28628673d7adef1d6f1958116922fea1946
git worktree add --detach "$PAIR/agent-builder" HEAD
python3 -m venv "$PAIR/venv" && "$PAIR/venv/bin/python3" -m pip install -e "$PAIR/agent-builder[sql-pack]"
(cd "$PAIR/agent-builder" && . "$PAIR/venv/bin/activate" && PATH="$THCLAWS_PATH:$PATH" python3 scripts/check_m7a_sql_pack.py)
(cd "$PAIR/agent-builder" && . "$PAIR/venv/bin/activate" && PATH="$THCLAWS_PATH:$PATH" python3 scripts/check_m6_live.py --model oai/gpt-5.4-mini --provider-key-env OPENAI_COMPAT_API_KEY)
# เลิกใช้: git -C ~/Documents/GitHub/AI worktree remove "$PAIR/AI" && git worktree remove "$PAIR/agent-builder" && rm -rf "$PAIR/venv"
# M8: ต้องใช้ worktree ของ Atlas ที่ pin daa0f49 ไม่ใช่ ../atlas-control-plane
ATLAS_ROOT="$HOME/Documents/GitHub/atlas-control-plane-daa0f49" PATH="$THCLAWS_PATH:$PATH" python3 scripts/check_m8_export.py
```

ข้อควรรู้เรื่อง layout (M13, DESIGN §4 "Host + bots layout"): desktop build ของ thClaws 0.138.0 แปลงโฟลเดอร์ agent แบบเดิมเป็น host workspace เมื่อเปิดด้วย
`--serve`/desktop; harness ทุกตัวตั้ง `THCLAWS_AUTO_MIGRATE=0` และตรวจ `info.workspace_dir` เองแล้ว จึง**ไม่ต้อง**ตั้ง env หรือใช้ launcher นอก repo อีก.
`builder/` เป็น host workspace ใน git แล้ว (agent ที่ `.thclaws/bots/main/`): เปิดด้วย desktop ได้โดยไม่ migrate; ห้ามรัน `thclaws bots migrate`/`unmigrate` กับมัน.

## กติกาสำหรับ Claude Code บน local

- ห้ามแก้ gate, pin ของ version/revision, เกณฑ์ audit หรือ golden cases เพื่อให้ผ่าน; ถ้า gate fail ให้วาง output เต็มและหยุดถามก่อน
- ห้าม commit ค่า key, `.env`, `out/`, `pack-conformance.json` หรือ `builder/work`/`builder/out` (ทั้งหมด gitignored อยู่แล้ว)
- ถ้า M10 ยัง fail บน Mac ให้เก็บ `report['detail']` และ `type(exc).__name__` จาก `forge/pack_test.py` ก่อนเสนอแก้; อย่าแก้ harness โดยไม่รู้สาเหตุ
- ถ้า M11 live fail ให้อ่าน `out/m11-live/report.json` (`final_text`, assertion ที่ fail) แล้วรายงาน; การแก้ prompt/skill ของ `builder/` ต้องรัน live ซ้ำอย่างน้อย 3 รอบตามกติกา M7b ก่อนอ้างว่าแก้แล้ว
- ถ้า binary ไม่ใช่ `0.138.0/d452ece` ให้หยุดและรายงาน ไม่ build รุ่นอื่นเองและไม่เพิ่มคู่ version+revision
- เมื่อผ่านครบ ให้บันทึกผลลง `docs/PLAN.md` (ย่อหน้า "M12 live" ใหม่: วันที่, model, binary version+revision, exit code ทุก gate, path evidence) และ `CHANGELOG.md` แล้วค่อย commit เมื่อผู้ใช้สั่งตาม `CLAUDE.md`

## รูปแบบรายงานผล

```
checkout: <git rev-parse --short HEAD>   thclaws: <version> revision <rev> (desktop build: thclaws --help มี bots)   model: <id>
CI mode:     m0=0 m1=0 ... m13=2
native mode: m0=0 ... m12=0 m13=<n>
M13 native: exit=<n>  (control migration เกิด bots.json: yes|no; host เปิด builder/ แล้วไฟล์ที่ track เปลี่ยน: none|<รายการ>)
M11 live:   exit=<n>  rounds passed=<k>/3  evidence: out/m11-live/round-*.json  (ต่อรอบ: turn1 หยุดถาม yes|no, turn2 build.py yes|no, detail)
M7b live:   exit=<n> x3
M9 live:    exit=<n>   write-boundary: exit=<n>   (ถ้ารันซ้ำ)
optional:   m7a=<n> m6=<n> m8=<n> (ATLAS_ROOT = worktree ที่ pin)
สิ่งที่ fail และ output เต็ม: ...
```

## Prompt สำหรับวางใน Claude Code บน Mac (session แยก)

```
git fetch origin claude/adoring-feynman-kqs8us && git checkout claude/adoring-feynman-kqs8us && git pull --ff-only
อ่าน docs/local-test-plan.md แล้วทำตามลำดับทั้งหมดบนเครื่องนี้ด้วย binary thclaws 0.138.0/d452ece (desktop build) ของเครื่อง
ห้ามแก้ gate/pin/เกณฑ์ audit/golden cases เพื่อให้ผ่าน และห้าม thclaws bots migrate/unmigrate กับ builder/
ลำดับ: 1 CI mode ทุก gate → 2 native ทุก gate → 3 check_m13_host_layout.py → 4 check_m11_builder_agent.py --rounds 3
→ 5 check_m7b_packs.py --live x3 → 7 optional (M8 กับ ATLAS_ROOT ที่ pin daa0f49, M6/M7a จากโฟลเดอร์คู่ worktree AI@d95dc28 + agent-builder ตามข้อ 7)
ถ้า step ใด fail ให้วาง output เต็ม (M11: detail และ final_text ของแต่ละ turn จาก out/m11-live/round-<n>-<ts>.json หลัง redact) หยุด แล้วถามก่อนแก้
ใช้ key จาก .env ตาม mapping ในแผนโดยไม่พิมพ์ค่าออกมา และตรวจว่า log ที่วางไม่มีค่า key
เมื่อผ่านครบ บันทึกผลลง docs/PLAN.md (ย่อหน้า "M13 เสร็จ (Mac)" + "M11 live" + "M12 live") และ CHANGELOG.md ตามรูปแบบรายงานผล แล้ว commit/push
ขึ้น claude/adoring-feynman-kqs8us ได้เลย
```
