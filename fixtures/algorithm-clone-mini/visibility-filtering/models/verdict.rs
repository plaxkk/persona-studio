// 测试夹具节选，不是上游源码。见 fixtures/algorithm-clone-mini/README.md。
// 取自 xai-org/x-algorithm（Apache-2.0）的 visibility-filtering/models/verdict.rs，
// 参照提交 92f0918f6cb52837cb53a8c790c68f906b2c0db1。

// 可见性判定不是「允许/提示/拦截/删除」四个平行档，是两维：
// 「拿掉」还是「照常展示」，展示时再带不带提示 / 媒体限制 / 互动限制。
pub enum Verdict {
    Withheld(Decided<Withholding>),
    Shown {
        notice: Decided<Notice>,
        media: Decided<MediaRestriction>,
        engagement: Decided<LimitedEngagement>,
    },
}
