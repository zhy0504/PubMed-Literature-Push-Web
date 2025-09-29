# 时区配置说明

## 概述

从版本 v2.1.0 开始，PubMed Literature Push 支持灵活的时区配置，不再强制使用北京时间。

## 配置方法

### 1. 环境变量配置

在 `.env` 文件中设置时区：

```bash
# 设置时区（使用标准的 TZ 环境变量）
TZ=Asia/Shanghai    # 北京时间
```

### 2. Docker 配置

在 `docker-compose.yml` 中设置：

```yaml
services:
  app:
    environment:
      - TZ=Asia/Shanghai
```

或者在启动时传递环境变量：

```bash
# 使用北京时间启动
docker run -e TZ=Asia/Shanghai pubmed-app

# 使用纽约时间启动  
docker run -e TZ=America/New_York pubmed-app

# 使用UTC时间启动
docker run -e TZ=UTC pubmed-app
```

## 支持的时区

支持所有标准的 IANA 时区数据库时区标识符：

### 常用时区示例

| 时区标识符 | 描述 | UTC偏移 |
|-----------|------|---------|
| `Asia/Shanghai` | 北京时间 | UTC+8 |
| `Asia/Tokyo` | 东京时间 | UTC+9 |
| `America/New_York` | 纽约时间 | UTC-5/-4 |
| `America/Los_Angeles` | 洛杉矶时间 | UTC-8/-7 |
| `Europe/London` | 伦敦时间 | UTC+0/+1 |
| `Europe/Paris` | 巴黎时间 | UTC+1/+2 |
| `UTC` | 协调世界时 | UTC+0 |

完整时区列表请参考：https://en.wikipedia.org/wiki/List_of_tz_database_time_zones

## 功能影响

时区配置会影响以下功能：

1. **定时推送**：推送时间将基于配置的时区执行
2. **日志时间戳**：所有日志记录使用配置的时区
3. **数据库时间**：数据创建和更新时间使用配置的时区
4. **管理员界面**：显示的时间信息使用配置的时区
5. **文章发布日期**：PubMed文章日期转换为配置的时区

## 验证配置

在管理员页面的"推送管理"部分，可以查看：

- **系统时区**：当前配置的时区
- **当前时间**：基于配置时区的当前时间
- **下次执行**：调度器下次执行时间

## 注意事项

1. **默认时区**：如果未配置 `TZ` 或 `TIMEZONE` 环境变量，系统默认使用 `Asia/Shanghai`
2. **时区验证**：系统会验证时区配置的有效性，无效时区会回退到默认时区
3. **向后兼容**：原有的 `beijing_now()` 等函数仍然可用，但现在使用配置的时区
4. **容器重启**：修改时区配置后需要重启容器生效

## 示例：配置不同时区

### 纽约时间配置
```bash
# .env 文件
TZ=America/New_York
```

用户设置9:00推送，将在纽约时间上午9点执行推送。

### UTC时间配置
```bash
# .env 文件
TZ=UTC
```

用户设置9:00推送，将在UTC时间上午9点执行推送。

## 故障排除

1. **时区不生效**：检查环境变量是否正确设置，重启容器
2. **时区格式错误**：使用标准 IANA 时区标识符，避免使用缩写（如 EST、PST）
3. **推送时间异常**：在管理员页面查看"系统时区"和"当前时间"确认配置正确