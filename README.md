# Amazon ERP

首次安装、升级和换机器迁移请先看 [简明部署文档](DEPLOY.md)。

自托管的 Amazon 运营管理系统，包含 Listing、订单、FBA 货件、采购、发货计划、库存、物流、财务、广告、用户权限管理及待办协作。

项目仓库：https://github.com/x781047225/Amazon-ERP

## 技术与目录

- 后端：Python 3.12、FastAPI、SQLAlchemy、Alembic、Celery。
- 数据：PostgreSQL 17、Redis 7；前端：Vue 3、TypeScript、Element Plus、Vite。
- `backend/app/`：业务接口、权限校验、后台任务及 Amazon 适配器。
- `backend/migrations/`：数据库版本迁移；`backend/tests/`：隔离测试库用例。
- `frontend/src/`：页面；`scripts/`：安装、启动、备份及维护工具。
- `resources/templates/`、`assets/shipping/`：导入模板和标签资源。
- `config/`：本机私有配置；`data/`：业务数据；`backups/`：备份；`logs/`：运行日志。这些目录中的私有内容不提交 Git。

## 本地安装

提供的启动脚本管理本机 PostgreSQL、Redis、API 及任务进程。当前开发和验证环境为 macOS Apple Silicon。Linux 服务器请先验证依赖、字体及打印输出，并使用下述生产部署方式。

准备 Git、Node.js 22.12+（建议 24 LTS）、npm、uv、C 编译工具和 make。macOS 可安装 Xcode Command Line Tools。

```sh
git clone https://github.com/x781047225/Amazon-ERP.git
cd Amazon-ERP
bash scripts/setup.sh
```

安装会下载依赖、编译 Redis、构建前端，生成 `config/local.json`。数据库、Redis 密码和授权加密密钥均随机生成，不存在通用默认密码。

首次启动前，使用终端安全输入一个至少 12 位的管理员密码，保存为环境变量 `ERP_ADMIN_PASSWORD`，然后启动：

```sh
./backend/.venv/bin/python scripts/control.py start
```

打开 `http://127.0.0.1:8787`，用账号 `admin` 和刚设置的密码登录。环境变量只在空数据库创建首个管理员时使用；已有用户不会被覆盖。不要把密码写进启动脚本或 Git。启动成功后可从终端环境移除该变量。

macOS 也可使用 `start.command`；停止使用 `stop.command` 或：

```sh
./backend/.venv/bin/python scripts/control.py stop
```

## 首次业务配置

1. 管理员在「设置 → 店铺授权管理」新增店铺。店铺名称作为业务标识，保存后不可直接改名。
2. 填写 SP-API 的 `seller_id`、`client_id`、`client_secret`、`refresh_token`。
3. 按需配置店铺代理：协议（HTTP、HTTPS、SOCKS5、SOCKS5H）、IP/主机、端口、账号、密码。不填主机表示直连。SOCKS5 链路可另外填写不含认证信息的上游 HTTP 代理。
4. 如使用广告模块，单独填写广告 `client_id`、`client_secret`、`refresh_token`、`api_url`。广告授权不会借用 SP-API 密钥。API URL 从 Amazon 官方北美、欧洲、远东地址中选择，与授权区域匹配。
5. 打开「设置 → 同步数据」，选择店铺，为需要的数据分别执行初始化；广告初始化会自动发现并关联授权账户。已关联其他店铺的广告账户不能被覆盖。
6. 设置每类数据的定时增量周期并保存；维护仓库、产品、采购成本、物流渠道、合规信息（英代、欧代和各国 VAT）及发货地址。

店铺和广告配置保存在数据库，服务在调用时读取。更新授权后新请求使用最新配置，SP-API 访问令牌缓存按配置版本失效。密钥、刷新令牌和代理密码加密保存，不在页面回显；编辑时留空保留原值，明确勾选「清除已保存密钥」才会清除。SP-API 数据同步仅对 SP-API 授权完整的店铺执行，广告任务独立检查广告授权。代理由同一店铺的 SP-API 和广告请求共用。

授权保存仅保存资料，不代表已完成 Amazon 联通校验。正式同步需要有效授权、正确网络以及 Amazon 侧的相应权限。

## 初始化与定时同步

入口统一为「设置 → 同步数据」，每个店铺的八类数据独立设置（绩效邮件需另行授权微软邮箱）。需要该 Tab 的查看或操作权限，同时受用户店铺范围限制。

1. 配置授权，选择店铺。
2. Listing 和库存使用「当前全量」；其他数据选择最近 **1、7、14、30、90、180、365 天**，点击对应行「初始化」。任务进入后台队列，关闭页面不会中止。初始化补充或更新数据，不清空已有记录；重复启动同类活动任务会被拒绝。
3. 广告初始化成功后自动执行推荐策略，无需选择周期；可暂停或手动补拉。其他数据开启定时同步后选择周期，点击该行「保存设置」。首次定时执行安排在保存后的一个周期。关闭定时同步不取消已开始的任务。
4. 在任务记录中查看状态、分段或广告账户进度、失败原因及覆盖限制。任务失败时保留已写入数据，初始化旁边和失败任务记录中显示「继续同步」，从原任务保存位置恢复；其他状态不显示此按钮。增量同步只有在全部分段成功后才推进成功位置，重启后可继续未完成的任务。

「继续同步」沿用原任务日期范围，不使用下拉框新选的天数；已完成分段跳过，只恢复未完成部分。例如 365 天任务完成 11/13 后失败，继续处理剩余 2 个分段。订单分页位置若被接口明确判定过期，只重新读取对应分段，已入库订单按编号更新去重。「重新初始化」则创建新任务，重新请求所选时间范围，失败后选择此操作会先提示确认。

库存没有历史分页续传，继续时重新读取当前完整快照；Listing 保留列表扫描进度，但会重新检查模板与目录；货件保留已完成的明细工作，广告保留已完成日报。STA 和邮件保留已保存分页位置；邮箱重新授权后不能继续旧账户任务，需要重新初始化。权限、授权等错误仍需先处理，继续同步不会绕过接口错误；目录单商品404按下述正常缺失规则处理。

| 数据 | 初始化范围与定时更新口径 |
| --- | --- |
| Listing | 初始化和定时均扫描当前全量：SKU/ASIN/FNSKU、标题/品牌/主图、类型、属性、报价、状态、问题和变体关系等；未返回记录保留，不能据此当作删除。 |
| 订单 | 订单及商品明细（状态、配送方式、金额/币种、SKU/ASIN、名称、数量）。初始化按下单时间；定时按最后更新时间并重读前 48 小时。旧订单取消/发货会更新，同订单编号去重，旧快照不能覆盖新状态。长范围按 30 天分段。 |
| 库存 | 当前 FBA 库存完整快照（可售、预留/处理中、不可售、待发、在途、接收中、总量等）；不生成历史库存，不修改本地仓库出入库。 |
| 货件 | 初始化按所选范围的最后更新时间发现货件；定时扫描接口可返回的全部状态货件并重读明细（含关闭/取消），更新数量、接收情况及到达资料，保留本地物流字段。 |
| 利润结算 | 按入账时间下载回款、退款与扣费流水；长范围按 30 天分段，定时重叠至少 1 天，按交易编号更新去重。利润需结合本地采购/头程成本及广告费用。 |
| 广告活动 | 每次读取当前 SP 活动、广告组、商品、关键词、投放及否定规则；SB/SD 仅已有支持的概览。初始化后自动按账户当地时间复核：每6小时最近7个完整日加当天、每天14天、每周60天；较大范围合并覆盖较小范围。日报适配器回溯约 95 天，超限标记部分完成，已有历史保留。 |

| STA任务 | 按所选范围发现历史入库计划，定时重新读取全部状态；只读归档，不重复创建或提交计划。 |
| 绩效邮件 | 每店铺单独连接微软邮箱；按收件时间归档亚马逊邮件的主题、发件人和纯文本正文。定时回读约 48 小时并去重，完整分页成功后才更新同步时间。 |

微软邮箱需要管理员先配置一次公共客户端应用，各店铺再分别授权；配置步骤、权限及同步限制见 [微软邮箱接入](DEPLOY.md#6-微软邮箱接入每店铺独立授权)。

服务器、数据库、Redis、普通任务进程、FBA 任务进程及调度进程需保持运行。调度器每 30 秒检查数据库中的设置，队列繁忙或 Amazon 限流时可能延后；同一店铺同一类数据不会叠加运行。旧版本已同步店铺的周期迁移到新配置，新店铺默认关闭定时任务。原订单自动同步若已关闭，升级时继续保持关闭。

各业务页的主动同步入口已移除，保留页面本地刷新、查询和导出，以及 **STA 入库计划详情 → 同步亚马逊状态**。历史同步记录继续可查；旧的单次同步接口保留兼容；原全局订单自动同步开关已停用，返回新入口提示。

Listing 日期过滤参数参考 [Amazon searchListingsItems 文档](https://developer-docs.amazon/sp-api/reference/searchlistingsitems)。可回溯数据仍受 Amazon 授权范围、账户和接口保留期限制；失败或不完整覆盖会显示在任务记录中。

## 用户、角色及数据范围

用户管理包含三个 Tab：**用户管理、角色管理、MCP**。

- 用户管理：新增用户、赋予角色，单独授权店铺和仓库。
- 角色管理：为每个菜单及 Tab 分配「查看 / 操作」，支持列全选。操作权限包含相应查看权限。
- MCP：生成、撤销个人 Token，默认有效期 90 天，到期可重新生成。明文 Token 仅在生成时显示一次。
- 菜单权限与店铺 / 仓库范围共同生效，不能仅凭菜单权限读取其他店铺或仓库的数据。

Listing 的调价、编辑、关联等操作记录在 Listing 业务日志中；个人 Token 发起的修改关联到对应用户和 Token 标识，不记录 Token 明文。历史操作发生在日志功能上线之前的，不会凭空补造日志。

## MCP 接入

Listing 查询工具 `listings_query`、`listing_detail`、`fba_inventory_query` 返回独立的 `inventory.transfer`（待调仓）；库存字段 `null` 表示尚未获取，`0` 表示已确认零库存。首页在库包含待调仓，不再次与在库相加。

以上工具还返回 `listing_status`（在售／停售／已删除／缺货／未知）、`listing_status_code` 和 `listing_status_detail`。后者包含 `can_update`、判断原因、数据来源、Listing/库存读取时间与相关问题。更新外部表格前检查读取时间，仅在 `can_update=true` 时采用该状态；未知时保留原值。BUYABLE 优先于零库存，父体和读取异常不误判缺货；缺货须确认所有配送渠道可售为零且没有未解决的 ERROR；停售限制或目录移除不等于删除。当前未接入删除通知，无明确 DELETED 证据不会返回已删除。

ERP 已提供 `/mcp` 无状态 JSON 接口（POST），可直接使用个人 Token 连接；不提供 GET SSE 流。也可另行部署 MCP 适配服务，将工具映射到 ERP HTTP 接口，并转发用户的 `Authorization: Bearer <个人Token>`。不要在适配层改用共享管理员身份。

支持 Listing 查询及当前支持字段的修改、货件查询、订单查询、库存查询，以及产品管理五个 Tab 的增删改查。以 `tools/list` 返回的 `inputSchema` 为准，也可通过 `/api/integration/capabilities` 查看当前用户获准使用的工具。Listing 提交继续使用已有的修改预览和确认流程。

| 产品管理内容 | MCP 工具 |
| --- | --- |
| 产品信息 | `products_query`、`product_detail`、`product_options`、`product_create`、`product_update`、`product_archive`、`product_restore` |
| 供应商 | `supplier_query/detail/create/update/delete` |
| 清关编码 | `clearance_code_query/detail/create/update/delete` |
| 报关编码 | `declaration_code_query/detail/create/update/delete` |
| 清关＆报关资料库 | `customs_profile_query/detail/create/update/delete` |
| 标品上的资料快照 | `product_declaration_update`、`product_clearance_update` |
| 亚马逊 SKU 与标品关联 | `product_listings_query`、`product_listing_link`、`product_links_apply` |

表中的斜杠表示独立工具名称，例如 `supplier_query`、`supplier_detail`。查询支持分页与搜索；编辑、删除先读取当前 `revision`，编辑必须带上结构中指定的必填字段，未指定的可选字段保留。标品删除进入回收站，仍关联 SKU 时不能删除；删除资料库条目会解除引用，但保留产品中已保存的资料快照。

SKU 关联先按店铺、站点、SKU 查询取得 `listing_id` 和当前 `product_id`。`product_links_apply` 的每一项都需填写 `expected_product_id`（未关联填 `null`），目标 `product_id=null` 表示解绑；最多 100 项，任一关联冲突或越权则整批不保存。产品管理工具仅修改本地 ERP，不更新亚马逊 Listing。

工具权限沿用用户的查看/操作权限及店铺范围；只读用户不会获得写入工具。有效个人 Token 无需重新生成，客户端重新连接或刷新工具列表即可发现新工具。

已认证的 MCP 请求持久记录调用用户、用户/Token 编号、工具、对象编号、字段名、时间及结果，包括查询、写入、参数错误、权限拒绝和执行失败。写入另保留原有业务日志；不记录 Token 原文、完整请求/返回内容或异常中的凭据。日志复用 `AccessAudit`，可通过已有用户管理审计接口 `/api/access/audit` 查看，普通用户仅能查看自己的记录。

客户端连接示例：

```json
{
  "mcpServers": {
    "amazon-erp": {
      "url": "https://erp.example.com/mcp",
      "headers": { "Authorization": "Bearer <个人Token>" }
    }
  }
}
```

将域名替换为部署的 ERP HTTPS 地址；使用独立 MCP 服务时则填写该服务地址。ERP API 默认监听 8787 端口。客户端配置含个人 Token，不应提交 Git。

## 配置与生产部署

`config/local.json` 为本机私有文件，权限应为 `600`，包括：

| 配置 | 用途 |
| --- | --- |
| `pg_port`、`db_password` | 本机 PostgreSQL |
| `redis_port`、`redis_password` | 本机 Redis |
| `http_port`、`http_host` | API 端口及监听地址，默认仅本机 |
| `http_allowed_hosts` | 允许访问的域名/IP，不包含协议和端口 |
| `credential_key` | 数据库中店铺授权资料的 Fernet 加密密钥 |

可用环境变量覆盖 `DATABASE_URL`、`REDIS_URL`、`ERP_CREDENTIAL_KEY`；`ERP_ROOT` 指定项目根目录。当前运行配置仍需要 `config/local.json`，请先运行 `scripts/init_config.py` 生成基础文件。外部服务的连接地址仅放入部署环境，不写进源码。

生产建议使用独立 PostgreSQL、Redis 和进程管理器，备份业务数据及授权加密密钥，反向代理通过 HTTPS 暴露 API。数据库、Redis 只对应用网络开放。配置可信主机；反向代理转发协议头，仅信任已知代理。不要直接把开发端口暴露到公网。

构建与迁移：

```sh
uv sync --project backend --python 3.12 --frozen
npm ci --prefix frontend
npm run build --prefix frontend
cd backend
.venv/bin/alembic upgrade head
```

以下进程均从 `backend/` 启动，并使用同一份数据库、Redis 和加密密钥配置：

```sh
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8787
.venv/bin/celery -A app.tasks:celery worker --pool=threads --concurrency=4 --loglevel=WARNING --without-gossip --without-mingle
.venv/bin/celery -A app.tasks:celery beat --loglevel=WARNING --schedule ../data/celerybeat-schedule
.venv/bin/celery -A app.fba_tasks:celery worker -Q fba --hostname fba@%h --pool=threads --concurrency=4 --loglevel=WARNING --without-gossip --without-mingle
.venv/bin/celery -A app.fba_tasks:celery beat --loglevel=WARNING --schedule ../data/fba-celerybeat-schedule
```

每个调度器只运行一个实例。用 systemd、容器或其他进程管理器管理这些进程；`scripts/supervisor.py` 专用于自带本机数据库的运行方式，不用于启动外部数据库。更新时先停调度、等待执行中的任务结束，再迁移和重启，避免中断外部写入。

生产初始化应使用全新的空数据库。应用通过 Alembic 建表，通过 `ERP_ADMIN_PASSWORD` 创建首个管理员，再由用户维护授权和业务配置。安装和升级命令不会清空已有数据库；不要使用测试脚本清理生产库。

## 从旧文件配置升级

1. 备份数据库和原私有配置，保留授权加密密钥。
2. 运行 `scripts/init_config.py`，为尚未配置密钥的安装生成加密密钥。
3. 在 `backend/` 执行 `.venv/bin/alembic upgrade head`。
4. 回项目根目录运行：

```sh
./backend/.venv/bin/python scripts/migrate_store_credentials.py
```

该工具一次性读取旧 `config/stores.json`、`config/account_network.json`、`config/advertising.json` 及其中指定的广告凭据文件，写入加密配置。不会覆盖数据库已有配置，核对结果只输出数量，不打印凭据。迁移后运行时不再依赖这些旧文件，可将其移入受控备份。

历史权限 `users.permissions.*` 自动归并到角色管理权限。独立「权限管理」Tab 已移除，角色自身的权限配置保留。

## 备份与恢复

```sh
./backend/.venv/bin/python scripts/backup.py
```

生成 `backups/orders-时间.json.gz`，包含业务表、商品图片及**加密的店铺授权**，不包含登录会话。备份也包含个人 Token 的摘要和业务记录，必须作为敏感数据保管。文件名沿用历史命名，实际不只备份订单。

同时单独安全备份 `credential_key` 或 `ERP_CREDENTIAL_KEY`。丢失密钥后无法恢复已加密授权；不要直接生成新密钥替换旧密钥。数据库连接密码、Redis 密码和环境配置也应由部署管理方安全保存。

恢复要求：停止应用和后台任务；准备空数据库；迁移到备份记录的相同 schema 版本；配置原授权加密密钥；执行：

```sh
./backend/.venv/bin/python scripts/restore.py backups/orders-时间.json.gz
```

恢复工具拒绝覆盖非空库，校验加密密钥后才写入。恢复后关闭定时同步并撤销旧个人 Token，排队外部操作暂停；先核对亚马逊状态，再手动操作。旧备份的 schema 元数据可能沿用旧脚本的固定版本，请先在隔离环境核验，不要直接恢复到生产。

## 开发验证

后端测试使用独立数据库 `amazon_erp_test`，会重建其中所有表。当前测试入口从本机 `config/local.json` 连接 PostgreSQL；不得将此测试库名用于业务数据，也不要把测试环境指向生产数据库服务。

```sh
cd backend
PYTHONPATH=. .venv/bin/python -m pytest -q
cd ..
npm run build --prefix frontend
./backend/.venv/bin/python scripts/check_secrets.py
```

`check_secrets.py` 按 `.gitignore` 检查待发布文件，匹配本机已知凭据及常见密钥格式，只报告文件位置，不显示秘密内容。测试通过不能替代 Git 提交前审查；若凭据曾进入 Git 历史，仅删除当前文件不够，需要轮换凭据并清理历史。

## Git 提交范围

提交源码、依赖锁文件、迁移、公开模板和 README。不要提交真实授权、本机配置、数据库目录、备份、业务导出、运行日志、浏览器登录状态或个人 Token。一次性诊断脚本默认忽略；正式维护脚本在 `.gitignore` 中逐项放行。

发布前检查实际暂存内容：`scripts/check_secrets.py --staged --local-db`。不要上传本机配置、数据库和登录状态。

## 常见问题

- 启动失败：查看 `logs/supervisor.log`、`logs/api.log`，检查端口占用、数据库迁移、首个管理员密码及目录写入权限。
- 授权失败：检查对应店铺的 SP-API 或独立广告授权、代理和 API 区域；不要在截图或错误报告中附带 Token/密码。
- 广告无账户：保存广告授权后，在「设置 → 同步数据」初始化广告活动，再检查用户的店铺权限。
- 授权无法解密：核对 `ERP_CREDENTIAL_KEY` 是否覆盖了正确的本机密钥；恢复原密钥，不要删除数据库记录。
- 页面仍是旧版：完成前端构建后刷新；后台代码改动还需重启 API 和任务进程。


### 工作区与个人账号

- 侧边业务菜单与顶部页面标签使用深色导航；拖动已打开的页面标签可调整顺序，本浏览器会话内刷新后保留。
- 每个列表搜索／操作行末尾的「重置列宽」只恢复该列表；列宽按账号和列表分别保存。广告列表沿用原有服务器列宽偏好。
- 点击右上圆形头像可修改密码或退出登录。修改密码需验证当前密码，新密码至少 12 位；其他网页登录会话失效，当前会话保留。个人 MCP Token 有独立的有效期与撤销入口。
- 同步数据说明列明字段和更新策略。订单按更新时间抓取，包括旧订单的取消和发货变化；重叠窗口用于容纳延迟，全部分页成功后才推进同步位置。亚马逊暂未返回的数据仍需等待后续同步，并非实时推送。


### 合规信息与编码资料

- 「设置 → 合规信息」统一维护英代、欧代和 VAT，支持按店铺名称和类型组合筛选。
- VAT 按店铺、国家分别保存 VAT 号、EORI 号和税务代理地址，同一店铺可登记多个国家。英代、欧代的标签资料保持独立，VAT 不会覆盖代理及制造商信息。
- 「仓库管理」支持仓库名称搜索，「店铺授权管理」支持店铺名称搜索。
- 清关编码、报关编码可分别按商品名称和 HSCODE 组合搜索。报关编码另有申报要素，支持手工编辑及 Excel/UTF-8 CSV 导入。
- 报关导入列：商品名称、HSCODE、税率、申报要素（选填）。HSCODE 列设为文本以保留前导零；税率填百分数。同名同编码更新资料，旧三列文件不会清空已有申报要素，提供空的申报要素列则会清空该字段。
- 合规维护沿用 `settings.profiles` 角色权限和用户店铺授权；修改使用版本校验，避免相互覆盖。

### 重做某店铺的初始化测试

维护命令 `backend/.venv/bin/python scripts/reset_store_sync_data.py --store 店铺名称` 只预览条数；明确需要清除数据时加 `--execute`。

该命令不创建备份。执行时暂停任务领取并等待正在运行的任务结束，在同一事务内清空指定店铺的 Listing、订单、FBA 库存、货件、结算、广告数据及相关同步记录／缓存，撤除该店铺待执行消息，关闭七类定时任务并重置同步位置。保留授权、用户权限、合规信息、产品、采购、发货计划、本地创建的 STA 和本地仓库资料，其他店铺不变。Listing 删除也会移除其 SKU 与本地产品的关联，重新同步后需要重新关联；产品主档不删除。操作只用于明确授权的测试数据重置，不应在日常同步时运行。


### 列表筛选与历史 STA

- 订单日期、利润结算入账日期均提供最近 1、3、7、14、30、90、180、365 天，默认 1 天。查询列表按过去 N×24 小时筛选；订单默认按下单时间降序，可切换为按每笔订单商品件数（销量）降序。回款按入账时间降序，包含未关联本地订单的流水，不再显示订单时间或同步记录 Tab。
- 财务汇总下载使用同样的天数选项，按站点当地日历日期汇总，默认当天。同步执行记录统一在设置 → 同步数据查看。
- 货件默认按发货日期降序，日期为空的记录最后显示；仓库显示选项放在打印 FNSKU 标签后；用户可按账号和角色组合筛选，角色可按名称筛选。
- 设置 → 同步数据 → STA任务：可初始化指定最近更新时间范围，也可设置定时全量重新读取。读取亚马逊 ACTIVE、SHIPPED、VOIDED 计划及关联货件、商品明细，分页和完成记录持久化；读取失败不推进成功时间。每个完整计划原子保存，重跑按店铺和入库计划编号更新。
- FBA货件 → STA任务列表合并展示本地任务和亚马逊历史计划，同店铺同计划优先展示本地任务。历史计划单独标为只读，仅供查看，不生成本地发货单、不锁库存、不调用亚马逊创建或修改接口。可读取范围受亚马逊接口限制，不能保证所有旧版历史货件都能返回对应计划。
- 升级执行 Alembic `0041` 创建历史计划表；不会自动启动任何店铺的 STA 初始化，定时任务默认关闭。维护用单店铺重置包含导入的 STA 历史，但保留本地创建任务；日常升级不要执行数据重置。

## 待办协作

左侧「首页」下方新增独立「待办事项」菜单，提供「我发起的」「我负责的」「抄送我的」「已完成」，支持分配负责人、抄送人、优先级、截止时间、事件详情、处理与验收标准、多图说明及结果证据。负责人提交验收，发起人可退回或验收完成；保留全程记录，左侧菜单旁显示待处理与待验收合计，未读动态用圆点提醒；提示可查看逾期数量。发起人可填写原因撤销未完成事项，保留历史。列表支持优先级/截止时间降序、状态筛选与刷新。详情和图片仅参与者可见。详情以对话消息展示图文内容，可上传、粘贴或拖放截图，支持参与者单独留言，抄送人不能改变事项状态。权限、图片限制及升级说明见 [部署文档](DEPLOY.md#7-待办事项)。

## Listing 目录缺失与可售状态

可售状态以同店铺、同站点 Listings Items 返回的状态为准：有 BUYABLE 显示「可售」，明确的状态列表没有 BUYABLE 显示「不可售」；连状态字段也未返回时显示「状态待同步」，不根据目录404推断状态。

Catalog Items 某个 ASIN 返回404时，批量同步保留已读取的卖家资料，详情加载则补读该店铺的卖家 SKU 信息；不跨站点借用目录、不修改亚马逊商品。已有目录快照保留，页面显示普通资料来源说明，这类正常目录缺失不再使整次同步失败。详情页24小时内不反复查询已确认缺失的目录，后续定时同步仍检查目录是否恢复，恢复后自动更新。401/403、限流、网络故障、格式或身份异常仍提示错误。

「已完成」待办汇总自己发起、负责或被抄送且已验收完成的事项；撤销与待验收不会列入。顶部移除空白面包屑栏，头像菜单与可拖动页面标签同排，小屏通过菜单按钮展开导航。

### Advertising automatic report refresh

After successful initialization, advertising uses local-profile schedules: 00:00/06:00/12:00/18:00 refresh seven completed dates plus today (8 dates); 06:00 daily reviews 14 dates, and Monday 06:00 reviews 60 dates, both including today. Wider requests satisfy narrower tiers. Stored per-profile request-time checkpoints survive restarts and do not advance on failure. Automatic failures retry after one hour. At most two store runs execute concurrently; each store processes profiles serially, prioritizing profiles with campaigns. Recent-only runs skip metadata; daily/weekly runs refresh metadata.

Users need not choose an interval after initialization. Pause and manual refresh remain available; an explicit pause is preserved across reinitialization and migration. Missing daily reports show a notice, not zero metrics. Advertising status defaults to non-archived; All and clearing the selector include all states.

### Listing 批量促销调价

在 Listing 勾选商品后点击“批量调价”，可选择仅售价、仅促销、售价＋促销。价格按币种分别批量填写，促销开始/结束时间可统一应用或逐行调整；时间按窗口标注的浏览器时区填写并转换成带时区时间提交。仅促销只合并 discounted_price，不修改常规售价、企业价和其他属性。促销价必须低于对应常规售价，结束时间必须晚于开始及当前时间。提交保留价格快照校验、后台队列、逐项结果和含操作人的修改日志。


## 产品批量维护与发票商品链接

产品信息支持“无成本、无头程、无供应商”组合筛选，以及供应商、包装尺寸、重量、成本价批量修改。无头程按英国或欧洲任一实际头程为空/0判断。重量修改默认重新计算头程（英国15元/kg、欧洲20元/kg），可取消以保留手工头程。创建人/时间及逐产品日志可查询；历史无创建记录时显示未记录。批量修改按产品版本检查，冲突时整批不保存。

货件发票装箱明细按目的国家带入已维护的产品链接，优先产品资料，其次关联清关报关资料。“产品链接→默认”使用对应国家的ASIN售卖链接；英国为amazon.co.uk/gp/product/ASIN，德国为amazon.de/gp/product/ASIN?th=1。合并明细存在多个ASIN或缺少ASIN时保留原链接并提示，需保存发票才保留修改。

广告投放状态结合自身与所属活动/广告组限制展示；活动预算不足会向下级显示，但不会改变开启开关，已暂停/归档项目保留其状态。状态基于最近同步资料，与选择的业绩日期无关。广告页的ASIN、SKU、标品名称合并展示，不显示FNSKU。添加广告商品窗口按当前账户店铺站点选择，只列SKU、品名、ASIN、FNSKU、图片，使用轻量数据库分页。

广告搜索词支持中文匹配类型多选、单条/批量添加关键词、分组批量否定，手动组默认当前组并可切换授权店铺的手动组。竞价默认目标广告组默认值，可选择关键词三档建议价、自定义及百分比调整；已有匹配跳过。否定投放展示商品图片和所选日期关联搜索词表现。预算行内保存无需确认弹窗，失败回退，增加预算后等待实际状态核对。各广告列表每行末尾可查看该对象的用户操作日志，最近修改星号显示修改人；日志包含今天的记录，不受业绩日期限制。

## 采购协作、批量广告调整与系统帮助

供应商支持1/3/5/7/14/30天发货周期。采购计划支持备注；生成采购单时按北京时间当天加供应商自然日周期带出共用预计到货日期，未配置留空，可手工调整。采购与发货展开明细移除独立重置列宽按钮，主表保留。新增字段通过0057迁移升级。

MCP新增采购计划、采购单、发货计划查询及增删改查、详情和日志工具。沿用用户/令牌/店铺/仓库授权、版本检查、幂等创建和状态限制；MCP调用和单据修改保留用户日志。整批采购计划和整张采购单不能通过局部授权读取或修改。具体参数以tools/list公布的schema为准。

广告批量预算及投放竞价支持固定值、提高百分比、降低百分比。百分比以亚马逊当前值计算并四舍五入到两位；未配置投放竞价时取广告组默认竞价，提交时重新核对基数。批量状态增加归档，必须二次确认，不可撤回。广告界面隐藏内部ID，保留商品ASIN/SKU；全站数值表格居中。

头像左侧问号通过新浏览器标签页打开/help，提供20篇静态帮助及全文关键词检索，无本地业务数据。今日畅销榜分页右对齐；Listing去掉“本地配送预估”字样，保留费用。历史销量完整性连接历史初始化与后续增量区间，不因较短区间起点晚于初始化结束而误报；仍保留真实缺口提示。库存增量合并快照后，也确认未变化商品的读取时间，批量更新时间避免逐商品查询；ASIN/FNSKU身份不符及失败仍保留旧时间。
