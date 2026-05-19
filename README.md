# yansd-ss

基于 `shadowsocks/shadowsocks-libev` 的多端口管理面板，单容器运行，宿主机网络模式。

## 功能

- 多端口/多密码管理：通过 Web 界面动态添加、删除 SS 端口
- 端口有效期管理：支持设置到期时间，过期自动停止访问
- 简易 Web 管理面板：登录鉴权、端口列表、随机密码生成
- 分流规则管理：可视化/原文编辑，支持一键恢复
- 数据持久化：使用 MongoDB 存储，方便迁移和扩展
- 首次访问引导设置：管理员密码和 MongoDB 连接在 Web 界面配置
- 单容器部署：supervisord 管理 ss-manager + Flask 进程

## 快速部署

```bash
docker run -d \
  --name yansd-ss \
  --network host \
  --restart unless-stopped \
  -v /usr/ss-data:/data \
  ghcr.io/fistbaozi/yansd-ss:latest
```

### 首次设置

浏览器打开 `http://你的服务器IP:15231`，首次访问会自动跳转到设置页面：

1. 设置管理员登录密码
2. 填写 MongoDB 连接字符串（如 `mongodb://user:pass@host:27017/yansd`）
3. 点击"完成设置"

设置完成后自动跳转到登录页面。配置保存在 `/data/config.json`。

### 管理端口

在 Web 界面中：
- 填写端口号（1024-65535）、密码、备注、有效期，点击"添加"
- 点击"设期/改期"管理端口有效期，过期自动停止访问
- 点击"删除"移除不需要的端口
- 点击"随机"自动生成安全密码


## 客户端连接

添加端口后，在 SS 客户端中配置：

| 配置项 | 值 |
|--------|-----|
| 服务器 | 你的服务器 IP |
| 端口 | 在面板中添加的端口 |
| 密码 | 在面板中设置的密码 |
| 加密方式 | chacha20-ietf-poly1305（或你配置的） |

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `WEB_PORT` | 15231 | Web 管理面板端口 |
| `SS_METHOD` | chacha20-ietf-poly1305 | SS 加密方式 |
| `SS_MANAGER_PORT` | 6001 | ss-manager 内部管理端口 |

> 管理员密码和 MongoDB 连接字符串通过 Web 界面首次访问时设置，不再使用环境变量。

## 支持的加密方式

- `chacha20-ietf-poly1305`（推荐）
- `aes-256-gcm`
- `aes-128-gcm`
- `aes-256-cfb`
- `aes-128-cfb`

## 数据持久化

- **配置文件**：`/data/config.json` — 管理员密码哈希 + MongoDB 连接字符串
- **端口数据**：存储在 MongoDB 数据库中，方便跨服务器迁移
