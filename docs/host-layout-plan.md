# แผน M13 — รองรับ host + bots layout ของ thClaws 0.138.0 (`d452ece`)

สถานะเอกสาร: **แผน ยังไม่ได้แก้ DESIGN/โค้ด** (2026-10-27). เขียนจาก session cloud ที่ clone source `kaebmoo/thClaws`
branch `baseline-0.138.0-d452ece` (HEAD = `d452ece`) แล้วอ่าน source โดยตรง; ไม่มี binary ใน session นี้ จึงยังไม่ได้รันพฤติกรรมจริง
ข้อที่ต้องยืนยันด้วย binary บน Mac ระบุไว้ใน §6. กติกาที่บังคับทุกขั้น: AGENTS.md (DESIGN ก่อนโค้ด, generator deterministic,
หนึ่ง milestone หนึ่ง `scripts/check_<m>.py` exit 0/1/2, ไม่แก้ gate/pin/เกณฑ์ audit เพื่อให้ผ่าน, แก้ prompt/skill ของ `builder/` ต้องรัน M11 live ซ้ำ 3 รอบ)

## 1. ปัญหา (สรุป)

M11 live บน Mac exit 1: binary 0.138.0 (desktop build) ที่เปิดด้วย `--serve` แปลงโฟลเดอร์ agent แบบเดิม (v2) เป็น host workspace
ในที่เดิม แล้ว harness ส่ง `workspace_dir` = root ซึ่งตอนนี้ว่าง (AGENTS.md/skills ย้ายไป `.thclaws/bots/main/`) agent จึงไม่มีคำสั่งใด
DESIGN §4 ของ M12 บันทึกเฉพาะ `agent pack` strip `.thclaws/bots/` ไม่ได้บันทึก layout migration นี้. M9 live ผ่านบนเครื่องเดียวกัน
เพราะใช้ `thclaws agent run` ไม่ใช่ `--serve` (ยืนยันแล้วทั้งจากผลรันและจาก source §2 F1)

## 2. ข้อเท็จจริงจาก source `d452ece` (ตรวจใน session นี้, path ใต้ `crates/core/src/`)

F1 **เงื่อนไข auto-migrate** (`bin/app.rs:1017-1060`): ทำงานเฉพาะเมื่อ binary build ด้วย feature `gui` (`#[cfg(feature = "gui")]`;
`Cargo.toml:219` `default = []` → build CLI-only ไม่มีโค้ดนี้เลย), `auto_migrate_allowed()` เป็นจริง, ไม่ใช่ `-p`/`--cli`, ไม่ใช่ `--multi-tenant`,
และเป็น `--serve` หรือ desktop. `auto_migrate_allowed()` (`bots/migrate.rs:256-265`): `THCLAWS_AUTO_MIGRATE=0` หรือ `THCLAWS_SUPERVISED=1` → false;
มิฉะนั้น true เว้นแต่ `THCLAWS_INSIDE_DOCKER=1` ที่ไม่มี `THCLAWS_AUTO_MIGRATE=1`. เป้าหมาย = โฟลเดอร์ที่ไม่มี `.thclaws/bots.json` แต่มี `AGENTS.md`
หรือ `manifest.json` หรือ `.thclaws/settings.json` หรือ `.thclaws/state/` (`:1237-1245`) → **ทุก package ที่ generator สร้างและ `builder/` เข้าเกณฑ์**.
ผลต่อเรา: Mac (desktop build) กับ cloud (CLI-only build, placeholder frontend) ต่างกันที่ **build feature** ไม่ใช่แค่ container;
build CLI-only ไม่ migrate, ไม่เป็น host (`workspace_is_v3()` คืน false, `:870-872`) และไม่มี subcommand `thclaws bots` (`:354-358`)

F2 **สิ่งที่ migration ทำ** (`bots/migrate.rs:63,79,470-532,1024-1072`): ย้ายเฉพาะ `.thclaws/`, `AGENTS.md`, `CLAUDE.md`, `manifest.json`
ไป `.thclaws/bots/main/`; ไฟล์อื่นอยู่ root (`workspaceVersion` 4, ไม่มี tombstone AGENTS.md ที่ root แล้ว); host เขียน `.thclaws/settings.json`
(`workspaceVersion: 4`, `_doc`), `.thclaws/bots.json`, `.thclaws/state/.gitignore`; ใน bot: `mint_identity` เติม `agent.id/name/description`
จากชื่อโฟลเดอร์ถ้าขาด, `ProjectConfig::backfill_defaults_in` เติม key ใน settings, และ `agent_paths::fix` **เขียนทับไฟล์ของ agent**
(`AGENTS.md`, `CLAUDE.md`, `.md/.js/.py` ใต้ `.thclaws/{scripts,skills,workflows,agent_workflow,gui-shell,agents,commands,templates}`):
`python3 .thclaws/x.py` → `python3 "$THCLAWS_AGENT_DIR/.thclaws/x.py"`, `Path("./.thclaws/..")`/`open(...)` → `AGENT_DIR / "..."` พร้อมแทรกบรรทัด
`AGENT_DIR = Path(os.environ.get("THCLAWS_AGENT_DIR") or ".")` (`bots/agent_paths.rs:191-260`); ต้นฉบับสำรองที่ `.thclaws/state/pre-agent-dir/`.
→ tree ที่ถูก migrate **ไม่ byte-identical กับ package ที่ audit** (static audit จะรายงาน drift ถูกต้องแล้ว) และ `unmigrate` ย้อนได้เฉพาะเมื่อมี agent เดียวชื่อ `main` (`:773`)

F3 **host process** (`server.rs:1067-1210`, `bots/supervisor.rs:613-660`): host ที่ port ที่สั่ง ไม่โหลด agent/model/MCP; spawn ต่อ bot
`thclaws --serve --bind 127.0.0.1 --port 0` ด้วย cwd = `.thclaws/bots/<slug>` และ env เพิ่ม `THCLAWS_SERVE_TOKEN`, `THCLAWS_SERVE_ADDR_FILE`,
`THCLAWS_SUPERVISED=1`, `THCLAWS_WORKSPACE_ROOT=<root>`; env อื่นสืบทอด (`THCLAWS_API_TOKEN`, `THCLAWS_CONFIG`, `HOME`, provider key จึงไปถึง bot).
`/v1/*` และ `/agent/run` ถูก forward ไป bot ที่เลือกด้วย query `?bot=<slug>` (ไม่ระบุ = ตัวแรกใน `bots.json`) โดยส่ง `Authorization` ของผู้เรียกผ่านไปตรง ๆ
(`server.rs:1362-1390`); `/v1/agent/info.workspace_dir` ของ bot = process cwd = bot dir (`api_v1/info.rs:128`).
ที่ host เปิดบน v2 ที่ยังไม่ migrate (เช่น CLI-only build ไม่มีโค้ด migrate แต่ถ้ามี `bots.json`) จะ error ชัด "`thclaws bots migrate` first" (`server.rs:1093-1101`)

F4 **`/agent/run` ใต้ host ใช้ `workspace_dir` เดียวกับสองความหมาย**: skills ค้นจาก `<workspace_dir>/.thclaws/skills` (`agent_runtime.rs:167-171`),
AGENTS.md walk ขึ้นจาก `workspace_dir` และ **หยุดที่ shelf** ไม่โหลด AGENTS.md ของ host (`context.rs:238-262`), `collect_files` snapshot เดิน
`workspace_dir` โดยข้าม `.thclaws` (`api_v1/artifacts.rs:110-150`, เรียกจาก `api_v1/agent.rs:460`). แต่ file tools และ Bash ทำงานที่
`THCLAWS_WORKSPACE_ROOT` (sandbox root `sandbox.rs:28-45`; `workdir.rs:55-110`): path relative ทั่วไป → root, `.thclaws/...` ของ agent → bot dir
(ยกเว้น `state/kms`, `state/logs`), Bash cwd = root และได้ `THCLAWS_AGENT_DIR` **เสมอแม้ไม่มี host** (`tools/bash.rs:1417-1430`);
system prompt อธิบายสองโฟลเดอร์นี้ให้ model (`context.rs:112-130`). MCP config, `allowedTools`, `.thclaws/prompt/system.md`, `todos.md` ยังอ่านจาก cwd
ของ process = bot dir จึงไม่พัง. ผลคือบน host:

| `workspace_dir` ที่ส่ง | identity (AGENTS.md/skills) | ไฟล์ที่ agent เขียน | `collect_files` |
|---|---|---|---|
| root | ไม่มี (อาการ M11) | root | เห็น |
| `.thclaws/bots/<slug>` | ครบ | root | **ไม่เห็น** (snapshot เดิน bot dir) |

→ ไม่มีค่าใดที่ทำให้ contract `atlas-worker` (identity + `collect_files` + handoff ใน `workspace_dir` เดียว) สมบูรณ์บน host ใน `d452ece`

F5 **`bots add`** (`bots/install.rs:30-80` → `cloud/cmd.rs:708-723`): รับเฉพาะ slug ของ Agent Template ใน catalogue thClaws.cloud, ต้อง login token,
download แล้วแตกลง `.thclaws/bots/<slug>/` และเติม `bots.json`; **ไม่รับ archive หรือ path local**. `bots.json` ออกแบบให้แก้มือได้ (`bots/mod.rs:34`)
จึงมีทางติดตั้งมือ = แตก archive ของ `thclaws agent pack` ลง `.thclaws/bots/<slug>/` แล้วเติม `bots.json` (ยังไม่ทดสอบ)

F6 **validate/pack ต้องการ `manifest.json` ที่โฟลเดอร์ที่ชี้** (`cloud/agent_cli.rs:32-36,58-63,96-104`) และ pack strip `.thclaws/bots/`
→ ชี้ที่ root ของ host ไม่ได้ ต้องชี้ bot dir; archive ที่ได้เป็น agent เดี่ยว (v2 shape) เสมอ → output ของ generator และ `forge export` ไม่ต้องเปลี่ยนรูป

F7 `thclaws -p` / `--cli` ที่ root ของ v3 ก้าวเข้า `bots[0]` และตั้ง `THCLAWS_WORKSPACE_ROOT` (gui build, `bin/app.rs:1067-1095`); `thclaws agent run <dir>`
(M9) ไม่อยู่ในเงื่อนไข migrate

F8 host ซ่อม agent "stale" ตอนเปิด (`server.rs:1150`, `agent_paths::repair_stale`) เฉพาะ agent ที่ข้อความของตนไม่เคยกล่าวถึง `$THCLAWS_AGENT_DIR`
(`migrate.rs:565`) → agent ที่เขียน `$THCLAWS_AGENT_DIR` ไว้แล้วจะไม่ถูกเขียนทับ

F9 `THCLAWS_AGENT_WORKSPACE_ROOT` จำกัดให้ `workspace_dir` ของ request อยู่ใต้ root ที่กำหนด (`agent_runtime.rs:284-325`) — ตัวเลือก hardening ของ deployment, นอกแผนนี้

F10 (ประเด็นข้างเคียงสำหรับ milestone `allowedTools`) `apply_filter(allowed_tools, disallowed_tools)` ถูกเรียก **ก่อน** ลงทะเบียน Skill tools และ MCP tools
(`agent_runtime.rs:128-138` เทียบ `:171-185`, `:190-200`) → `allowedTools` กรองเฉพาะ built-in, ไม่กรอง MCP/Skill. ตอบคำถามที่ M12 ทิ้งไว้ ("ต้องตรวจตอนทำ")

## 3. คำตอบคำถามเปิด (ข้อเสนอการตัดสินใจ)

1. **ครอบคลุมแค่ไหน** — แยกสี่ชั้น: (a) harness: ล็อก v2 ด้วย `THCLAWS_AUTO_MIGRATE=0` ทุกจุดที่สตาร์ท `--serve` + guard ตรวจว่า tree ไม่ถูกแปลง;
   (b) deploy contract กับ Atlas: **คง v2 (single-agent workspace) เท่านั้น** host layout ไม่รองรับสำหรับ `/agent/run` (เหตุผล F2, F4) และบันทึกเป็นข้อห้ามใน §8;
   (c) `builder/`: **แปลงเป็น host workspace (v4) ใน git** เพราะเป็นที่เดียวที่ผู้ใช้เปิดด้วย desktop/`--serve` จริง และถ้าปล่อยเป็น v2 desktop จะเขียนทับ tree
   ที่ git track ทุกครั้งที่เปิด (F2) ส่วนการให้ตั้ง env ก่อนเปิด GUI ไม่ปฏิบัติได้จริง; (d) output ของ generator: **ไม่เปลี่ยน** (F6)
2. **`workspace_dir` ที่ถูกต้องบน host** — สำหรับ identity คือ bot dir แต่ `collect_files` จะเดินผิดที่ (F4) จึงไม่ adopt กับ Atlas; host proxy `/agent/run` ไป bot ด้วย `?bot=` (F3)
3. **`bots add`** — catalogue เท่านั้น ไม่รับ archive จาก `agent pack` (F5) → ไม่ใช่ขั้น deploy มาตรฐาน; ทางมือ (แตก archive + แก้ `bots.json`) เป็น optional check บน Mac
4. **หลาย agent ต่อ host กับ DESIGN §8** — ไม่ขัดเพราะ deploy คง v2; ถ้าวันหน้า adopt host, Atlas เห็น host เป็น daemon URL เดียว → bot เดียว (default) เพราะ
   `base_url` ของ export ห้าม `?` → กฎ "หนึ่ง daemon URL ต่อหนึ่ง worker" ยังเป็นจริง และ Atlas ไม่ต้องแก้โค้ด
5. **root ที่ใช้ร่วมกัน** — บน host `write_scope` และ `collect_files` อ้างคนละ directory (F4) และตำแหน่ง `inputs/incoming/...` ของ Atlas บน host ยังไม่ได้ตรวจ
   → guarantee matrix ไม่เพิ่ม claim ใด; ไม่ต้องตรวจ Atlas handoff บน host จนกว่าจะคิด adopt
6. **`THCLAWS_AUTO_MIGRATE`** — ตั้ง `0` ชัดเจนทุกจุดที่เราสตาร์ท `--serve` และในเอกสาร operator; แต่ env อย่างเดียวไม่พอ gate ต้องตรวจผล
   (`/v1/agent/info.workspace_dir` ต้องเท่ากับ run dir และต้องไม่มี `.thclaws/bots.json` โผล่) และต้องรายงาน build flavour (gui/CLI-only) เพราะ host path ทดสอบได้เฉพาะ gui build
7. **เลข milestone** — M13 = host layout (งานนี้); ข้อเสนอเดิมเลื่อนเป็น M14 ปลด T1 และ M15 `allowedTools` (ยังเป็นข้อเสนอทั้งคู่; F10 เข้า scope M15)

## 4. ขั้นตอน (ตามลำดับบังคับ: DESIGN → PLAN → gate → โค้ด → docs → รัน)

A. **DESIGN** — §4 เพิ่มหัวข้อ "Host + bots layout (0.138.0 / `d452ece`)" บันทึก F1–F10 พร้อม path:บรรทัด และแก้ข้อ 4 ของรายการ M12 ให้ชี้มาที่นี่;
   §8 เพิ่มกฎ: package/daemon-per-package เป็น single-agent workspace, start `--serve` ด้วย `THCLAWS_AUTO_MIGRATE=0`, ห้ามเปิด package ที่ audit ใน desktop
   หรือรัน `thclaws bots migrate` (tree จะ drift), `thclaws agent validate/pack` ชี้ bot dir เสมอเมื่ออยู่ใน host workspace; §9 ระบุว่า `builder/` เป็น host workspace,
   shell ของ builder มี cwd = workspace root และต้องเรียก runner ผ่าน `"$THCLAWS_AGENT_DIR/.thclaws/scripts/build.py"`; `work/`, `out/` อยู่ที่ root ของ workspace
B. **PLAN/CHANGELOG** — แถว M13 (ผลลัพธ์, DoD, `check_m13_host_layout.py`), เลื่อน M13/M14 เดิมเป็น M14/M15, ย่อหน้า "ขั้นถัดไป" บันทึกสาเหตุ M11 live fail และผล M9 live;
   CHANGELOG Unreleased
C. **gate `scripts/check_m13_host_layout.py`** (เขียนก่อนโค้ด ให้ fail ก่อน):
   - offline (exit 0/1): `builder/.thclaws/bots.json` มี agent เดียว, host settings `workspaceVersion: 4`, ไม่มี agent file ที่ root (`AGENTS.md`, `manifest.json`,
     `.thclaws/settings.json` ของ agent ย้ายครบ), agent dir มี manifest/AGENTS.md/settings/skills/scripts; AGENTS.md และ skills อ้าง
     `$THCLAWS_AGENT_DIR/.thclaws/scripts/build.py` และไม่มี `python3 .thclaws/` เปล่า; build.py หา checkout ได้จากตำแหน่งใหม่โดยไม่ตั้ง `AGENT_BUILDER_ROOT`
     (รันจาก copy ชั่วคราว); env ของทุก `--serve` site มาจาก helper เดียวที่ตั้ง `THCLAWS_AUTO_MIGRATE=0` (import ตรวจ ไม่ grep); `.gitignore` ครอบ state dirs ใหม่;
     export registration text + deployment hints + docs กล่าวถึง env และข้อห้าม
   - native (มี binary): ตรวจ flavour ด้วย `thclaws bots status --path <tmp>` (ไม่มี subcommand = CLI-only). ทุก flavour: `thclaws agent validate <agent dir>` ผ่าน;
     `--serve` บน copy ของ package v2 (fixture publisher ที่ generate แล้ว) ด้วย `THCLAWS_AUTO_MIGRATE=0` → tree byte-identical, `info.workspace_dir` == copy,
     ไม่มี `bots.json`. เฉพาะ gui build: (i) copy เดิม **ไม่ตั้ง** env → ต้องเกิด `bots.json` (negative control ว่า guard จำเป็นจริง; ทำบน temp copy เท่านั้น);
     (ii) `--serve` บน copy ของ `builder/` → host; `/v1/agent/info` ผ่าน host คืน `workspace_dir` ลงท้าย `.thclaws/bots/main` และ `skills` มี `spec-author`, `pack-author`;
     ไฟล์ที่ git track ต้อง byte-identical หลังปิด host (ยกเว้น runtime state dirs ที่ gate ระบุชื่อชัด). CLI-only build: ประกาศ skip ส่วน (i)(ii) → exit 2
   - ไม่มี binary → offline ผ่านแล้ว exit 2 พร้อมประกาศ
D. **โค้ด**
   - D1 `forge/live_test.py`: helper `daemon_env(...)` รวม env isolation เดิม + `THCLAWS_AUTO_MIGRATE: '0'`; หลัง readiness ตรวจ `info.workspace_dir`
     (resolve ทั้งสองข้าง เพราะ macOS `/tmp` → `/private/tmp`) == run dir และไม่มี `.thclaws/bots.json` มิฉะนั้น fail ข้อความชัด "thClaws converted the run
     directory into a host workspace (DESIGN §4)"; `run_standalone` ใช้ helper เดียวกันเพื่อความสม่ำเสมอ; `check_m8_export.py::daemon_probe` และ M11 ใช้ helper นี้
   - D2 `builder/` → v4: `builder/.thclaws/bots.json`, `builder/.thclaws/settings.json` (host), agent ที่ `builder/.thclaws/bots/main/{AGENTS.md,manifest.json,
     .thclaws/{settings.json,skills/,scripts/build.py}}`; `builder/README.md` คงที่ root (ไม่ใช่ agent file). ใช้ slug `main` เพื่อให้ `thclaws bots unmigrate`
     และตัวอย่างใน manual ใช้ได้. AGENTS.md/skills/README เปลี่ยนคำสั่งเป็น `python3 "$THCLAWS_AGENT_DIR/.thclaws/scripts/build.py" work/<name>/spec.yaml --out out/<name>`
     (ทำงานทั้งมี/ไม่มี host เพราะ env ตั้งเสมอ F4). `build.py`: หา checkout โดย walk ขึ้นจาก `__file__` จนพบ `pyproject.toml` + `forge/` แทน `parents[3]`;
     `.gitignore` เพิ่ม `builder/.thclaws/state/`, `builder/.thclaws/bots/main/.thclaws/state/` (`builder/work/`, `builder/out/` คงเดิม เพราะ shell cwd = root)
   - D3 `scripts/check_m11_builder_agent.py`: path ทั้งหมดชี้ agent dir; `REQUIRED_PHRASES` ใช้ path ใหม่และเพิ่มข้อห้าม `python3 .thclaws/` เปล่า;
     live ค่าเริ่มต้นสตาร์ท `--serve` ด้วย cwd = agent dir + `THCLAWS_WORKSPACE_ROOT=<copy root>` (เท่ากับที่ host spawn child, F3; ทำงานทั้งสอง flavour)
     และส่ง `workspace_dir` = agent dir; flag `--via-host` สตาร์ทที่ root ของ copy แล้วเรียกผ่าน host (gui build; CLI-only → skip ประกาศ); assertion `work/`, `out/`
     ที่ copy root คงเดิม; `scripts/check_m12_baseline.py::native` validate agent dir
   - D4 `forge/atlas_export.py` registration ขั้น 1 และ `forge/generate.py` deployment hint ข้อ "Start the daemon with CWD=package" เพิ่ม `THCLAWS_AUTO_MIGRATE=0`
     และ "never open the package in the desktop app or run thclaws bots migrate on it"; regenerate `out/` ของ fixture ที่ M4/M5/M8 ใช้ (report byte เปลี่ยน; M4 determinism ต้องยังผ่าน)
   - D5 docs: `docs/export.md`, `docs/generator.md`, `builder/README.md`, `docs/local-test-plan.md`, `README.md`
E. **ตรวจใน cloud (CI mode)**: M0–M13 ทุกตัว 0/2 ไม่มี 1; Ruff; `git diff --check`; M4 determinism; ไม่ commit จนผู้ใช้สั่ง
F. **บน Mac (ต้องมี binary/key)**: native M13 (รวม negative control), M11 (validate + live **3 รอบ** เพราะแก้ prompt/skill), M12 native, M8 ด้วย `ATLAS_ROOT` worktree
   ที่ pin, แล้วต่อ step 5 เดิม (M6 live, M7b `--live` ×3; ต้องมี `mcp` ใน venv และ `../AI` ที่ `d95dc28`), บันทึก "M12 live" และ "M13" ลง PLAN/CHANGELOG

ลำดับที่แนะนำเพื่อไม่ให้ step 5 รอทั้ง milestone: ทำ A (เฉพาะย่อหน้า §4/§8) + D1 ก่อนเป็นชุดเล็ก → M6/M7b live รันบน Mac ได้ทันทีและผลยังใช้ได้หลัง M13 เสร็จ
(deploy contract ไม่เปลี่ยน) → ระหว่างนั้นทำ D2–D5. M11 live ต้องรอ D2/D3

## 5. Definition of done ของ M13

- DESIGN §4/§8/§9 บันทึกครบตาม A พร้อม version+revision; PLAN/CHANGELOG อัปเดต
- ทุก `--serve` site ใช้ `daemon_env()` และ guard ตรวจผลจริง; M8/M11 gate ใช้ helper เดียวกัน
- `builder/` เป็น v4 ใน git, `thclaws agent validate` ผ่านที่ agent dir, host เปิดแล้วไฟล์ที่ track ไม่เปลี่ยน, `-p` ที่ root ก้าวเข้า agent ได้ (gui build)
- `check_m13_host_layout.py` exit 0 บน Mac (gui build) และ 2 บน CLI-only/ไม่มี binary โดยประกาศส่วนที่ skip; `check_m11_builder_agent.py` live exit 0 สามรอบติด
  บน `oai/gpt-5.4-mini`; M0–M12 ไม่ถดถอย
- ไม่มี claim ใหม่ใน guarantee matrix และไม่เลื่อน `package_status` ใด

## 6. ต้องยืนยันด้วย binary บน Mac (source อย่างเดียวไม่พอ)

1. desktop/`--serve` เปิด `builder/` v4 ที่ commit ไว้แล้วไม่เขียนทับไฟล์ที่ track (F8 บอกว่าไม่ ถ้าข้อความกล่าวถึง `$THCLAWS_AGENT_DIR`) และระบุชื่อ runtime files ที่ host สร้าง
   (lock, `.thclaws/state/`) เพื่อใส่ใน ignore list ของ gate
2. `/agent/run` ผ่าน host ไปถึง bot จริง และ `work/`, `out/` ลงที่ root ตามที่ F4 สรุป
3. `thclaws agent validate` ยอมรับ agent dir ที่ซ้อนใน `.thclaws/bots/` และ `build.py` ที่ย้ายแล้ว (validate syntax-check `.thclaws/scripts/*.py`)
4. (optional) ติดตั้งมือจาก archive ของ `agent pack` + แก้ `bots.json` แล้ว host โหลดได้ (F5)
5. ผล `scripts/check_m13_host_layout.py` negative control บน gui build เกิด `bots.json` จริงตาม F1

## 7. นอกขอบเขต M13

- adopt host layout สำหรับ deploy กับ Atlas (ต้องรอ thClaws แยก directory ของ identity/files ใน `/agent/run` หรือมี `collect_files` root แยก) และการตรวจ `inputs/incoming` บน host
- `THCLAWS_AGENT_WORKSPACE_ROOT` เป็น hardening ของ deployment (F9)
- M14 ปลด T1, M15 `allowedTools` (รับ F10 ไปด้วย)

## 8. ความเสี่ยง

- แก้ prompt/skill ของ `builder/` → ต้อง 3 รอบ live (ค่า provider); ลดโดยเปลี่ยนเฉพาะบรรทัดคำสั่ง build
- flavour ของ binary: gate ต้องแยก gui/CLI-only และ skip ชัด ไม่ปล่อยให้ host path "ผ่าน" บน CLI-only
- การเปลี่ยน deployment hint ทำให้ report ของ fixture เปลี่ยน byte → ต้อง regenerate และดู M4/M5/M8 ครบ
- หาก Mac พบว่า host เขียนไฟล์ที่ track (ข้อ 6.1) ต้องกลับมาแก้ DESIGN ก่อนปรับ layout ไม่ใช่เพิ่ม ignore เพื่อให้ผ่าน
