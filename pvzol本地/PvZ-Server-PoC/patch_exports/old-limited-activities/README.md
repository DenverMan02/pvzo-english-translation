# 历史限时活动兼容补丁包

**当前安全配置会隐藏所有缺少服务端活动数据的历史活动。不要为了显示旧入口而打开 allowlist；旧活动缺少状态/奖励数据时会导致客户端报错。**

本包保留旧版 `REQUEST_HANDLERS` 适配器，但 `old_limited_activities.py` 的历史活动 allowlist 默认为空：

- `old_limited_activities.py`：默认不生成任何历史活动列表项；为旧客户端残留请求提供安全的空状态/领奖响应。
- `REQUEST_HANDLERS_old_activities.py`：包装现有 `REQUEST_HANDLERS` 中的活动列表、状态和领奖处理器，保留现有活动逻辑并接入历史活动。

## 接入方式

把两个 `.py` 文件复制到 `PvZ-Server-PoC` 目录（同一目录），在 `pvz_poc_server.py` 加载完 `REQUEST_HANDLERS` 后调用：

```python
from REQUEST_HANDLERS_old_activities import install_request_handlers

install_request_handlers(REQUEST_HANDLERS, _activity_server_api())
```

也可以在启动入口处这样调用：

```python
import pvz_poc_server
from REQUEST_HANDLERS_old_activities import install_request_handlers

install_request_handlers(
    pvz_poc_server.REQUEST_HANDLERS,
    pvz_poc_server._activity_server_api(),
)
```

适配器要求原有 `REQUEST_HANDLERS` 已含这三个命令：`GetActivityList`、`GetActivityStat`、`TakeReward`。按当前默认配置安装不会显示历史活动。

当前修复版服务器已内置安全处理；无需另装此兼容包。若要重新开放某项历史活动，必须先实现并核对该活动的完整服务端数据、状态及领奖逻辑，再单独启用对应 ID。
