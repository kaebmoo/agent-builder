# ประเด็นสำหรับวางแผน: ใช้ layout host + bots ของ thClaws 0.138.0

> สถานะ (2026-10-29): แผนนี้ทำแล้ว — M13 (scoped adoption: `builder/` เป็น host workspace, harness ตั้ง `THCLAWS_AUTO_MIGRATE=0` และตรวจ layout จริง,
> package ที่ generate คง single-agent) และ M16 (host deployment ของ package `prompt_json`/`assistant_json` ผ่าน `forge export --layout host`).
> ข้อเท็จจริงและการตัดสินใจที่ใช้จริงอยู่ที่ DESIGN §4 "Host + bots layout" และ "Host deployment ของ package prompt/assistant" กับ `docs/PLAN.md`;
> เนื้อหาข้างล่างเป็นบันทึกการวางแผน ณ 2026-10-27 และไม่อัปเดตตามอีก

ที่มา: ผลจากการรัน `docs/local-test-plan.md` บน Mac เมื่อ 2026-10-27 · checkout `0837eef` · thClaws `0.138.0` revision `d452ece` ·
model `oai/gpt-5.4-mini` ผ่าน MATCHA (`OPENAI_COMPAT_*`) เอกสารนี้เป็นข้อมูลสำหรับวางแผนเท่านั้น ยังไม่มีการแก้ gate, harness หรือ DESIGN

## ข้อจำกัดของ session บน cloud
- ไม่มี provider key จึงรัน live gate ไม่ได้ การยืนยันผลหลังแก้ต้องให้ผู้ใช้รันซ้ำบน Mac (`docs/local-test-plan.md`)
- evidence ดิบ (log และ `report.json` ของทุกรอบ) อยู่ในเครื่องผู้ใช้ ไม่อยู่ใน repo เพราะ `out/` ถูก gitignore ขอจากผู้ใช้ได้
  ข้อเท็จจริงสำคัญสรุปไว้ในเอกสารนี้แล้ว
- source thClaws ที่ `d452ece` อยู่บน GitHub `kaebmoo/thClaws` branch `baseline-0.138.0-d452ece` (M12 เคย build จาก branch นั้นบน Linux แบบ
  CLI-only ด้วย placeholder frontend) แต่ auto-migrate อาจไม่ทำงานใน build/container นั้น (ข้อ 2: โค้ดอยู่ใต้ feature `gui` และปิดตัวเองใน
  container) จะจำลองได้ด้วย `THCLAWS_AUTO_MIGRATE=1` หรือ `thclaws bots migrate` — ยังไม่ได้ตรวจว่า build แบบนั้นมี feature/คำสั่งนี้

## 1. ปัญหา
thClaws 0.138.0 `--serve` (และ desktop) อัปเกรดโฟลเดอร์ agent แบบ v2 เป็น workspace แบบ host ในที่เดิมตอนเปิด: ย้าย `AGENTS.md`,
`CLAUDE.md`, `manifest.json`, `.thclaws/*` ไปไว้ `.thclaws/bots/main/` (ไฟล์อื่นอยู่ที่ root) harness ของเรายังสมมติ v2 (package อยู่ที่
root และเป็น `workspace_dir`) agent ที่รันผ่าน `/agent/run` จึงไม่เห็น `AGENTS.md`, skills และ scripts M12 บันทึกใน DESIGN §4 แค่ว่า
`cloud/pack.rs` strip `.thclaws/bots/` เพิ่ม (`docs/DESIGN.md:351`) ไม่ได้บันทึก layout migration cloud ไม่เจอเพราะน่าจะเป็น container
ที่ migrate ปิดตัวเอง (สันนิษฐานจาก source ไม่ได้ตรวจใน cloud) พฤติกรรมจึงต่างกันตาม environment

## 2. ข้อเท็จจริงจาก source `d452ece` (ตรวจแล้ว)
- **Layout** v2: root คือ agent · host (`workspaceVersion` 4): ย้ายเฉพาะ `.thclaws`, `AGENTS.md`, `CLAUDE.md`, `manifest.json` ไป
  `.thclaws/bots/main/` (`crates/core/src/bots/migrate.rs` `AGENT_ENTRIES`) ไฟล์อื่นอยู่ที่ root ใช้ร่วมกัน; host เขียน `.thclaws/bots.json`
  และ `.thclaws/settings.json` ของตัวเอง
- **Trigger** `--serve` หรือ desktop เปิดโฟลเดอร์ที่ไม่มี `.thclaws/bots.json` แต่มี `AGENTS.md`/`manifest.json`/`.thclaws/settings.json`/
  `.thclaws/state/` (`crates/core/src/bin/app.rs:1041`, โค้ดอยู่ใต้ feature `gui`) ไม่ทำกับ `-p`, `--cli`, `--workflow`
- **ปิด** `THCLAWS_AUTO_MIGRATE=0` (ทุกที่), `THCLAWS_SUPERVISED=1`; ใน container (`THCLAWS_INSIDE_DOCKER=1`) ไม่ทำเว้นแต่
  `THCLAWS_AUTO_MIGRATE=1` (`auto_migrate_allowed`) · CLI: `thclaws bots {status,add,remove,migrate,fix-paths,unnest,unmigrate}`;
  `bots add` = นำ "Agent Template" เข้า workspace เป็น `.thclaws/bots/<slug>/` แล้ว host โหลดตอนสตาร์ทครั้งถัดไป
- **`/agent/run`** โหลด skills/settings/policy จาก `<workspace_dir>/.thclaws/` ไม่ใช่ cwd ของ daemon (`agent_runtime.rs:10-11`, `:167-171`);
  `workspace_dir` ต้อง absolute และอยู่ใต้ allowed root (`:300-321`); คำสั่ง `AGENTS.md`/`CLAUDE.md` หาโดยไล่ขึ้นจากโฟลเดอร์เริ่มต้น
  (`context.rs:153-162`)
- **host mode** สตาร์ท 2 process: host (port ที่สั่ง) กับ bot `main` (port สุ่ม); `/v1/agent/info.workspace_dir` รายงาน `.thclaws/bots/main`;
  MCP tool ยังใช้ได้แม้ `workspace_dir` ไม่มี `.thclaws/mcp.json` (มาจากฝั่ง host/bot) ส่วน `AGENTS.md`/skills/scripts อ่านจาก `workspace_dir`
- **Atlas (pin `daa0f49`)** เก็บ `workspace_dir` ต่อ workspace (`atlas/db.py:239`) และส่งไปกับ request ถึง worker
  (`atlas/jobs.py:678,910` → `atlas/thclaws_client.py`)

## 3. ผลทดลองบน Mac (oai/gpt-5.4-mini)
| gate | ใช้ `--serve` | คำสั่งตามแผน (ไม่ตั้งอะไร) | `THCLAWS_AUTO_MIGRATE=0` (ผ่าน launcher นอก repo, ข้อ 10) |
|---|---|---|---|
| M9 live + `--write-boundary` | ไม่ (`run_standalone`) | ผ่าน exit 0 / 0 | ไม่ต้อง |
| M7b live | ใช่ (`run_daemon`) | **fail 3/3** (ครั้งแรกทั้ง 4 case; ครั้งหลัง `normal` ผ่าน อีก 3 case `expect: refusal` fail) | **ผ่าน 4/4 ครั้งติดกัน** `package_status=candidate` |
| M11 live | ใช่ | fail (migrate ย้ายไฟล์) | ยังไม่ผ่านเลย 4 รอบ (ข้อ 4) |
| M6 live / M7a | M6 ใช่ | ตรวจไม่ได้ (exit 2) | ตรวจไม่ได้: venv ไม่มี `mcp`; `../AI` อยู่ที่ `aae9456` แต่ pack pin `d95dc28…`; fixture ฮาร์ดโค้ด `../AI` |
| M8 | daemon_probe ใช่ | ผ่านเมื่อ `ATLAS_ROOT` ชี้ worktree ที่ pin; ค่า default `../atlas-control-plane` (`375ac85`) fail pin check แม้ใน CI mode | – |

กลไกของ M7b: refusal schema และคำสั่ง "ตอบเป็น JSON ตาม schema" ถูกสร้างลง `AGENTS.md` (`templates/AGENTS.md.j2:80-90`, `:55`) ซึ่งหายไปหลัง
migrate case `normal` รอดได้เพราะแนวทางผ่าน MCP initialize (เปราะ: fail ครั้งเดียวโดยไม่ทราบสาเหตุ) · M8 daemon_probe น่าจะถูก migrate ด้วย
แต่ผ่านเพราะตรวจแค่ MCP inventory (สันนิษฐาน)

## 4. M11 live — ผลทุกรอบ
| รอบ | ผล | ไปถึงไหน |
|---|---|---|
| 0 (ไม่ตั้ง) | exit 1, 23 วินาที | agent เห็นแค่ `README.md` และ `.thclaws/{bots.json,bots/,settings.json,state/}`; skill `spec-author` "not found (available: extract-and-save, gui-shell)"; `build.py` ไม่พบ; spec ที่เขียนผิด schema (`ScannerError` บรรทัด 70 `network: allowlist: Google Weather API only`) |
| A (`=0`) | exit 1, 11 วินาที | พบ skill; `Ls packs` ไม่เจอ (ENOENT) → หยุดถามหา builder root หรือให้ยืนยันว่าร่างเป็น pack ที่ missing; ไม่เขียน spec |
| 1 (`=0`) | exit 1 `TimeoutError` 900 วินาที | `events: 0` (events หายทั้งหมดเมื่อ timeout) |
| 2 (`=0`) | ผู้ใช้ยกเลิก | ค้างเงียบ ~14 นาที: เขียนแค่ `todos.md`, CPU รวม ~0.3 วินาที, TCP ESTABLISHED 1 ตัว (รอ provider) |
| U1 (`=0`, ผู้ใช้รันเอง) | exit 1 | อ่าน skill → `Ls packs` ENOENT แต่เดินต่อ → เขียน `spec.yaml` 2,975 ไบต์ (ไม่มี `confirmed_by`) → ขอให้ผู้ใช้ยืนยัน `permissions`/`env`/`capabilities` แล้วหยุด |

ทุกรอบ gate ล้มก่อนเรียก `build.py` (assertion "the builder never ran build.py through Bash") ยังไม่เคยมีรอบใดไปถึง forge/audit/รายงานสถานะ

## 5. ปัจจัยที่ทำให้ M11 ไม่ถึง `build.py` (นอกเหนือจาก migration)
1. **workspace ของ live ไม่มี `packs/`** คัดลอกจาก `builder/` อย่างเดียว แต่ `builder/AGENTS.md:35-36` สั่งให้ `ls packs` "through the builder checkout
   (path printed by build.py, or `AGENT_BUILDER_ROOT`)"; env นี้ตั้งใน daemon ที่ `scripts/check_m11_builder_agent.py:204` แต่ model ไม่เคยอ่าน
2. **กฎยืนยันขัดกับ request** `builder/AGENTS.md:27-28`, `:41`, `:45` และ `builder/.thclaws/skills/spec-author/SKILL.md:37-50` ให้แสดงบล็อกแล้ว
   รอยืนยัน ห้ามเติม `confirmed_by` โดยไม่มีการยืนยันชัดเจน แต่ `fixtures/builder/weather-request.md` ยืนยันล่วงหน้าในข้อความเดียว และ gate เป็น
   `/agent/run` turn เดียว (รอบ U1 หยุดตรงนี้)
3. **request ข้อ 4** บอกให้ประกาศ pack ตามชื่อแม้ยังไม่มี แต่รอบ A ก็ยังหยุดถาม
4. **ความแปรปรวนและ provider** 4 รอบได้ 4 พฤติกรรม; มี stall/timeout ของ provider; `request()` ใน `forge/live_test.py` คืน events เมื่อจบเท่านั้น
   จึงเสียหลักฐานทั้งหมดเมื่อ timeout; `--timeout 900` นานเกินไปสำหรับ stream ที่เงียบ
5. **model** ผลทั้งหมดเป็นของ `oai/gpt-5.4-mini` (gate ตั้ง `gpt-5.4` ไว้ แต่ MATCHA ไม่มี)

กติกาโปรเจกต์: แก้ prompt/skill ของ `builder/` ต้องรัน live ซ้ำอย่างน้อย 3 รอบก่อนอ้างว่าแก้แล้ว

## 6. จุดในระบบเราที่เกี่ยวข้อง
- harness ที่สตาร์ท `--serve`: `forge/live_test.py:233` (`run_daemon`: M6 live, M7b `--live`; ส่ง `workspace_dir` ที่ `:254`),
  `scripts/check_m11_builder_agent.py:214` (`workspace_dir` ที่ `:230`), `scripts/check_m8_export.py:323`
- deploy: `forge/atlas_export.py:169` ("Unpack … start thclaws --serve there"), `docs/export.md`, DESIGN §8 (`workspace_dir`, หนึ่ง daemon URL ต่อหนึ่ง worker)
- `builder/`: README บอกให้เปิดด้วย thClaws GUI/CLI → desktop จะ migrate โฟลเดอร์ที่ git track อยู่ในที่เดิม (จาก source ไม่ได้ลองเปิด GUI);
  พาธ `.thclaws/scripts/build.py` ฝังใน AGENTS.md/README/skills
- generator/package: ไม่มีโค้ดที่รู้จัก bots layout เลย; `agent pack` strip `.thclaws/bots/` ออก package จึงน่าจะยังเป็น agent folder
- `scripts/check_m8_export.py:278` เลือก `../atlas-control-plane` เองเมื่อไม่ตั้ง `ATLAS_ROOT` และเทียบกับ `ATLAS_REF` (`forge/atlas_export.py:32`)
- `docs/local-test-plan.md`: ยังไม่พูดถึง `ATLAS_ROOT` ที่ pin, `mcp` + `../AI` ที่ `d95dc28` และ `THCLAWS_AUTO_MIGRATE`

## 7. คำถามเปิดที่แผนต้องตอบ
1. "migrate ไปกับ thClaws" ครอบคลุมแค่ไหน: harness / deploy contract กับ Atlas / `builder/` เป็น Agent Template / output ของ generator
2. `workspace_dir` ที่ถูกต้องบน host คือ root หรือ `bots/<slug>`; host proxy `/agent/run` ไป bot หรือไม่ (อ่าน `crates/core/src/bots/`, `api_v1/agent.rs`)
3. `bots add` รับ Agent Template แบบไหน รับ archive จาก `thclaws agent pack` ได้ไหม (ถ้าได้ = ขั้น deploy มาตรฐานใหม่)
4. หลาย agent ต่อ host ขัดกับ DESIGN §8 หรือไม่; Atlas ต้องเปลี่ยนโค้ดหรือแค่ค่าที่ลงทะเบียน
5. root ที่ไฟล์ใช้ร่วมกันกระทบ `write_scope`, `collect_files`, `inputs/incoming/...` และ guarantee matrix อย่างไร (ยังไม่ได้ตรวจ)
6. ตั้ง `THCLAWS_AUTO_MIGRATE` ให้ชัดทุกที่ เพราะ default ขึ้นกับการตรวจ container (Mac กับ container ได้ layout ต่างกัน)
7. ออกแบบ gate M11: turn เดียวหรือหลาย turn, `packs/` อยู่ตรงไหนสำหรับ model, ความหมายของการยืนยันล่วงหน้า, เก็บ events บางส่วนเมื่อ timeout,
   ตรวจจับ stall, เลือก model
8. gate ใหม่และเลข milestone (PLAN เสนอ M13 ปลด T1 และ M14 `allowedTools` ไว้แล้ว)

รายการงานที่น่าจะเกิดจากแผน (ให้แผนตัดสิน ไม่ใช่ข้อสรุป): DESIGN §4 + §8 บันทึก layout migration · นโยบาย `THCLAWS_AUTO_MIGRATE` ใน harness หรือย้ายไปใช้
host layout · ข้อความ/เอกสาร deploy (`forge/atlas_export.py:169`, `docs/export.md`) · การติดตั้งและใช้ `builder/` · ออกแบบ gate M11 ใหม่ ·
อัปเดต `docs/local-test-plan.md` · gate ใหม่ตามกติกาหนึ่ง milestone หนึ่ง check script

## 8. ทางลัดชั่วคราว (ไม่ได้ใช้ความสามารถใหม่)
ใส่ `THCLAWS_AUTO_MIGRATE=0` ใน env ของ 3 จุดที่สตาร์ท `--serve` (ข้อ 6) ยืนยันแล้วว่าแก้ M7b ได้ (4/4); ไม่พอสำหรับ M11 (ข้อ 5);
ต้องแก้ DESIGN §4 ก่อนตาม AGENTS.md; ยังไม่ได้แก้โค้ดใด ๆ

## 9. ข้อกำหนดที่ต้องรักษา (AGENTS.md และ docs/local-test-plan.md)
แก้ DESIGN §4 ก่อนแก้โค้ด และระบุ version+revision ที่ตรวจ · ไม่ reimplement validate/pack · generator deterministic · หนึ่ง milestone หนึ่ง
`scripts/check_<m>.py` (exit 0/1/2, skip ประกาศชัด) · ไม่แก้ gate/pin/เกณฑ์ audit เพื่อให้ผ่าน · ไม่ commit key/`.env`/`out/` · ไม่ commit จนกว่าผู้ใช้สั่ง

## 10. สถานะและวิธีทดลอง
```
checkout: 0837eef   thclaws: 0.138.0 revision d452ece   model: oai/gpt-5.4-mini
CI mode:     m0=0 m1=0 m2=0 m3=0 m4=2 m5=2 m6=2 m7a=2 m7b=2 m8=2 m9=2 m10=2 m11=2 m12=2   (m8=1 ถ้าใช้ ../atlas-control-plane@375ac85)
native mode: m0=0 m1=0 m2=0 m3=0 m4=0 m5=0 m6=2 m7a=2 m7b=0 m8=0 m9=2 m10=0 m11=2 m12=0   (m8 ผ่าน ATLAS_ROOT = worktree ที่ pin)
M11 live: exit=1 (ไม่ตั้ง); ที่ =0 ดูข้อ 4   M9 live: exit=0   write-boundary: exit=0
optional: m7a=2 m6=2 m7b-live: คำสั่งตามแผน=1 (3 ครั้ง), =0 ผ่าน launcher=0 (4 ครั้ง)   m8=0 (worktree ที่ pin)
```
ยังไม่ได้บันทึก "M12 live" ลง `docs/PLAN.md`/`CHANGELOG.md` (แผนให้บันทึกเมื่อผ่านครบ)

วิธีทดลอง `=0` โดยไม่แก้ repo (สคริปต์ scratch อยู่ในเครื่องผู้ใช้ ไม่อยู่ใน repo) ครอบ `subprocess.Popen` ใน process เดียวกับ gate เพื่อเติม env
ให้ `thclaws --serve` แล้วรัน gate ตัวจริงผ่าน `runpy`:
```python
import runpy, subprocess, sys
variant, gate, *args = sys.argv[1:]      # เช่น 0 scripts/check_m7b_packs.py --live --model oai/gpt-5.4-mini --provider-key-env OPENAI_COMPAT_API_KEY
real = subprocess.Popen
def popen(argv, **kw):
    if isinstance(argv, list) and '--serve' in argv and variant != 'unset':
        kw['env'] = {**kw['env'], 'THCLAWS_AUTO_MIGRATE': variant}
    return real(argv, **kw)
subprocess.Popen = popen
sys.argv = [gate, *args]
runpy.run_path(gate, run_name='__main__')
```
หลังแก้ harness แล้ว ให้ผู้ใช้รัน `docs/local-test-plan.md` ซ้ำบน Mac M11 เขียนทับ `out/m11-live/report.json` ทุกรอบ ให้คัดลอกเก็บหลังแต่ละรอบ
