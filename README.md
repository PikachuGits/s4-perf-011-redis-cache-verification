# S4-PERF-011 · 会员空间平台 Redis 缓存策略验证

ClawHunt 工单 **#2984** 交付物。本仓库构造一个可运行的会员空间平台缓存验证服务（FastAPI + PostgreSQL + Redis），用真实请求和真实数据库/Redis 实例验证四类缓存点，并输出：

- 缓存点清单：键规则、TTL、失效触发、防护措施
- 缓存开启 / 关闭的 P95、RPS 与数据库负载对比
- 数据修改后读到新值的实测时延
- 缓存穿透与击穿的端到端对照

## 边界声明（先读）

1. 本仓库是**缓存验证工程**，不是会员空间平台完整业务系统；只实现本单需要的字典树、首页聚合、驾驶舱指标、权限点四个演示模块及其写接口。
2. 所有数据均为虚构：固定 UUID、`虚构会员企业`、`虚构商机`，不含客户名称、真实会员企业、手机号、邮箱或个人信息。
3. 依赖 S2-TAX 字典与标签中心的部分以本仓库内的演示字典表实现，属于本单允许的接口桩/mock 范围；真实对接点为 `GET /api/tax/dicts/{dict_code}/tree` 与字典项读接口。
4. 实测 PostgreSQL 为 18.3，SQL 未使用 17/18 专属语法，与项目约定的 16 版本兼容；Redis 7.4.8。
5. 本仓库不代表平台验收或结算；交付后仍需按平台流程验收。

---

## 1. 快速开始

### 1.1 依赖

- Python 3.11+（本次实测 3.12.12）
- PostgreSQL 16+（本次隔离实例 18.3）
- Redis 7+（本次 7.4.8）

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 1.2 准备数据库与 Redis

**方式 A：Docker Compose（推荐给复现者）**

```bash
docker compose -f docker-compose.test.yml up -d
# PostgreSQL 55436，Redis 56380；初始化脚本会创建 clawhunt_2984 与 clawhunt_2984_test
export DATABASE_URL='postgresql+psycopg://clawhunt:clawhunt@127.0.0.1:55436/clawhunt_2984'
export REDIS_URL='redis://127.0.0.1:56380/0'
export ADMIN_TOKEN='dev-admin-token'
```

**方式 B：已有本机 PostgreSQL/Redis**

```bash
createdb -h 127.0.0.1 -p 55432 -U clawhunt clawhunt_2984
createdb -h 127.0.0.1 -p 55432 -U clawhunt clawhunt_2984_test
export DATABASE_URL='postgresql+psycopg://clawhunt@127.0.0.1:55432/clawhunt_2984'
export REDIS_URL='redis://127.0.0.1:56379/0'
export ADMIN_TOKEN='dev-admin-token'
```

### 1.3 初始化压测数据

```bash
# 压测数据集：3 站点、40 用户、24k 商机、10k 合同、20k 回款
.venv/bin/python scripts/seed.py --profile perf --reset
```

单测库由 `tests/conftest.py` 自动建表并在每个用例前写入 test 数据集，无需手工 seed。

### 1.4 跑单测

```bash
# 测试库名必须含 test，防止误连演示/生产库
# 本机实例端口 55432；Docker Compose 暴露的是 55436，按实际端口替换
export TEST_DATABASE_URL='postgresql+psycopg://clawhunt@127.0.0.1:55432/clawhunt_2984_test'
export REDIS_URL='redis://127.0.0.1:56379/15'
./scripts/test.sh
```

实测结果：**47 passed**（`evidence/01-pytest.log`）。

### 1.5 启动缓存开/关两个进程

```bash
DATABASE_URL="$DATABASE_URL" REDIS_URL="$REDIS_URL" ADMIN_TOKEN="$ADMIN_TOKEN" \
  CACHE_ENABLED=1 .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8011 &
DATABASE_URL="$DATABASE_URL" REDIS_URL="$REDIS_URL" ADMIN_TOKEN="$ADMIN_TOKEN" \
  CACHE_ENABLED=0 .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8012 &
```

- `8011`：缓存开启（缓存命中时不再访问 DB）
- `8012`：缓存关闭（所有读请求绕过 Redis，直接 DB；响应头 `X-Cache-State: bypass`）

### 1.6 复现压测与验证

```bash
# 冒烟：健康检查、四点键规则/TTL、四接口响应头、Bloom 拦截
./scripts/smoke.sh http://127.0.0.1:8011 | tee evidence/08-smoke.log

# 常态：4 并发、8,000 请求
.venv/bin/python scripts/loadtest.py --base-url http://127.0.0.1:8011 \
  --label cache-on --mode cache-on --requests 8000 --concurrency 4 \
  --out evidence/03-loadtest-cache-on.json
.venv/bin/python scripts/loadtest.py --base-url http://127.0.0.1:8012 \
  --label cache-off --mode cache-off --requests 8000 --concurrency 4 \
  --out evidence/04-loadtest-cache-off.json

# 历史单轮峰值样本：24 并发、4,000 请求（正式峰值结论以 8 轮汇总为准）
.venv/bin/python scripts/loadtest.py --base-url http://127.0.0.1:8011 \
  --label peak-on --mode cache-on --requests 4000 --concurrency 24 \
  --out evidence/09-peak-cache-on.json
.venv/bin/python scripts/loadtest.py --base-url http://127.0.0.1:8012 \
  --label peak-off --mode cache-off --requests 4000 --concurrency 24 \
  --out evidence/10-peak-cache-off.json

# 失效时延：4 个缓存点 ×30 次
.venv/bin/python scripts/measure_invalidation.py --base-url http://127.0.0.1:8011 \
  --iterations 30 --out-prefix evidence/06-invalidation

# 穿透/击穿对照
.venv/bin/python scripts/verify_protections.py \
  --on-url http://127.0.0.1:8011 --off-url http://127.0.0.1:8012 \
  --output evidence/07-protections.json

# 8 轮峰值汇总（逐轮文件按 s4-perf-011-repeat-<n>-cache-on/off.json 放置）
.venv/bin/python scripts/summarize_peak_repeats.py --input-dir /tmp \
  --output evidence/11-peak-multirun-summary.json

# 重新生成清单与报告
.venv/bin/python scripts/export_cache_points.py
.venv/bin/python scripts/build_report.py
```

> 注意：`scripts/loadtest.py` 的数据库统计默认连接 `postgresql://clawhunt@127.0.0.1:55432/clawhunt_2984`；使用 Docker 时加 `--db-url 'postgresql://clawhunt:clawhunt@127.0.0.1:55436/clawhunt_2984'`。

---

## 2. 四个缓存点

| 缓存点 | 键规则 | TTL | 失效 |
|---|---|---:|---|
| 字典树 | `ms:tax:dict:tree:{org_id}:{dict_code}` | 900s ±10% | 字典项写后 DEL 对应字典键 |
| 首页聚合 | `ms:site:home:{org_id}:{site_id}:{date}` | 300s ±10% | Banner/活动/会员变更后按站点 DEL 当日键 |
| 驾驶舱指标 | `ms:dashboard:metrics:{org_id}:{site_id}:{period}` | 600s ±10% | 商机/合同/回款变更后按站点+账期 DEL |
| 权限点 | `ms:iam:perms:{org_id}:{user_id}` | 1800s ±5% | 用户角色/角色权限变更后 DEL 用户键 |

辅助键：

| 用途 | 键规则 | TTL |
|---|---|---:|
| 字典项负缓存 | `ms:tax:dict:item:neg:{org_id}:{dict_code}:{item_code}` | 30s |
| 字典项 Bloom | `ms:tax:bloom:{org_id}:{dict_code}` | 3600s |
| single-flight 锁 | `ms:lock:{cache_key}` | 5000ms |

完整键空间规则与 TTL 依据见 [`docs/cache-points.md`](docs/cache-points.md)；机器可读版本 `evidence/02-cache-points.json`，也由 `GET /api/admin/cache/points` 返回。

---

## 3. 实测结果

### 3.1 缓存开 / 关（4 并发、8,000 请求）

| 指标 | 缓存开启 | 缓存关闭 | 变化 |
|---|---:|---:|---:|
| P95 | **3.933 ms** | **8.591 ms** | **-54.22%** |
| P99 | 4.784 ms | 18.469 ms | -74.10% |
| RPS | 1,504.61 | 615.72 | +144.37% |
| 数据库查询总数 | 0 | 25,207 | -100% |
| 数据库查询/请求 | 0 | 3.151 | — |
| 数据库执行时间/请求 | 0 ms | 1.976 ms | — |
| PG 共享缓冲区命中增量 | 967 | 1,401,364 | -99.93% |
| PG 返回元组增量 | 1,287 | 23,878,723 | -99.99% |

分接口 P95：首页 3.689 vs 8.085 ms；字典树 4.35 vs 8.217 ms；驾驶舱 3.681 vs 9.625 ms；权限点 3.566 vs 7.027 ms。

### 3.2 修改后读到新值（每点 30 次）

| 缓存点 | 端到端 P95 | 服务端 commit→DEL P95 |
|---|---:|---:|
| 字典树 | 4.001 ms | 0.177 ms |
| 首页聚合 | 3.673 ms | 0.164 ms |
| 驾驶舱指标 | 4.756 ms | 0.162 ms |
| 权限点 | 2.406 ms | 0.171 ms |

120/120 次修改均读到新值；端到端数字含 2ms 轮询量化下限。

### 3.3 穿透 / 击穿

- 不存在字典项：缓存开启 300 次请求，Bloom 拦截 300 次，DB 查询 0；缓存关闭 60 次请求，DB 查询 60。
- 不存在字典的负缓存：首次 DB 1 次，第二次 DB 0，TTL 30 秒。
- 热点驾驶舱键删除后 100 并发：缓存开启只回源 1 次（3 条查询），关闭时 300 条查询。

### 3.4 峰值敏感性

正式峰值结论基于 8 轮复测，每轮 24 并发、4,000 请求，使用不同 request seed，并轮换缓存开/关启动顺序。缓存开启/关闭的 P95 中位数为 121.16 / 122.08 ms，轮间范围分别为 117.265–132.886 / 85.590–147.696 ms。逐轮配对 P95 差值（开启相对关闭）中位数为 -1.58%，范围 -20.50%–+40.82%；开启先跑的 5 轮中位数 +11.55%，关闭先跑的 3 轮中位数 -5.71%。P95 对启动顺序敏感，不能据此认定缓存开启造成稳定尾延迟回归。

较稳定的观察是 P50 中位数 27.32 vs 39.644 ms（缓存开启约降低 31%）、RPS 中位数 577.43 vs 490.84（约提升 18%），数据库查询为 0 vs 12,568–12,656 次/轮。此前单轮配对差值 +9.12%（仓库 09/10）与 +34.48%（独立复测）仅作历史观测，均不作为峰值 P95 结论。8 轮汇总见 `evidence/11-peak-multirun-summary.json`；`runs` 数组含逐轮统计值、seed 与启动顺序，可由 `scripts/summarize_peak_repeats.py` 重算。详细分析见 `docs/verification-report.md` 第 3.3、6 节。

---

## 4. 验收标准对应

| 验收标准 | 交付位置 | 结果 |
|---|---|---|
| 1. 缓存点清单写明键规则与过期时间 | `docs/cache-points.md`、`evidence/02-cache-points.json`、`/api/admin/cache/points` | 4 个业务缓存点 + 3 类辅助键（负缓存/Bloom/单飞锁），键规则/TTL/失效/防护齐全 |
| 2. 开启与关闭缓存的 P95 与数据库负载对比 | `docs/verification-report.md` 第 3 节、`evidence/03`、`04`、`05-loadtest-comparison.json`、峰值单轮 `09`/`10` 与 8 轮汇总 `11-peak-multirun-summary.json` | 常态 P95 3.933 vs 8.591 ms；DB 查询 0 vs 25,207；峰值 P95 中位数、范围及启动顺序敏感性已披露 |
| 3. 数据修改后读取到新值的时间有实测记录 | `docs/verification-report.md` 第 4 节、`evidence/06-invalidation.csv`、`evidence/06-invalidation.json` | 4 点各 30 次，120/120 命中新值；服务端 P95 ≤0.177 ms |

---

## 5. 目录结构

```
app/
  main.py            # FastAPI 应用、请求级指标中间件
  config.py          # 环境变量配置
  db.py              # SQLAlchemy 引擎与查询计数
  models.py          # 13 张演示表（统一 7 字段、金额分）
  services.py        # 四个读路径的 DB 回源函数与写函数
  seed.py            # perf/test 数据集
  cache/
    registry.py      # 缓存点注册表（键规则/TTL/失效/防护唯一来源）
    client.py        # cache-aside、single-flight、负缓存、失效统计
    bloom.py         # Redis 位图 Bloom
  routers/           # tax / home / dashboard / iam / admin 路由
scripts/
  seed.py            # 初始化数据集
  export_cache_points.py
  loadtest.py        # 缓存开/关对比压测
  measure_invalidation.py
  verify_protections.py
  summarize_peak_repeats.py  # 汇总 8 轮峰值逐轮 JSON
  build_report.py    # 从 evidence 回填报告
  test.sh            # 单测入口（测试库+独立 Redis DB）
  smoke.sh           # 运行中服务的冒烟检查
tests/               # 47 项单测/集成测试
docs/
  cache-points.md
  verification-report.md
evidence/            # 原始结果与报告数据
docker-compose.test.yml  # 可复现的 PostgreSQL 16 + Redis 7 测试依赖
docker/initdb/       # 初始化两个数据库
```

---

## 6. 关键实现说明

- **cache-aside + single-flight**：未命中时 `SET NX PX 5000` 抢锁，抢到后二次检查缓存再回源；未抢到的请求 10ms 轮询等待，约 1 秒超时后回源兜底。锁释放用 Lua 比对 token，避免误删他人锁。
- **TTL 抖动**：按缓存点配置 ±5%~±10%，避免同一时刻批量过期。
- **负缓存**：业务 loader 返回 `None` 时写入 30 秒空值哨兵；字典项查询另有 Bloom 前置判断。
- **失效**：所有写接口先 `commit`，再记录 commit 时间并对精确键 `DEL`；响应头返回 `X-DB-Commit-At`、`X-Invalidation-Ms`、`X-Invalidation-Keys`，供失效时延实测。
- **可观测性**：每个响应带 `X-Cache-State`、`X-Cache-Point`、`X-DB-Queries`、`X-DB-Time-Ms`、`X-Response-Time-Ms`；`/api/admin/cache/stats` 给出命中率、回源次数、single-flight 等待/超时、失效记录。
- **管理鉴权**：所有 `/api/admin/*` 需要 `X-Admin-Token`，token 只从环境变量 `ADMIN_TOKEN` 读取；仓库中没有真实密钥。
- **键安全**：键组件只接受 UUID/受控枚举，非法值返回 422，不拼接用户输入原文；键中无姓名、手机号、邮箱。

---

## 7. 已知边界与改进方向

1. `commit` 与 `DEL` 不是原子操作；进程崩溃或 Redis 不可用时旧值会保留到 TTL 到期。生产建议 outbox/CDC + 重试失效消费者，并对失效失败告警。
2. 峰值 P95 受测试启动顺序影响：8 轮配对差值跨越 -20.50%–+40.82%，分组中位数方向不同，暂不能归因于缓存回归。复测应保留逐轮 seed/JSON、平衡或随机化开关顺序，并尽量隔离压测客户端与服务端；受控复测稳定出现回归后，再评估 `redis.asyncio`、多 worker 或 L1 + Pub/Sub 失效（需同时解决可靠失效）。
3. 未实现多实例本地 L1，因此未测量跨实例 L1 失效；当前所有实例共享 Redis，DEL 后立即可见。
4. Bloom 在字典项变更后整体重建；生产建议预热或可增量维护的过滤器。
5. 压测客户端与服务端同机，峰值绝对数值受本机 CPU/调度影响；4 并发常态数据作为本单容量基线，不作为生产 SLA。
6. 演示 Redis 未启用 AUTH/TLS，仅监听 127.0.0.1；生产必须启用 ACL/TLS 并限制管理接口来源。

## 8. 证据索引

| 文件 | 内容 |
|---|---|
| `evidence/01-pytest.log` | 47 项测试通过 |
| `evidence/02-cache-points.json` | 缓存点注册表快照 |
| `evidence/03-loadtest-cache-on.json` | 缓存开启原始压测数据（含逐请求样本、PG 前后快照） |
| `evidence/04-loadtest-cache-off.json` | 缓存关闭原始压测数据 |
| `evidence/05-loadtest-comparison.json` | 开/关与峰值派生对比 |
| `evidence/06-invalidation.json` / `.csv` | 4 点 ×30 次失效时延 |
| `evidence/07-protections.json` | 穿透/负缓存/击穿对照 |
| `evidence/08-smoke.log` | 运行中服务的冒烟输出（四点键规则/TTL、响应头、Bloom 拦截） |
| `evidence/09-peak-cache-on.json` / `10-peak-cache-off.json` | 24 并发峰值历史单轮原始数据 |
| `evidence/11-peak-multirun-summary.json` | 8 轮峰值汇总，含逐轮统计值、seed 与启动顺序；逐请求样本未纳入 |

> 数据全部为虚构演示数据；本仓库不包含客户名称、真实会员企业或个人信息。
