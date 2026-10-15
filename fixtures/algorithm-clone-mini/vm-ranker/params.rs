// 测试夹具节选，不是上游源码。见 fixtures/algorithm-clone-mini/README.md。
// 取自 xai-org/x-algorithm（Apache-2.0）的 vm-ranker/params.rs，
// 参照提交 92f0918f6cb52837cb53a8c790c68f906b2c0db1。
use xai_feature_switches::param;

// 上游这两份文件里真的有同名参数、而且值不完全一样（本夹具里 0.99 对 0.5）。
// 清单只引 home-mixer 那一份，另一份在 curation 的 exclude 里写明原因，
// 两个值则由清单的 `conflicts` 段机器列出。
param!(FavoriteWeight, f64, "rust_home_mixer_favorite_weight", 0.5);

// 只在 vm-ranker 里定义的：站外折扣。清单收它，值应当来自这里（0.25），
// 而不是上游那份的 0.75。
param!(OonWeightFactor, f64, "rust_oon_weight_factor", 0.25);
