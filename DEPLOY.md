# 简明部署与迁移

当前一键安装已在 **macOS Apple Silicon** 验证。Linux 生产环境的独立 PostgreSQL、Redis、HTTPS 和进程配置见 [README 的生产部署说明](README.md#配置与生产部署)；不要把本机开发端口直接暴露到公网。

## 1. 全新安装

准备 Git、Node.js 22.12+（建议 24 LTS）、uv、C 编译工具和 make。Mac 编译工具可用 `xcode-select --install` 安装。

```sh
git clone https://github.com/x781047225/Amazon-ERP.git
cd Amazon-ERP
bash scripts/setup.sh
```

安装会构建前端、生成本机数据库密码和授权加密密钥；这些资料保存在私有的 `config/local.json`，不进入 Git。没有通用默认登录密码。

第一次启动，用下列命令安全输入管理员密码（至少 12 位，不会显示在终端或保存到命令历史）：

```sh
./backend/.venv/bin/python - <<'PY'
import getpass, os, subprocess
password = getpass.getpass('设置 admin 密码（至少12位）：')
if len(password) < 12:
    raise SystemExit('密码过短，未启动。')
os.environ['ERP_ADMIN_PASSWORD'] = password
subprocess.run(['./backend/.venv/bin/python', 'scripts/control.py', 'start'], check=True)
PY
```

打开 `http://127.0.0.1:8787`，账号为 `admin`。已有数据库不会通过此变量覆盖密码。

以后启动、停止：

```sh
./backend/.venv/bin/python scripts/control.py start
./backend/.venv/bin/python scripts/control.py stop
```

## 2. 首次配置与同步

1. 设置 → 店铺授权管理：填写店铺的 SP-API 授权、代理；广告授权单独填写。凭据由页面维护，加密保存到数据库。
2. 用户管理：新增用户、角色，分配菜单操作权限以及店铺、仓库范围。
3. 设置 → 同步数据：分别初始化 Listing、订单、库存、货件、利润结算、广告活动、STA任务，再设置需要的定时周期。
4. 同步失败时先查看该行错误说明和任务记录；检查授权、代理和接口权限，不要重复清库。服务器和后台任务进程需持续运行。

## 3. 更新代码

先关闭定时同步，等待正在运行的任务结束，特别是调价、Listing 修改、STA 提交。然后停止服务：

```sh
./backend/.venv/bin/python scripts/control.py stop
```

确认停止完成（`data/supervisor.pid` 消失）后更新并启动：

```sh
git pull --ff-only
bash scripts/setup.sh
./backend/.venv/bin/python scripts/control.py start
```

启动会自动执行数据库迁移。保留 `config/`、`data/`；不要用测试脚本操作业务库。启动后检查 `/api/health`、登录、店铺授权和任务进度，再按需要重新开启定时同步。

## 4. 换 Mac：恢复已有数据

**数据库备份和原授权加密密钥必须成对迁移。不能生成新密钥后直接解密旧授权。** 本节只说明操作，不会自动为当前机器创建备份。

旧机器：准备 `scripts/backup.py` 生成的逻辑备份，以及旧 `config/local.json` 中的 `credential_key`。如原来使用 `ERP_CREDENTIAL_KEY`，需要保存该环境变量的原值。通过受控私密渠道传送，不能上传 Git、发到聊天或放进公开文档。

新机器操作：

1. 获取**与备份 schema 相同版本**的代码并运行安装。暂时不要启动完整应用，避免自动创建 admin 或启动任务。
2. 恢复原 `credential_key`（或设置原 `ERP_CREDENTIAL_KEY`）。新机器的数据库/Redis 密码可以保留；不要覆盖旧授权密钥。确保 `config/local.json` 仅当前用户可读：`chmod 600 config/local.json`。
3. 将备份放入 `backups/`。在终端 A 只启动数据库，保持窗口打开：

   ```sh
   node scripts/postgres.mjs
   ```

4. 终端 B 在项目根目录执行：

   ```sh
   cd backend
   .venv/bin/alembic upgrade head
   cd ..
   ./backend/.venv/bin/python scripts/restore.py backups/orders-时间.json.gz
   ```

   把文件名替换为实际备份名。恢复脚本要求空库，允许移除空库迁移生成的唯一默认查看员角色；拒绝覆盖已有用户或业务数据。加密密钥不匹配时，在写入前停止。

5. 恢复成功后，在终端 A 按 Ctrl+C 停止单独数据库，再正常启动 ERP。
6. 使用备份中的账号登录，核对授权、产品、库存、货件和结算。恢复后定时任务关闭，未完成任务暂停，网页登录会话不恢复，旧个人 Token 已撤销；需要时重新生成 Token。对调价、Listing、STA 等未完成外部操作，先查询亚马逊状态，确认后再手动操作。
7. 若需要升级到新版本，恢复成功并核对后，再按“更新代码”执行。不要直接把旧 schema 备份塞进不同版本数据库。

## 5. 发布前检查与故障排查

```sh
./backend/.venv/bin/python scripts/check_secrets.py --local-db
git add .
./backend/.venv/bin/python scripts/check_secrets.py --staged --local-db
git diff --cached --stat
```

扫描失败时先处理，不要直接跳过。`--staged` 检查实际将提交的文件；`--local-db` 额外只读比对当前机器的店铺凭据和业务编号，不打印秘密值。

- Git 只放源码、锁文件、迁移、脱敏测试和公共模板。配置、密钥、数据库、日志、备份、截图、浏览器会话都不提交。
- smoke 脚本的登录密码通过 `ERP_SMOKE_PASSWORD` 提供，只在独立测试环境运行，不能使用真实生产账号；测试数据使用 Demo 标识。
- 启动失败查看 `logs/supervisor.log`、`logs/api.log`，不要把带凭据的日志原文上传。
- 页面没更新：重新构建前端并刷新浏览器；后端改动需要重启服务。
- 无法解密店铺授权：检查原密钥及环境变量覆盖；不要删除授权记录或随意生成新密钥。


### 编辑 Listing 提示“品类模板待同步”

旧版本的批量 Listing 同步可能只导入商品资料，未下载品类编辑模板。更新代码、重新运行安装构建并重启服务后，打开商品编辑页会自动补载缺少的模板；失败时点击“加载编辑模板”重试，并检查页面返回的授权或网络错误。也可以在“设置 → 同步数据”重新初始化 Listing，批量补齐各店铺、站点和品类的模板。无需删除订单或商品数据。模板加载只读取亚马逊资料，不提交商品修改。
