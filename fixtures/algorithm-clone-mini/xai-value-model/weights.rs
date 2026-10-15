// 测试夹具节选，不是上游源码。见 fixtures/algorithm-clone-mini/README.md。
// 取自 xai-org/x-algorithm（Apache-2.0）的 xai-value-model/weights.rs，
// 参照提交 92f0918f6cb52837cb53a8c790c68f906b2c0db1。

// 负分不是直接减。上游把所有负分压进 0..NEGATIVE_SCORES_OFFSET 这一段。
// 这个常量不是 param，但少了它，页面上那些 -234 就没法解释，所以也机器抽、也给行号。
pub const NEGATIVE_SCORES_OFFSET: f64 = 0.001;

// 同名不同风格的常量：没有 pub、类型是整数——两种写法都要能认。
const FIXTURE_UNRELATED_FLAG: u32 = 7;
