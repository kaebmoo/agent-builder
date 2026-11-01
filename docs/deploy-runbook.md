# Deploy runbook: Atlas channel (M18a)

Runbook สำหรับ operator ที่จะเอา package ซึ่ง `forge export` ออกมาแล้วไปวางบน host จริง (ไม่ใช่เครื่อง dev), start daemon ด้วย launcher,
ลงทะเบียนกับ Atlas และรัน workflow หนึ่งครั้ง. ทุกขั้นเป็นงานนอก builder: builder สร้างไฟล์ export, template ของ launcher และ gate
ที่ตรวจแบบ static เท่านั้น. **builder ไม่เคยตั้ง `deployment_verified` เป็น true** (DESIGN §6, §8) ค่านี้ใน `atlas-register.json` เป็น `false` เสมอ
และข้อสุดท้ายของ checklist ข้อ 10 คือ operator บันทึกผลตรวจเองนอก builder.

ฐานข้อเท็จจริง: [export.md](export.md) (ไฟล์ export, ลำดับลงทะเบียน 5 ขั้น), [DESIGN §4](DESIGN.md) "Host + bots layout" ข้อ 1, 13, 14 และ
"Baseline 0.146.0 / 9f69995" ข้อ 4, 5, 9, 11, [DESIGN §8](DESIGN.md) (Atlas registration contract). ไม่มีขั้นใดใน runbook นี้เพิ่ม claim ใน guarantee matrix.

สิ่งที่ runbook นี้ยังไม่ครอบ: layout `host` ของ M16 (ใช้ `daemon_cwd` = host root และ `host_files`; ขั้นที่ต่างกันอยู่ใน export.md "Host layout (M16)"),
T2 package (ต้องมี daemon แยกและ `human_gate`; M20), monitoring/alerting และ secret rotation.

## 1. Prerequisites บน host

| สิ่งที่ต้องมี | ตรวจอย่างไร |
|---|---|
| thClaws **desktop build** ที่ baseline `0.146.0` revision `9f69995` (`cargo build --features gui --bin thclaws` จาก checkout `9f69995`; build CLI-only ไม่มี `--serve` — DESIGN §4 ข้อ 13) | `THCLAWS_DISABLE_KEYCHAIN=1 thclaws --version` ต้องพิมพ์ `thclaws 0.146.0` และ `revision: 9f69995`; `thclaws --help` ต้องมี `--serve` และ `bots`. รุ่นอื่น = หยุด ไม่ใช้ `--allow-unverified-runtime` เพื่อ deploy (M22 เป็นทางเลือกของผู้ใช้ package ไม่ใช่หลักฐาน) |
| user ของ service ชื่อ `thclaws` ที่มี HOME ของตัวเอง | user-level config ของ daemon (`~/.config/thclaws/mcp_allowlist.json`, `secrets.json`) อ่านจาก HOME นี้; ห้ามใช้ HOME ของคนที่มี provider key ใน keychain |
| runtime ของ pack ที่ `deployment.packs` ต้องการ (เช่น `python3` สำหรับ `sql-readonly` และ checkout ที่ `SQL_READONLY_AI_ROOT` ชี้) | ตาม README ของ pack; builder ไม่ติดตั้งให้ |
| Atlas control plane ที่ operator มีสิทธิ์ `admin` และ Atlas ถึง host นี้ทาง network ที่ `worker.base_url` ระบุ | `curl -fsS <atlas>/api/workers` ด้วย token ของ Atlas; firewall เปิดเฉพาะ IP ของ Atlas มาที่ port ของ daemon |
| ไม่มี org policy ที่ปิด external MCP หรือ `allow_serve` (DESIGN §4 M17 ข้อ 11) | ถ้ามี policy MCP ของ pack จะไม่ spawn และ reachability check ข้อ 7 จับได้ |

## 2. บนเครื่อง dev: export และ render launcher

```bash
export THCLAWS_DISABLE_KEYCHAIN=1 PATH="$HOME/Documents/GitHub/thClaws/target/debug:$PATH"
.venv/bin/forge generate fixtures/sql-reader/spec.yaml --out out/sql-reader
.venv/bin/forge audit out/sql-reader --write            # ต้องได้ package_status=draft ขึ้นไป (live/security ตาม audit.md ถ้าต้องการ candidate/shippable)
.venv/bin/forge export out/sql-reader --out out/sql-reader-export \
  --base-url http://<daemon-host>:4317 --workspace-dir /srv/atlas/sql-reader
.venv/bin/python3 scripts/render_launcher.py out/sql-reader-export --out out/sql-reader-deploy \
  --thclaws-bin /usr/local/bin/thclaws            # --env-file ถ้าไม่ใช้ /etc/thclaws/<name>.env
```

ได้จาก export: `atlas-register.json`, `<name>-<version>.tar.gz` + `.sha256` (และ `atlas-workflow.json` เฉพาะ target `atlas-workflow`).
ได้จาก `render_launcher.py` (อ่านเฉพาะ `atlas-register.json`; deterministic; ไม่มีค่า secret):

- `<name>.service` (systemd): `WorkingDirectory` = `deployment.daemon_cwd`, `EnvironmentFile=/etc/thclaws/<name>.env`, `Environment=THCLAWS_AUTO_MIGRATE=0`
  และ `THCLAWS_DISABLE_KEYCHAIN=1` เป็นค่าคงที่, `ExecStart=<bin> --serve --bind <bind> --port <port>` โดย port มาจาก `worker.base_url`
  (ไม่มี port = 80/443 ตาม scheme) และ bind เป็น `127.0.0.1` เมื่อ base_url เป็น loopback มิฉะนั้น `0.0.0.0` (ต้องมี firewall ตามข้อ 1)
- `thclaws.<name>.plist` (launchd LaunchDaemon): launchd ไม่มี EnvironmentFile จึงใช้ `/bin/sh -c 'set -a; . <env-file>; set +a; exec thclaws --serve …'`
  ด้วย `WorkingDirectory`, `UserName` `thclaws`, `KeepAlive`, log ที่ `/var/log/thclaws/<name>.log`
- `<name>.env.example`: ชื่อ env ทุกตัวที่ env file บน host ต้องมี (`THCLAWS_API_TOKEN`, `THCLAWS_MCP_ALLOW_ALL`, ทุกชื่อใน `deployment.env`) เป็นบรรทัด `NAME=` ว่าง
  พร้อม comment เรื่อง provider key ของ `node_template.model`; ไม่มีค่าใด ๆ

ถ้า daemon อยู่หลัง reverse proxy/TLS ให้แก้ `--port` ใน launcher ให้ตรง port ที่ daemon ฟังจริง แล้วให้ proxy รับที่ port ของ `base_url`.
ส่งไปยัง host: archive + `.sha256`, `atlas-register.json`, ไฟล์ launcher ทั้งสาม. ห้ามส่ง `out/<pkg>` (source package) ไปแตกใช้เอง เพราะ archive คือสิ่งที่ตรวจแล้ว (export.md).

## 3. บน host: แตก archive และตรวจ `.sha256`

```bash
sudo install -d -o thclaws -g thclaws -m 0750 /srv/atlas/sql-reader          # = deployment.package_root
cd /path/to/uploaded && sha256sum -c sql-reader-0.1.0.tar.gz.sha256         # macOS: shasum -a 256 -c
sudo -u thclaws tar -xzf sql-reader-0.1.0.tar.gz -C /srv/atlas/sql-reader
sudo -u thclaws python3 -c 'import json;print(json.load(open("/srv/atlas/sql-reader/.thclaws/settings.json"))["workspaceVersion"])'   # ต้องเป็น 2
test ! -e /srv/atlas/sql-reader/.thclaws/bots.json && echo single-agent-ok
```

- tree ที่แตกได้ต่างจาก source package เฉพาะ `manifest.json` และ `.thclaws/settings.json` (block `agent` ถูกตัด) ตาม DESIGN §8; ห้ามรัน `audit.py` กับ tree นี้
- **ห้ามเปิดโฟลเดอร์นี้ด้วย thClaws desktop app และห้ามรัน `thclaws bots migrate`** — desktop build จะแปลงเป็น host workspace ในที่เดิม
  (DESIGN §4 ข้อ 1, 2); ถ้าเกิดขึ้น (`.thclaws/bots.json` โผล่) ให้ทำข้อ 9 rollback แล้วแตกใหม่จาก archive
- ห้ามวาง `AGENTS.md`/`CLAUDE.md` เพิ่มที่ใดใน tree (M17 ข้อ 3) และห้ามแก้ไฟล์ใน package บน host; การแก้ต้องกลับไปที่ spec → generate → audit → export ใหม่

## 4. env ของ daemon (ชื่อจาก export, ค่าจาก secret store ของ operator)

```bash
sudo install -d -m 0750 -o root -g thclaws /etc/thclaws
sudo install -m 0600 -o thclaws -g thclaws sql-reader.env.example /etc/thclaws/sql-reader.env
sudo -u thclaws "${EDITOR:-vi}" /etc/thclaws/sql-reader.env                   # เติมค่า; ไฟล์นี้ไม่เข้า repo และไม่เข้า package
```

| ชื่อ | ที่มา | หมายเหตุ |
|---|---|---|
| `THCLAWS_API_TOKEN` | สร้างใหม่ต่อ daemon (เช่น `openssl rand -base64 32`) | token เดียวกันนี้ส่งให้ Atlas ตอน `POST /api/workers` ข้อ 7; ไม่อยู่ในไฟล์ export ใด ๆ |
| `THCLAWS_MCP_ALLOW_ALL` | `1` เมื่อยอมให้ daemon spawn ทุก server ใน `.thclaws/mcp.json` | ทางเลือก: ลบบรรทัดนี้แล้วเขียน `~/.config/thclaws/mcp_allowlist.json` ของ user `thclaws` ให้มี**ทั้ง command line** (`command` + `args`) ของทุก server ใน `.thclaws/mcp.json` (DESIGN §4 M17 ข้อ 4; entry แบบ command อย่างเดียวใช้กับ project server ไม่ได้) |
| ทุกชื่อใน `deployment.env` (เช่น `SQL_READONLY_DSN`, `SQL_READONLY_AI_ROOT`) | secret store / config ของ operator | ชื่อมาจาก `env:` ของ AgentSpec และ `deployment.hints` ต่อ pack; ค่าไม่เคยอยู่ใน package |
| provider key ของ `node_template.model` | provider ของ operator | `OPENAI_API_KEY` สำหรับ `gpt-*` ผ่าน OpenAI, `OPENAI_COMPAT_API_KEY` + `OPENAI_COMPAT_BASE_URL` สำหรับ `oai/*` (DESIGN §4 M17); daemon ไม่อ่าน keychain เพราะ launcher ตั้ง `THCLAWS_DISABLE_KEYCHAIN=1` |

ค่าคงที่ที่ launcher ตั้งเองและห้ามเอาออก: `THCLAWS_AUTO_MIGRATE=0` (กัน auto-migrate ของ desktop build, DESIGN §4 M13 (b)) และ `THCLAWS_DISABLE_KEYCHAIN=1` (M17 ข้อ 9).
ห้ามตั้ง `THCLAWS_PROJECT_ROOT` (allowedTools fail-open, DESIGN §4 "Tool allowlist") และ `THCLAWS_OFFICE_APPS` (ทับ `officeApps: []` ของ package).
ถ้าเครื่องมี AI Server hub ที่ `127.0.0.1:9000` ให้ตั้ง `LITELLM_BASE_URL` หรือถอด hub มิฉะนั้น `aiserver-rag` จะโผล่ใน inventory และข้อ 7 fail (M17 ข้อ 5).

## 5. ติดตั้งและ start launcher

systemd (Linux):

```bash
sudo install -m 0644 sql-reader.service /etc/systemd/system/sql-reader.service
sudo systemctl daemon-reload && sudo systemctl enable --now sql-reader
sudo systemctl status sql-reader --no-pager && sudo journalctl -u sql-reader -n 50 --no-pager
```

launchd (macOS host):

```bash
sudo install -d -o thclaws -m 0755 /var/log/thclaws
sudo install -m 0644 -o root -g wheel thclaws.sql-reader.plist /Library/LaunchDaemons/thclaws.sql-reader.plist
sudo launchctl bootstrap system /Library/LaunchDaemons/thclaws.sql-reader.plist
sudo launchctl print system/thclaws.sql-reader | head -20 && tail -n 50 /var/log/thclaws/sql-reader.log
```

daemon ต้อง start จาก `deployment.daemon_cwd` (= `deployment.package_root` สำหรับ layout single-agent) เท่านั้น เพราะ `.thclaws/mcp.json`, `.thclaws/prompt/system.md`
และ `allowedTools` ถูกอ่านจาก CWD (DESIGN §4). ถ้า log พิมพ์ `--serve not available` แปลว่า binary เป็น CLI-only build (ข้อ 1).

## 6. ตรวจ daemon ก่อนลงทะเบียน

```bash
. /etc/thclaws/sql-reader.env   # ใน shell ของ operator เพื่อเอา token มาใช้ชั่วคราว; อย่า echo
curl -fsS http://127.0.0.1:4317/healthz
curl -fsS -H "Authorization: Bearer $THCLAWS_API_TOKEN" http://127.0.0.1:4317/v1/agent/info \
  | python3 -c 'import json,sys;i=json.load(sys.stdin);print(i["workspace_dir"]);print(sorted(s["name"] for s in i.get("agent",{}).get("mcp_servers",[])))'
test ! -e /srv/atlas/sql-reader/.thclaws/bots.json && echo still-single-agent
```

ต้องได้: `workspace_dir` เท่ากับ `deployment.package_root` ทุกตัวอักษร (บน macOS ระวัง `/tmp` → `/private/tmp`), รายชื่อ MCP server เท่ากับ `deployment.mcp_servers`
(ไม่มี `gridcraft`/`wordcraft`/`deckcraft`/`aiserver-rag` เกินมา) และไม่มี `.thclaws/bots.json`. ไม่ตรงข้อใด = หยุด ทำข้อ 9 แล้วหาสาเหตุ ไม่ลงทะเบียน.
ไฟล์ใน `package_root` นอก `.thclaws/state/` ต้องยังเท่ากับ archive (`tar -tzf` + เทียบ sha ของไฟล์ที่สงสัย) ยกเว้น `.thclaws/settings.json` ที่ประกาศ `workspaceVersion: 2` ไว้แล้วจึงไม่ถูกเขียนทับ (DESIGN §4 ข้อ 14).

## 7. ลงทะเบียนกับ Atlas

ลำดับบังคับ worker → workspace → workflow (Atlas validate reference ตอนบันทึก, DESIGN §8). ใช้ `ATLAS=https://<atlas>` และ token ที่มี permission `admin`.

```bash
R=atlas-register.json
# 1) worker: payload = .worker + token ของ daemon (upsert ด้วย id หรือ base_url — หนึ่ง URL ต่อหนึ่ง worker, export.md ข้อ 3)
python3 -c 'import json,os;w=json.load(open("'$R'"))["worker"];w["token"]=os.environ["THCLAWS_API_TOKEN"];print(json.dumps(w))' \
  | curl -fsS -H "Authorization: Bearer $ATLAS_TOKEN" -H 'Content-Type: application/json' -d @- "$ATLAS/api/workers"      # POST /api/workers
# 2) workspace: payload = .workspace (workspace_dir = deployment.package_root บน host นี้)
python3 -c 'import json;print(json.dumps(json.load(open("'$R'"))["workspace"]))' \
  | curl -fsS -H "Authorization: Bearer $ATLAS_TOKEN" -H 'Content-Type: application/json' -d @- "$ATLAS/api/workspaces"   # POST /api/workspaces
# 3) reachability: poll ต้องได้ status online และ agent_info.agent.mcp_servers ตรง deployment.mcp_servers
curl -fsS -X POST -H "Authorization: Bearer $ATLAS_TOKEN" "$ATLAS/api/workers/wrk_sql-reader/poll" | tee poll.json
```

- `atlas-worker` (เช่น `sql-reader`): ประกอบ `node_template` + `policy_template` (และ `edge_template` เมื่อมี input `atlas_file_handoff` โดยแทน `__UPSTREAM_NODE_ID__`)
  เป็น workflow ของตนเอง แล้ว `POST /api/workflows`; node เดี่ยวใช้ `{"name": "<id>", "graph": {"start": "<id>", "nodes": [node_template], "edges": []}, "policy": policy_template}`
- `atlas-workflow`: `POST /api/workflows` ด้วย `atlas-workflow.json` ตรง ๆ
- `poll.json` ต้องมี `"status": "online"`; `offline` = Atlas ถึง daemon ไม่ได้ (firewall, bind, port, token) — ย้อนไปข้อ 5–6. `deployment.reachability_checks` ในไฟล์ export คือรายการเดียวกับข้อนี้
  และพิสูจน์แค่ reachability กับ MCP inventory ไม่ใช่ isolation, egress หรือ approval

## 8. รัน workflow หนึ่งครั้ง

```bash
WF=<workflow id จาก POST /api/workflows>
curl -fsS -H "Authorization: Bearer $ATLAS_TOKEN" -H 'Content-Type: application/json' \
  -d '{"workflow_definition_id": "'$WF'", "input": {"query": {"question": "Show invoice totals by vendor for August 2026."}}, "execution_mode": "test"}' \
  "$ATLAS/api/workflow-runs" | tee run.json                                             # POST /api/workflow-runs (202)
RUN=$(python3 -c 'import json;print(json.load(open("run.json"))["run"]["id"])')
curl -fsS -H "Authorization: Bearer $ATLAS_TOKEN" "$ATLAS/api/workflow-runs/$RUN" | tee atlas-run.json   # จน run.state เป็น succeeded
curl -fsS -H "Authorization: Bearer $ATLAS_TOKEN" "$ATLAS/api/workflow-runs/$RUN/artifacts"
```

- `input` คือ `golden_cases[].input` ของ case `normal` ใน `agentspec.json` (key ระดับบนต้องครบตาม `node_template.prompt` มิฉะนั้น Atlas fail node หลังสร้าง run, DESIGN §8)
- `execution_mode: test` ใช้กับ workflow ที่ยังเป็น draft ใน Atlas; การรัน production เป็นการตัดสินใจของ operator หลัง checklist ข้อ 10 ครบ
- ผลที่ต้องได้: `run.state` เป็น `succeeded` และ artifact `query_result` เป็น JSON ที่ตรง output schema ใน `agentspec.json` (Atlas ทำแค่ `json.loads`; ตรวจ schema เองด้วย `python3 -m jsonschema` หรือ `forge` บนเครื่อง dev)
- `failed`/`recovery_required` = ดู log ของ daemon (ข้อ 5) และ `nodes` ใน `atlas-run.json`; สาเหตุที่พบบ่อยคือ provider key, MCP ไม่ spawn (allowlist/policy) หรือ `SQL_READONLY_*` ผิด

## 9. Rollback

ทำเมื่อข้อ 6–8 ไม่ผ่าน, package ถูกแปลงเป็น host workspace, หรือจะถอน deployment:

1. หยุดและปิด launcher: `sudo systemctl disable --now sql-reader` / `sudo launchctl bootout system/thclaws.sql-reader`
2. ถอนจาก Atlas (ref `daa0f49` มี `DELETE /api/workers/<id>` และ `DELETE /api/workspaces/<id>`; ลบ workflow ที่อ้าง worker นี้ก่อน หรือปล่อยไว้แล้ว poll จะเป็น `offline`):
   `curl -fsS -X DELETE -H "Authorization: Bearer $ATLAS_TOKEN" "$ATLAS/api/workspaces/wsp_sql-reader"` แล้ว `…/api/workers/wrk_sql-reader`
3. เก็บ tree เดิมไว้สอบสวน: `sudo mv /srv/atlas/sql-reader /srv/atlas/sql-reader.failed-$(date +%Y%m%d%H%M)` (หรือลบเมื่อไม่ต้องการ); ห้าม `thclaws bots unmigrate` แล้วใช้ต่อ (DESIGN §8)
4. แตกใหม่จาก archive ตามข้อ 3 เมื่อจะ deploy ซ้ำ; revoke `THCLAWS_API_TOKEN` เดิม (สร้างใหม่ใน env file) ถ้ามีโอกาสรั่ว
5. บันทึกใน checklist ว่า rollback เพราะอะไร; `deployment_verified` ยังเป็น `false` ในไฟล์ export (ไม่มีอะไรต้องแก้ใน builder)

เปลี่ยน version ของ package = deploy ใหม่ทั้งชุด (archive ใหม่ + launcher render ใหม่ + register ซ้ำด้วย worker id เดิมหรือ URL เดิม ซึ่ง Atlas upsert ทับ); ไม่ copy ไฟล์ทับใน `package_root`.

## 10. Checklist ของ operator (ติ๊กนอก builder)

คัดลอกรายการนี้ไปเป็น `checklist.md` ในโฟลเดอร์หลักฐาน (ข้อ 11) แล้วติ๊กทีละข้อพร้อมวันที่และชื่อผู้ตรวจ:

- [ ] host ไม่ใช่เครื่อง dev; `thclaws --version` = `0.146.0` / `9f69995` desktop build; user `thclaws` แยก
- [ ] archive ผ่าน `.sha256`; tree ที่แตกมี `workspaceVersion: 2` และไม่มี `.thclaws/bots.json`
- [ ] env file มีทุกชื่อจาก `.env.example` (รวม `THCLAWS_API_TOKEN` และทุกชื่อใน `deployment.env`), mode 0600, ไม่อยู่ใน repo/package; ไม่ตั้ง `THCLAWS_PROJECT_ROOT`/`THCLAWS_OFFICE_APPS`
- [ ] launcher ติดตั้งจากไฟล์ที่ `render_launcher.py` สร้าง (ไม่แก้มือ) และ start จาก `deployment.daemon_cwd` ด้วย `THCLAWS_AUTO_MIGRATE=0`
- [ ] `/v1/agent/info.workspace_dir` = `deployment.package_root`; MCP inventory = `deployment.mcp_servers`; ไม่มี server เกิน
- [ ] firewall/egress: port ของ daemon รับเฉพาะ Atlas; egress ของ host ตาม `deployment.network`/`hosts` (builder รับรองไม่ได้ — DESIGN §6 "network เฉพาะ host" Not guaranteed)
- [ ] Atlas: worker + workspace ลงทะเบียนแล้ว, poll `online`, workflow บันทึกได้
- [ ] workflow run หนึ่งครั้ง `succeeded` และ artifact ตรง output schema
- [ ] ขั้น rollback ข้อ 9 ซ้อมแล้วอย่างน้อยหนึ่งครั้ง (หยุด → ถอน → แตกใหม่) หรือมีผู้รับผิดชอบที่ทำได้
- [ ] `deployment_verified`: operator ยืนยันทุกข้อข้างบนด้วยตนเองและบันทึกไว้นอก builder — **builder ไม่เคยตั้ง `deployment_verified` เป็น true** และ `atlas-register.json` ยังเป็น `false` เสมอ

## 11. หลักฐานสำหรับ gate (`--host-evidence`)

`scripts/check_m18a_deploy_runbook.py` ตรวจ runbook และ template แบบ static แล้วจบที่ exit 2 (`M18a SKIP … real host not available`) เพราะ builder ไม่มี host จริง.
operator ที่ทำข้อ 1–10 บน host จริงแล้ว ให้รวบรวมไฟล์ต่อไปนี้ไว้ในโฟลเดอร์หนึ่ง (เช่น `out/m18a-evidence/<วันที่>/` ซึ่ง gitignored; ห้ามใส่ค่า token หรือ secret ใด ๆ):

| ไฟล์ | ที่มา | gate ตรวจ |
|---|---|---|
| `atlas-register.json` | สำเนาไฟล์ export ที่ใช้ deploy | `deployment.deployment_verified` ยัง `false`; ใช้ `package_root`/`mcp_servers`/`worker.id` เทียบไฟล์อื่น |
| `agent-info.json` | body ของ `GET /v1/agent/info` (ข้อ 6) | `workspace_dir` = `deployment.package_root`; ชื่อ `agent.mcp_servers` = `deployment.mcp_servers` |
| `poll.json` | body ของ `POST /api/workers/<id>/poll` (ข้อ 7) | `worker.status` = `online`, `worker.id` ตรง |
| `atlas-run.json` | body ของ `GET /api/workflow-runs/<id>` (ข้อ 8) | `run.state` = `succeeded` |
| `checklist.md` | checklist ข้อ 10 ที่ติ๊กครบ | ไม่มี `[ ]` เหลือ และมีข้อ `deployment_verified` |

```bash
python3 scripts/check_m18a_deploy_runbook.py --host-evidence out/m18a-evidence/2026-11-xx   # exit 0 เมื่อหลักฐานครบ
```

exit 0 ของ gate แปลว่า operator ส่งหลักฐานตามสัญญานี้และไฟล์สอดคล้องกัน ไม่ใช่ builder ยืนยัน deployment: `deployment_verified` ใน export ยังเป็น `false` และสถานะ package มาจาก build report เท่านั้น.
