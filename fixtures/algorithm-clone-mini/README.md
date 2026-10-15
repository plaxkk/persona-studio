# X For You Feed Algorithm —— 测试夹具（节选）

**这不是上游源码，是给离线测试用的一份节选。** 目录结构、`param!(...)` 的写法、
可见性规则的结构都照抄上游 `xai-org/x-algorithm`（Apache-2.0），但**数值是改过的**
——改得和上游不一样，测试才能证明清单里的数是从源码解析出来的，而不是从别处抄来的。

- 上游仓库：`xai-org/x-algorithm`
- 参照的下游提交：`92f0918f6cb52837cb53a8c790c68f906b2c0db1`（`main`）
- 许可：Apache-2.0（见 `LICENSE`）
- 抄了结构的文件：`home-mixer/params/param.rs`、`vm-ranker/params.rs`、
  `xai-value-model/weights.rs`、`user-cred-v2/UserCredV2Config.scala`、
  `visibility-filtering/**`

`README.md` 的首行必须含 `For You`——解析脚本靠这一行确认目录看着像不像那份仓库。

这份夹具**没有 `.git`**，所以跑脚本时要显式 `--sha`。它永远不会成为运行期依赖：
线上那份清单是构建期打进的 JSON，`.local/` 下的真克隆只是维护期的方便。
