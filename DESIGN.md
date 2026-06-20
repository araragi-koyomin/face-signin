# 人脸签到系统 — 设计文档

## 1. 系统概述

基于百度智能云人脸识别服务的人脸签到系统。用户通过手机端拍照上传，服务器调用百度云端人脸搜索 API 完成身份匹配，无需设备关联目标人脸。支持照片库管理、签到记录查看等功能。

支持三种访问方式：
- **PWA** — 手机浏览器直接打开（已支持 Service Worker 离线缓存）
- **APK** — 安装为原生 Android 应用（WebView 壳，静态资源本地打包）
- **内网穿透** — 本地开发环境通过 ngrok 暴露公网域名

## 2. 系统架构

```
                        ┌──────────────────┐
                        │   Android APK     │
                        │  (WebView 壳)     │
                        │  静态资源本地打包  │
                        └────────┬─────────┘
                                 │ HTTPS
                ┌────────────────┼────────────────┐
                │                │                │
       ┌────────▼───┐   ┌───────▼───────┐        │
       │  ngrok 隧道  │   │  Render Cloud │        │
       │ (开发/测试)  │   │   (生产)      │        │
       └────────┬───┘   └───────┬───────┘        │
                │               │                │
                └───────┬───────┘                │
                        │                        │
               ┌────────▼────────────────────────▼──┐
               │          FastAPI (Docker)           │
               │  ┌──────────────────────────────┐   │
               │  │  限流中间件 (per-IP, 滑动窗口) │   │
               │  │  用户缓存 (TTL 60s, 512 条)   │   │
               │  └──────────────────────────────┘   │
               │  ┌────────────┐  ┌────────────┐     │
               │  │  Uvicorn   │  │ PostgreSQL  │     │
               │  │  workers=N │──│  (Managed)  │     │
               │  └─────┬──────┘  └────────────┘     │
               └────────┼────────────────────────────┘
                        │ HTTPS
               ┌────────▼──────────┐
               │  百度智能云 FRS   │
               │  ┌─────────────┐  │
               │  │ Semaphore(1)│  │  ← QPS=1 串行化
               │  │ 指数退避重试 │  │
               │  └─────────────┘  │
               │  人脸搜索 M:N      │
               │  人脸库管理        │
               └───────────────────┘
```

### 部署结构

| 组件 | 方式 | 说明 |
|------|------|------|
| API 服务 | Render Web Service (Docker) | FastAPI + Uvicorn (多 Worker) |
| 数据库 | Render Managed PostgreSQL | 免费套餐 |
| 文件存储 | Render Persistent Disk | 10GB 照片存储 |
| 人脸识别 | 百度智能云 API | 人脸检测 + 人脸搜索 M:N |
| 内网穿透 | ngrok | 本地开发暴露公网 HTTPS 地址 |
| 移动端 | TWA/WebView APK | 静态资源打包进 APK，API 走 HTTPS |

## 3. 技术选型

| 层级 | 技术 | 选型理由 |
|------|------|----------|
| 后端框架 | **Python FastAPI** | 异步支持、自动生成 Swagger 文档、Pydantic 类型校验 |
| ASGI 服务器 | **Uvicorn (workers=N)** | 多 Worker 进程，自动检测 CPU 核心数分配 |
| 数据库 | **PostgreSQL 16** | 成熟的关系型数据库，支持 UUID、JSONB、窗口函数 |
| ORM | **SQLAlchemy 2.0 (async)** | 异步支持、声明式模型、连接池 pool_size=20 |
| 认证 | **JWT (python-jose)** | 无状态认证 + TTL 缓存减少 DB 查询 |
| 密码哈希 | **bcrypt** | 主流密码哈希算法，抗暴力破解 |
| HTTP 客户端 | **httpx (共享单例)** | 全局连接池复用（keepalive 20, max 50） |
| 容器化 | **Docker + Docker Compose** | 本地开发环境一致性 |
| 部署 | **Render (Blueprint)** | 支持 Dockerfile + 托管 PostgreSQL，一键部署 |
| 限流 | **自研滑动窗口** | per-IP 限流，API 30次/分，静态 120次/分 |
| 缓存 | **自研 TTLCache** | JWT 用户查询 60s 过期，最大 512 条 |
| 并发控制 | **asyncio.Semaphore(1)** | 百度 QPS=1 串行化 + 指数退避重试 |

### 百度智能云 API

| 接口 | 端点 | 用途 |
|------|------|------|
| 人脸注册 | `POST /faceset/user/add` | 将人脸照片注册到百度云端人脸库 |
| 人脸搜索 | `POST /search` | M:N 搜索，在指定人脸库中匹配人脸 |
| 人脸删除 | `POST /faceset/face/delete` | 从百度人脸库删除指定人脸 |
| 鉴权 | `POST /oauth/2.0/token` | 获取 Access Token（async Lock 防并发刷新） |

## 4. 数据模型

### 4.1 数据库 ER 图

```
┌──────────┐       ┌──────────┐       ┌──────────────┐
│   User   │ 1───N │   Face   │       │  Attendance   │
├──────────┤       ├──────────┤       ├──────────────┤
│ id (PK)  │       │ id (PK)  │       │ id (PK)      │
│ username │       │ user_id  │──FK   │ user_id      │──FK
│ password │       │ token    │       │ face_id      │──FK (可选)
│ name     │       │ group_id │       │ photo_path   │
│ role     │       │ image    │       │ confidence   │
│ created  │       │ created  │       │ timestamp    │
│ updated  │       └──────────┘       └──────────────┘
└──────────┘                              ▲
     ▲                                    │
     └────────────────────────────────────┘
                  1───N
```

### 4.2 表结构

**users** — 用户表

| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID (PK) | 主键 |
| username | VARCHAR(64) UNIQUE | 登录用户名 |
| password_hash | VARCHAR(256) | bcrypt 哈希 |
| display_name | VARCHAR(128) | 显示名称 |
| role | VARCHAR(16) | 角色（admin/user） |
| created_at | TIMESTAMPTZ | 创建时间 |
| updated_at | TIMESTAMPTZ | 更新时间 |

**faces** — 人脸注册表

| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID (PK) | 主键 |
| user_id | UUID (FK→users) | 关联用户 |
| baidu_face_token | VARCHAR(256) UNIQUE | 百度云端人脸标识 |
| baidu_group_id | VARCHAR(64) | 百度云端人脸库 ID |
| image_path | VARCHAR(512) | 本地照片路径 |
| created_at | TIMESTAMPTZ | 注册时间 |

**attendance** — 签到记录表

| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID (PK) | 主键 |
| user_id | UUID (FK→users) | 签到用户 |
| face_id | UUID (FK→faces, NULLABLE) | 匹配的人脸记录 |
| photo_path | VARCHAR(512) | 签到照片路径 |
| confidence | FLOAT | 百度返回置信度 |
| timestamp | TIMESTAMPTZ | 签到时间 |

### 4.3 关键设计决策

- **百度 user_id 格式**：PostgreSQL UUID 含连字符 `-`，百度 API 不接受。注册时去除连字符传递给百度，搜索返回时用 `uuid.UUID(str)` 还原。
- **face_id 可为空**：搜索返回的 `user_list` 不含 `face_token`，签到记录通过 `user_id` 反查关联的人脸记录。
- **百度 Access Token 服务端缓存**：全局变量 + `asyncio.Lock` 防止并发刷新，过期提前 60s 续期。

## 5. API 设计

### 5.1 认证相关

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| POST | `/api/auth/register` | 无 | 注册用户 |
| POST | `/api/auth/register-with-face` | 无 | 注册用户 + 人脸照片 |
| POST | `/api/auth/login` | 无 | 登录，返回 JWT |
| GET | `/api/auth/me` | JWT | 获取当前用户信息 |

### 5.2 人脸库管理（需登录）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/faces?user_id=` | 上传照片并注册到百度人脸库 |
| GET | `/api/faces` | 列出所有人脸记录 |
| DELETE | `/api/faces/{id}` | 删除人脸（本地 + 百度云端） |

### 5.3 签到（无需登录）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/sign-in` | 上传照片 → 百度搜索匹配 → 写签到记录 |

### 5.4 记录查询（需登录）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/records?date=&user_id=` | 查询签到记录，支持日期和用户筛选 |

## 6. 前端页面

### 6.1 页面路由

| 路径 | 页面 | 认证 |
|------|------|------|
| `/` | 重定向到签到页 | 无 |
| `/app/signin` | 签到页（拍照/上传/结果） | 无 |
| `/app/login` | 登录页 | 无 |
| `/app/register` | 注册页（信息 + 人脸照片） | 无 |
| `/app/dashboard` | 控制台（统计 + 快捷入口） | JWT |
| `/app/faces` | 人脸库管理（增删） | JWT |
| `/app/records` | 签到记录查看 | JWT |

### 6.2 技术实现

- 纯 HTML/CSS/JS，无前端框架，零构建步骤
- Mobile-first 自适应布局（320px ~ 480px）
- 支持摄像头拍照（`getUserMedia`）和文件上传
- JWT 存储于 `localStorage`，页面加载时校验
- 底部固定导航栏，5 个页面一键切换
- PWA 支持：manifest.json + Service Worker + Apple meta 标签

### 6.3 APK 客户端

- **类型**：WebView 壳（非 TWA，绕过 ngrok 域名验证限制）
- **静态资源**：HTML / CSS / JS / 图标打包进 APK，本地加载
- **API 请求**：通过 WebView 直连后端（注入 `ngrok-skip-browser-warning` 头绕过拦截页）
- **权限**：`INTERNET` + `CAMERA`（支持 `getUserMedia` 摄像头预览）
- **构建**：Gradle + Android SDK 34，minSdk 21

## 7. 签到核心流程

```
用户拍照上传
      │
      ▼
POST /api/sign-in (multipart/form-data)
      │
      ▼
服务器保存照片到 uploads/signin/
      │
      ▼
base64 编码 → Semaphore(1) 获取令牌
      │
      ▼
调用百度 /search API（QPS=1 串行化）
      │
      ├─ 匹配成功 (score ≥ 80)
      │     │
      │     ├─ baidu_user_id → 还原 UUID → 查 users 表
      │     ├─ user_id → 查 faces 表获取 face_id
      │     ├─ 写入 attendance 表
      │     └─ 返回 { success: true, display_name, confidence }
      │
      └─ 匹配失败 / 百度 API 异常
            │
            ├─ 错误码 18/19 (QPS 超限) → 指数退避重试 (最多 3 次)
            └─ 其他错误 → 返回 { success: false, message }
```

## 8. 高并发处理策略

### 8.1 并发架构

```
请求流入
    │
    ▼
┌─────────────┐
│ IP 限流      │ ← 滑动窗口 per-IP (API: 30/min, 静态: 120/min)
└──────┬──────┘
       │
       ▼
┌─────────────┐
│ Uvicorn      │ ← 8 Workers (自动检测 CPU)
│ (多进程)     │    每个 Worker 独立持有 Semaphore / 限流器 / 缓存
└──────┬──────┘
       │
       ├──────────────────────────┐
       ▼                          ▼
┌─────────────┐          ┌─────────────┐
│ 读请求       │          │ 写请求       │
│ (认证/列表)  │          │ (签到/注册)  │
│ 完全并行     │          │ Semaphore(1) │
│ + JWT 缓存   │          │ 串行化       │
└──────┬──────┘          └──────┬──────┘
       │                        │
       ▼                        ▼
┌─────────────────────────────────────┐
│        httpx 共享连接池              │
│  keepalive: 20, max_conn: 50       │
│  百度 Token: Lock 防并发刷新        │
└─────────────────┬───────────────────┘
                  │
                  ▼
┌─────────────────────────────────────┐
│          百度智能云 API              │
│         QPS=1 (免费版)              │
│    超限自动重试 (指数退避, 3 次)     │
└─────────────────────────────────────┘
```

### 8.2 详细策略

| 策略 | 实现 | 位置 |
|------|------|------|
| 多 Worker 进程 | `uvicorn.run(workers=N)`, N = min(max(2, cpu//2), 8) | `start.py` |
| Baidu QPS 控制 | `asyncio.Semaphore(1)` 全局串行化 | `app/services/baidu_face.py` |
| QPS 超限重试 | 错误码 18/19 → 指数退避 (0.5s, 1s, 2s) | `app/services/baidu_face.py::_call_baidu` |
| HTTP 连接复用 | 共享 `httpx.AsyncClient` 单例，异步连接池 | `app/services/baidu_face.py::get_http_client` |
| Token 刷新锁 | `asyncio.Lock` 防止并发刷新 Access Token | `app/services/baidu_face.py::_get_access_token` |
| IP 限流 | `RateLimiter` 滑动窗口，API 30/min，静态 120/min | `app/concurrency.py` + `app/main.py` |
| 用户查询缓存 | `TTLCache` maxsize=512, ttl=60s | `app/concurrency.py` + `app/services/auth.py` |
| 连接池 | SQLAlchemy pool_size=20, max_overflow=10 | `app/database.py` |
| 优雅关闭 | lifespan 中 `close_http_client()` + `engine.dispose()` | `app/main.py` |

### 8.3 瓶颈分析

| 瓶颈 | 限制 | 缓解措施 |
|------|------|----------|
| 百度 QPS=1 | 免费版硬限制 | Semaphore(1) 串行化 + 重试，读操作完全不受影响 |
| 单机内存 | Worker 数 × 缓存大小 | TTLCache 每 Worker 512 条，8 Workers 约 4096 条 |
| PostgreSQL | Render 免费版连接数限制 | pool_size=20 + overflow=10，连接高效复用 |
| ngrok 免费版 | 随机子域名 + 警告拦截页 | APK 注入 bypass 头；生产环境部署到 Render 固定域名 |

## 9. CI/CD 流水线

### 9.1 流水线结构

```
Push/PR to main
      │
      ├─► test       (Python 3.12, 22 tests, ~22s)
      │       └─► 上传 junit.xml 报告
      │
      ├─► stress     (依赖 test, 5 轮完整测试)
      │       └─► 验证并发稳定性
      │
      ├─► docker     (依赖 test, 构建镜像 + 启动验证)
      │       └─► 确认镜像可用
      │
      └─► summary    (汇总 Gate)
```

### 9.2 测试套件（22 个用例）

**核心并发原语** (`tests/test_concurrency.py` — 14 tests)

| 模块 | 测试 | 验证点 |
|------|------|--------|
| RateLimiter | `test_allow_within_limit` | 配额内全部放行 |
| RateLimiter | `test_deny_when_exceeded` | 超配额拒绝 |
| RateLimiter | `test_different_ips_independent` | IP 独立计数 |
| RateLimiter | `test_window_resets_after_expiry` | 窗口过期后恢复 |
| RateLimiter | `test_high_concurrency_same_ip` | 300 并发 → 50 通过 |
| RateLimiter | `test_concurrent_mixed_ips` | 20 IP × 15 请求 → 每 IP 10 通过 |
| TTLCache | `test_set_and_get` | 基本读写 |
| TTLCache | `test_miss_returns_none` | 未命中返回 None |
| TTLCache | `test_expiry` | 0.2s 后过期 |
| TTLCache | `test_maxsize_eviction` | 超量驱逐旧条目 |
| TTLCache | `test_delete` | 显式删除 |
| TTLCache | `test_concurrent_reads` | 100 并发读无损坏 |
| TTLCache | `test_concurrent_writes_same_key` | 100 并发写无损坏 |
| TTLCache | `test_does_not_hold_expired_entries` | 过期后清空 |

**集成测试** (`tests/test_api_concurrency.py` — 8 tests)

| 模块 | 测试 | 验证点 |
|------|------|--------|
| BaiduSemaphore | `test_serializes_calls` | 10 并发 → max=1 在途 |
| BaiduSemaphore | `test_throughput_serial` | 10 × 50ms → ~500ms |
| BaiduSemaphore | `test_fair_fifo` | FIFO 公平性 |
| RateLimiter | `test_massive_concurrency` | 2000 请求 100 IP → 全通过 |
| TTLCache | `test_large_dataset` | 1000 条 → 保留 500 |
| TTLCache | `test_concurrent_throughput` | 读写混合压力 |
| Resource | `test_http_client_singleton` | 单例复用 |
| Resource | `test_http_client_recreate_after_close` | 关闭后重建 |

### 9.3 成本控制

- 所有测试使用 `unittest.mock` / `AsyncMock`，0 次真实百度 API 调用
- 0 次真实数据库连接
- 纯内存运行，22 个测试 < 5s 完成
- CI 免费额度充足（GitHub Actions public repo 无限免费）

## 10. 演进路线

| 阶段 | 内容 | 状态 |
|------|------|------|
| Phase 1 | 基础功能：注册/登录/签到/人脸库 CRUD | ✓ 已完成 |
| Phase 2 | 内网穿透部署（ngrok + APK 客户端） | ✓ 已完成 |
| Phase 3 | 高并发增强（多 Worker / 限流 / 缓存 / Semaphore） | ✓ 已完成 |
| Phase 4 | CI/CD 流水线（测试 / 压力 / 镜像构建） | ✓ 已完成 |
| Phase 5 | Render 生产部署（render.yaml 已有） | 待部署 |
| Phase 6 | Redis 分布式缓存 / 分布式限流 | 待扩展 |
| Phase 7 | 百度 QPS 升级到付费版 + 并发扩容 | 待评估 |
