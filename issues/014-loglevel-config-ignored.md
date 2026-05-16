# logLevel 配置项不生效

严重度：低

## 现象

用户在 VS Code 设置中将 `nextone.logLevel` 配为 `INFO` 或 `WARNING`，服务端日志仍然输出大量 DEBUG 级别信息。

## 根因

`activate()` 读取了配置值，但构造 `serverArgs` 时写死了 `"DEBUG"`，`logLevel` 变量未被使用：

```typescript
const logLevel = config.get<string>("logLevel", "INFO");  // 读了
// ...
const serverArgs = ["--stdio", "--log-level", "DEBUG", "--log-file", logFile];  // 没用
```

## 修复

将硬编码替换为配置变量：

```typescript
const serverArgs = ["--stdio", "--log-level", logLevel, "--log-file", logFile];
```
