# Redis 缓存策略验证报告 · S4-PERF-011

> 生成时间：2026-11-21T14:44:13.207208+00:00；单轮实测数字由 `evidence/` 下原始 JSON/CSV 回填；8 轮峰值结果来自 `evidence/11-peak-multirun-summary.json`，由 `scripts/summarize_peak_repeats.py` 从逐轮 JSON 汇总。
> 8 轮汇总含逐轮统计值、seed 与启动顺序，可独立重算；逐请求样本未纳入仓库。数据全部为虚构演示数据，不包含客户名称、真实会员企业或个人信息。

## 0. 结论摘要

1. **缓存有效性**：常态 4 并发、8,000 次混合读请求下，缓存开启相对关闭，P95 从 8.591 ms 降到 3.933 ms（-54.22%），RPS 从 615.72 提升到 1,504.61（+144.37%）。
2. **数据库负载**：缓存关闭时 8,000 次请求共产生 25,207 次数据库查询（3.151 次/请求、1.976 ms/请求）；缓存开启时为 **0 次**。PostgreSQL 共享缓冲区命中从 1,401,364 降到 967（-99.93%）。
3. **失效时延**：4 个缓存点各 30 次「改数据 → 轮询读」全部在 4.857 ms 内读到新值（P95 最大 4.756 ms）；服务端「commit → DEL 完成」P95 最大 0.177 ms。
4. **穿透防护**：同一不存在字典项的 300 次请求，缓存开启时 Bloom 前置拦截 300 次、数据库查询 0 次；缓存关闭时 60 次请求产生 60 次查询。
5. **击穿防护**：热点驾驶舱键删除后 100 个并发请求，缓存开启只回源 1 次（DB 查询合计 3 次，loads=1，lock_waits=29，lock_timeouts=0）；缓存关闭为 300 次。
6. **峰值敏感性（8 轮汇总）**：24 并发、每轮 4,000 请求，使用不同 request seed 并轮换缓存开/关启动顺序。P50 中位数为 27.32/39.644 ms（缓存开启降低 31.09%），RPS 中位数为 577.43/490.835（提升 17.64%），DB 查询总数为 0 vs 12,568–12,656。P95 中位数为 121.16/122.076 ms；逐轮配对差值中位数 -1.58%，范围 -20.50% 至 +40.82%。差值随启动顺序分组而异，因此 P95 仅作波动观察，不构成稳定回归结论；详见第 3.3、6 节。

## 1. 测试环境与数据

| 项 | 配置 |
|---|---|
| 机器 | macOS (arm64)，单机同时运行服务端与压测客户端 |
| Python | 3.12.12 |
| Web | FastAPI 0.165.0 / Uvicorn 0.38（单 worker） |
| ORM/驱动 | SQLAlchemy 2.1.9 / psycopg 3.3.9 |
| Redis | 7.4.8（独立实例，端口 56379，DB 0） |
| PostgreSQL | 18.3 隔离实例（端口 55432）；本单 SQL 未使用 17/18 专属语法，与客户约定的 16 版本兼容 |
| 数据集 | 3 个站点、40 个演示用户；24,000 条商机、10,000 份合同、20,000 条回款计划、318 个字典项 |
| 请求组合 | 首页 40% / 字典树 25% / 驾驶舱 20% / 权限点 15% |

**压测方法**：缓存开、关各启动一个独立 Uvicorn 进程（8011 / 8012），共享同一 PostgreSQL 与 Redis；关闭缓存的进程完全绕过 Redis（`X-Cache-State: bypass`）。每个进程先发 64 次预热请求覆盖全部热键，再清空应用计数器，发 8,000 次读请求（4 并发），按最近秩法计算 P95/P99。PostgreSQL 侧用 `pg_stat_database` 与 `pg_stat_user_tables` 前后快照相减，取真实缓冲区/元组/扫描增量。

**为什么主场景用 4 并发**：本单对应约 100 家会员企业的私有化管理系统，管理端不是公网高并发场景；4 并发用于观察缓存本身的收益。另用 24 并发做峰值敏感性测试，见 3.3。

## 2. 缓存点与键规则（摘要）

| 缓存点 | 键规则 | TTL | 失效触发 |
|---|---|---:|---|
| 字典树 | `ms:tax:dict:tree:{org_id}:{dict_code}` | 900 秒（±10%） | 字典项写接口提交后 DEL 对应字典键 |
| 首页聚合 | `ms:site:home:{org_id}:{site_id}:{date}` | 300 秒（±10%） | Banner/活动/会员资料变更后按站点 DEL 当日键 |
| 驾驶舱指标 | `ms:dashboard:metrics:{org_id}:{site_id}:{period}` | 600 秒（±10%） | 商机/合同/回款变更后按站点+账期 DEL |
| 权限点 | `ms:iam:perms:{org_id}:{user_id}` | 1800 秒（±5%） | 用户角色或角色权限变更后 DEL 用户键 |

完整清单（含辅助负缓存键、Bloom 位图、single-flight 锁、键组件校验规则）：`docs/cache-points.md`；机器可读版本：`evidence/02-cache-points.json`。

## 3. 开启 vs 关闭缓存：P95 与数据库负载

### 3.1 总体对比（4 并发、8,000 请求）

| 指标 | 缓存开启 | 缓存关闭 | 变化 |
|---|---:|---:|---:|
| 成功率 | 8000/8000 | 8000/8000 | — |
| RPS | 1,504.61 | 615.72 | +144.37% |
| P50 | 2.527 ms | 6.26 ms | -59.63% |
| **P95** | **3.933 ms** | **8.591 ms** | **-54.22%** |
| P99 | 4.784 ms | 18.469 ms | -74.10% |
| 数据库查询总数 | 0 | 25,207 | -100.00% |
| 数据库查询/请求 | 0 | 3.151 | — |
| 数据库执行时间/请求 | 0 ms | 1.976 ms | — |
| PG `blks_hit` 增量 | 967 | 1,401,364 | -99.93% |
| PG `tup_returned` 增量 | 1,287 | 23,878,723 | -99.99% |
| 缓存命中率 | 100.0% | 0%（bypass） | — |

### 3.2 分接口对比

| 接口 | 缓存开启 P95 | 缓存关闭 P95 | P95 变化 | 查询/请求（开） | 查询/请求（关） |
|---|---:|---:|---:|---:|---:|
| 首页聚合 | 3.689 ms | 8.085 ms | -54.4% | 0 | 4 |
| 字典树 | 4.35 ms | 8.217 ms | -47.1% | 0 | 2 |
| 驾驶舱指标 | 3.681 ms | 9.625 ms | -61.8% | 0 | 3 |
| 权限点 | 3.566 ms | 7.027 ms | -49.2% | 0 | 3 |

### 3.3 峰值敏感性（24 并发、每轮 4,000 请求，共 8 轮）

**方法与统计口径**：8 轮使用不同 request seed，并轮换缓存开启/关闭的先后顺序；缓存开启先跑 5 轮，缓存关闭先跑 3 轮。P95 轮间范围分别取缓存开启/关闭各 8 个测量值的最小值至最大值；配对差值按每轮 `(cache_on_p95 / cache_off_p95 - 1) × 100%` 计算后再汇总。

| 指标 | 缓存开启 | 缓存关闭 | 观察 |
|---|---:|---:|---|
| RPS 中位数 | 577.43 | 490.835 | 提升 17.64% |
| P50 中位数 | 27.32 ms | 39.644 ms | 降低 31.09% |
| P95 中位数（轮间范围） | 121.16 ms（117.265–132.886） | 122.076 ms（85.590–147.696） | 配对差值中位数 -1.58%；范围 -20.50% 至 +40.82% |
| DB 查询总数/轮 | 0 | 12,568–12,656 | 缓存开启各轮均为 0 |

**解读**：P95 配对差值跨越正负区间，且按启动顺序分组后方向不同：缓存开启先跑 5 轮的差值中位数为 +11.55%，缓存关闭先跑 3 轮为 -5.71%。缓存关闭侧 P95 范围（85.590–147.696 ms）明显宽于缓存开启侧（117.265–132.886 ms）。这些观测提示启动顺序/运行时状态影响差值，但不足以判定因果；不能据此认定缓存开启造成稳定的尾延迟回归。较稳定的方向是 P50 中位数约降低 31%、RPS 中位数约提升 18%，且缓存开启时 DB 查询为 0。

**单轮结果定位**：仓库此前单轮配对差值为 +9.12%，另一次独立单轮复测为 +34.48%；二者均落在本次 8 轮配对差值范围内，不应单独作为峰值 P95 结论。

**数据来源边界**：8 轮汇总见 `evidence/11-peak-multirun-summary.json`；`runs` 数组含逐轮 P50/P95/P99/RPS/DB、seed 与启动顺序，可由 `scripts/summarize_peak_repeats.py` 重算。逐请求样本未纳入仓库，历史单轮样本见 `evidence/09/10`。

## 4. 数据修改后读到新值的时间

**方法**：每个缓存点独立循环 30 次。先读一次使键热；调用管理写接口修改数据（写接口在 DB commit 后执行 `DEL`）；写响应返回后，以 2 ms 间隔轮询读接口，直到观察到新值。`observed` 为「写响应返回 → 首次读到新值」；`server` 为服务端记录的「commit → DEL 完成」。2 ms 轮询使 observed 存在约 2 ms 量化下限。

| 缓存点 | observed P50 (ms) | observed P95 (ms) | observed max (ms) | server P50 (ms) | server P95 (ms) | 命中新值次数 |
|---|---:|---:|---:|---:|---:|---:|
| `tax.dict.tree` | 3.24 | 4.001 | 4.34 | 0.141 | 0.177 | 30/30 |
| `site.home` | 2.813 | 3.673 | 3.683 | 0.137 | 0.164 | 30/30 |
| `dashboard.metrics` | 3.664 | 4.756 | 4.857 | 0.133 | 0.162 | 30/30 |
| `iam.perms` | 2.183 | 2.406 | 2.436 | 0.136 | 0.171 | 30/30 |

由表可见，四个缓存点的服务端失效动作都在 **0.2 ms 以内**完成；端到端观测 P95 不超过 **4.8 ms**，全部 120 次修改均读到新值，没有出现旧值残留。原始逐条记录：`evidence/06-invalidation.csv`；汇总：`evidence/06-invalidation.json`。

## 5. 缓存穿透与击穿防护

| 场景 | 缓存开启 | 缓存关闭 | 结论 |
|---|---:|---:|---|
| 不存在字典项，同一编码并发/重复请求 | 300 次请求，DB 查询 0，Bloom 拦截 300 | 60 次请求，DB 查询 60 | Bloom 先行拒绝，未知编码不落到 DB |
| 不存在字典（整棵字典树） | 首次 DB 1 次，第二次 DB 0 次，状态 `negative`，TTL 30 秒 | 无负缓存 | 空值哨兵短 TTL，避免反复打 DB |
| 热点驾驶舱键删除后 100 并发 | DB 查询 3 次（1 次 loader × 3 查询），lock_waits=29，timeout=0 | DB 查询 300 次 | single-flight 把并发回源收敛为 1 次 |

补充说明：字典项 Bloom 位图为 65,536 bit / 5 hash，演示数据 318 个字典项，理论误判率约 10^-8 量级；负缓存 TTL 30 秒作为 Bloom 误判与 Redis 故障的兜底。击穿锁 `SET NX PX 5000`，waiters 最多等待约 1 秒后回源兜底，避免死锁。

## 6. 发现的问题与改进建议

1. **commit 与 DEL 不是原子操作**（优先级 P1）：当前实现提交成功后同步 `DEL`；若进程在两者之间崩溃或 Redis 瞬时不可用，旧值会保留到 TTL 到期（字典 15 分钟、首页 5 分钟、驾驶舱 10 分钟、权限 30 分钟）。建议改为事务性 outbox / CDC 事件 + 带重试的失效消费者，并对 `cache_errors`、失效失败、陈旧读告警。
2. **峰值 P95 对测试顺序敏感，暂不能归因于缓存回归**：8 轮峰值配对差值为 -20.50% 至 +40.82%（中位 -1.58%），启动顺序分组后的中位数方向不同。建议保留逐轮 seed 与原始 JSON、平衡或随机化执行顺序，并尽可能将压测客户端与服务端隔离；在受控测试稳定复现前，不把 P95 变化归因于同步 Redis 客户端。若后续数据确认尾延迟问题，再评估 `redis.asyncio` 等方案。
3. **穿透防护的 Bloom 重建成本**：字典项新增/删除会删 Bloom，下一次查询重建需要读全部编码。建议部署时预热 Bloom，或改为可增量维护的计数 Bloom/布谷鸟过滤器；同时保留负缓存兜底。
4. **驾驶舱数据库成本仍高**：缓存关闭时 3 条聚合查询/请求、约 175 个共享缓冲区命中/请求。建议把月度指标做成物化视图/预聚合表，再用缓存承载读流量；缓存不能替代查询优化。
5. **冷启动**：Redis 清空或发布后首波请求会同时回源。建议发布后异步预热四类热键（按站点/账期/用户），并给预热任务限速。
6. **可观测性**：建议把缓存命中率、Redis 延迟、single-flight 等待/超时、失效失败、DB 查询/请求纳入监控；本仓库的 `/api/admin/cache/stats` 与响应头 `X-DB-Queries`/`X-Cache-State` 可作为埋点样板。
7. **安全与部署**：演示环境 Redis 无 AUTH/TLS，仅监听 127.0.0.1；生产必须启用 Redis ACL/TLS、限制管理接口来源，并确保 `ADMIN_TOKEN` 只从环境变量/密钥管理读取。
8. **TTL 不是一致性机制**：权限点 30 分钟 TTL 仅用于兜底，角色变更必须立即 DEL；首页按自然日分键，跨日自然失效。不要用「等 TTL」替代写后失效。

## 7. 验收标准对应

| 验收标准 | 交付位置 | 结果 |
|---|---|---|
| 1. 缓存点清单写明键规则与过期时间 | `docs/cache-points.md`、`evidence/02-cache-points.json`、`/api/admin/cache/points` | 4 个业务缓存点 + 3 类辅助键（负缓存/Bloom/单飞锁），键规则/TTL/失效/防护齐全 |
| 2. 开启与关闭缓存的 P95 与数据库负载对比 | 本报告第 3 节、`evidence/03`、`04`、`09`、`10`、`11-peak-multirun-summary.json`、`05-loadtest-comparison.json` | 常态 P95 3.933 vs 8.591 ms、DB 查询 0 vs 25,207；峰值 8 轮 P95 中位数/范围与启动顺序敏感性已披露 |
| 3. 数据修改后读取到新值的时间有实测记录 | 本报告第 4 节、`evidence/06-invalidation.csv`、`evidence/06-invalidation.json` | 4 点各 30 次，120/120 命中新值；服务端 P95 ≤0.177 ms |

## 8. 复现命令

```bash
# 1) 准备隔离依赖（也可用 docker-compose.test.yml）
# PostgreSQL 16+ 与 Redis 7+，库名含 test；以下为本次实测端口
export DATABASE_URL='postgresql+psycopg://clawhunt@127.0.0.1:55432/clawhunt_2984'
export REDIS_URL='redis://127.0.0.1:56379/0'
export ADMIN_TOKEN='dev-admin-token'

# 2) 启动缓存开/关两个进程
CACHE_ENABLED=1 .venv/bin/uvicorn app.main:app --port 8011 &
CACHE_ENABLED=0 .venv/bin/uvicorn app.main:app --port 8012 &

# 3) 初始化虚构数据集（24k 商机 / 10k 合同 / 20k 回款）
.venv/bin/python scripts/seed.py --profile perf --reset

# 4) 缓存开/关压测（4 并发、8,000 请求）
.venv/bin/python scripts/loadtest.py --base-url http://127.0.0.1:8011 --label 'cache-on' --mode cache-on --requests 8000 --concurrency 4 --out evidence/03-loadtest-cache-on.json
.venv/bin/python scripts/loadtest.py --base-url http://127.0.0.1:8012 --label 'cache-off' --mode cache-off --requests 8000 --concurrency 4 --out evidence/04-loadtest-cache-off.json

# 5) 失效时延与穿透/击穿
.venv/bin/python scripts/measure_invalidation.py --base-url http://127.0.0.1:8011 --iterations 30 --out-prefix evidence/06-invalidation
.venv/bin/python scripts/verify_protections.py --on-url http://127.0.0.1:8011 --off-url http://127.0.0.1:8012 --output evidence/07-protections.json

# 6) 单测与报告回填
TEST_DATABASE_URL='postgresql+psycopg://clawhunt@127.0.0.1:55432/clawhunt_2984_test' REDIS_URL='redis://127.0.0.1:56379/15' .venv/bin/python -m pytest
.venv/bin/python scripts/summarize_peak_repeats.py --input-dir /tmp --output evidence/11-peak-multirun-summary.json
.venv/bin/python scripts/build_report.py
```

## 9. 证据索引

| 文件 | 内容 |
|---|---|
| `evidence/01-pytest.log` | 47 项测试通过输出 |
| `evidence/02-cache-points.json` | 缓存点注册表机器可读快照 |
| `evidence/03-loadtest-cache-on.json` | 缓存开启压测原始数据（含逐请求样本、PG 前后快照） |
| `evidence/04-loadtest-cache-off.json` | 缓存关闭压测原始数据 |
| `evidence/05-loadtest-comparison.json` | 开/关与峰值的派生对比指标 |
| `evidence/06-invalidation.json` / `.csv` | 4 个缓存点 ×30 次失效时延原始记录 |
| `evidence/07-protections.json` | 穿透/负缓存/击穿端到端对照 |
| `evidence/08-smoke.log` | 运行中服务冒烟输出（四点键规则/TTL、响应头、Bloom 拦截） |
| `evidence/09-peak-cache-on.json` / `10-peak-cache-off.json` | 原始单轮 24 并发峰值数据（保留作历史单轮观测） |
| `evidence/11-peak-multirun-summary.json` | 8 轮峰值汇总（含逐轮统计值、seed 与启动顺序；逐请求样本未纳入） |

## 10. 边界与未做事项

- 本仓库是**缓存验证工程**，不是会员空间平台完整业务实现；S2-TAX 字典中心、首页、驾驶舱、IAM 均为按本单需求构造的演示模块。
- 未实现跨实例 L1 缓存与 Pub/Sub 失效链路，故未测量「多实例 + 本地缓存」的失效时延；当前所有实例共享 Redis，DEL 后立即可见。
- 未接入真实客户数据、真实会员企业、真实联系方式；数据集全部由固定 UUID 与虚构名称生成。
- 压测客户端与服务端同机，峰值测试的绝对数值受本机 CPU/调度影响；常态 4 并发结论用于本单容量基线，不作为生产 SLA 承诺。
- PostgreSQL 实测版本为 18.3；交付 SQL 未使用 17/18 新特性，正式环境按客户约定 16 执行。
