# 人脸签到系统 — 设计文档

## 1. 系统概述

基于百度智能云人脸识别服务的人脸签到系统。用户通过手机端拍照上传，服务器调用百度云端人脸搜索 API 完成身份匹配，无需设备关联目标人脸。支持照片库管理、签到记录查看等功能。

## 2. 系统架构

```
┌─────────────────┐        ┌──────────────────────────────────┐
│   手机浏览器      │  HTTPS │            Render Cloud           │
│  (HTML5 拍照)    │◄──────►│  ┌────────────┐  ┌────────────┐  │
└─────────────────┘        │  │  FastAPI   │  │ PostgreSQL  │  │
                           │  │  (Docker)  │──│  (Managed)  │  │
                           │  └─────┬──────┘  └────────────┘  │
                           │        │ HTTPS                    │
                           └────────┼──────────────────────────┘
                                    │
                           ┌────────▼──────────┐
                           │  百度智能云 FRS   │
                           │  人脸搜索 M:N      │
                           │  人脸库管理        │
                           └───────────────────┘
```

### 部署结构

| 组件 | 方式 | 说明 |
|------|------|------|
| API 服务 | Render Web Service (Docker) | FastAPI + Uvicorn |
| 数据库 | Render Managed PostgreSQL | 免费套餐 |
| 文件存储 | Render Persistent Disk | 10GB 照片存储 |
| 人脸识别 | 百度智能云 API | 人脸检测 + 人脸搜索 M:N |

## 3. 技术选型

| 层级 | 技术 | 选型理由 |
|------|------|----------|
| 后端框架 | **Python FastAPI** | 异步支持、自动生成 Swagger 文档、Pydantic 类型校验 |
| ASGI 服务器 | **Uvicorn** | 高性能异步服务器，与 FastAPI 深度集成 |
| 数据库 | **PostgreSQL 16** | 成熟的关系型数据库，支持 UUID、JSONB、窗口函数 |
| ORM | **SQLAlchemy 2.0 (async)** | 异步支持、声明式模型、与 PostgreSQL 深度集成 |
| 认证 | **JWT (python-jose)** | 无状态认证，适合 API 场景 |
| 密码哈希 | **bcrypt** | 主流密码哈希算法，抗暴力破解 |
| HTTP 客户端 | **httpx (async)** | 异步 HTTP 客户端，连接池复用，用于调用百度 API |
| 容器化 | **Docker + Docker Compose** | 本地开发环境一致性 |
| 部署 | **Render (Blueprint)** | 支持 Dockerfile + 托管 PostgreSQL，一键部署 |

### 百度智能云 API

| 接口 | 端点 | 用途 |
|------|------|------|
| 人脸注册 | `POST /faceset/user/add` | 将人脸照片注册到百度云端人脸库 |
| 人脸搜索 | `POST /search` | M:N 搜索，在指定人脸库中匹配人脸 |
| 人脸删除 | `POST /faceset/face/delete` | 从百度人脸库删除指定人脸 |
| 鉴权 | `POST /oauth/2.0/token` | 获取 Access Token（服务端缓存复用） |

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
- **百度 Access Token 服务端缓存**：全局变量 + 过期时间检查，避免每次请求都鉴权。

## 5. API 设计

### 5.1 认证相关

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| POST | `/api/auth/register` | 无 | 注册用户 |
| POST | `/api/auth/login` | 无 | 登录，返回 JWT |

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
base64 编码 → 调用百度 /search API
      │
      ├─ 匹配成功 (score ≥ 80)
      │     │
      │     ├─ baidu_user_id → 还原 UUID → 查 users 表
      │     ├─ user_id → 查 faces 表获取 face_id
      │     ├─ 写入 attendance 表
      │     └─ 返回 { success: true, display_name, confidence }
      │
      └─ 匹配失败
            └─ 返回 { success: false, message }
```

## 8. 高并发处理策略

| 策略 | 实现 |
|------|------|
| 异步非阻塞 | FastAPI async + httpx.AsyncClient |
| 百度 Token 缓存 | 全局变量 + 过期时间，避免重复鉴权 |
| HTTP 连接池 | httpx 默认连接池复用 |
| 百度 QPS 限制 | 免费版 QPS=1，业务侧无需额外限流 |
| 文件存储 | 本地磁盘（Render Persistent Disk）|
| 数据库 | Render Managed PostgreSQL，连接池由 SQLAlchemy 管理 |

## 9. 测试验证结果

| 功能 | 状态 |
|------|------|
| 用户注册 + 人脸照片上传到百度云 | 通过 |
| JWT 登录认证 | 通过 |
| 签到拍照上传 → 百度人脸搜索匹配 → 签到成功 | 通过（置信度 94.43%） |
| 人脸库列出/删除 | 通过 |
| 签到记录按日期/用户筛选查询 | 通过 |
| 手机端页面拍照/上传/导航 | 通过 |
