# 规范发布与采用追踪

公共场所外语译写规范以**签名发布包**发布，跟踪机场、医院、景区、道路、政务网页等
载体对各版本的真实采用情况：差异与影响清单、专业确认后的更新要求、限时豁免、离线
幂等回执，以及分批换版期间现场实际与当前推荐的公开对照。

服务通过 HTTP 接口交换业务事件，使用 SQLite 文件保存本地状态。监听端口由 `PORT`
指定，数据文件由 `DATABASE_PATH` 指定，发布包签名主密钥由 `SIGNING_SECRET` 指定，
管理端密钥由 `ADMIN_KEY` 指定（开发默认值见 `app/security.py`，生产必须覆盖）。
`contracts/entities.json` 记录稳定字段，`fixtures/example.json` 提供不含真实身份信息
的示例。

## 本地开发

```bash
make migrate   # 初始化/升级数据文件（启动服务时也会自动迁移）
make test      # 执行全部自动化检查
make run       # 启动服务（默认 :8080）
```

也可 `docker compose up --build`，宿主机端口用 `APP_PORT` 调整。交互式文档在
`/docs`。

## 接口一览

管理端需请求头 `X-Admin-Key`；单位端需 `X-Unit-Id` 与 `X-Unit-Token`（注册时签发）。

| 角色 | 方法与路径 | 说明 |
| --- | --- | --- |
| 管理 | `POST /admin/units` | 注册责任单位与可管理场所类别，返回 token |
| 管理 | `POST /admin/units/{id}/subscriptions` | 按管理范围订阅 |
| 管理 | `POST /admin/packages` | 签发签名发布包（语种/类别/词条/例外/依据/生效时间） |
| 管理 | `POST /admin/packages/{id}/withdraw` | 废止（追加事件，包内容不变） |
| 管理 | `POST /admin/diffs` | 生成新旧包可复核差异与影响清单 |
| 管理 | `POST /admin/candidates/{id}/decision` | 专业确认/驳回；确认才生成更新要求 |
| 管理 | `POST /admin/requirements/{id}/exemption/approve` `/revoke` | 豁免审批/撤销 |
| 管理 | `POST /admin/exemptions/sweep` | 追加到期决定 |
| 管理 | `GET  /admin/revisions/{package_id}/impact` | 受影响载体/单位/承诺/真实完成比例 |
| 单位 | `POST /units/me/subscriptions` | 自助订阅（限本单位管理范围） |
| 单位 | `POST /units/me/catalog-reports` | 上报标识目录与引用版本（batch 幂等） |
| 单位 | `GET  /units/me/notifications` | 发布包投递与回执状态 |
| 单位 | `POST /units/me/requirements/{id}/promise` | 上报承诺期限 |
| 单位 | `POST /units/me/requirements/{id}/exemption` | 申请限时豁免 |
| 公开 | `GET  /public/signs/{sign_ref}` | 现场实际 vs 当前推荐（aligned/differs） |
| 公开 | `GET  /public/packages/{package_id}` | 历史包查询（标注是否可用于新建） |
| 公开 | `GET  /public/versions/{vid}` | 由标识版本反查当时合法引用的发布包 |

## 关键不变量

- 发布包内容签名可离线复核；生命周期状态由事件链推导，历史不可变。
- 撤回/未生效包不得引用；新建标识只能引用当前推荐包；旧包仅供历史查询。
- 同一标识词条的引用版本只进不退；离线回执重放不产生新版本、不回退进度。
- 自动分析只能提出候选，专业确认后才能要求载体更新。
- 豁免的批准、到期、撤销均留痕且互不覆盖。
- 真实完成只能由现场版本推进自动判定，管理者看到的是真实完成比例。

详见 `docs/domain.md`。
