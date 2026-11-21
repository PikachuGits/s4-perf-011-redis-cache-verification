# 缓存点清单 · S4-PERF-011

> 本清单由 `scripts/export_cache_points.py` 从 `app/cache/registry.py` 生成；文档、管理接口 `/api/admin/cache/points` 与测试共用同一注册表。

- Redis：`redis://127.0.0.1:56379/<db>`，键前缀 `ms:`
- TTL 缩放：`1.0`（压测/测试可整体缩放，默认 1.0）
- 所有 UUID 统一小写标准格式；非法键组件在进入缓存前返回 422，不拼接进 Redis 键

## 一、业务缓存点（4 个）

| 缓存点 | 键规则 | 基础 TTL | 抖动后范围 | 作用域 | 失效触发 | 缓存内容 | 防护 |
|---|---|---:|---:|---|---|---|---|
| 字典树（S2-TAX 字典与标签中心） (`tax.dict.tree`) | `ms:tax:dict:tree:{org_id}:{dict_code}` | 900 秒 | 810–990 秒 | org_id + dict_code | 字典项新增/改名/排序/软删除：数据库提交后 DEL 该字典的树键；下一次读取重建 | 整棵字典树 JSON（industry/tags/region，约 300 个节点） | single-flight 分布式锁；TTL ±10% 抖动；Redis 故障时回源 DB |
| 首页聚合 (`site.home`) | `ms:site:home:{org_id}:{site_id}:{date}` | 300 秒 | 270–330 秒 | org_id + site_id + 自然日 | Banner 发布/修改、活动或会员资料变更：提交后按站点 DEL 当日键 | Banner 5 条 + 近期活动 5 条 + 推荐会员 5 条 + 三项计数 | single-flight 分布式锁；TTL ±10% 抖动；按自然日分键，跨日自然失效 |
| 驾驶舱指标 (`dashboard.metrics`) | `ms:dashboard:metrics:{org_id}:{site_id}:{period}` | 600 秒 | 540–660 秒 | org_id + site_id + 账期（YYYY-MM） | 商机阶段、合同状态、回款计划变更：提交后按站点+账期 DEL（批量改动按前缀清理） | 商机阶段汇总 + 合同状态汇总 + 回款应收/实收/逾期汇总 | single-flight 分布式锁；TTL ±10% 抖动；按账期分键，避免跨月脏读 |
| 权限点 (`iam.perms`) | `ms:iam:perms:{org_id}:{user_id}` | 1800 秒 | 1710–1890 秒 | org_id + user_id | 用户角色或角色权限变更：提交后 DEL 该用户键；支持传入多个用户批量清理 | 用户角色列表 + 去重后的 allow 权限点列表 | single-flight 分布式锁；TTL ±5% 抖动；权限变更立即 DEL，不依赖过期 |

## 二、辅助键（穿透防护与并发控制，不是业务内容缓存）

| 用途 | 键规则 | TTL | 说明 |
|---|---|---:|---|
| 字典项查询负缓存（穿透防护） | `ms:tax:dict:item:neg:{org_id}:{dict_code}:{item_code}` | 30 秒（无抖动） | 固定空值哨兵（不缓存真实内容）；仅缓存「不存在」结果，30 秒自然过期；字典项新增后重建 Bloom 并删除负缓存 |
| 字典项 Bloom 位图 | `ms:tax:bloom:{org_id}:{dict_code}` | 3600 秒 | 65536 bit / 5 hash；字典项新增或删除后由管理接口删除并在下次查询时重建 |
| single-flight 锁 | `ms:lock:{cache_key}`（其中 `{cache_key}` 为完整业务键） | 5000 毫秒 | `SET NX PX`；Lua 比对 token 后释放，避免误删他人锁 |

## 三、键空间规则

1. 键只由 UUID、受控枚举（`dict_code`/`item_code`/`period`/`date`）组成，不包含姓名、手机号、邮箱、token 或用户输入原文。
2. `dict_code` 规则：`^[a-z][a-z0-9_-]{0,63}$`；`item_code` 规则：`^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`；`period` 为 `YYYY-MM`；`date` 为服务端本地自然日 `YYYY-MM-DD`。
3. 业务键统一以 `ms:` 开头；管理接口 `POST /api/admin/cache/flush` 只清理该命名空间。
4. 失效在数据库提交之后执行：精确 `DEL` 本请求影响的键；驾驶舱批量改动可按 `ms:dashboard:metrics:{org_id}:{site_id}:*` 前缀清理。
5. 首页按自然日分键，跨日由新键自然替换；权限点变更必须 DEL，不依赖 TTL 到期。

## 四、TTL 策略说明

| 缓存点 | 为什么是这个 TTL | 一致性要求 |
|---|---|---|
| 字典树 | 读多写少、全树 JSON 较大（约 300 节点）；15 分钟允许吸收低频改动的回源成本 | 字典项写接口提交后立即 DEL，TTL 只是兜底 |
| 首页聚合 | 运营位/活动/会员卡片允许分钟级延迟；5 分钟兼顾命中率与新鲜度 | 运营发布/修改后按站点 DEL 当日键 |
| 驾驶舱指标 | 聚合扫描多张表，成本最高；10 分钟适合管理驾驶舱的阅读节奏 | 商机/合同/回款写接口按站点+账期 DEL |
| 权限点 | 变更频率最低，30 分钟可显著降低权限校验压力 | 角色或角色权限变更后 DEL 用户键，不等过期 |

## 五、与验收标准的对应

- AC-1「缓存点清单写明键规则与过期时间」：本文第一、二、三、四节。
- AC-2「开启与关闭缓存的 P95 与数据库负载对比」：`docs/verification-report.md` 第 3 节 + `evidence/03`、`04`、`05`。
- AC-3「数据修改后读取到新值的时间有实测记录」：`docs/verification-report.md` 第 4 节 + `evidence/06-invalidation.csv`。

> 数据全部为虚构演示数据，不包含客户名称、真实会员企业或个人信息。
