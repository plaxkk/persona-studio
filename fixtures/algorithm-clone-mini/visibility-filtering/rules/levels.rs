// 测试夹具节选，不是上游源码。见 fixtures/algorithm-clone-mini/README.md。
// 取自 xai-org/x-algorithm（Apache-2.0）的 visibility-filtering/rules/levels.rs，
// 参照提交 92f0918f6cb52837cb53a8c790c68f906b2c0db1。

// 推荐位比关注流严：夹具里关注流 2 条、推荐位 3 条，多出来的那 1 条就是
// 「只在推荐位被拿掉」的那一类。上游真实的差集是 39 对 65。
pub(super) const TIMELINE_HOME: &[&Rule] = &[
    &RULE_EVERYONE_DROP,
    &RULE_NON_FOLLOWER_DROP,
];

pub(super) const TIMELINE_HOME_RECOMMENDATIONS: &[&Rule] = &[
    &RULE_EVERYONE_DROP,
    &RULE_NON_FOLLOWER_DROP,
    &RULE_RECOMMENDATIONS_ONLY_DROP,
];
