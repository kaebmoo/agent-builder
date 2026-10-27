# แผนทดสอบบนเครื่อง local (Mac) สำหรับ Claude Code

จุดประสงค์: ปิดส่วนที่ cloud session ทำไม่ได้ เพราะไม่มี provider key, ไม่มี thClaws revision ที่ pin และไม่ใช่ macOS
งานทั้งหมดอยู่บน branch `claude/adoring-feynman-kqs8us` (commit `0b2a58d` ขึ้นไป). อ่าน `AGENTS.md`, `CLAUDE.md`, `docs/PLAN.md`
ย่อหน้า "M10 macOS follow-up" และ "M11 เสร็จ" ก่อนเริ่ม

## สิ่งที่ต้องยืนยัน

| # | สิ่งที่ตรวจ | ทำไมต้องทำบน local | ผลที่คาด |
|---|---|---|---|
| 1 | gate ทุกตัวในโหมด CI (ไม่มี `thclaws` ใน PATH) | ยืนยันว่า checkout ของเครื่องตรงกับ cloud | ทุกตัว exit 0 หรือ 2 ไม่มี 1 |
| 2 | `check_m10_pack_authoring.py` กับ thclaws 0.116.0 | รอบก่อน fail บน Mac ด้วย `PermissionError`; แก้แล้วแต่ยังไม่มี Mac ยืนยัน | exit 0 |
| 3 | `check_m11_builder_agent.py` live (ต้องมี key) | DoD ส่วน live ของ M11 ยังไม่เคยรัน | exit 0 และ evidence ใน `out/m11-live/report.json` |
| 4 | `check_m9_standalone.py` กับ binary revision ที่ pin | revision `75edc48`/`35ccf16`/`7a27129` มีเฉพาะใน checkout ของ Mac | exit 0 ถ้ามี revision นั้น; binary รุ่นอื่น exit 1 ตามออกแบบ |
| 5 | (optional) M6/M7a กับ `../AI`, M8 กับ `ATLAS_ROOT` | ต้องมี checkout ข้างเคียง | exit 0 หรือ skip 2 พร้อมเหตุผล |

## เตรียมเครื่อง

```bash
cd ~/Documents/GitHub/agent-builder
git fetch origin claude/adoring-feynman-kqs8us
git checkout claude/adoring-feynman-kqs8us
git pull --ff-only origin claude/adoring-feynman-kqs8us
python3 -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
```

thClaws: binary ปัจจุบันบน Mac คือ `0.138.0/d452ece` ซึ่ง gate ฝั่ง Atlas (pin `0.116.0`) และ M9 (pin คู่ version+revision) จะ fail exit 1
ให้สร้าง worktree แยกแทนการ checkout ทับงานปัจจุบัน:

```bash
cd ~/Documents/GitHub/thClaws
git log --oneline --all | grep -E '^(75edc48|35ccf16|7a27129)'     # ดูว่า revision ที่ pin ยังอยู่ในเครื่องไหม
git worktree add ../thClaws-0116 v0.116.0                            # release = b962abc (ใช้กับ M4/M5/M7b/M8/M10/M11)
git worktree add ../thClaws-m9 75edc48                               # ถ้า grep ข้างบนพบ (ใช้กับ M9)
# build แบบ CLI-only ถ้าไม่อยาก build frontend: mkdir -p frontend/dist && touch frontend/dist/index.html ใน worktree นั้นก่อน
(cd ../thClaws-0116/crates/core && cargo build --bin thclaws)
(cd ../thClaws-m9/crates/core && cargo build --bin thclaws)
../thClaws-0116/target/debug/thclaws --version                        # ต้องได้ thclaws 0.116.0
```

Provider: ใช้ mapping เดิมจาก `.env` (ไม่พิมพ์ค่าออก terminal): `MATCHA_API_KEY` → `OPENAI_COMPAT_API_KEY`,
`MATCHA_API_URL` → `OPENAI_COMPAT_BASE_URL`; model ที่เคยผ่านคือ `oai/gpt-5.4-mini`

## ลำดับการรัน

```bash
# 1. โหมด CI (ไม่มี thclaws ใน PATH)
for f in scripts/check_*.py; do printf "%-45s " "$f"; python3 "$f" > /tmp/ci_$(basename $f .py).log 2>&1; echo "exit=$?"; done

# 2. M10 ด้วย 0.116.0 (ยืนยัน fix macOS)
PATH="$HOME/Documents/GitHub/thClaws-0116/target/debug:$PATH" python3 scripts/check_m10_pack_authoring.py

# 3. M11 offline + native (คาด exit 2 เพราะยังไม่ใส่ key) แล้ว live
PATH="$HOME/Documents/GitHub/thClaws-0116/target/debug:$PATH" python3 scripts/check_m11_builder_agent.py
set -a; . ./.env; set +a; export OPENAI_COMPAT_API_KEY="$MATCHA_API_KEY" OPENAI_COMPAT_BASE_URL="$MATCHA_API_URL"
PATH="$HOME/Documents/GitHub/thClaws-0116/target/debug:$PATH" python3 scripts/check_m11_builder_agent.py \
  --model oai/gpt-5.4-mini --provider-key-env OPENAI_COMPAT_API_KEY --timeout 900

# 4. M9 ด้วย revision ที่ pin (ถ้ามี worktree thClaws-m9) และยืนยัน fail-closed ด้วย 0.138.0
PATH="$HOME/Documents/GitHub/thClaws-m9/target/debug:$PATH" python3 scripts/check_m9_standalone.py \
  --model oai/gpt-5.4-mini --provider-key-env OPENAI_COMPAT_API_KEY
PATH="$HOME/Documents/GitHub/thClaws/target/debug:$PATH" python3 scripts/check_m9_standalone.py   # คาด exit 1 พร้อมข้อความ revision

# 5. optional
PATH="$HOME/Documents/GitHub/thClaws-0116/target/debug:$PATH" python3 scripts/check_m7a_sql_pack.py
PATH="$HOME/Documents/GitHub/thClaws-0116/target/debug:$PATH" python3 scripts/check_m6_live.py --model oai/gpt-5.4-mini --provider-key-env OPENAI_COMPAT_API_KEY
ATLAS_ROOT=../atlas-control-plane PATH="$HOME/Documents/GitHub/thClaws-0116/target/debug:$PATH" python3 scripts/check_m8_export.py
```

## กติกาสำหรับ Claude Code บน local

- ห้ามแก้ gate, pin ของ version/revision, เกณฑ์ audit หรือ golden cases เพื่อให้ผ่าน; ถ้า gate fail ให้วาง output เต็มและหยุดถามก่อน
- ห้าม commit ค่า key, `.env`, `out/`, `pack-conformance.json` หรือ `builder/work`/`builder/out` (ทั้งหมด gitignored อยู่แล้ว)
- ถ้า M10 ยัง fail บน Mac ให้เก็บ `report['detail']` และ `type(exc).__name__` จาก `forge/pack_test.py` ก่อนเสนอแก้; อย่าแก้ harness โดยไม่รู้สาเหตุ
- ถ้า M11 live fail ให้อ่าน `out/m11-live/report.json` (`final_text`, assertion ที่ fail) แล้วรายงาน; การแก้ prompt/skill ของ `builder/` ต้องรัน live ซ้ำอย่างน้อย 3 รอบตามกติกา M7b ก่อนอ้างว่าแก้แล้ว
- การย้าย baseline ไป thClaws 0.138.0 เป็นงานแยก: ต้องตรวจ source ตาม DESIGN §4 (writePaths traversal/symlink, `todos.md`, MCP naming, `InitializeResult.instructions`, `.thclaws/prompt/system.md`) ก่อนเพิ่มคู่ version+revision ไม่ใช่เพิ่มลง list เฉย ๆ
- เมื่อผ่านครบ ให้บันทึกผลลง `docs/PLAN.md` (ย่อหน้า "M11 live" ใหม่: วันที่, model, binary version+revision, exit code, path evidence) และ `CHANGELOG.md` แล้วค่อย commit เมื่อผู้ใช้สั่งตาม `CLAUDE.md`

## รูปแบบรายงานผล

```
checkout: <git rev-parse --short HEAD>   thclaws: <version> revision <rev>   model: <id>
CI mode:  m0=0 m1=0 ... m11=2
M10 native: exit=<n>  <บรรทัดสุดท้ายของ output>
M11 native: exit=<n>  live: exit=<n>  evidence: out/m11-live/report.json  final_text มีคำว่า missing/unverified: yes|no
M9 (<rev>): exit=<n>  M9 (0.138.0): exit=1 ตามออกแบบ
optional: m7a=<n> m6=<n> m8=<n>
สิ่งที่ fail และ output เต็ม: ...
```

## Prompt สำหรับวางใน Claude Code บน Mac

```
อ่าน docs/local-test-plan.md แล้วทำตามลำดับทั้งหมดบนเครื่องนี้ ห้ามแก้ gate/pin/เกณฑ์ audit เพื่อให้ผ่าน
ถ้า step ใด fail ให้วาง output เต็ม หยุด แล้วถามก่อนแก้ ใช้ key จาก .env ตาม mapping ในแผนโดยไม่พิมพ์ค่าออกมา
จบด้วยรายงานตาม "รูปแบบรายงานผล" ในแผน และยังไม่ commit จนกว่าผมสั่ง
```
