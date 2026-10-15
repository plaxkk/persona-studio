// 测试夹具节选，不是上游源码。见 fixtures/algorithm-clone-mini/README.md。
// 取自 xai-org/x-algorithm（Apache-2.0）的 user-cred-v2/UserCredV2Config.scala，
// 参照提交 92f0918f6cb52837cb53a8c790c68f906b2c0db1，只留了需要的几行。
package com.twitter.health.platform_manipulation.user_cred_v2

import com.twitter.scalding.Args

object UserCredV2Config {
  // 带类型标注的 val：JVM 的类型名是 `Int`，不是 Rust 的 `u32`——
  // 少认一套写法，这个真实存在的值就会渲染成「暂无数据」。
  val EngagementWindowDays: Int = 7

  def fromArgs(args: Args): UserCredV2Config = {
    // getOrElse 的形态：值是第二个字符串，后面跟一个 cast。
    val jumpProbability = args.getOrElse("jump_prob", "0.2").toDouble
    val maxIterations = args.getOrElse("max_iterations", "50").toInt
    UserCredV2Config(jumpProbability = jumpProbability, maxIterations = maxIterations)
  }
}
